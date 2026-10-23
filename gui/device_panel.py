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
from gui.ttk_bootstrap import (
    make_button as tb_button,
    make_combobox as tb_combobox,
    make_frame as tb_frame,
    make_label as tb_label,
    SUCCESS_BOOTSTRAP,
    SECONDARY_BOOTSTRAP,
    DANGER_BOOTSTRAP,
    INFO_BOOTSTRAP,
)


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
        self.connect_btn: Optional[object] = None

        self._configure_ttk_style()
        self._create_panel()

    def _configure_ttk_style(self) -> None:
        """ttkbootstrap 主题在 apply_theme() 已应用, 这里不再覆盖."""
        pass

    def _create_panel(self) -> None:
        # ttkbootstrap 容器 (圆角 + 主题色背景)
        frame = tb_frame(self.parent, bootstyle="secondary")
        frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.frame = frame

        # 标题
        tb_label(
            frame, text="设备",
            font=Theme.FONT_SUBTITLE, bootstyle="inverse-secondary",
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=(8, 4))

        self.device_var = tk.StringVar()
        self.device_combo = tb_combobox(
            frame, values=[], textvariable=self.device_var,
            state="readonly", bootstyle="secondary",
        )
        self.device_combo.bind("<<ComboboxSelected>>", self.on_select)
        self.device_combo.pack(fill=tk.X, padx=10, pady=(0, 8))

        # 按钮行
        btn_frame = tb_frame(frame, bootstyle="")
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.refresh_btn = tb_button(
            btn_frame, text="刷新", icon="↻",
            command=self.on_refresh,
            bootstyle=SECONDARY_BOOTSTRAP, width=8,
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.connect_btn = tb_button(
            btn_frame, text="连接", icon=ICONS["connect"],
            command=self.on_connect,
            bootstyle=SUCCESS_BOOTSTRAP, width=10,
        )
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
          未连接 (text='连接'): icon='⏵' bootstyle='success' (绿)
          已连接 (text='断开'): icon='⏹' bootstyle='danger'  (红)
        """
        connected = '断开' in text
        if connected:
            new_text = '⏹ 断开'
            new_bootstyle = DANGER_BOOTSTRAP
        else:
            new_text = '⏵ 连接'
            new_bootstyle = SUCCESS_BOOTSTRAP
        try:
            self.connect_btn.configure(text=new_text, bootstyle=new_bootstyle)
        except Exception:
            pass

    def get_frame(self) -> object:
        return self.frame

    def set_selected_device(self, display_name: str) -> None:
        if display_name in self.device_combo['values']:
            self.device_var.set(display_name)
        else:
            print_log(LogLevel.WARN, "DevicePanel", f"设备 {display_name} 不在设备列表中")


__all__ = ["DevicePanel"]
