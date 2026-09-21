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
主窗口模块
"""

import os
import time
import os
import threading
import webbrowser
import gc
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from typing import Optional, Callable, Dict, List, Any

from core import (
    AUTHOR,
    PROJECT_URL,
    VERSION,
    HOST,
    HEARTBEAT_TIMEOUT,
    LogLevel,
    ServerDeployState,
    print_log,
    HDCCommandExecutor,
    ServerManager,
    DeviceManager,
)

from video import VideoStreamClient

from gui.device_panel import DevicePanel
from gui.control_panel import ControlPanel, InfoPanel
from gui.video_panel import VideoPanel
from gui.branding import BrandColor, Logo, StatusDot, SplashScreen
from gui.keyboard_mapper import KeyboardMapper
from gui.device_controller import DeviceController
from gui.connection_manager import ConnectionManager, ConnectionState
from gui.video_display import VideoDisplay
from gui.server_deployer import ServerDeployer
from gui.theme import Theme, ICONS
from gui.widgets import ModernButton
from core.mjpeg_server_manager import MjpegServerManager


class MainWindow:
    """主窗口"""
    
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"OHScrcpy {VERSION}")
        # 关键: tk.Tk() 一旦创建 root, OS 会立刻渲染它 (空窗口, 默认 300x200).
        # 立刻 withdraw 让 OS 不渲染空主窗口.
        # 同时, 把所有 zoomed / geometry / 自适应窗口尺寸 计算 全部延后到
        # _on_splash_finish() 之后 (那时候主窗口还没 visible, 但 geometry 已经计算好,
        # _setup_ui() 创建的 widget 拿到的尺寸已经是 1100x800 这种最终尺寸).
        # 这样 splash 显示期间 root 完全 hidden, 用户看不到空白主窗口一闪.
        try:
            self.root.withdraw()
        except Exception:
            pass
        # 注意: 不设 minsize, 让自适应计算窗口尺寸
        # 之前设 minsize(1100,920) 在 xrdp RDP 上, WM 强制最小 height=920,
        # 导致 client=900 也被拉成 920, 加上 WM 装饰 + y=80 总高度超 949, 底部被截
        # 之前设 minsize(1100,920) 在 xrdp RDP 上, WM 强制最小 height=920,
        # 导致 client=900 也被拉成 920, 加上 WM 装饰 + y=80 总高度超 949, 底部被截
        # 提升窗口到最前 + focus (RDP/wayland 下有时窗口被 z-序压住)
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.update()
            self.root.attributes("-topmost", False)  # 关掉 topmost, 避免挡住其他应用
        except Exception:
            pass
        # 优化: 让视频 canvas 尽量占满窗口
        # 布局: 顶部工具栏 (35px) + 主区域 (视频fill BOTH, 控制面板 300px RIGHT) + 状态栏 (可折叠)
        self._status_bar_visible = False  # 默认隐藏状态栏, 视频显示最大化
        
        self.log_title = "GUI"
        
        self.is_connected = False
        self.server_deploy_lock = threading.Lock()
        self.server_deploy_state = ServerDeployState.IDLE
        
        self.video_canvas = None
        self.status_text_id = None
        self.running_status_text_id = None
        
        self.device_panel = None
        self.control_panel = None
        self.video_panel = None
        self.info_panel = None
        
        self.hdc_executor: Optional[HDCCommandExecutor] = None
        self.device_manager: Optional[DeviceManager] = None
        self.server_manager = None
        self.device_controller: Optional[DeviceController] = None
        self.video_client: Optional[VideoStreamClient] = None
        
        self.connection_manager: Optional[ConnectionManager] = None
        self.video_display: Optional[VideoDisplay] = None
        self.server_deployer: Optional[ServerDeployer] = None
        
        self.device_status_label = None
        self.status_label = None
        self.connection_status_label = None
        self.performance_label = None
        
        # 跨线程 GUI 更新队列 (必须在 _setup_ui 前初始化)
        import queue as _queue
        self._gui_queue: "_queue.Queue" = _queue.Queue()
        self._main_tid = threading.get_ident()

        # 跨平台品牌启动: 1.5 秒 Splash 后自动销毁
        # Splash 必须在 _setup_ui 之前, 因为 _setup_ui 创建主窗口 widget
        # 主窗口暂时隐藏, splash 关闭后显示
        self._splash_done = False
        self._show_splash_then_init_ui()  # 内部会调 _setup_ui + _init_components_async (同步顺序)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._bind_shortcuts()

        # 启动 GUI queue pump (主线程)
        try:
            self.root.after(50, self._pump_gui_queue)
        except Exception:
            pass

        print_log(LogLevel.INFO, self.log_title, f"初始化完成")
    
    def _show_splash_then_init_ui(self) -> None:
        """显示启动画面, 完成后自动调用 _setup_ui.

        品牌 Splash 1.5s 内显示 Logo + 加载动画, 然后:
          1. 调用 _setup_ui 创建主界面
          2. 显示主窗口

        测试短路: 设环境变量 OHCRCPY_NO_SPLASH=1 跳过 Splash (单元测试用)
        """
        import os as _os
        if _os.environ.get("OHCRCPY_NO_SPLASH") == "1":
            # 测试模式: 立即 setup, 不显示 splash
            try:
                self._setup_ui()
            except Exception as e:
                from core import print_log, LogLevel
                print_log(LogLevel.ERROR, self.log_title, f"GUI 初始化失败: {e}")
                import traceback
                traceback.print_exc()
                return
            # 测试模式: 也调一次 init_components (no splash 路径)
            try:
                self._init_components_async()
            except Exception:
                pass
            return

        # 主窗口已经在 __init__ 顶部 withdraw 了, 这里不再重复

        def _on_splash_finish():
            # 1) 先做窗口尺寸自适应 + zoomed (在 root 仍 withdrawn 时设置,
            #    这样 _setup_ui 创建 widget 时拿到的尺寸是最终尺寸)
            try:
                self._apply_initial_geometry()
            except Exception as e:
                print_log(LogLevel.WARN, "GUI", f"窗口尺寸自适应失败: {e}")
            # 2) 然后 _setup_ui 创建 widget
            try:
                self._setup_ui()
            except Exception as e:
                from core import print_log, LogLevel
                print_log(LogLevel.ERROR, self.log_title, f"GUI 初始化失败: {e}")
                import traceback
                traceback.print_exc()
                return
            # 3) 最后才 deiconify 显示 -> 用户只看到完整主窗口 (无白闪)
            try:
                self.root.deiconify()
                self.root.update_idletasks()
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass
            # 4) [关键修复] 必须在 _setup_ui 完成后 (video_canvas 就绪) 才能 init components
            # 否则 device_controller.bind_video_canvas 收到 None -> AttributeError
            try:
                self._init_components_async()
            except Exception as e:
                from core import print_log, LogLevel
                print_log(LogLevel.ERROR, self.log_title, f"组件初始化失败: {e}")
                import traceback
                traceback.print_exc()

        try:
            SplashScreen(self.root, on_finish=_on_splash_finish)
        except Exception as e:
            from core import print_log, LogLevel
            print_log(LogLevel.WARN, self.log_title,
                f"SplashScreen 启动失败 (非致命), 直接进入主界面: {e}")
            _on_splash_finish()

    def _apply_initial_geometry(self) -> None:
        """窗口尺寸自适应. 在 root 仍 withdrawn 时调用, _setup_ui 之前.

        注意: 不要在这里调 state('zoomed') 或 root.deiconify/update_idletasks,
        会导致 root 提前 visible -> 用户看到一闪空白主窗口 (在 splash 后).
        _setup_ui 完成后, on_finish 会一次性 deiconify + lift.
        """
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            cur_w = self.root.winfo_width()
            cur_h = self.root.winfo_height()
            # WM 边框 + 标题栏 + 任务栏预留
            WM_RESERVE_TOP = 30
            WM_RESERVE_BOTTOM = 40
            WM_DECORATION_H = 41
            WM_DECORATION_W = 4
            avail_h = screen_h - WM_RESERVE_TOP - WM_RESERVE_BOTTOM - WM_DECORATION_H
            avail_w = screen_w - WM_DECORATION_W * 2
            MIN_W, MIN_H = 1100, 800
            win_w = max(MIN_W, int(avail_w * 0.95))
            win_h = max(MIN_H, int(avail_h * 0.95))
            win_w = min(win_w, avail_w)
            win_h = min(win_h, avail_h)
            if cur_w < win_w * 0.95 or cur_h < win_h * 0.95 or cur_w < MIN_W or cur_h < MIN_H:
                self.root.geometry(f"{win_w}x{win_h}")
                print_log(LogLevel.INFO, "GUI",
                    f"自适应窗口: {win_w}x{win_h} (屏幕 {screen_w}x{screen_h}, avail {avail_w}x{avail_h})")
            else:
                print_log(LogLevel.INFO, "GUI",
                    f"窗口尺寸足够: {cur_w}x{cur_h} (屏幕 {screen_w}x{screen_h}, avail {avail_w}x{avail_h})")
        except Exception as e:
            print_log(LogLevel.WARN, "GUI", f"窗口尺寸自适应失败: {e}, 用默认 1450x900")
            self.root.geometry("1450x900")

    def _setup_ui(self) -> None:
        """设置UI (现代扁平化重构版)"""
        # 根窗口背景
        self.root.configure(bg=Theme.BG_BASE)
        self._create_title_bar()
        self._create_main_content()
        self._create_status_bar()
        self._show_waiting_screen()
        # 初始窗口比例: 根据设备默认 9:16, 留出右侧工具条空间
        # 实际尺寸在连接后根据 _video_canvas 比例动态调整
        self.root.update_idletasks()

    def _create_title_bar(self) -> None:
        """现代扁平标题栏 (36px 高, 深色 Slate + 顶部光带)."""
        title_frame = tk.Frame(
            self.root, height=Theme.TITLE_BAR_HEIGHT, bg=Theme.BG_PANEL,
        )
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)

        # 顶部 2px 青色光带 (品牌高光)
        title_glow = tk.Frame(
            title_frame, height=2, bg=BrandColor.PRIMARY_LIGHT,
        )
        title_glow.pack(side=tk.TOP, fill=tk.X)

        # 左侧: 应用名
        left = tk.Frame(title_frame, bg=Theme.BG_PANEL)
        left.pack(side=tk.LEFT, padx=12, pady=0)

        tk.Label(
            left, text="OHScrcpy",
            font=Theme.FONT_TITLE, fg=Theme.ACCENT, bg=Theme.BG_PANEL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(
            left, text=f"OpenHarmony 投屏  •  {VERSION}",
            font=Theme.FONT_SMALL, fg=Theme.TEXT_MUTED, bg=Theme.BG_PANEL,
        ).pack(side=tk.LEFT)

        # 右侧: 帮助图标 + 设备状态
        right = tk.Frame(title_frame, bg=Theme.BG_PANEL)
        right.pack(side=tk.RIGHT, padx=12, pady=0)

        # 帮助按钮 (?)
        help_btn = ModernButton(
            right, icon="?", text="", command=self._show_help_dialog,
            style="ghost", width=28, height=28, icon_only=True,
        )
        help_btn.pack(side=tk.RIGHT, padx=(4, 0))
        help_btn._radius = 14   # 圆形
        help_btn._draw()

        # 设备状态文字
        self.device_status_label = tk.Label(
            right, text="● 未连接",
            font=Theme.FONT_SMALL, fg=Theme.TEXT_MUTED, bg=Theme.BG_PANEL,
        )
        self.device_status_label.pack(side=tk.RIGHT, padx=(0, 8))

    def _create_main_content(self) -> None:
        """主内容区: 投屏画布 + 紧凑工具条 (右侧 68px)."""
        main_frame = tk.Frame(self.root, bg=Theme.BG_BASE)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # ── 右侧 (先 pack, 固定宽度) ──
        right_container = tk.Frame(main_frame, bg=Theme.BG_BASE, width=Theme.TOOLBAR_WIDTH + 16)
        right_container.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 8), pady=8)
        right_container.pack_propagate(False)

        # ── 左侧 (后 pack, 占满剩余空间) ──
        left_container = tk.Frame(main_frame, bg=Theme.BG_BASE)
        left_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)

        # 视频显示容器
        video_container = tk.Frame(left_container, bg=Theme.BG_BASE)
        video_container.pack(fill=tk.BOTH, expand=True)

        self.video_panel = VideoPanel(video_container)
        self.video_canvas = self.video_panel.get_canvas()

        # 设备面板 (放在工具条上方)
        self.device_panel = DevicePanel(
            right_container, self._refresh_devices,
            self._trigger_connection, self._on_combobox_select,
        )

        # 工具条
        callbacks = {
            'refresh': self._refresh_devices,
            'connect': self._trigger_connection,
            'power': self._power_key,
            'volume_up': self._volume_up,
            'volume_down': self._volume_down,
            'unlock': self._unlock_screen,
            'screenshot': self._screenshot,
            'record': self._toggle_recording,
            'rotate': self._rotate_screen,
            'screen_off': self._screen_off,
            'toggle_keyboard': self._toggle_keyboard_mapper,
        }
        self.control_panel = ControlPanel(right_container, callbacks)

    def _create_status_bar(self) -> None:
        """底部状态栏 (默认隐藏, F11 切换)."""
        self.status_frame = tk.Frame(self.root, height=22, bg=Theme.BG_PANEL)
        self._status_bar_packed = False

        self.status_label = tk.Label(
            self.status_frame, text="就绪",
            font=Theme.FONT_SMALL, fg=Theme.TEXT_MUTED, bg=Theme.BG_PANEL,
            anchor=tk.W,
        )
        self.status_label.pack(side=tk.LEFT, padx=10)

        self.connection_status_label = tk.Label(
            self.status_frame, text="未连接",
            font=Theme.FONT_SMALL, fg=Theme.ACCENT_HOT, bg=Theme.BG_PANEL,
            anchor=tk.E,
        )
        self.connection_status_label.pack(side=tk.RIGHT, padx=10)

        self.performance_label = tk.Label(
            self.status_frame, text="FPS: 0 | 帧数: 0",
            font=Theme.FONT_SMALL, fg=Theme.ACCENT, bg=Theme.BG_PANEL,
        )
        self.performance_label.pack(side=tk.RIGHT, padx=20)

        self.root.bind('<F11>', lambda e: self._toggle_status_bar())

    def _toggle_status_bar(self) -> None:
        """F11 切换状态栏显示."""
        if self._status_bar_packed:
            self.status_frame.pack_forget()
            self._status_bar_packed = False
        else:
            self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)
            self.status_frame.pack_propagate(False)
            self._status_bar_packed = True

    def _toggle_osd(self) -> None:
        """F8 切换 OSD 显示 (PillBadge)."""
        if self.video_display:
            self.video_display.toggle_osd()
            state = "隐藏" if not self.video_display._osd_visible else "显示"
            self._update_device_status(f"OSD {state} (F8 切换)")

    def _show_help_dialog(self) -> None:
        """点击 ? 弹出帮助/快捷键说明."""
        win = tk.Toplevel(self.root)
        win.title("操作说明")
        win.geometry("460x520")
        win.configure(bg=Theme.BG_BASE)
        win.attributes("-topmost", True)

        tk.Label(
            win, text="OHScrcpy 快捷键",
            font=Theme.FONT_TITLE, fg=Theme.TEXT_PRIMARY, bg=Theme.BG_BASE,
        ).pack(pady=(16, 8), anchor="w", padx=20)

        rows = [
            ("F5",  "刷新设备列表"),
            ("F6",  "保存当前帧为调试图像"),
            ("F8",  "切换 OSD (FPS/帧数) 显示"),
            ("F9",  "强制垃圾回收"),
            ("F11", "切换底部状态栏"),
            ("F12", "启用/禁用键盘映射"),
            ("",    ""),
            ("↑↓←→", "桌面焦点导航 (自动重复)"),
            ("Enter","激活当前焦点项"),
            ("Esc",  "返回"),
            ("F1",   "回主页"),
            ("",    ""),
            ("截屏", "工具条按钮 ◉"),
            ("录屏", "工具条按钮 ●"),
            ("熄屏运行", "保持投屏但关闭设备屏幕, 省电"),
        ]
        frame = tk.Frame(win, bg=Theme.BG_PANEL)
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        for i, (k, v) in enumerate(rows):
            bg = Theme.BG_PANEL if i % 2 == 0 else Theme.BG_ELEVATED
            row = tk.Frame(frame, bg=bg)
            row.pack(fill=tk.X)
            tk.Label(
                row, text=k, font=("Consolas", 10, "bold"),
                fg=Theme.ACCENT, bg=bg, width=14, anchor="w",
            ).pack(side=tk.LEFT, padx=(12, 6), pady=4)
            tk.Label(
                row, text=v, font=Theme.FONT_SMALL,
                fg=Theme.TEXT_PRIMARY, bg=bg, anchor="w",
            ).pack(side=tk.LEFT, padx=6, pady=4)

        ModernButton(
            win, text="关闭", command=win.destroy,
            style="secondary", width=100, height=32,
        ).pack(pady=(4, 16))

    def _screenshot(self) -> None:
        """截屏: 把当前帧保存到 logs/."""
        try:
            from datetime import datetime
            import os
            from PIL import Image
            vc = self.connection_manager.get_video_client() if self.connection_manager else None
            frame = vc.frame_queue.queue[-1] if vc and not vc.frame_queue.empty() else None
            if frame is None:
                self._update_device_status("截屏失败: 暂无帧")
                return
            os.makedirs("logs", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join("logs", f"screen_{ts}.png")
            Image.fromarray(frame).save(path)
            self._update_device_status(f"截屏已保存: {path}")
            print_log(LogLevel.INFO, "GUI", f"截屏: {path}")
        except Exception as e:
            print_log(LogLevel.ERROR, "GUI", f"截屏失败: {e}")
            self._update_device_status(f"截屏失败: {e}")

    def _toggle_recording(self) -> None:
        """录屏 (基础版: 复用截屏, 后续可扩展)."""
        self._update_device_status("录屏功能: 待扩展 (TODO)")

    def _rotate_screen(self) -> None:
        """旋转屏幕: 切换 video display 的旋转角度 (TODO 集成到 server)."""
        self._update_device_status("旋转屏幕: 待扩展 (TODO)")

    def _screen_off(self) -> None:
        """熄屏运行: 关掉设备屏幕但保持投屏."""
        # OHOS 没有标准关屏 API; 用 power 键触发屏幕灭 + 关闭背光
        if self.device_controller:
            self.device_controller.power_key()
            self._update_device_status("已发送电源键 (设备屏幕将熄灭, 投屏继续)")

    def _recents_key(self) -> None:
        """多任务键: 长按 home (OHOS 多任务通常由 home 触发)."""
        if self.device_controller:
            self.device_controller.home_key()
            self._update_device_status("多任务 (home)")

    def _bind_shortcuts(self) -> None:
        """绑定快捷键 (含键盘映射到设备)"""
        self.root.bind('<F5>', lambda e: self._refresh_devices())
        self.root.bind('<F6>', lambda e: self._save_debug_frame())
        # F8 切换 OSD (FPS/帧数) 显示; Shift+F8 弹原调试窗口
        self.root.bind('<F8>', lambda e: self._toggle_osd())
        self.root.bind('<Shift-F8>', lambda e: self._show_debug_window())
        self.root.bind('<F9>', lambda e: self.video_display.force_garbage_collection() if self.video_display else None)
        # F12 切换键盘映射开关
        self.root.bind('<F12>', lambda e: self._toggle_keyboard_mapper())
        
        # 键盘事件 -> 设备按键注入 (全局捕获)
        # 注意: 当 canvas 有焦点时也会触发,这样可以实现"边看边输入"
        self.keyboard_mapper = None  # 在 _init_components_async 中初始化
        self.root.bind('<Key>', self._on_host_key_press)
        self.root.bind('<KeyRelease>', self._on_host_key_release)
        # 关键: 让 root 接收键盘焦点 (Tkinter 默认不会自动获取)
        self.root.focus_set()
        # 同时绑定到 ALL 模式,确保焦点在任意子组件时都能收到事件
        self.root.bind_all('<Key>', self._on_host_key_press, add="+")
        self.root.bind_all('<KeyRelease>', self._on_host_key_release, add="+")
    
    def _on_host_key_press(self, event) -> None:
        """处理主机键盘按下事件 -> 转发到设备"""
        if self.keyboard_mapper is None:
            return
        # 焦点在输入控件时不转发 (避免误输入)
        focused = self.root.focus_get()
        if focused is not None:
            cls = focused.winfo_class()
            if cls in ('Entry', 'TEntry', 'Text', 'TCombobox'):
                return
        self.keyboard_mapper.handle_key_press(event)
    
    def _on_host_key_release(self, event) -> None:
        """处理主机键盘释放事件 -> 转发到设备"""
        if self.keyboard_mapper is None:
            return
        self.keyboard_mapper.handle_key_release(event)
    
    def _toggle_keyboard_mapper(self) -> None:
        """F12 切换键盘映射开关"""
        if self.keyboard_mapper is not None:
            self.keyboard_mapper.toggle()
            status = "启用" if self.keyboard_mapper.enabled else "禁用"
            self._update_device_status(f"键盘映射 {status} (F12 切换)")
    

    def _init_components_sync(self) -> None:
        """主线程同步初始化 (GUI 相关组件, 必须在主线程创建).

        关键修复: 原来全部丢给 _init_components_async 子线程,
        导致 device_controller.bind_video_canvas 抛
        RuntimeError("main thread is not in main loop"), video_display 永远 None,
        canvas 全黑. 现在把 video_display (要 bind canvas) 放主线程.
        """
        # 1) hdc/manager/server_deployer 可以后台线程初始化
        # 2) device_controller 创建 + bind_video_canvas (GUI 操作) 必须在主线程
        # 3) video_display 创建必须在主线程
        # 4) video_display.start_main_poll() 必须在主线程
        # 5) connection_manager 创建需要 video_display.on_frame_decoded (主线程创建的)

        # 简化方案: 全部放到后台线程, 但用 self.root.after 把 GUI 操作转到主线程
        self._defer_to_main(lambda: self._do_init_in_main())

    def _do_init_in_main(self) -> None:
        """在主线程真实执行组件初始化 (GUI 操作)."""
        try:
            # 关键修复: 必须先在主线程创建 HDCCommandExecutor, 再传给 DeviceController.
            # 之前 self.hdc_executor = None 时传给 DeviceController, 导致 send_tap / _ensure_uitest_daemon
            # 全都报 "'NoneType' object has no attribute 'execute'", 鼠标点击无任何反应.
            # HDCCommandExecutor 构造只是常量赋值 + 找 hdc 路径, 无 GUI, 主线程创建安全.
            if self.hdc_executor is None:
                self.hdc_executor = HDCCommandExecutor()

            # device_controller 和 video_display 必须主线程创建
            if not hasattr(self, 'device_controller') or self.device_controller is None:
                self.device_controller = DeviceController(self.hdc_executor)
                self.device_controller.bind_video_canvas(self.video_canvas)

            if not hasattr(self, 'keyboard_mapper') or self.keyboard_mapper is None:
                self.keyboard_mapper = KeyboardMapper(self.hdc_executor, enabled=True)

            if self.video_display is None:
                self.video_display = VideoDisplay(
                    root=self.root,
                    canvas=self.video_canvas,
                    device_controller=self.device_controller,
                    performance_label=self.performance_label
                )
                # 启动主线程 poll 循环
                self.video_display.start_main_poll()
        except Exception as e:
            from core import LogLevel, print_log
            print_log(LogLevel.ERROR, self.log_title, f"主线程组件初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return

        # 关键: hdc 是阻塞 IO, 必须放后台线程, 但所有 GUI 操作包到主线程
        # hdc_executor 已在 _do_init_in_main 头部创建, 这里直接复用
        def init_background() -> None:
            try:
                self.device_manager = DeviceManager(self.hdc_executor)

                self.server_deployer = ServerDeployer(
                    device_manager=self.device_manager,
                    on_deploy_finish=self._on_server_deploy_finish,
                )

                self.connection_manager = ConnectionManager(
                    device_manager=self.device_manager,
                    hdc_executor=self.hdc_executor,
                    on_frame_decoded=self.video_display.on_frame_decoded,
                    on_state_changed=self._on_connection_state_changed,
                    debug=False,
                )
                self.video_client = self.connection_manager.get_video_client()
                self.video_display.connection_manager = self.connection_manager
                # _refresh_devices 包含 GUI 操作, 转到主线程
                self._defer_to_main(lambda: self._refresh_devices())
            except Exception as e:
                from core import LogLevel, print_log
                print_log(LogLevel.ERROR, self.log_title, f"后台组件初始化失败: {e}")
                import traceback
                traceback.print_exc()
        threading.Thread(target=init_background, daemon=True).start()

    def _pump_gui_queue(self) -> None:
        """主线程周期 poll GUI 更新队列."""
        import queue as _queue
        try:
            while True:
                fn, args, kwargs = self._gui_queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    from core import LogLevel, print_log
                    print_log(LogLevel.WARN, self.log_title, f"GUI queue task failed: {e}")
        except _queue.Empty:
            pass
        try:
            self.root.after(50, self._pump_gui_queue)
        except Exception:
            pass

    def _defer_to_main(self, fn: Callable[[], None]) -> None:
        """把 GUI 操作放入队列, 主线程 pump 会执行 (子线程安全)."""
        try:
            self._gui_queue.put_nowait((fn, (), {}))
        except Exception as e:
            from core import LogLevel, print_log
            print_log(LogLevel.WARN, self.log_title, f"defer_to_main failed: {e}")

    def _init_components_async(self) -> None:
        """向后兼容 - 改为同步分发到主线程."""
        self._init_components_sync()
    
    def _on_connection_state_changed(self, state: str) -> None:
        """连接状态变化回调"""
        self.is_connected = (state == ConnectionState.CONNECTED)
        self.root.after(0, self._update_connection_ui)
    
    def _update_connection_ui(self) -> None:
        """更新连接相关UI"""
        if self.is_connected:
            self.device_panel.set_connect_button_state("断开", "#e74c3c")
            self.connection_status_label.config(text="已连接", fg="#2ecc71")
        else:
            self.device_panel.set_connect_button_state("连接", "#2ecc71")
            self.connection_status_label.config(text="未连接", fg="#e74c3c")
            self.performance_label.config(text="FPS: 0 | 帧数: 0")
    
    def _open_project_url(self, event: Optional[tk.Event] = None) -> None:
        """打开项目地址"""
        webbrowser.open(PROJECT_URL)
    
    def _show_about_dialog(self) -> None:
        """显示关于对话框"""
        about_window = tk.Toplevel(self.root)
        about_window.title("关于 OHScrcpy")
        about_window.resizable(False, False)
        about_window.transient(self.root)
        about_window.grab_set()
        
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        win_w = 420
        win_h = 260
        x = root_x + (root_w - win_w) // 2
        y = root_y + (root_h - win_h) // 2
        about_window.geometry(f"{win_w}x{win_h}+{x}+{y}")
        
        main_frame = tk.Frame(about_window, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(main_frame, text="OHScrcpy - OpenHarmony投屏工具",
                font=("Microsoft YaHei", 12, "bold"), anchor="center").pack(pady=(0, 15))
        
        content_frame = tk.Frame(main_frame)
        content_frame.pack(fill=tk.X, anchor="w")
        
        tk.Label(content_frame, text=f"版本: {VERSION}",
                font=("Microsoft YaHei", 10), anchor="w").pack(anchor="w", padx=10, pady=2)
        
        tk.Label(content_frame, text=f"作者: {AUTHOR}",
                font=("Microsoft YaHei", 10), anchor="w").pack(anchor="w", padx=10, pady=2)
        
        tk.Label(content_frame, text="项目地址:", font=("Microsoft YaHei", 10), anchor="w").pack(anchor="w", padx=10, pady=2)
        
        url_label = tk.Label(content_frame, text=PROJECT_URL,
                             font=("Microsoft YaHei", 10), fg="blue", cursor="hand2", anchor="w")
        url_label.pack(anchor="w", padx=10, pady=2)
        url_label.bind("<Button-1>", self._open_project_url)
        
        tk.Button(main_frame, text="关闭", command=about_window.destroy,
                 font=("Microsoft YaHei", 9), width=10).pack(pady=(20, 0))
        about_window.focus_set()
    
    def _update_video_display(self) -> None:
        """更新视频显示（委托给 VideoDisplay）"""
        if self.video_display is None:
            return
        self.video_display._do_render()
    
    def _show_waiting_screen(self) -> None:
        """显示等待画面"""
        if self.video_display is not None:
            self.video_display.show_waiting_screen("等待连接设备...")
        elif self.video_canvas is not None:
            self._show_waiting_screen_direct("等待连接设备...")
    
    def _show_waiting_screen_direct(self, message: str) -> None:
        """直接操作canvas显示等待画面（video_display未初始化时使用）"""
        self.video_canvas.delete("all")
        self.video_canvas.config(bg="#1a1a2e")
        
        canvas_width = self.video_canvas.winfo_width()
        canvas_height = self.video_canvas.winfo_height()
        
        if canvas_width <= 10 or canvas_height <= 10:
            canvas_width = 800
            canvas_height = 600
        
        self.video_canvas.create_text(
            canvas_width // 2, canvas_height // 2 - 30,
            text="OHScrcpy - OpenHarmony投屏工具",
            fill="white",
            font=("Microsoft YaHei", 14, "bold")
        )
        
        self.running_status_text_id = self.video_canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 10,
            text=message,
            fill="#3498db",
            font=("Microsoft YaHei", 12)
        )
        
        self.video_canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 50,
            text="请先选择设备，然后点击[连接]按钮进行投屏",
            fill="#95a5a6",
            font=("Microsoft YaHei", 10)
        )
        
        self.status_text_id = None
    
    def _update_running_status(self, content: str) -> None:
        """更新运行状态"""
        if self.video_display is not None:
            self.video_display.update_running_status(content)
    
    def _refresh_devices(self) -> None:
        """刷新设备"""
        if self.device_manager is None or self.device_panel is None:
            return
        
        self._update_device_status("正在扫描设备...")
        devices = self.device_manager.discover_devices()
        display_names = [d.display_name() for d in devices]
        current_selection = self.device_panel.get_selected_device()
        
        if not self.is_connected:
            if devices:
                self.device_panel.update_devices(display_names)
                self._update_device_status(f"发现 {len(devices)} 个设备")
                self._on_combobox_select(None)
            else:
                self.device_panel.update_devices([])
                self._update_device_status("未发现设备")
            return
        
        if not devices:
            messagebox.showwarning("提示", "当前投屏设备已断开")
            self._disconnect_device()
            self.device_panel.update_devices([])
            self._update_device_status("设备已断开，未发现其他设备")
            return
        
        if current_selection and current_selection not in display_names:
            messagebox.showwarning("提示", "当前投屏设备已断开")
            self._disconnect_device()
            self.device_panel.update_devices(display_names)
            self._update_device_status(f"设备已断开，发现 {len(display_names)} 个设备")
            return
        
        current_list = list(self.device_panel.device_combo['values'] or [])
        new_devices = [name for name in display_names if name not in current_list]
        
        if new_devices:
            self.device_panel.update_devices(display_names, current_selection)
            self._update_device_status(f"发现 {len(new_devices)} 个新设备（投屏中）")
            print_log(LogLevel.INFO, self.log_title, f"新增设备: {new_devices}")
        else:
            self._update_device_status(f"未发现新设备（投屏中，列表 {len(current_list)} 个）")
    
    def _trigger_connection(self) -> None:
        """连接/断开设备"""
        if not self.is_connected:
            self._connect_device()
        else:
            self._disconnect_device()
    
    def _set_server_deploy_state(self, state: ServerDeployState) -> None:
        """设置服务部署状态"""
        with self.server_deploy_lock:
            self.server_deploy_state = state
    
    def _get_server_deploy_state(self) -> ServerDeployState:
        """获取服务部署状态"""
        with self.server_deploy_lock:
            return self.server_deploy_state
    
    def _on_combobox_select(self, event: Optional[tk.Event]) -> None:
        """设备选择事件"""
        print_log(LogLevel.INFO, self.log_title, "-"*60)
        
        selected_device = self.device_panel.get_selected_device()
        print_log(LogLevel.INFO, self.log_title, f"用户选择设备: {selected_device}")
        
        if self.is_connected:
            current_device = self.device_manager.get_current_device()
            
            if current_device is None:
                print_log(LogLevel.WARN, self.log_title, f"投屏中但无法获取当前设备信息")
                return
            
            if current_device.display_name() == selected_device:
                print_log(LogLevel.INFO, self.log_title, f"选择了当前正在投屏的设备，保持投屏状态")
                return
            
            print_log(LogLevel.INFO, self.log_title, f"投屏中切换设备: {current_device.display_name()} -> {selected_device}")
            response = messagebox.askyesno(
                "切换设备确认",
                f"当前正在投屏设备:\n{current_device.display_name()}\n\n"
                f"是否切换到设备:\n{selected_device}?\n\n"
                "切换将断开当前投屏连接。"
            )
            
            if not response:
                print_log(LogLevel.INFO, self.log_title, f"用户取消切换，恢复设备选择显示")
                self.device_panel.set_selected_device(current_device.display_name())
                return
            
            print_log(LogLevel.INFO, self.log_title, f"用户确认切换设备，准备断开当前连接")
        
        print_log(LogLevel.INFO, self.log_title, f"投屏准备: 开始安装和启动服务端...")
        self._install_and_start_server_async()
    
    def _on_server_deploy_finish(self, succ: bool, msg: str) -> None:
        """服务部署完成回调"""
        self.connection_status_label.config(text="未连接", fg="#e74c3c")
        if not succ:
            messagebox.showerror("错误", f"{msg}")
            return
        # 修复:RK3568 HEVC bug 绕过 — MJPEG 模式下部署完直接连接
        if os.environ.get('OHCRCPY_MJPEG_MODE', '') in ('1', 'true', 'yes'):
            print_log(LogLevel.INFO, self.log_title, "[MJPEG模式] server_deployer 完成,自动连接 MJPEG 服务")
            self._connect_device()
    
    def _install_and_start_server_async(self) -> None:
        """异步安装并启动服务端"""
        was_connected = self.is_connected
        
        # 切换设备时先断开旧连接
        if was_connected:
            print_log(LogLevel.INFO, self.log_title, f"断开当前设备连接...")
            self.is_connected = False
            self._disconnect_device()
        
        selected = self.device_panel.get_selected_device()
        if not selected:
            print_log(LogLevel.WARN, self.log_title, f"用户选择设备为空")
            return
        
        self._show_waiting_screen()
        self.device_panel.set_connect_button_state("连接", "#2ecc71")
        self.connection_status_label.config(text="未连接", fg="#e74c3c")
        self.performance_label.config(text="FPS: 0 | 帧数: 0")
        
        self.server_deployer.deploy(
            selected_device_name=selected,
            devices=self.device_manager.devices,
            update_running_status=self._update_running_status,
            ui_callback=lambda f: self.root.after(0, f),
        )
    
    def _connect_device(self) -> None:
        """连接设备"""
        selected = self.device_panel.get_selected_device()
        if not selected:
            messagebox.showwarning("警告", "请先选择设备")
            return
        
        target_device: Optional[Any] = None
        for device in self.device_manager.devices:
            if device.display_name() == selected:
                target_device = device
                break
        
        if not target_device or not self.device_manager.select_device(target_device.sn):
            self._update_device_status("设备选择失败")
            return
        
        def connect_device_async() -> None:
            port = self.device_manager.get_port_forwarding()
            if port == -1:
                print_log(LogLevel.ERROR, self.log_title, f"获取可用转发端口失败")
                self.device_panel.set_connect_button_state("连接", "#2ecc71")
                return
            
            # 修复:RK3568 HEVC 编码器 bug 绕过路径 - 通过环境变量 OHCRCPY_MJPEG_MODE=1 启用
            use_mjpeg = os.environ.get('OHCRCPY_MJPEG_MODE', '') in ('1', 'true', 'yes')

            if use_mjpeg:
                # MJPEG 模式:无需部署 ohscrcpy_server,改为部署 busybox httpd + 截图循环
                mjpeg_port = int(os.environ.get('OHCRCPY_MJPEG_PORT', '27190'))
                print_log(LogLevel.INFO, self.log_title, f"[MJPEG模式] 部署设备端 MJPEG 服务 (port={mjpeg_port})")
                mjpeg_mgr = MjpegServerManager(self.hdc_executor)
                if not mjpeg_mgr.setup(port=mjpeg_port, env_override=True, interval_s=0.15):
                    print_log(LogLevel.ERROR, self.log_title, "MJPEG 服务部署失败")
                    self.device_panel.set_connect_button_state("连接", "#2ecc71")
                    return
                # 注意:port 转发已在 MjpegServerManager.setup 内完成
            else:
                # 原有 HEVC 路径
                self.connection_manager.ensure_server_manager(target_device.manufacturer, self.hdc_executor)

                if not self._install_and_start_server(port, self.connection_manager.get_server_manager()):
                    self.device_panel.set_connect_button_state("连接", "#2ecc71")
                    return

                print_log(LogLevel.INFO, self.log_title, f"设置端口转发...")
                if not self.device_manager.setup_port_forwarding(port, port):
                    print_log(LogLevel.ERROR, self.log_title, f"端口转发失败，请尝试重新连接...")
                    self.device_panel.set_connect_button_state("连接", "#2ecc71")
                    return

            try:
                device = self.device_manager.get_current_device()
                self._update_device_status(f"正在连接设备: {device.sn}...")

                print_log(LogLevel.DEBUG, self.log_title, f"连接视频流服务器...")
                # MJPEG 模式连接本地 mjpeg 端口,HEVC 模式连接 server 转发端口
                connect_port = int(os.environ.get('OHCRCPY_MJPEG_PORT', '27190')) if use_mjpeg else port
                if self.connection_manager.connect(connect_port):
                    config = self.connection_manager.get_video_client().config
                    self.is_connected = True
                    self.device_panel.set_connect_button_state("断开", "#e74c3c")
                    self.connection_status_label.config(text="已连接", fg="#2ecc71")
                    self.device_status_label.config(text=f"设备: {device.sn}")
                    
                    self.video_display.displayed_frames = 0
                    self.video_display.frame_counter = 0
                    self.video_display.last_fps_time = time.time()
                    self.video_display.last_print_frames = 0
                    
                    self.video_canvas.delete("all")
                    self.video_canvas.config(bg="black")
                    
                    self._update_video_display()
                    self._update_device_status(f"连接成功！分辨率: {config.width}x{config.height}")
                else:
                    self._update_device_status("连接失败")
                    self.device_panel.set_connect_button_state("连接", "#2ecc71")
                    messagebox.showinfo("连接失败",
                        "无法连接到服务端！请检查:\n"
                        "1. 服务端是否在设备上运行\n"
                        "2. 服务端口是否正确")
                    return
                
            except Exception as e:
                import traceback
                self._update_device_status(f"连接失败: {str(e)}")
                traceback.print_exc()
                self._disconnect_device()
                return
        
        threading.Thread(target=connect_device_async, daemon=True).start()
        self.device_panel.set_connect_button_state("连接中", "#e74c3c")
    
    def _install_and_start_server(self, port: int, server_manager) -> bool:
        """安装并启动服务端"""
        print_log(LogLevel.INFO, self.log_title, f"检查服务端安装状态...")
        if not self.device_manager.check_server_installed(server_manager):
            print_log(LogLevel.INFO, self.log_title, f"服务端未安装，开始安装...")
            
            if not self.device_manager.install_server(server_manager):
                messagebox.showerror("错误", "服务端安装失败！")
                print_log(LogLevel.ERROR, self.log_title, f"服务端安装失败")
                return False
        else:
            print_log(LogLevel.INFO, self.log_title, f"服务端已安装")
        
        print_log(LogLevel.INFO, self.log_title, f"检查服务端运行状态...")
        if not self.device_manager.check_server_running(server_manager):
            print_log(LogLevel.INFO, self.log_title, f"启动服务端...")
            if not self.device_manager.start_server(server_manager, port):
                messagebox.showerror("错误", f"服务端启动失败！")
                print_log(LogLevel.ERROR, self.log_title, f"服务端启动失败")
                return False
            
            print_log(LogLevel.INFO, self.log_title, f"等待服务端就绪...")
            time.sleep(1)
        else:
            print_log(LogLevel.INFO, self.log_title, f"服务端已在运行")
        return True
    
    def _disconnect_device(self) -> None:
        """断开设备"""
        if self.connection_manager is not None:
            self.connection_manager.disconnect()
        
        if self.video_display is not None:
            self.video_display.reset()
                
        if self.device_controller:
            self.device_controller.reset()
        
        self._show_waiting_screen()
        self.device_panel.set_connect_button_state("连接", "#2ecc71")
        self.connection_status_label.config(text="未连接", fg="#e74c3c")
        self.device_status_label.config(text="设备: 未连接")
        self.performance_label.config(text="FPS: 0 | 帧数: 0")

        if self.video_display is not None:
            self.video_display.force_garbage_collection()
        self._update_device_status("设备已断开")
        print_log(LogLevel.INFO, self.log_title, "-"*60)
    
    def _power_key(self) -> None:
        """电源键"""
        if self.is_connected and self.device_controller:
            self.device_controller.power_key()
            print_log(LogLevel.INFO, self.log_title, f"发送电源键")
    
    def _home_key(self) -> None:
        """主页键"""
        if self.is_connected and self.device_controller:
            self.device_controller.home_key()
            print_log(LogLevel.INFO, self.log_title, f"发送Home键")
    
    def _back_key(self) -> None:
        """返回键"""
        if self.is_connected and self.device_controller:
            self.device_controller.back_key()
            print_log(LogLevel.INFO, self.log_title, f"发送返回键")
    
    def _unlock_screen(self) -> None:
        """解锁"""
        if self.is_connected and self.device_controller:
            self.device_controller.unlock_screen()
            print_log(LogLevel.INFO, self.log_title, f"发送解锁屏幕")
    
    def _volume_up(self) -> None:
        """音量+"""
        if self.is_connected and self.device_controller:
            self.device_controller.volume_up()
            print_log(LogLevel.INFO, self.log_title, f"发送音量+")
    
    def _volume_down(self) -> None:
        """音量-"""
        if self.is_connected and self.device_controller:
            self.device_controller.volume_down()
            print_log(LogLevel.INFO, self.log_title, f"发送音量-")
    
    def _print_debug_info(self) -> None:
        """打印调试信息"""
        debug_info_title = "调试信息"
        print_log(LogLevel.INFO, debug_info_title, f"\n======== 调试信息 ========")
        print_log(LogLevel.INFO, debug_info_title, f"连接状态: {self.is_connected}")
        if self.video_display:
            print_log(LogLevel.INFO, debug_info_title, f"当前fps: {self.video_display.fps}")
            print_log(LogLevel.INFO, debug_info_title, f"已显示帧数: {self.video_display.displayed_frames}")
            print_log(LogLevel.INFO, debug_info_title, f"视频尺寸: {self.video_display.video_width}x{self.video_display.video_height}")
            print_log(LogLevel.INFO, debug_info_title, f"图像引用数: {len(self.video_display.image_refs)}")
        
        if self.video_client:
            print_log(LogLevel.INFO, debug_info_title, f"总接收帧数: {self.video_client.frame_count}")
            print_log(LogLevel.INFO, debug_info_title, f"总字节数: {self.video_client.total_bytes}")
            print_log(LogLevel.INFO, debug_info_title, f"队列大小: {self.video_client.frame_queue.qsize()}")
            print_log(LogLevel.INFO, debug_info_title, f"最后数据时间: {time.time() - self.video_client.last_data_time:.1f}秒前")
            print_log(LogLevel.INFO, debug_info_title, f"坏包数: {self.video_client.bad_packet_bytes}")
            print_log(LogLevel.INFO, debug_info_title, f"SPS状态: {self.video_client.sps_received}")
            print_log(LogLevel.INFO, debug_info_title, f"PPS状态: {self.video_client.pps_received}")
            print_log(LogLevel.INFO, debug_info_title, f"VPS状态: {self.video_client.vps_received}")
            
            if self.video_client.decoder:
                decoder = self.video_client.decoder
                print_log(LogLevel.INFO, debug_info_title, f"解码统计: 成功={decoder.decode_success}, 失败={decoder.decode_failure}")
                print_log(LogLevel.INFO, debug_info_title, f"解码器状态: initialized={decoder.codec_ctx is not None}")
        
        try:
            import psutil
            process = psutil.Process()
            mem_info = process.memory_info()
            print_log(LogLevel.INFO, debug_info_title, f"内存使用: RSS={mem_info.rss / 1024 / 1024:.1f}MB, VMS={mem_info.vms / 1024 / 1024:.1f}MB")
        except ImportError:
            pass
        print_log(LogLevel.INFO, debug_info_title, f"==========================\n")
    
    def _save_debug_frame(self) -> None:
        """保存当前帧用于调试"""
        debug_frame_title = "保存调试帧"
        if self.video_display is not None and self.video_display.current_frame is not None:
            try:
                from PIL import Image
                debug_dir = "debug_frames"
                os.makedirs(debug_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = os.path.join(debug_dir, f"debug_{timestamp}.png")
                
                pil_img = Image.fromarray(self.video_display.current_frame)
                pil_img.save(filename)
                
                print_log(LogLevel.INFO, debug_frame_title, f"调试帧保存在: {filename}")
                print_log(LogLevel.INFO, debug_frame_title, f"图像尺寸: {pil_img.size}")
            except Exception as e:
                print_log(LogLevel.ERROR, debug_frame_title, f"保存调试帧失败: {e}")
    
    def _show_debug_window(self) -> None:
        """显示调试窗口"""
        debug_window = tk.Toplevel(self.root)
        debug_window.title("调试信息")
        debug_window.geometry("600x400")
        debug_window.transient(self.root)
        debug_window.grab_set()
        
        text_widget = tk.Text(debug_window, wrap=tk.WORD)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        debug_info = []
        debug_info.append(f"连接状态: {self.is_connected}")
        if self.video_display:
            debug_info.append(f"当前fps: {self.video_display.fps}")
            debug_info.append(f"已显示帧数: {self.video_display.displayed_frames}")
            debug_info.append(f"视频尺寸: {self.video_display.video_width}x{self.video_display.video_height}")
        
        if self.video_client:
            debug_info.append(f"总接收帧数: {self.video_client.frame_count}")
            debug_info.append(f"总字节数: {self.video_client.total_bytes}")
            debug_info.append(f"队列大小: {self.video_client.frame_queue.qsize()}")
            debug_info.append(f"坏包数: {self.video_client.bad_packet_bytes}")
            debug_info.append(f"SPS状态: {self.video_client.sps_received}")
            debug_info.append(f"PPS状态: {self.video_client.pps_received}")
            debug_info.append(f"VPS状态: {self.video_client.vps_received}")
            
            if self.video_client.decoder:
                decoder = self.video_client.decoder
                debug_info.append(f"解码成功: {decoder.decode_success}")
                debug_info.append(f"解码失败: {decoder.decode_failure}")
            
            if hasattr(self.video_client, 'last_data_time'):
                time_since_last_data = time.time() - self.video_client.last_data_time
                debug_info.append(f"心跳状态: {time_since_last_data:.1f}秒前收到数据")
        
        text_widget.insert(tk.END, "\n".join(debug_info))
        text_widget.config(state=tk.DISABLED)
    
    def _update_device_status(self, message: str) -> None:
        """更新状态 (同时同步到窗口标题栏)"""
        try:
            current = self.root.title()
            if " [" in current:
                base = current.split(" [")[0]
            else:
                base = current
            self.root.title(f"{base} [{message}]")
        except Exception:
            pass
        # 子线程保护: config widget 必须在主线程
        if hasattr(self, '_main_tid') and threading.get_ident() != self._main_tid:
            try:
                self.root.after(0, lambda: self._update_device_status(message))
                return
            except Exception:
                pass
        if hasattr(self, 'status_label') and self.status_label:
            try:
                self.status_label.config(text=message)
            except Exception:
                pass
        try:
            print_log(LogLevel.INFO, "GUI设备状态更新", f"{message}")
        except Exception:
            pass
        # 同步动画状态点 (StatusDot): 根据 message 关键词推断状态
        try:
            if hasattr(self, "status_dot") and self.status_dot is not None:
                if message is None:
                    new_state = "idle"
                elif "失败" in message or "错误" in message:
                    new_state = "error"
                elif "成功" in message or "已连接" in message:
                    new_state = "connected"
                elif any(k in message for k in ("连接中", "正在", "扫描", "部署", "探测", "获取")):
                    new_state = "connecting"
                else:
                    new_state = "idle"
                if new_state != self.status_dot.state:
                    self.status_dot.set_state(new_state)
        except Exception:
            pass

    def _on_closing(self) -> None:
        """关闭窗口"""
        if self.is_connected:
            if messagebox.askokcancel("退出", "设备仍处于连接状态，确定要退出吗？"):
                self._disconnect_device()
                self.root.destroy()
        else:
            if self.video_client:
                self.video_client.disconnect()
            self.root.destroy()
    
    def run(self) -> None:
        """运行"""
        self.root.mainloop()


__all__ = ["MainWindow"]
