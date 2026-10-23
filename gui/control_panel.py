"""
设备控制面板 (现代扁平化重构版)

设计要点:
- 紧凑工具条 (68px 宽), 垂直堆叠图标按钮
- 删除"操作说明"文字面板, 收纳到 MainWindow 顶部的 ? 帮助弹窗
- 高频物理键 (Back/Home/Recents) 移到 FloatingBar (投屏画面正底部)
- 本面板只保留: 截屏/录屏/旋转/熄屏运行/音量+/音量-/刷新/连接 等工具
"""

import tkinter as tk
from tkinter import messagebox
from typing import Dict, Callable, Optional, List

from gui.theme import Theme, ICONS
from gui.ttk_bootstrap import (
    make_button as tb_button,
    make_separator,
    SECONDARY_BOOTSTRAP,
    SUCCESS_BOOTSTRAP,
    DANGER_BOOTSTRAP,
    INFO_BOOTSTRAP,
    WARNING_BOOTSTRAP,
)


class ControlPanel:
    """紧凑工具条式控制面板 — ttkbootstrap 风格.

    每个按钮是 ttkbootstrap.Button, 垂直堆叠 (grid 布局) + 分组分隔线 (ttk.Separator).
    整体宽度 Theme.TOOLBAR_WIDTH (68px), 高度自适应.
    """

    # 按钮规格定义: (key, icon, tooltip_text, bootstyle, callback_name)
    BUTTON_SPECS = [
        # 分组 1: 连接/刷新
        ("refresh",     ICONS["refresh"],     "刷新设备",     "secondary", "refresh"),
        ("connect",     ICONS["connect"],     "连接/断开",     "success",   "connect"),
        "__sep__",
        # 分组 2: 设备控制
        ("power",       ICONS["power"],       "电源",         "danger",    "power"),
        ("volume_up",   ICONS["volume_up"],   "音量+",        "secondary", "volume_up"),
        ("volume_down", ICONS["volume_down"], "音量-",        "secondary", "volume_down"),
        ("unlock",      ICONS["unlock"],      "解锁屏幕",     "secondary", "unlock"),
        "__sep__",
        # 分组 3: 工具
        ("screenshot",  ICONS["screenshot"],  "截屏",         "info",      "screenshot"),
        ("record",      ICONS["record"],      "录屏",         "warning",   "record"),
        ("rotate",      ICONS["rotate"],      "旋转屏幕",     "secondary", "rotate"),
        ("screen_off",  ICONS["screen_off"],  "熄屏运行",     "secondary", "screen_off"),
        "__sep__",
        # 分组 4: 键盘映射
        ("keyboard",    ICONS["keyboard"],    "键盘映射 F12",  "secondary", "toggle_keyboard"),
    ]

    def __init__(self, parent: tk.Widget, callbacks: Dict[str, Callable[[], None]]) -> None:
        self.parent: tk.Widget = parent
        self.callbacks: Dict[str, Callable[[], None]] = callbacks
        self.frame: Optional[tk.Widget] = None
        self.toolbar: Optional[tk.Widget] = None
        self._buttons: Dict[str, object] = {}

        self._create_panel()

    def _create_panel(self) -> None:
        from tkinter import ttk as tk_ttk
        # 外层容器: ttkbootstrap frame (背景跟主题)
        outer = tk_ttk.Frame(self.parent, padding=2)
        outer.pack(fill=tk.BOTH, expand=True, padx=(2, 4), pady=2)
        self.frame = outer

        toolbar = tk_ttk.Frame(outer, padding=2)
        toolbar.pack(fill=tk.BOTH, expand=True)
        self.toolbar = toolbar

        # 网格布局: 1 列 N 行, 每个按钮或分隔线占一行
        toolbar.columnconfigure(0, weight=1, uniform="col")

        row = 0
        for spec in self.BUTTON_SPECS:
            if spec == "__sep__":
                # 分隔线
                make_separator(toolbar, bootstyle="secondary").grid(
                    row=row, column=0, sticky="ew", padx=4, pady=4
                )
                row += 1
                continue

            key, icon, tooltip, bootstyle, cb_name = spec
            btn = tb_button(
                toolbar,
                text=icon,
                command=self.callbacks.get(cb_name),
                bootstyle=bootstyle,
                width=3,  # 紧凑
            )
            btn.grid(row=row, column=0, sticky="nsew", padx=2, pady=2)
            # tooltip (通过 bind + Toplevel)
            self._bind_tooltip(btn, tooltip)
            self._buttons[key] = btn
            row += 1

        # 让按钮行也 expand (占满垂直空间)
        for r in range(row):
            toolbar.rowconfigure(r, weight=1)

    def _bind_tooltip(self, widget: tk.Widget, text: str) -> None:
        """简易 tooltip: 鼠标 hover 显示 .5s 后小提示."""
        tip_win = [None]

        def show(_e=None):
            if tip_win[0]:
                return
            try:
                x = widget.winfo_rootx() + widget.winfo_width() + 4
                y = widget.winfo_rooty() + widget.winfo_height() // 2 - 10
                w = tk.Toplevel(widget)
                w.wm_overrideredirect(True)
                w.geometry(f"+{x}+{y}")
                tk.Label(
                    w, text=text, font=("Microsoft YaHei", 9),
                    bg="#1e293b", fg="#f1f5f9",
                    padx=6, pady=2, bd=1, relief="solid",
                ).pack()
                tip_win[0] = w
            except Exception:
                pass

        def hide(_e=None):
            if tip_win[0]:
                try:
                    tip_win[0].destroy()
                except Exception:
                    pass
                tip_win[0] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)
        widget.bind("<Button-1>", hide, add="+")

    def get_frame(self) -> tk.Widget:
        return self.frame

    def get_button(self, name: str) -> Optional[object]:
        return self._buttons.get(name)

    def update_connect_button(self, connected: bool) -> None:
        """更新连接按钮: connected=True -> 断开图标(可点击), False -> 连接图标."""
        btn = self._buttons.get('connect')
        if not btn:
            return
        try:
            if connected:
                btn.configure(text=ICONS["disconnect"], bootstyle="danger")
                btn.configure(state="normal")
            else:
                btn.configure(text=ICONS["connect"], bootstyle="success")
                btn.configure(state="normal")
        except Exception:
            pass

    def set_connect_button_connecting(self) -> None:
        """连接中状态: 显示 connecting 图标, 禁用点击."""
        btn = self._buttons.get('connect')
        if not btn:
            return
        try:
            btn.configure(text=ICONS["connecting"], bootstyle="secondary")
            btn.configure(state="disabled")
        except Exception:
            pass

    def set_buttons_enabled(self, enabled: bool) -> None:
        """批量启用/禁用除连接按钮外的所有操作按钮.

        未连接时: 操作按钮灰色不可点击.
        已连接时: 操作按钮正常可点击.
        """
        skip = {'connect', 'refresh'}
        state = "normal" if enabled else "disabled"
        for name, btn in self._buttons.items():
            if name in skip:
                continue
            try:
                btn.configure(state=state)
            except Exception:
                pass


# 保留 InfoPanel 占位以兼容旧 import (但实际不再使用)
class InfoPanel:
    """已弃用, 保留以兼容旧 import."""
    def __init__(self, parent: tk.Widget) -> None:
        self.frame: Optional[tk.Frame] = None

    def get_frame(self) -> Optional[tk.Frame]:
        return self.frame


__all__ = ["ControlPanel", "InfoPanel"]
