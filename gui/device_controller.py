#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
OpenHarmony_Scrcpy 设备控制器
"""

import tkinter as tk
from typing import Tuple, Optional, Any

from core.constants import LogLevel
from core.logger import print_log
from core.hdc_executor import HDCCommandExecutor


class DeviceController:
    """设备控制器"""
    
    KEY_MAPPINGS: dict = {
        "home": 1,         # KEY_HOME
        "back": 2,         # KEY_BACK
        "volume_up": 16,   # KEY_VOLUME_UP
        "volume_down": 17, # KEY_VOLUME_DOWN
        "power": 18,       # KEY_POWER
        "dpad_up": 19,     # KEY_DPAD_UP (OHOS 标准, 不要用作 camera)
        "dpad_down": 20,   # KEY_DPAD_DOWN
        "dpad_left": 21,   # KEY_DPAD_LEFT
        "dpad_right": 22,  # KEY_DPAD_RIGHT
        "camera": 27,      # KEY_CAMERA (OHOS 标准, 避免和 DPAD 冲突)
    }
    
    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc: HDCCommandExecutor = hdc_executor
        self.display_width: int = 0
        self.display_height: int = 0
        self.display_ratio: float = 0.0
        self.left: int = 0
        self.right: int = 0
        self.top: int = 0
        self.bottom: int = 0
        self.video_width: int = 0  # 设备原始宽度
        self.video_height: int = 0
        # 修复: video_width/height 在 mjpeg_client 路径下会被 frame.shape 覆盖成显示尺寸,
        # 导致 mouse_up 的边界检查失败 (dev_x=539 > video_width=417), 点击被丢弃.
        # 下面两个字段保存真正的设备原始分辨率, 用于 mouse 坐标转换 + 边界检查.
        self.device_video_width: int = 0
        self.device_video_height: int = 0
        self.video_canvas: Optional[tk.Canvas] = None
        self.drag_start: Optional[Tuple[int, int]] = None
        self._raw_drag: bool = False
        self._uitest_daemon_started: bool = False
        # 防抖: 同一坐标 + 短间隔的 mouse_up 视为重复 (RDP 反复 press 时会触发)
        self._last_tap_time: float = 0.0
        self._last_tap_pos: Optional[Tuple[int, int]] = None
        self.log_title: str = "设备控制器"
    
    def set_display_resolution(self, video_width: int, video_height: int, canvas_width: int, canvas_height: int) -> Tuple[int, int, float]:
        """计算并设置显示分辨率"""
        if canvas_width <= 10:
            canvas_width = 800
        if canvas_height <= 10:
            canvas_height = 600
        
        self.video_width = video_width
        self.video_height = video_height
        # 保存原始设备尺寸 (用于 mouse 坐标转换)
        self.device_video_width = video_width
        self.device_video_height = video_height
        self.display_ratio = min(canvas_width / video_width, canvas_height / video_height)
        self.display_width = int(video_width * self.display_ratio)
        self.display_height = int(video_height * self.display_ratio)
        print_log(LogLevel.INFO, self.log_title, f"显示尺寸: {self.display_width}x{self.display_height} ratio:{self.display_ratio}")

        if self.video_canvas:
            self.left = int((self.video_canvas.winfo_width() - self.display_width) / 2)
            self.right = int((self.video_canvas.winfo_width() - self.display_width) / 2 + self.display_width)
            self.top = int((self.video_canvas.winfo_height() - self.display_height) / 2)
            self.bottom = int((self.video_canvas.winfo_height() - self.display_height) / 2 + self.display_height)
        return self.display_width, self.display_height, self.display_ratio
    
    def bind_video_canvas(self, canvas: tk.Canvas) -> None:
        """绑定视频画布"""
        self.video_canvas = canvas
        
        canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        canvas.bind("<B1-Motion>", self._on_mouse_drag)
        canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
    
    def reset(self) -> None:
        """重置控制器状态（切换设备时调用）"""
        self.display_width = 0
        self.display_height = 0
        self.display_ratio = 0.0
        self.left = 0
        self.right = 0
        self.top = 0
        self.bottom = 0
        self.drag_start = None
    

    def _ensure_uitest_daemon(self) -> bool:
        """Start uitest daemon (required for uitest uiInput on RK3568 OH3.2).
        Idempotent — safe to call multiple times.
        """
        if self._uitest_daemon_started:
            return True
        try:
            r = self.hdc.execute(["shell", "uitest", "start-daemon", "uitestdaemon"], timeout=10)
            self._uitest_daemon_started = r.get("success", False)
            if self._uitest_daemon_started:
                print_log(LogLevel.INFO, self.log_title, "uitest daemon 启动成功")
            return self._uitest_daemon_started
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"uitest daemon 启动失败: {e}")
            return False

    def _send_tap_via_uitest(self, x: int, y: int) -> bool:
        """用 uitest uiInput swipe (短距离) 作为 click 回退路径.
        RK3568 OpenHarmony 3.2 上 uinput -T 的 MMI SimulateInputEvent 静默失败,
        但 uitest uiInput swipe 在某些场景下能 dispatch (主要用于 fling / swipe).
        本函数作为最终 fallback -- 不保证 click 生效。
        """
        if not self._ensure_uitest_daemon():
            return False
        # 距离 1 pixel 的 swipe 模拟 tap (很多 launcher + WebView 会识别为 click)
        r = self.hdc.execute(
            ["shell", "uitest", "uiInput", "swipe", str(x), str(y), str(x + 1), str(y + 1)],
            timeout=8,
        )
        return r.get("success", False)

    def _send_tap_via_uinput_long(self, x: int, y: int) -> bool:
        """用 uitest uiInput longClick 作为另一回退路径 (异步启动)."""
        if not self._ensure_uitest_daemon():
            return False
        return self.hdc.execute_async(
            ["shell", "uitest", "uiInput", "longClick", str(x), str(y)],
            timeout=8,
        )


    def _window_to_device_coords(self, window_x: int, window_y: int) -> Tuple[int, int]:
        """窗口坐标转设备坐标

        兜底:若 left/top 因 video_canvas 还未布局仍为 0,
        用 (canvas_w - display_w)/2 反推, 避免点击全部落到 (0,0)。
        """
        if not self.video_canvas:
            return 0, 0
        if self.display_ratio == 0:
            return 0, 0
        # 若 left/top 还未被 set_display_resolution 计算 (==0),
        # 用居中布局的公式估算, 让点击坐标仍然落在合理范围。
        if self.left == 0 and self.right == 0 and self.video_canvas.winfo_width() > 0 and self.display_width > 0:
            canvas_w = self.video_canvas.winfo_width()
            canvas_h = self.video_canvas.winfo_height()
            left = max(0, (canvas_w - self.display_width) // 2)
            top = max(0, (canvas_h - self.display_height) // 2)
        else:
            left = self.left
            top = self.top
        device_x = int((window_x - left) / self.display_ratio)
        device_y = int((window_y - top) / self.display_ratio)
        return device_x, device_y
    
    def _on_mouse_down(self, event: tk.Event) -> None:
        """鼠标按下"""
        # 修复:即使 device_controller 还没初始化完成,也要记录点击,
        # 否则所有点击都会被边界检查过滤掉(left/right/top/bottom=0)。
        if self.display_width <= 0 or self.display_height <= 0:
            print_log(LogLevel.DEBUG, self.log_title, f"未初始化,记录原始坐标 ({event.x}, {event.y})")
            self.drag_start = (event.x, event.y)
            self._raw_drag = True
            return
        if self.left <= event.x <= self.right and self.top <= event.y <= self.bottom:
            self.drag_start = (event.x, event.y)
            self._raw_drag = False
    
    def _on_mouse_drag(self, event: tk.Event) -> None:
        """鼠标拖动"""
        pass  # 实时预览可以在这里实现
    
    def _on_mouse_up(self, event: tk.Event) -> None:
        """鼠标释放"""
        if self.drag_start is None:
            return
        
        start_x, start_y = self.drag_start
        end_x, end_y = event.x, event.y
        is_raw = getattr(self, '_raw_drag', False)
        # 调试: 记录每个 mouse_up 事件
        print_log(LogLevel.DEBUG, self.log_title,
            f"mouse_up: start=({start_x},{start_y}) end=({end_x},{end_y}) raw={is_raw} "
            f"display=({self.display_width},{self.display_height}) ratio={self.display_ratio:.4f}")
        
        if is_raw or self.display_width <= 0 or self.display_height <= 0:
            # device_controller 还没初始化:用 canvas 比例粗略换算
            canvas_w = self.video_canvas.winfo_width() if self.video_canvas else 1
            canvas_h = self.video_canvas.winfo_height() if self.video_canvas else 1
            if canvas_w <= 10: canvas_w = 800
            if canvas_h <= 10: canvas_h = 600
            if self.device_video_width > 0 and self.device_video_height > 0:
                dev_start_x = int(start_x / canvas_w * self.device_video_width)
                dev_start_y = int(start_y / canvas_h * self.device_video_height)
                dev_end_x = int(end_x / canvas_w * self.device_video_width)
                dev_end_y = int(end_y / canvas_h * self.device_video_height)
            else:
                # 完全没有 video 尺寸信息,丢弃
                print_log(LogLevel.WARN, self.log_title, f"忽略点击: device_controller 未初始化且无视频尺寸")
                self.drag_start = None
                self._raw_drag = False
                return
        else:
            dev_start_x, dev_start_y = self._window_to_device_coords(start_x, start_y)
            dev_end_x, dev_end_y = self._window_to_device_coords(end_x, end_y)
        
        # 边界检查 (用设备原始尺寸, 不是显示尺寸)
        # mjpeg_client 路径下 self.video_width 已被 frame.shape 覆盖成显示尺寸,
        # 所以必须用 self.device_video_width (在 set_display_resolution 时存的真设备尺寸)
        if self.device_video_width > 0 and self.device_video_height > 0:
            if (dev_start_x < 0 or dev_start_x > self.device_video_width or
                dev_start_y < 0 or dev_start_y > self.device_video_height):
                print_log(LogLevel.WARN, self.log_title,
                    f"忽略超出设备范围的点击 ({dev_start_x}, {dev_start_y}) video={self.video_width}x{self.video_height}")
                self.drag_start = None
                self._raw_drag = False
                return
        
        drag_distance = ((end_x - start_x)**2 + (end_y - start_y)**2)**0.5
        
        if drag_distance > 10:
            smooth_time = 100
            print_log(LogLevel.DEBUG, self.log_title, f"swipe ({dev_start_x},{dev_start_y}) -> ({dev_end_x},{dev_end_y})")
            self.send_swipe(dev_start_x, dev_start_y, dev_end_x, dev_end_y, smooth_time)
            self._last_tap_time = 0.0
            self._last_tap_pos = None
        else:
            # 防抖: 同坐标 200ms 内重复 tap 丢弃 (RDP focus 跳动 / 多 press 场景)
            import time as _t
            now = _t.time()
            if (self._last_tap_pos == (dev_start_x, dev_start_y) and
                    now - self._last_tap_time < 0.2):
                print_log(LogLevel.DEBUG, self.log_title,
                    f"去重重复 tap ({dev_start_x},{dev_start_y}) 间隔 {now - self._last_tap_time:.3f}s")
                self.drag_start = None
                self._raw_drag = False
                return
            self._last_tap_pos = (dev_start_x, dev_start_y)
            self._last_tap_time = now
            print_log(LogLevel.DEBUG, self.log_title, f"tap ({dev_start_x},{dev_start_y})")
            self.send_tap(dev_start_x, dev_start_y)
        
        self.drag_start = None
        self._raw_drag = False
    
    def send_key(self, key_name: str) -> bool:
        """发送按键"""
        if key_name not in self.KEY_MAPPINGS:
            print_log(LogLevel.WARN, self.log_title, f"未知按键: {key_name}")
            return False
        
        keycode = self.KEY_MAPPINGS[key_name]
        args = ["shell", "uinput", "-K", "-d", str(keycode), "-u", str(keycode)]
        # 异步: 主线程立即返回, 避免 Popen 阻塞 Tk 主循环
        launched = self.hdc.execute_async(args, timeout=3)
        print_log(LogLevel.DEBUG, self.log_title,
            f"发送按键 (async): {key_name} (keycode={keycode}), launched={launched}")
        return launched
    
    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 100) -> bool:
        """发送滑动 (异步, 主线程立即返回).

        优先级:
          1. uinput -T -m   (主路径, 异步)
          2. uitest uiInput swipe (回退, 同步, 因为异步链路上连续注入 swipe 会乱序)

        修复: 之前同步执行时, mouse_up 阻塞主线程 30-100ms, 期间新的 mouse_down
        事件被 Tk 主循环排队, 用户感受是"点击无反应 / 滑动卡顿".
        """
        # 主路径: 异步, 主线程立即返回
        args = ["shell", "uinput", "-T", "-m",
                str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
        launched = self.hdc.execute_async(args, timeout=3)
        if launched:
            print_log(LogLevel.DEBUG, self.log_title,
                f"uinput -T 滑动 (async): ({x1},{y1}) -> ({x2},{y2}), {duration_ms}ms")
            return True

        # 回退: 同步 (异步失败概率极低, 这里兜底)
        self._ensure_uitest_daemon()
        r = self.hdc.execute(
            ["shell", "uitest", "uiInput", "swipe", str(x1), str(y1), str(x2), str(y2)],
            timeout=8,
        )
        if r.get("success"):
            print_log(LogLevel.WARN, self.log_title,
                f"uinput -T async 失败, 改用 uitest swipe: ({x1},{y1}) -> ({x2},{y2})")
            return True

        print_log(LogLevel.WARN, self.log_title,
            f"所有滑动路径都失败: ({x1},{y1}) -> ({x2},{y2})")
        return False
    
    def send_tap(self, x: int, y: int) -> bool:
        """发送点击 (异步, 主线程立即返回)."""
        print_log(LogLevel.INFO, self.log_title, f"send_tap({x}, {y})")

        """发送点击 (异步, 主线程立即返回).

        修复说明: 原代码用 `uinput -T -d xy -u xy` 是"立即 down+up" (0ms hold),
        在 RK3568 + OpenHarmony 3.2 上 MMI SimulateInputEvent 经常被吞,
        表现为"鼠标点击无反应"。

        现在的优先级 (全部异步, 主线程立即返回):
          1. uitest uiInput click xy   (OHOS 标准 API, 最稳定)
          2. uinput -T -d ... -u ...    (回退)
          3. uitest uiInput longClick   (回退)
          4. uitest uiInput swipe 1px   (最后回退)

        关键的二次修复: 即使注入命令正确, 同步执行下 Popen 阻塞主线程 30-100ms
        期间 Tk 主循环无法处理 mouse_up / 后续事件, 这是用户感受的"无反应"
        的另一个根因. 全部走 execute_async 解决.
        """
        # 主路径 1: uinput -T (直接触摸事件注入, ~220ms)
        # 实测: uinput -T -d xy -u xy 在 RK3568 OH3.2 上稳定且快 (220ms vs uitest 540ms)
        args = ["shell", "uinput", "-T", "-d", str(x), str(y), "-u", str(x), str(y)]
        if self.hdc.execute_async(args, timeout=3):
            print_log(LogLevel.DEBUG, self.log_title,
                      f"uinput -T tap (async): ({x},{y})")
            return True
        print_log(LogLevel.WARN, self.log_title,
                  f"uinput -T 异步启动失败, 尝试 uitest click: ({x},{y})")

        # 主路径 2: uitest uiInput click (~540ms, 慢但兼容性更广)
        if self._send_tap_via_uitest_click(x, y):
            print_log(LogLevel.WARN, self.log_title,
                      f"uinput -T 失败但 uitest click 启动成功: ({x},{y})")
            return True

        # 回退 1: uitest longClick
        if self._send_tap_via_uinput_long(x, y):
            print_log(LogLevel.WARN, self.log_title,
                      f"uitest click/uinput 都失败, 用 uitest longClick: ({x},{y})")
            return True

        # 回退 2: uitest swipe 1px
        if self._send_tap_via_uitest_swipe(x, y):
            print_log(LogLevel.WARN, self.log_title,
                      f"longClick 失败, 用 swipe(1px): ({x},{y})")
            return True

        print_log(LogLevel.WARN, self.log_title,
                  f"所有点击路径都失败: ({x},{y}) — 设备可能不支持触摸注入, "
                  f"请检查 uitest daemon 与 uinput 权限")
        return False

    def _send_tap_via_uitest_click(self, x: int, y: int) -> bool:
        """OHOS 标准点击 API: uitest uiInput click x y (异步启动)."""
        if not self._ensure_uitest_daemon():
            return False
        return self.hdc.execute_async(
            ["shell", "uitest", "uiInput", "click", str(x), str(y)],
            timeout=8,
        )

    def _send_tap_via_uitest_swipe(self, x: int, y: int) -> bool:
        """内部 helper: 用 uitest swipe(短距离) 模拟 click (异步)."""
        if not self._ensure_uitest_daemon():
            return False
        return self.hdc.execute_async(
            ["shell", "uitest", "uiInput", "swipe", str(x), str(y), str(x + 1), str(y + 1)],
            timeout=8,
        )
    
    def power_key(self) -> bool:
        """电源键"""
        return self.send_key("power")
    
    def home_key(self) -> bool:
        """Home键"""
        return self.send_key("home")
    
    def back_key(self) -> bool:
        """返回键"""
        return self.send_key("back")
    
    def unlock_screen(self) -> bool:
        """解锁屏幕"""
        return self.send_swipe(350, 1100, 350, 500, 200)
    
    def volume_up(self) -> bool:
        """音量加"""
        return self.send_key("volume_up")
    
    def volume_down(self) -> bool:
        """音量减"""
        return self.send_key("volume_down")



__all__ = ["DeviceController"]
