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

import time
import threading
import sys
import queue

import tkinter as tk
from typing import Tuple, Optional, Any

from core.constants import LogLevel
from core.logger import print_log
from core.hdc_executor import HDCCommandExecutor

_IS_MACOS = sys.platform == "darwin"
if _IS_MACOS:
    try:
        import Quartz
        from Quartz import (
            CGEventTapCreate, CGEventMaskBit, kCGEventScrollWheel,
            kCGEventRightMouseDown, kCGEventRightMouseUp,
            kCGEventOtherMouseDown, kCGEventOtherMouseUp,
            kCGSessionEventTap, kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
        )
        from CoreFoundation import (
            CFMachPortCreateRunLoopSource, CFRunLoopAddSource,
            CFRunLoopGetCurrent, CFRunLoopRun, CFRunLoopStop,
            kCFRunLoopDefaultMode,
        )
        _PYOBJC_AVAILABLE = True
    except ImportError:
        _PYOBJC_AVAILABLE = False
else:
    _PYOBJC_AVAILABLE = False


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
        self._scroll_monitor = None
        self._focus_keep_active: bool = False
        self._cgevent_tap = None
        self._cgevent_source = None
        self._cgevent_run_loop = None
        self._cgevent_thread = None
        self._tap_callback = None
        self._run_tap_loop = None
        self._wheel_queue: queue.Queue = queue.Queue()
        self._wheel_poll_active: bool = False
        # macOS 触控板右键队列 (CGEventTap 监听 RightMouseDown/OtherMouseDown 后
        # push 到此, 主线程 poll 取出 -> video_canvas.event_generate Button-3/2).
        # 解决: macOS 默认设置下双指点按 = Button-1 (Tk 收不到 Button-3),
        # 用户又不愿意去改系统设置 -> 这里在系统层把双指点按转成 Button-3.
        self._mouse_btn_queue: queue.Queue = queue.Queue()
        self._mouse_btn_poll_active: bool = False
        # 右键事件去重:
        # _last_synthetic_btn3_time: 最近一次合成 down 的时间, 用于 down 端 dedup
        #   (CGEventTap 注入后 100ms 内 AquaTk 原生 Button-3 视为重复, 丢弃).
        # _last_btn3_up_time: 最近一次 Button-3 up 的处理时间, 用于 up 端 dedup
        #   (CGEventTap up 和 AquaTk up 都触发 send_mouse_right_click 时,后者丢弃).
        self._last_synthetic_btn3_time: float = 0.0
        self._last_btn3_up_time: float = 0.0
        self._synthetic_btn3_down: bool = False
        self._last_move_time: float = 0.0
        # macOS 触控板 / 鼠标右键识别: 临时记录按下的是哪个按钮, mouse_up 时分支
        self._pressed_button: int = 0  # 0=none, 1=left, 2=middle, 3=right
        # 是否已经探测过设备端 uinput -M -b 2 是否支持,避免每次右键都探测
        self._uinput_btn2_probed: bool = False
        self._uinput_btn2_works: bool = False
    
    def set_display_resolution(self, video_width: int, video_height: int, canvas_width: int, canvas_height: int) -> Tuple[int, int, float]:
        """计算并设置显示分辨率 (等比 contain, 留黑边).

        视频不裁剪,完整保留原始画面;黑边由 main_window 的 _adjust_window_to_video
        自动消除 — 视频比例变了,窗口整体跟着变 (竖屏 -> 竖窗口, 横屏 -> 横窗口).
        """
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
        print_log(LogLevel.INFO, self.log_title, f"显示尺寸: {self.display_width}x{self.display_height} ratio:{self.display_ratio:.4f}")

        if self.video_canvas:
            self.left = int((self.video_canvas.winfo_width() - self.display_width) / 2)
            self.right = int((self.video_canvas.winfo_width() - self.display_width) / 2 + self.display_width)
            self.top = int((self.video_canvas.winfo_height() - self.display_height) / 2)
            self.bottom = int((self.video_canvas.winfo_height() - self.display_height) / 2 + self.display_height)
        return self.display_width, self.display_height, self.display_ratio
    
    def bind_video_canvas(self, canvas: tk.Canvas) -> None:
        """绑定视频画布.

        macOS 双指滑动(trackpad scroll)关键:
          AquaTk 上 <MouseWheel> 事件只发到当前 focus 的 widget,
          且 bind_all 在 macOS 上对 <MouseWheel> 不可靠.
          NSEvent local monitor 在 Tkinter mainloop 中不工作 (NSRunLoop 未驱动).
          修复方案 (按优先级):
            1. CGEventTap (系统级事件拦截, 最可靠, 不依赖 NSRunLoop)
            2. Tkinter 递归绑定 (回退方案)
        """
        self.video_canvas = canvas
        self._root_widget = canvas.winfo_toplevel()

        canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        canvas.bind("<B1-Motion>", self._on_mouse_drag)
        canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        canvas.bind("<Motion>", self._on_mouse_motion)
        canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        canvas.bind("<Button-4>", self._on_mouse_wheel)
        canvas.bind("<Button-5>", self._on_mouse_wheel)
        # macOS 触控板右键 (用户需求):
        #   * <Button-2>      鼠标中键 / 部分触控板配置
        #   * <Button-3>      鼠标右键 / 部分触控板配置
        #   * <Control-Button-1>  macOS 默认触控板双指 tap (AquaTk 在没改系统设置时
        #                          会表现为左键;用 Ctrl+左键 强制判定为右键)
        # 触控板双指 tap 在 macOS AquaTk 默认映射为 Button-1,需要用户改
        # 系统设置 -> 触控板 -> 辅助点按 (Bottom-Right) 或单独二指 tap 改右键,
        # 否则双指 tap 不会产生 Button-3. 这里同时绑三种事件,任意一个触发都算右键.
        canvas.bind("<ButtonPress-2>", self._on_mouse_down)
        canvas.bind("<ButtonRelease-2>", self._on_mouse_up)
        canvas.bind("<ButtonPress-3>", self._on_mouse_down)
        canvas.bind("<ButtonRelease-3>", self._on_mouse_up)
        canvas.bind("<Control-Button-1>", self._on_mouse_down)
        canvas.bind("<Control-ButtonRelease-1>", self._on_mouse_up)

        if _IS_MACOS and _PYOBJC_AVAILABLE:
            self._install_cgevent_tap()
        else:
            root = self._root_widget
            self._bind_wheel_recursive(root)
            root.after_idle(lambda: self._bind_wheel_recursive(root))

        try:
            self._root_widget.after_idle(lambda: canvas.focus_force())
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"after_idle focus_force 失败: {e!r}")
        try:
            canvas.bind("<Enter>", lambda ev: canvas.focus_force(), add="+")
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"bind <Enter> 失败: {e!r}")
        self._focus_keep_active = True
        self._root_widget.after(300, self._keep_canvas_focus)

    def _install_cgevent_tap(self) -> None:
        """安装 CGEventTap 监听 scroll 事件.

        CGEventTap 在系统级拦截事件, 不依赖 NSApplication/NSRunLoop.
        在独立线程中运行 CFRunLoop 来处理事件.
        使用 kCGEventTapOptionListenOnly 不需要辅助功能权限.
        事件通过 queue.Queue + Tkinter 定时轮询转发到主线程
        (root.after() 从非主线程调用不生效).

        重要: tap_callback 和 run_tap_loop 必须保存为实例变量,
        否则方法返回后 Python GC 回收闭包, CGEventTap callback 变成无效指针.
        """
        if self._cgevent_tap is not None:
            return
        try:
            def tap_callback(proxy, event_type, event, refcon):
                try:
                    # 1) 滚轮: 原 scroll 监听保留
                    if event_type == Quartz.kCGEventScrollWheel:
                        delta_y = Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGScrollWheelEventDeltaAxis1
                        )
                        if delta_y != 0:
                            self._wheel_queue.put(delta_y)
                        return event
                    # 2) 右键 / 中键: macOS 触控板双指点按 (Big Sur+ 默认设置)
                    #    是 Button-1 不会触发 Tk 的 Button-3. 这里在系统层捕
                    #    RightMouseDown / OtherMouseDown, 转成 Tk Button-3/2 注入 canvas.
                    #    鼠标位置用 CGEventGetLocation + video_canvas.winfo_rootx/_rooty
                    #    转换成 canvas 坐标; 不在 canvas 内的丢弃 (避免误触发).
                    if event_type in (kCGEventRightMouseDown, kCGEventRightMouseUp,
                                      kCGEventOtherMouseDown, kCGEventOtherMouseUp):
                        # 诊断: 打印 CGEventTap 收到的事件, 确认监听器是否真的在跑
                        name = {kCGEventRightMouseDown: "RightMouseDown",
                                kCGEventRightMouseUp:   "RightMouseUp",
                                kCGEventOtherMouseDown:  "OtherMouseDown",
                                kCGEventOtherMouseUp:    "OtherMouseUp"}.get(event_type, str(event_type))
                        loc = Quartz.CGEventGetLocation(event)
                        try:
                            canvas = self.video_canvas
                            cx = canvas.winfo_rootx()
                            cy = canvas.winfo_rooty()
                            cw = canvas.winfo_width()
                            ch = canvas.winfo_height()
                            px, py = loc.x, loc.y
                            in_canvas = (cx <= px < cx + cw and cy <= py < cy + ch)
                            canvas_x = int(px - cx) if in_canvas else -1
                            canvas_y = int(py - cy) if in_canvas else -1
                        except Exception:
                            in_canvas = False
                            canvas_x, canvas_y = -1, -1
                        print_log(LogLevel.DEBUG, self.log_title,
                            f"[CGEventTap] {name} screen=({int(loc.x)},{int(loc.y)}) "
                            f"canvas_root=({cx},{cy}) size=({cw},{ch}) "
                            f"in_canvas={in_canvas} canvas_xy=({canvas_x},{canvas_y})")
                        if not in_canvas:
                            return event
                        if event_type == kCGEventRightMouseDown:
                            self._mouse_btn_queue.put(("down", 3, canvas_x, canvas_y))
                        elif event_type == kCGEventRightMouseUp:
                            self._mouse_btn_queue.put(("up", 3, canvas_x, canvas_y))
                        elif event_type == kCGEventOtherMouseDown:
                            self._mouse_btn_queue.put(("down", 2, canvas_x, canvas_y))
                        elif event_type == kCGEventOtherMouseUp:
                            self._mouse_btn_queue.put(("up", 2, canvas_x, canvas_y))
                except Exception:
                    pass
                return event

            self._tap_callback = tap_callback

            # 监听事件: scroll + 右键按下/释放 + 中键按下/释放
            # 不监听 LeftMouseDown/Up — 避免与 Tk 自身的 Button-1 重复
            event_mask = (
                CGEventMaskBit(kCGEventScrollWheel) |
                CGEventMaskBit(kCGEventRightMouseDown) |
                CGEventMaskBit(kCGEventRightMouseUp) |
                CGEventMaskBit(kCGEventOtherMouseDown) |
                CGEventMaskBit(kCGEventOtherMouseUp)
            )

            tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionListenOnly,
                event_mask,
                tap_callback,
                None,
            )
            if tap is None:
                # CGEventTap 失败的最常见原因: macOS 辅助功能权限未授予.
                # 用户可去: 系统设置 -> 隐私与安全 -> 辅助功能 -> 启用 Terminal/iTerm
                raise RuntimeError(
                    "CGEventTapCreate 返回 None — macOS 辅助功能权限未授予. "
                    "去 系统设置->隐私与安全->辅助功能 启用此 Python 进程."
                )
            self._cgevent_tap = tap

            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            if source is None:
                raise RuntimeError("CFMachPortCreateRunLoopSource 返回 None")
            self._cgevent_source = source

            def run_tap_loop():
                run_loop = CFRunLoopGetCurrent()
                self._cgevent_run_loop = run_loop
                CFRunLoopAddSource(run_loop, source, kCFRunLoopDefaultMode)
                CFRunLoopRun()

            self._run_tap_loop = run_tap_loop

            self._cgevent_thread = threading.Thread(target=run_tap_loop, daemon=True)
            self._cgevent_thread.start()

            self._wheel_poll_active = True
            self._root_widget.after(50, self._poll_wheel_queue)

            # 启动右键/中键队列 poll (Tk 主线程)
            self._mouse_btn_poll_active = True
            self._root_widget.after(30, self._poll_mouse_btn_queue)

            # 诊断: 打印 mask 里监听的具体事件 (右键/头)
            mask_events = []
            if event_mask & CGEventMaskBit(kCGEventScrollWheel):
                mask_events.append("scroll")
            if event_mask & CGEventMaskBit(kCGEventRightMouseDown):
                mask_events.append("RightMouseDown")
            if event_mask & CGEventMaskBit(kCGEventRightMouseUp):
                mask_events.append("RightMouseUp")
            if event_mask & CGEventMaskBit(kCGEventOtherMouseDown):
                mask_events.append("OtherMouseDown")
            if event_mask & CGEventMaskBit(kCGEventOtherMouseUp):
                mask_events.append("OtherMouseUp")
            print_log(LogLevel.INFO, self.log_title,
                f"CGEventTap 监听事件 mask = {event_mask:#x} (events: {mask_events})")
            print_log(LogLevel.INFO, self.log_title,
                "CGEventTap scroll + 右键监听已安装 (支持触控板双指点按 -> Button-3)")
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title,
                f"CGEventTap 安装失败, 回退到 Tkinter 绑定: {e!r}")
            self._cgevent_tap = None
            self._cgevent_source = None
            root = self._root_widget
            self._bind_wheel_recursive(root)
            root.after_idle(lambda: self._bind_wheel_recursive(root))

    def _poll_wheel_queue(self) -> None:
        """定时从 _wheel_queue 取出滚轮事件, 在主线程中处理.

        CGEventTap callback 在 CFRunLoop 线程执行, 不能直接调用 Tkinter 方法.
        callback 将 delta_y 放入 queue, 此方法在 Tkinter 主线程中轮询取出,
        判断鼠标位置后调用 _dispatch_wheel.
        """
        if not self._wheel_poll_active:
            return
        try:
            while True:
                try:
                    delta_y = self._wheel_queue.get_nowait()
                except queue.Empty:
                    break
                if self.video_canvas and self._root_widget:
                    canvas = self.video_canvas
                    root = self._root_widget
                    px = root.winfo_pointerx()
                    py = root.winfo_pointery()
                    cx = canvas.winfo_rootx()
                    cy = canvas.winfo_rooty()
                    cw = canvas.winfo_width()
                    ch = canvas.winfo_height()
                    if cx <= px <= cx + cw and cy <= py <= cy + ch:
                        canvas_x = int(px - cx)
                        canvas_y = int(py - cy)
                        if delta_y > 0:
                            delta = -1
                        else:
                            delta = 1
                        self._dispatch_wheel(delta, canvas_x, canvas_y)
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title,
                f"轮询滚轮队列异常: {e!r}")
        self._root_widget.after(50, self._poll_wheel_queue)

    def _remove_cgevent_tap(self) -> None:
        """移除 CGEventTap."""
        self._wheel_poll_active = False
        if self._cgevent_run_loop is not None:
            try:
                CFRunLoopStop(self._cgevent_run_loop)
            except Exception as e:
                print_log(LogLevel.WARN, self.log_title,
                    f"停止 CFRunLoop 失败: {e!r}")
        self._cgevent_tap = None
        self._cgevent_source = None
        self._cgevent_run_loop = None
        self._cgevent_thread = None

    def _dispatch_wheel(self, delta: int, canvas_x: int, canvas_y: int) -> None:
        """统一滚轮派发: 构造伪 event 并调用 _on_mouse_wheel.

        CGEventTap 捕获的 trackpad scroll 事件强制使用 swipe 模拟,
        因为 uinput -M -s 在设备端不生效 (execute_async 返回 True
        只表示进程启动, 不代表命令执行成功).
        """
        if self.display_width <= 0 or self.display_height <= 0:
            return
        class _FakeEvent:
            def __init__(self, d):
                self.delta = d
                self.num = 0
                self.serial = 0
        self._on_mouse_wheel(_FakeEvent(delta), canvas_x, canvas_y, force_swipe=True)

    def _poll_mouse_btn_queue(self) -> None:
        """主线程 poll 右键/中键队列.

        CGEventTap 捕到 RightMouseDown/OtherMouseDown (macOS 触控板双指点按) 后
        push 到 _mouse_btn_queue; 这里在 Tk 主线程取出, 用 video_canvas.event_generate
        注入 <Button-3> / <Button-2> / 对应的 release 事件.
        """
        if not self._mouse_btn_poll_active:
            return
        try:
            items = 0
            while True:
                action, btn, cx, cy = self._mouse_btn_queue.get_nowait()
                items += 1
                if not self.video_canvas:
                    print_log(LogLevel.WARN, self.log_title,
                        f"[poll] btn={btn} {action} 丢弃: video_canvas 为空")
                    continue
                print_log(LogLevel.INFO, self.log_title,
                    f"[poll] 取出 btn={btn} {action} at ({cx},{cy})")
                try:
                    if action == "down":
                        if btn == 3:
                            # 关键: 预先设 _pressed_button=3 + drag_start, 然后调 handler.
                            # handler 内部看到 _pressed_button==3 and drag_start != None
                            # 会走"已处理过 down"分支,直接返回 -> 不重复设置 status + drag_start.
                            # (这里也设置 _last_synthetic_btn3_time 给 AquaTk 端 dedup 用)
                            self._synthetic_btn3_down = True
                            self._pressed_button = 3
                            self.drag_start = (cx, cy)
                            self._raw_drag = (self.display_width <= 0 or self.display_height <= 0)
                            self._last_synthetic_btn3_time = time.time()
                        # 关键: 直接调 _on_mouse_down handler, 不用 event_generate.
                        # event_generate 在 overrideredirect(True) 窗口下可能因为
                        # Tk 内部焦点丢失而失效; 直接调 handler 走完全相同的代码路径,
                        # 行为更可靠.
                        _FakeEv = type("FakeButtonEvent", (), {
                            "num": btn, "x": cx, "y": cy,
                            "state": 0, "widget": self.video_canvas,
                            "serial": 0, "time": time.time(),
                        })
                        try:
                            self._on_mouse_down(_FakeEv())
                        except Exception as ex:
                            print_log(LogLevel.WARN, self.log_title,
                                f"_on_mouse_down btn={btn} 失败: {ex!r}")
                    else:
                        if btn == 3:
                            self._synthetic_btn3_down = False
                        _FakeEv = type("FakeButtonEvent", (), {
                            "num": btn, "x": cx, "y": cy,
                            "state": 0, "widget": self.video_canvas,
                            "serial": 0, "time": time.time(),
                        })
                        try:
                            self._on_mouse_up(_FakeEv())
                        except Exception as ex:
                            print_log(LogLevel.WARN, self.log_title,
                                f"_on_mouse_up btn={btn} 失败: {ex!r}")
                except Exception as ex:
                    print_log(LogLevel.WARN, self.log_title,
                        f"event_generate Button-{btn} {action} 失败: {ex!r}")
        except queue.Empty:
            pass
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title,
                f"_poll_mouse_btn_queue 异常: {e!r}")
        # 持续 poll
        if self._mouse_btn_poll_active and self._root_widget:
            try:
                self._root_widget.after(30, self._poll_mouse_btn_queue)
            except Exception:
                pass

    def _bind_wheel_recursive(self, widget: tk.Widget) -> None:
        """递归遍历所有子 widget, 绑定 <MouseWheel> 事件.

        macOS AquaTk 上 <MouseWheel> 只发到 focused widget,
        bind_all 不可靠. 逐个绑定确保任何 widget 有焦点时都能捕获.

        改动: 同时绑 Button-2/3 (右键/中键) 和 Control-Button-1 (macOS Ctrl+点),
        确保 overrideredirect(True) 窗口下, AquaTk 把事件发给哪个 widget 都能路由到
        _on_mouse_down/_up — 解决 "CGEventTap 失败时无右键" 的 fallback 漏洞.
        """
        try:
            widget.bind("<MouseWheel>", self._on_mouse_wheel_global, add="+")
            widget.bind("<Button-4>", self._on_mouse_wheel_global, add="+")
            widget.bind("<Button-5>", self._on_mouse_wheel_global, add="+")
        except Exception:
            pass
        # 右键/中键/Ctrl+点 全 widget 递归绑 (overrideredirect 兜底)
        try:
            widget.bind("<ButtonPress-2>", self._on_mouse_down, add="+")
            widget.bind("<ButtonRelease-2>", self._on_mouse_up, add="+")
            widget.bind("<ButtonPress-3>", self._on_mouse_down, add="+")
            widget.bind("<ButtonRelease-3>", self._on_mouse_up, add="+")
            widget.bind("<Control-Button-1>", self._on_mouse_down, add="+")
            widget.bind("<Control-ButtonRelease-1>", self._on_mouse_up, add="+")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_wheel_recursive(child)
    
    def reset(self) -> None:
        """重置控制器状态（切换设备时调用）"""
        self._remove_cgevent_tap()
        self.display_width = 0
        self.display_height = 0
        self.display_ratio = 0.0
        self.left = 0
        self.right = 0
        self.top = 0
        self.bottom = 0
        self.drag_start = None
        self._focus_keep_active = False

    def _keep_canvas_focus(self) -> None:
        """定时检查: 鼠标在 canvas 上但焦点不在时, 用 focus_force 夺回.

        macOS AquaTk 上 <MouseWheel> 只发到 focused widget.
        双指滑动(trackpad scroll)不按下鼠标按钮, 如果 canvas 没 focus,
        事件不会路由到 canvas -> 滚轮"无反应".
        每 300ms 检查一次, 只在鼠标确实在 canvas 区域内时才抢焦点,
        不影响 Combobox 等需要焦点的 widget.
        """
        if not self._focus_keep_active or not self.video_canvas or not self._root_widget:
            return
        try:
            root = self._root_widget
            canvas = self.video_canvas
            px = root.winfo_pointerx()
            py = root.winfo_pointery()
            cx = canvas.winfo_rootx()
            cy = canvas.winfo_rooty()
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cx <= px <= cx + cw and cy <= py <= cy + ch:
                current = root.focus_get()
                if current is not canvas:
                    canvas.focus_force()
        except Exception:
            pass
        root.after(300, self._keep_canvas_focus)
    

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

    def _canvas_coords_from_event(self, event) -> Optional[Tuple[int, int]]:
        """将滚轮事件坐标统一为 video_canvas 的逻辑坐标."""
        if not self.video_canvas:
            return None
        if getattr(event, "widget", None) is self.video_canvas:
            return int(getattr(event, "x", 0)), int(getattr(event, "y", 0))
        # 合成事件/测试事件没有 widget 时，event.x/event.y 就是唯一可靠的
        # Canvas 坐标；不能优先读取真实鼠标指针，否则测试和自动化注入会被
        # 误判为发生在 Canvas 外。
        if getattr(event, "widget", None) is None and hasattr(event, "x") and hasattr(event, "y"):
            return int(event.x), int(event.y)
        try:
            root = self._root_widget
            return (
                int(root.winfo_pointerx() - self.video_canvas.winfo_rootx()),
                int(root.winfo_pointery() - self.video_canvas.winfo_rooty()),
            )
        except Exception:
            x = getattr(event, "x", None)
            y = getattr(event, "y", None)
            if x is not None and y is not None:
                return int(x), int(y)
            return None

    def _render_bounds(self) -> Tuple[int, int, int, int]:
        """返回画面区域, 没有布局信息时用当前 Canvas 居中估算."""
        left, right, top, bottom = self.left, self.right, self.top, self.bottom
        if right <= left or bottom <= top:
            canvas_w = self.video_canvas.winfo_width() if self.video_canvas else 0
            canvas_h = self.video_canvas.winfo_height() if self.video_canvas else 0
            if canvas_w <= 10:
                canvas_w = max(self.display_width, 800)
            if canvas_h <= 10:
                canvas_h = max(self.display_height, 600)
            left = max(0, (canvas_w - self.display_width) // 2)
            top = max(0, (canvas_h - self.display_height) // 2)
            right = left + self.display_width
            bottom = top + self.display_height
        return left, right, top, bottom

    def _on_mouse_wheel_global(self, event) -> None:
        """递归绑定的兜底, 只处理画面区域内的滚轮."""
        print_log(LogLevel.DEBUG, self.log_title,
            f"global wheel: widget={getattr(event,'widget',None)!r} delta={getattr(event,'delta',0)} num={getattr(event,'num',0)}")
        coords = None
        try:
            root = self._root_widget
            coords = (
                int(root.winfo_pointerx() - self.video_canvas.winfo_rootx()),
                int(root.winfo_pointery() - self.video_canvas.winfo_rooty()),
            )
        except Exception:
            coords = self._canvas_coords_from_event(event)
        if coords is None:
            self._on_mouse_wheel(event)
            return
        self._on_mouse_wheel(event, *coords)

    def _on_mouse_wheel(self, event, canvas_x: Optional[int] = None,
                        canvas_y: Optional[int] = None,
                        force_swipe: bool = False) -> None:
        """鼠标滚轮 -> 设备纵向触摸滑动."""
        print_log(LogLevel.DEBUG, self.log_title,
            f"wheel: delta={getattr(event,'delta',0)} num={getattr(event,'num',0)} widget={getattr(event,'widget',None)!r}")
        delta = 0
        event_num = getattr(event, "num", 0)
        raw_delta = getattr(event, "delta", 0) or 0
        if event_num == 4:
            delta = -1
        elif event_num == 5:
            delta = 1
        else:
            if raw_delta > 0:
                delta = -1
            elif raw_delta < 0:
                delta = 1
        if delta == 0:
            return
        if self.display_width <= 0 or self.display_height <= 0:
            print_log(LogLevel.DEBUG, self.log_title,
                f"滚轮事件丢弃: display 未初始化 ({self.display_width}x{self.display_height})")
            return

        serial = getattr(event, "serial", None)
        if serial not in (None, 0):
            if serial == getattr(self, "_last_wheel_serial", None):
                return
            self._last_wheel_serial = serial

        if canvas_x is None or canvas_y is None:
            coords = self._canvas_coords_from_event(event)
            if coords is not None:
                canvas_x, canvas_y = coords
        if canvas_x is None or canvas_y is None:
            left, right, top, bottom = self._render_bounds()
            canvas_x = (left + right) // 2
            canvas_y = (top + bottom) // 2

        canvas_w = self.video_canvas.winfo_width() if self.video_canvas else 0
        canvas_h = self.video_canvas.winfo_height() if self.video_canvas else 0
        if canvas_w <= 10:
            canvas_w = max(self.display_width, 800)
        if canvas_h <= 10:
            canvas_h = max(self.display_height, 600)
        if canvas_x < 0 or canvas_x > canvas_w or canvas_y < 0 or canvas_y > canvas_h:
            print_log(LogLevel.DEBUG, self.log_title,
                f"忽略画布外滚轮: ({canvas_x},{canvas_y}) canvas=({canvas_w},{canvas_h})")
            return

        now = time.time()
        last_time = getattr(self, "_last_wheel_time", 0.0)
        last_dir = getattr(self, "_last_wheel_dir", 0)
        if last_time > 0 and (now - last_time) < 0.05:
            if last_dir == delta:
                self._wheel_accum = getattr(self, "_wheel_accum", 0) + 1
                return
        self._last_wheel_time = now
        self._last_wheel_dir = delta
        accum = getattr(self, "_wheel_accum", 0)
        self._wheel_accum = 0

        steps = max(1, min(5, int(abs(raw_delta) / 120) or 1)) if raw_delta else 1
        steps = max(steps, min(accum + 1, 5))
        scroll_value = delta * steps
        print_log(LogLevel.INFO, self.log_title,
            f"鼠标滚轮 -> scroll={scroll_value} "
            f"(delta={raw_delta}, num={event_num}, steps={steps}, force_swipe={force_swipe})")
        if force_swipe:
            print_log(LogLevel.INFO, self.log_title,
                "trackpad scroll -> swipe (uinput -M -s 在设备端不生效)")
            self._scroll_via_swipe(delta, steps, canvas_x, canvas_y)
        else:
            result = self.send_mouse_scroll(scroll_value)
            if not result:
                print_log(LogLevel.WARN, self.log_title,
                    "uinput -M -s 失败, 回退到 swipe 模拟滚动")
                self._scroll_via_swipe(delta, steps, canvas_x, canvas_y)
            else:
                print_log(LogLevel.INFO, self.log_title,
                    f"send_mouse_scroll -> {result}")

    def _scroll_via_swipe(self, delta: int, steps: int,
                          canvas_x: int, canvas_y: int) -> None:
        """用 swipe 模拟滚轮 (uinput -M 不可用时的回退).

        优化: distance 不乘以 steps (避免大跳跃),
        steps 影响 duration (steps 越大滚动越久, 模拟惯性).
        """
        device_w = self.device_video_width or self.video_width or self.display_width
        device_h = self.device_video_height or self.video_height or self.display_height
        if device_w <= 0 or device_h <= 0:
            return
        dev_x, dev_y = self._window_to_device_coords(canvas_x, canvas_y)
        dev_x = max(0, min(device_w - 1, dev_x))
        margin = max(40, min(120, device_h // 8))
        start_y = max(margin, min(device_h - 1 - margin, dev_y))
        base_distance = max(150, int(device_h * 0.12))
        distance = base_distance
        end_y = start_y - distance if delta < 0 else start_y + distance
        end_y = max(0, min(device_h - 1, end_y))
        if end_y == start_y:
            return
        duration_ms = 100 + steps * 30
        print_log(LogLevel.INFO, self.log_title,
            f"滚轮回退 swipe: ({dev_x},{start_y}) -> ({dev_x},{end_y}) dur={duration_ms}ms")
        self.send_swipe(dev_x, start_y, dev_x, end_y, duration_ms=duration_ms)

    def _on_mouse_down(self, event: tk.Event) -> None:
        """鼠标按下.

        按钮识别:
          * Button-1 / Control-Button-1 (后者在 mouse_up 视作右键)
          * Button-2 (中键 / 部分触控板配置)
          * Button-3 (右键)
        右键/中键按下时不发 uinput -M -m 鼠标移动 (避免设备端出现鼠标抖动);
        仅记录 _pressed_button,mouse_up 时统一调用 send_mouse_right_click/send_mouse_middle_click.
        """
        # 按钮号识别 (Tk 事件 num 字段就是按钮号; Control-Button-1 也带 num=1)
        btn = getattr(event, "num", 1)
        # 修饰键: Control-Button-1 在 macOS 触控板上是默认 "右键 tap" 映射;
        # 强制视作 button=3
        ctrl_held = bool(getattr(event, "state", 0) & 0x4) if isinstance(getattr(event, "state", 0), int) else False
        # 实际上 Control 修饰在 X11/AquaTk 上 event.state 是 bitmask; 0x4 = Control
        # Tk 在 macOS Aqua 上 state 数值略有差异, 这里用 key modifier keysym 列表兜底
        if ctrl_held and btn == 1:
            btn = 3

        # 诊断: 打印所有进入 _on_mouse_down 的事件 — 包括 btn=1 (左键)
        # 用于确认: AquaTk 是否真的传 Button-3 给我们
        try:
            w_name = event.widget.winfo_class() if event.widget else "None"
        except Exception:
            w_name = "?"
        synth = getattr(self, "_synthetic_btn3_down", False)
        print_log(LogLevel.INFO, self.log_title,
            f"[_on_mouse_down] btn={btn} ({event.x},{event.y}) widget={w_name} "
            f"state={getattr(event, 'state', 0):#x} synth_ahead={synth}")

        # 右键去重: 如果 100ms 内 CGEventTap 已经处理过 Button-3 down (即
        # _last_synthetic_btn3_time 时间戳是 CGEventTap 入队时设的, 而 _pressed_button=3
        # 也是 CGEventTap 路径设的), 则原生 AquaTk 传来的 Button-3 视为重复,丢弃.
        if btn == 3:
            last_syn = getattr(self, "_last_synthetic_btn3_time", 0.0)
            pressed_before = getattr(self, "_pressed_button", 0)
            synth_ahead = getattr(self, "_synthetic_btn3_down", False)
            # 场景 A: CGEventTap 已处理 down (synth_ahead=True) -> AquaTk 后到丢弃
            if synth_ahead:
                print_log(LogLevel.DEBUG, self.log_title,
                    f"右键去重: AquaTk Button-3 晚于 CGEventTap 注入, 丢弃")
                return
            # 场景 B: AquaTk 先到 (pressed_before=3), CGEventTap 后到 — 在 _poll_mouse_btn_queue
            # 里 _on_mouse_down 调用前已经设置了 _pressed_button=3 + drag_start, 这里的
            # 检测: 如果 _pressed_button 已经是 3 且 drag_start 不为空, 表示已经处理过
            # 右键 down, 丢弃 (避免双触发).
            if pressed_before == 3 and self.drag_start is not None:
                print_log(LogLevel.DEBUG, self.log_title,
                    f"右键去重: CGEventTap 注入晚于 AquaTk 原生, 丢弃")
                return

        self._pressed_button = btn

        # 右键 / 中键: 只记录坐标 + 按钮, mouse_up 触发右键注入,不做鼠标移动
        if btn in (2, 3):
            self.drag_start = (event.x, event.y)
            self._raw_drag = (self.display_width <= 0 or self.display_height <= 0)
            return

        # 左键 (原有路径)
        if self.display_width <= 0 or self.display_height <= 0:
            print_log(LogLevel.DEBUG, self.log_title, f"未初始化,记录原始坐标 ({event.x}, {event.y})")
            self.drag_start = (event.x, event.y)
            self._raw_drag = True
            return
        if self.left <= event.x <= self.right and self.top <= event.y <= self.bottom:
            self.drag_start = (event.x, event.y)
            self._raw_drag = False
            dev_x, dev_y = self._window_to_device_coords(event.x, event.y)
            if self.device_video_width > 0:
                dev_x = max(0, min(self.device_video_width - 1, dev_x))
            if self.device_video_height > 0:
                dev_y = max(0, min(self.device_video_height - 1, dev_y))
            self.hdc.execute_async(
                ["shell", "uinput", "-M", "-m", str(dev_x), str(dev_y)],
                timeout=3,
            )
            self._last_move_time = time.time()
    
    def _on_mouse_drag(self, event: tk.Event) -> None:
        """鼠标拖动 - 实时发送 uinput -M -m 实现鼠标随动.

        节流: 每 50ms 最多发送一次, 避免 drag 事件高频淹没设备.
        """
        if self.drag_start is None:
            return
        if self.display_width <= 0 or self.display_height <= 0:
            return
        now = time.time()
        if now - self._last_move_time < 0.05:
            return
        self._last_move_time = now
        dev_x, dev_y = self._window_to_device_coords(event.x, event.y)
        if self.device_video_width > 0:
            dev_x = max(0, min(self.device_video_width - 1, dev_x))
        if self.device_video_height > 0:
            dev_y = max(0, min(self.device_video_height - 1, dev_y))
        self.hdc.execute_async(
            ["shell", "uinput", "-M", "-m", str(dev_x), str(dev_y)],
            timeout=3,
        )
    
    def _on_mouse_motion(self, event: tk.Event) -> None:
        """鼠标移动(不按下按钮) - 实时发送 uinput -M -m 实现鼠标随动.

        与 _on_mouse_drag 互斥: <Motion> 只在无按钮按下时触发,
        <B1-Motion> 在按住左键时触发.
        节流: 每 50ms 最多发送一次.
        """
        if self.display_width <= 0 or self.display_height <= 0:
            return
        if not (self.left <= event.x <= self.right and self.top <= event.y <= self.bottom):
            return
        now = time.time()
        if now - self._last_move_time < 0.05:
            return
        self._last_move_time = now
        dev_x, dev_y = self._window_to_device_coords(event.x, event.y)
        if self.device_video_width > 0:
            dev_x = max(0, min(self.device_video_width - 1, dev_x))
        if self.device_video_height > 0:
            dev_y = max(0, min(self.device_video_height - 1, dev_y))
        self.hdc.execute_async(
            ["shell", "uinput", "-M", "-m", str(dev_x), str(dev_y)],
            timeout=3,
        )

    def _on_mouse_up(self, event: tk.Event) -> None:
        """鼠标释放"""
        if self.drag_start is None:
            # 没记录 drag_start (例如点在画布外),只清按钮状态
            self._pressed_button = 0
            return

        # 按钮识别: 右键/中键走独立分支
        # (右键 dedup 只在 _on_mouse_down 处理: AquaTk 原生 + CGEventTap 注入
        # 都产生 down 时,后者触发后 100ms 内丢弃前者)
        btn = getattr(event, "num", 1)
        ctrl_held = bool(getattr(event, "state", 0) & 0x4) if isinstance(getattr(event, "state", 0), int) else False
        if ctrl_held and btn == 1:
            btn = 3

        # 右键 up 端 dedup: 如果 100ms 内已经处理过 Button-3 up, 视为重复
        # (CGEventTap up + AquaTk up 同时触发 send_mouse_right_click 的场景)
        if btn == 3:
            last_up = getattr(self, "_last_btn3_up_time", 0.0)
            if last_up > 0 and (time.time() - last_up) < 0.1:
                print_log(LogLevel.DEBUG, self.log_title,
                    f"右键 up 去重: 100ms 内已处理过 up, 丢弃")
                self.drag_start = None
                self._pressed_button = 0
                self._raw_drag = False
                return

        pressed = getattr(self, "_pressed_button", 0) or btn
        # 优先用 _pressed_button (ButtonPress 时的判定);Control 在 Release 时可能丢失 state,
        # 这里以 _pressed_button 为准
        if pressed in (2, 3):
            # 右键/中键释放 -> 触发设备端右键注入
            sx, sy = self.drag_start
            dev_x, dev_y = self._window_to_device_coords(sx, sy)
            if self.device_video_width > 0:
                dev_x = max(0, min(self.device_video_width - 1, dev_x))
            if self.device_video_height > 0:
                dev_y = max(0, min(self.device_video_height - 1, dev_y))
            if pressed == 3:
                self._last_btn3_up_time = time.time()
                self.send_mouse_right_click(dev_x, dev_y)
            else:
                # 中键 -> 设备端通常用作滚轮键;此处给一个轻量回调
                print_log(LogLevel.INFO, self.log_title,
                    f"中键 tap ({dev_x},{dev_y}):OHOS 端未实现 middle button,仅记录")
            self.drag_start = None
            self._raw_drag = False
            self._pressed_button = 0
            return

        self._pressed_button = 0

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
        """发送按键.

        修复 (RK3568 OH 3.2 uinput -K bug):
          原实现单次 Popen 调 `shell uinput -K -d <kc> -u <kc>`.
          hdc 在设备端调 uinput 但 down/up 间隔 <5ms,
          设备 input service 会丢弃 0ms 间隔的事件, 表现为"按键无反应".
          keyboard_mapper.py 同样踩过这个坑, 改成 down/up 分两次 execute + 间隔.
        现在改为:
          1. 异步发 down (主线程立即返回)
          2. 30ms 后异步发 up
          间隔足以让设备 input service 处理 down 事件, 避免被丢弃.
        """
        if key_name not in self.KEY_MAPPINGS:
            print_log(LogLevel.WARN, self.log_title, f"未知按键: {key_name}")
            return False

        keycode = self.KEY_MAPPINGS[key_name]

        def _send_up_later():
            time.sleep(0.03)  # 30ms 间隔, 让设备 input service 处理 down
            self.hdc.execute_async(["shell", "uinput", "-K", "-u", str(keycode)], timeout=3)

        launched = self.hdc.execute_async(["shell", "uinput", "-K", "-d", str(keycode)], timeout=3)
        threading.Thread(target=_send_up_later, daemon=True).start()
        print_log(LogLevel.DEBUG, self.log_title,
            f"发送按键 (async split): {key_name} (keycode={keycode}), launched={launched}")
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

    def send_mouse_scroll(self, scroll_value: int) -> bool:
        """发送设备原生鼠标滚轮事件."""
        if not scroll_value:
            return False
        args = ["shell", "uinput", "-M", "-s", str(int(scroll_value))]
        launched = self.hdc.execute_async(args, timeout=3)
        if launched:
            print_log(LogLevel.DEBUG, self.log_title,
                      f"uinput -M 滚轮 (async): value={scroll_value}")
        else:
            print_log(LogLevel.WARN, self.log_title,
                      f"uinput -M 滚轮启动失败: value={scroll_value}")
        return launched

    def send_mouse_right_click(self, x: int, y: int) -> bool:
        """发送鼠标右键 -> 设备端 (OH 3.2 RK3568 实测可用).

        OHOS 行为关键说明 (重要):
          OHOS 没有传统意义上的"右键菜单" (如 Windows / Linux desktop / Android 原生
          popup menu). OHOS 把鼠标右键事件映射为 KEY_BACK (返回键) — 这是 by-design,
          跟 AndroidAuto / 车载 / 电视投屏场景一致.
          所以这里实现的"右键"实际上触发"返回"行为:
            1. uinput -T -d <x> <y> -u <x> <y>  (触摸点击定位, 用户能看到位置高亮)
            2. uinput -K -d 2 -u 2                (KEY_BACK 下发 — 等同于 OHOS 端的"右键")

        设备端 uinput 真实签名 (OH 3.2 实测):
          -M -d <btn>     按下鼠标按钮 (0=左 1=右 2=中) — 但 OH 不响应右键弹出菜单
          -K -d <kc> -u <kc>  按键 down/up (keycode=2 即 KEY_BACK)
          -T -d <x> <y>   触摸按下 (绝对坐标, 投屏场景必备)

        历史 bug: 之前用 "uinput -M -d x y -b 2" 是错误格式, 设备端报 "button is
        out of range"; 后来改 "uinput -M -d 1 -u 1" 但 OHOS 实际把鼠标右键等同为
        KEY_BACK — 鼠标 button event 派发到 input service 后被忽略, 必须用 -K 直接发键.
        """
        print_log(LogLevel.INFO, self.log_title,
            f"send_mouse_right_click({x}, {y}), btn2_probed={self._uinput_btn2_probed}")

        # 探测: 用 sync 跑一次 "uinput -M -d 1" 看设备是否响应.
        # OH uinput 输出 "press down 1" 到 stdout (不是 stderr!) — 之前误判.
        # 改: 同时检查 stdout + stderr, 任何一方含 "press down 1" 即认为设备支持右键.
        if not self._uinput_btn2_probed:
            self._uinput_btn2_probed = True
            try:
                r = self.hdc.execute(
                    ["shell", "uinput", "-K", "-d", "2", "-u", "2"],
                    timeout=2,
                )
                stdout = (r.get("stdout") or "").strip().lower()
                stderr = (r.get("stderr") or "").strip().lower()
                combined = stdout + " " + stderr
                # OH uinput -K -d 2 stdout 含 "you raised the key 2"
                self._uinput_btn2_works = (
                    "you raised the key 2" in combined or
                    "press down 0" in combined or  # 备用: mouse button 探测
                    "press down 1" in combined   # 备用: mouse button 探测
                )
                print_log(LogLevel.INFO, self.log_title,
                    f"uinput -K -d 2 -u 2 探测: works={self._uinput_btn2_works} "
                    f"returncode={r.get('returncode')} stdout=[{stdout[:60]}] stderr=[{stderr[:60]}]")
            except Exception as e:
                self._uinput_btn2_works = False
                print_log(LogLevel.WARN, self.log_title,
                    f"uinput -K -d 2 -u 2 探测异常: {e!r}")

        if self._uinput_btn2_works:
            # 主路径: -T -d/u 触摸定位 + -K -d 2 -u 2 (KEY_BACK, OHOS 把右键等同为 Back)
            # 用一条 shell 命令链式执行, 减少 hdc 调用次数.
            shell_cmd = (
                f"uinput -T -d {int(x)} {int(y)} -u {int(x)} {int(y)} ; "
                f"uinput -K -d 2 -u 2"
            )
            launched = self.hdc.execute_async(
                ["shell", shell_cmd],
                timeout=4,
            )
            if launched:
                print_log(LogLevel.INFO, self.log_title,
                    f"右键 [链式]: uinput -T -d/u {x} {y} + uinput -K -d 2 -u 2 (async, OHOS=Back)")
                return True

        # 回退 1: KEY_CONTEXTMENU (Linux input 0x199 = 409)
        launched = self.hdc.execute_async(
            ["shell", "uinput", "-K", "-d", "409", "-u", "409"],
            timeout=3,
        )
        if launched:
            print_log(LogLevel.DEBUG, self.log_title,
                f"右键 KEY_CONTEXTMENU (async 409): ({x},{y})")
            return True

        # 回退 2: uitest uiInput longClick (OHOS 长按通常触发右键菜单)
        launched = self.hdc.execute_async(
            ["shell", "uitest", "uiInput", "longClick", str(int(x)), str(int(y))],
            timeout=8,
        )
        if launched:
            print_log(LogLevel.WARN, self.log_title,
                f"右键回退 uitest longClick (async): ({x},{y}) — 兼容性最稳,但延迟较高")
            return True

        print_log(LogLevel.WARN, self.log_title,
            f"右键所有路径都失败: ({x},{y}) — 设备可能不支持")
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
