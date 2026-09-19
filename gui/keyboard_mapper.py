"""
键盘映射器 - 把主机的按键事件转发到设备

设计: 按下/释放 键事件直接调用 hdc shell uinput 注入到设备
支持:
  - F1/F2/F3/F4 → 主页/返回/音量+/音量-
  - F5 → 电源键
  - Ctrl+C → 锁屏
  - Escape → 返回键 (部分窗口能拦截)
  - 方向键 → 设备方向键 (但需要 OHOS 内核支持)
  - 字母数字 → 设备对应字符 (通过 uinput -K -d/-u key)

启用: 在主窗口启动时 bind '<Key>' 到 handle_key_press/release
"""
import threading
from typing import Optional, Callable

from core import LogLevel, print_log


class KeyboardMapper:
    """主机键盘 -> 设备按键 注入器"""
    
    # 主机按键名 → (设备 keycode, 描述, uitest_key_name|None)
    #
    # keycode 约定: 这套 uinput -K 的 keycode 不是标准 Linux input event code,
    # 而是 ohscrcpy server 自定义的紧凑编号 (F1=1=home, F2=2=back ...).
    # F3=16/17/18 在 RK3568 OH3.2 上确认能映射到音量/电源 (项目实际跑通过).
    #
    # 第三个字段为 uitest uiInput keyEvent 接受的语义名 (若可用, 优先用此路径).
    # uitest keyEvent 在 RK3568 OH3.2 上是最稳定的主路径 (touch_injector.py 验证过),
    # 但它只支持有限的内置名 (Back/Home/Power/...); 不支持的键 (D-pad 等) 仍走 uinput -K.
    KEY_MAP = {
        # 功能键
        'F1':     (1,  'home',          'Home'),
        'F2':     (2,  'back',          'Back'),
        'F3':     (16, 'volume_up',     'VolumeUp'),
        'F4':     (17, 'volume_down',   'VolumeDown'),
        'F5':     (18, 'power',         'Power'),
        'F6':     (19, 'camera',        None),

        # 桌面焦点导航: OHOS 桌面 (Banner) 完整支持 D-pad 选择图标
        'Up':     (19, 'dpad_up',       None),
        'Down':   (20, 'dpad_down',     None),
        'Left':   (21, 'dpad_left',     None),
        'Right':  (22, 'dpad_right',    None),
        'KP_Up':     (19, 'dpad_up',    None),
        'KP_Down':   (20, 'dpad_down',  None),
        'KP_Left':   (21, 'dpad_left',  None),
        'KP_Right':  (22, 'dpad_right', None),

        # 激活 / 确认 / 表单
        'Return': (66, 'enter',         None),
        'KP_Enter': (66, 'enter',       None),
        'space':  (62, 'space',         None),
        'Tab':    (61, 'tab',           None),

        # 编辑
        'BackSpace': (67, 'backspace',  None),
        'Escape':    (2, 'back',        'Back'),  # Esc 也映射到 back

        # 列表/页面
        'Home':  (2,  'home_via_back', None),
        'End':   (123,'end',           None),
        'Prior': (92, 'page_up',       None),
        'Next':  (93, 'page_down',     None),
    }

    # 这些键按下后会持续重复 (桌面图标逐个移动需要按住)
    REPEATABLE_KEYS = frozenset({'Up', 'Down', 'Left', 'Right',
                                 'KP_Up', 'KP_Down', 'KP_Left', 'KP_Right'})
    # 自动重复触发间隔 (ms)
    REPEAT_INITIAL_DELAY_MS = 280
    REPEAT_INTERVAL_MS = 80
    
    def __init__(self, hdc_executor, enabled: bool = True):
        self.hdc = hdc_executor
        self.enabled = enabled
        self.log_title = "键盘映射"
        # 防抖: 同键 100ms 内不重复触发 (避免长按导致洪水)
        self._last_press = {}
        self._debounce_ms = 80
        # D-pad 按住自动重复 (桌面图标逐个移动)
        self._repeat_keys: dict = {}  # keysym -> {'kc','desc','timer'}
        # 字符序列注入 (字母/数字)
        self._char_map = self._build_char_map()
    
    def _build_char_map(self):
        """字母/数字 → keycode 映射 (基于 OHOS 标准 keycode)
        这是简化的 A-Z + 0-9 + 常用符号
        """
        m = {}
        # 数字 0-9 (keycode 7-16)
        for i, c in enumerate('0123456789'):
            m[c] = 7 + i
        # A-Z (keycode 29-54)
        for i, c in enumerate('abcdefghijklmnopqrstuvwxyz'):
            m[c] = 29 + i
        # 常用符号
        m[' '] = 62
        m['.'] = 56  # KEY_PERIOD
        m[','] = 55  # KEY_COMMA
        m['/'] = 76  # KEY_SLASH
        m['-'] = 69  # KEY_MINUS
        m['='] = 70  # KEY_EQUAL
        m['\n'] = 66  # KEY_ENTER
        m['\t'] = 61  # KEY_TAB
        return m
    
    def handle_key_press(self, event):
        """主机按键按下回调"""
        if not self.enabled:
            return
        # 忽略 modifier 单独按下 (Ctrl/Alt/Shift)
        if event.keysym in ('Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R'):
            return
        # 防抖 (字符键才需要; D-pad 因为有自动 repeat, 不能走防抖)
        if event.keysym not in self.REPEATABLE_KEYS:
            now_ms = int(event.time * 1000)
            last = self._last_press.get(event.keysym, 0)
            if now_ms - last < self._debounce_ms:
                return
            self._last_press[event.keysym] = now_ms

        # 1) 功能键 / 方向键 映射
        if event.keysym in self.KEY_MAP:
            keycode, desc, uitest_name = self.KEY_MAP[event.keysym]
            self._inject_key_press(keycode, desc, uitest_name)
            print_log(LogLevel.DEBUG, self.log_title,
                f"注入设备按键 down: {desc} (keycode={keycode}, host={event.keysym})")

            # D-pad 自动重复: 启动延迟定时器
            if event.keysym in self.REPEATABLE_KEYS:
                self._start_repeat(event.keysym, keycode, desc, uitest_name)
            return

        # 2) 字符输入 (字母/数字)
        char = event.char
        if char and char in self._char_map:
            keycode = self._char_map[char]
            self._inject_key_press(keycode, repr(char), None)
            print_log(LogLevel.DEBUG, self.log_title,
                f"注入字符: {char!r} (keycode={keycode})")
            return

        # 3) 其他键暂不处理
    
    def handle_key_release(self, event):
        """主机按键释放回调"""
        if not self.enabled:
            return
        if event.keysym in ('Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R'):
            return
        # 取消 D-pad 自动重复
        if event.keysym in self._repeat_keys:
            timer = self._repeat_keys.pop(event.keysym, {}).get('timer')
            if timer and timer.is_alive():
                timer.cancel()
        if event.keysym in self.KEY_MAP:
            keycode, desc, uitest_name = self.KEY_MAP[event.keysym]
            self._inject_key_release(keycode, desc, uitest_name)
            return
        char = event.char
        if char and char in self._char_map:
            keycode = self._char_map[char]
            self._inject_key_release(keycode, repr(char), None)
            return

    def _inject_key_press(self, keycode: int, desc: str, uitest_name):
        """注入按键 down.

        主路径: uitest uiInput keyEvent <name> -- RK3568 OH3.2 上最稳定 (touch_injector 验证过).
        回退: hdc shell uinput -K -d <keycode>.
        """
        if uitest_name:
            r = self.hdc.execute(
                ["shell", "uitest", "uiInput", "keyEvent", uitest_name], timeout=3)
            if r.get("success"):
                return
            print_log(LogLevel.WARN, self.log_title,
                f"uitest keyEvent {uitest_name} 失败, 改用 uinput -K (keycode={keycode})")
        try:
            self.hdc.execute(["shell", "uinput", "-K", "-d", str(keycode)], timeout=3)
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"key down 失败 keycode={keycode}: {e}")

    def _inject_key_release(self, keycode: int, desc: str, uitest_name):
        """注入按键 up.

        uitest keyEvent 是原子操作, 不需要 up.
        uinput -K 的 down/up 必须有间隔 (RK3568 OH3.2 input service 会丢弃 0ms 间隔的事件).
        """
        if uitest_name:
            return
        try:
            self.hdc.execute(["shell", "uinput", "-K", "-u", str(keycode)], timeout=3)
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"key up 失败 keycode={keycode}: {e}")

    def _start_repeat(self, keysym: str, keycode: int, desc: str, uitest_name):
        """启动 D-pad 自动重复 (按住方向键连续移动焦点).

        第一次按: 立刻触发 1 次 (上面 _inject_key_press 已完成).
        之后: 280ms 后开始以 80ms 间隔重复触发.
        """
        prev = self._repeat_keys.pop(keysym, None)
        if prev and prev.get('timer') and prev['timer'].is_alive():
            prev['timer'].cancel()
        state = {'kc': keycode, 'desc': desc, 'uitest': uitest_name, 'timer': None}
        self._repeat_keys[keysym] = state
        timer = threading.Timer(
            self.REPEAT_INITIAL_DELAY_MS / 1000.0,
            self._repeat_tick, args=(keysym,))
        timer.daemon = True
        state['timer'] = timer
        timer.start()

    def _repeat_tick(self, keysym: str):
        """D-pad 自动重复触发 (按 host key 仍在按住状态下持续运行)."""
        state = self._repeat_keys.get(keysym)
        if state is None:
            return
        self._inject_key_press(state['kc'], state['desc'], state.get('uitest'))
        self._inject_key_release(state['kc'], state['desc'], state.get('uitest'))
        timer = threading.Timer(
            self.REPEAT_INTERVAL_MS / 1000.0,
            self._repeat_tick, args=(keysym,))
        timer.daemon = True
        state['timer'] = timer
        timer.start()

    def inject_text(self, text: str):
        """一次性注入一段文本 (每个字符 down + up)."""
        for ch in text:
            if ch == '\\n':
                self._inject_key_press(66, "enter", None)
                self._inject_key_release(66, "enter", None)
            elif ch.lower() in self._char_map:
                kc = self._char_map[ch.lower()]
                self._inject_key_press(kc, repr(ch), None)
                self._inject_key_release(kc, repr(ch), None)
    
    def toggle(self):
        """切换启用状态"""
        self.enabled = not self.enabled
        print_log(LogLevel.INFO, self.log_title, f"键盘映射 {'启用' if self.enabled else '禁用'}")


__all__ = ["KeyboardMapper"]
