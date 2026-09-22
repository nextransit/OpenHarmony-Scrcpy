"""
设备选择面板 (现代扁平化版)

设计:
- 紧凑深色面板, 与工具条配色一致
- 设备下拉用 ttk.Combobox 但通过 ttk.Style 自定义颜色
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, List

from core import LogLevel, print_log
from gui.theme import Theme, ICONS
from gui.widgets import ModernButton


class DevicePanel:
    """设备选择面板."""

    def __init__(
        self,
        parent: tk.Widget,
        on_refresh: Callable[[], None],
        on_connect: Callable[[], None],
        on_select: Callable[[Optional[tk.Event]], None],
    ) -> None:
        self.parent: tk.Widget = parent
        self.on_refresh: Callable[[], None] = on_refresh
        self.on_connect: Callable[[], None] = on_connect
        self.on_select: Callable[[Optional[tk.Event]], None] = on_select

        self.frame: Optional[tk.Frame] = None
        self.device_var: Optional[tk.StringVar] = None
        self.device_combo: Optional[ttk.Combobox] = None
        self.connect_btn: Optional[ModernButton] = None

        self._configure_ttk_style()
        self._create_panel()

    def _configure_ttk_style(self) -> None:
        """统一 ttk Combobox 风格."""
        style = ttk.Style()
        try:
            style.theme_use("clam")  # clam 是最可定制的内置主题
        except tk.TclError:
            pass
        style.configure(
            "OH.TCombobox",
            fieldbackground=Theme.BG_ELEVATED,
            background=Theme.BG_ELEVATED,
            foreground=Theme.TEXT_PRIMARY,
            arrowcolor=Theme.ACCENT,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            darkcolor=Theme.BORDER,
            selectbackground=Theme.ACCENT,
            selectforeground=Theme.TEXT_ON_ACCENT,
        )
        style.map(
            "OH.TCombobox",
            fieldbackground=[("readonly", Theme.BG_ELEVATED)],
            foreground=[("readonly", Theme.TEXT_PRIMARY)],
        )

    def _create_panel(self) -> None:
        frame = tk.LabelFrame(
            self.parent, text="设备",
            font=Theme.FONT_SUBTITLE,
            bg=Theme.BG_PANEL, fg=Theme.TEXT_PRIMARY,
            bd=1, relief=tk.FLAT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.frame = frame

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            frame, textvariable=self.device_var,
            state="readonly", style="OH.TCombobox",
        )
        self.device_combo.bind("<<ComboboxSelected>>", self.on_select)
        self.device_combo.pack(fill=tk.X, padx=10, pady=(8, 6))

        btn_frame = tk.Frame(frame, bg=Theme.BG_PANEL)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        btn_refresh = ModernButton(
            btn_frame, icon=Theme.__dict__.get("FONT_ICON", "") and "↻",  # 借用 icon 字段
            text="刷新", command=self.on_refresh,
            style="secondary", width=68, height=32,
        )
        # ModernButton 实际需要 icon_only=False 时显示 text
        btn_refresh._icon_only = False
        btn_refresh.set_text("刷新")
        btn_refresh._draw()
        btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        self.connect_btn = ModernButton(
            btn_frame, text="连接", icon=ICONS["connect"], command=self.on_connect,
            style="success", width=88, height=32,
        )
        self.connect_btn._icon_only = False
        self.connect_btn._draw()
        self.connect_btn.pack(side=tk.RIGHT)

    def update_devices(self, devices: List[str], current_device: str = None) -> None:
        if devices:
            self.device_combo['values'] = devices
            if current_device and current_device in devices:
                self.device_combo.current(devices.index(current_device))
            else:
                self.device_combo.current(0)
        else:
            self.device_combo['values'] = []
            self.device_var.set("")

    def get_selected_device(self) -> str:
        return self.device_var.get()

    def set_connect_button_state(self, text: str, bg_color: str) -> None:
        """兼容旧 API. 自动切 icon + 文字 + 颜色:
          未连接 (text='连接'): icon='⏵' style='success' (绿)
          已连接 (text='断开'): icon='⏹' style='danger'  (红)
        """
        connected = '断开' in text
        if connected:
            self.connect_btn._icon = ICONS["disconnect"]
            self.connect_btn._text = '断开'
            self.connect_btn._style = 'danger'
        else:
            self.connect_btn._icon = ICONS["connect"]
            self.connect_btn._text = '连接'
            self.connect_btn._style = 'success'
        self.connect_btn._current_colors = self.connect_btn._colors_for('normal')
        self.connect_btn._draw()

    def get_frame(self) -> tk.LabelFrame:
        return self.frame

    def set_selected_device(self, display_name: str) -> None:
        if display_name in self.device_combo['values']:
            self.device_var.set(display_name)
        else:
            print_log(LogLevel.WARN, "DevicePanel", f"设备 {display_name} 不在设备列表中")


__all__ = ["DevicePanel"]
