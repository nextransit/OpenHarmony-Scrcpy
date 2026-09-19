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
from gui.widgets import Toolbar, ModernButton


class ControlPanel:
    """紧凑工具条式控制面板."""

    def __init__(self, parent: tk.Widget, callbacks: Dict[str, Callable[[], None]]) -> None:
        self.parent: tk.Widget = parent
        self.callbacks: Dict[str, Callable[[], None]] = callbacks
        self.frame: Optional[tk.Frame] = None
        self.toolbar: Optional[Toolbar] = None
        self._buttons: Dict[str, ModernButton] = {}

        self._create_panel()

    def _create_panel(self) -> None:
        # 外层 frame (用于与主区域留 padding)
        outer = tk.Frame(self.parent, bg=Theme.BG_BASE)
        outer.pack(fill=tk.BOTH, expand=True, padx=(4, 8), pady=(0, 0))
        self.frame = outer

        toolbar = Toolbar(outer, width=Theme.TOOLBAR_WIDTH)
        toolbar.pack(fill=tk.BOTH, expand=True)
        self.toolbar = toolbar

        # ── 分组 1: 连接/刷新 ──
        b_refresh = toolbar.add_button(
            ICONS["refresh"], "刷新设备 (F5)",
            command=self.callbacks.get('refresh'),
        )
        self._buttons['refresh'] = b_refresh

        b_connect = toolbar.add_button(
            ICONS["connect"], "连接/断开 (F5)",
            command=self.callbacks.get('connect'),
            style="success",
        )
        self._buttons['connect'] = b_connect

        toolbar.add_separator()

        # ── 分组 2: 设备控制 ──
        self._buttons['power'] = toolbar.add_button(
            ICONS["power"], "电源 (F5)", command=self.callbacks.get('power'),
            style="danger",
        )
        self._buttons['volume_up'] = toolbar.add_button(
            ICONS["volume_up"], "音量+", command=self.callbacks.get('volume_up'),
        )
        self._buttons['volume_down'] = toolbar.add_button(
            ICONS["volume_down"], "音量-", command=self.callbacks.get('volume_down'),
        )
        self._buttons['unlock'] = toolbar.add_button(
            ICONS["unlock"], "解锁", command=self.callbacks.get('unlock'),
        )

        toolbar.add_separator()

        # ── 分组 3: 工具 ──
        self._buttons['screenshot'] = toolbar.add_button(
            ICONS["screenshot"], "截屏",
            command=self.callbacks.get('screenshot'),
        )
        self._buttons['record'] = toolbar.add_button(
            ICONS["record"], "录屏",
            command=self.callbacks.get('record'),
        )
        self._buttons['rotate'] = toolbar.add_button(
            ICONS["rotate"], "旋转屏幕",
            command=self.callbacks.get('rotate'),
        )
        self._buttons['screen_off'] = toolbar.add_button(
            ICONS["screen_off"], "熄屏运行 (省电)",
            command=self.callbacks.get('screen_off'),
        )

        toolbar.add_separator()

        # ── 分组 4: 键盘映射开关 + 帮助 ──
        self._buttons['keyboard'] = toolbar.add_button(
            ICONS["keyboard"], "键盘映射 (F12)",
            command=self.callbacks.get('toggle_keyboard'),
        )

    def get_frame(self) -> tk.Frame:
        return self.frame

    def get_button(self, name: str) -> Optional[ModernButton]:
        return self._buttons.get(name)

    def update_connect_button(self, connected: bool) -> None:
        """更新连接按钮的图标/风格."""
        btn = self._buttons.get('connect')
        if not btn:
            return
        if connected:
            btn.set_icon(ICONS["disconnect"])
            btn.configure(bg=Theme.BG_PANEL)  # 这里实际上 ModernButton 不直接读 configure
            # ModernButton 的颜色在 _current_colors 里, 通过替换 _style 来切换
            btn._style = "danger"  # 切到 danger 色, 红色表示"断开"
            btn._draw()
        else:
            btn.set_icon(ICONS["connect"])
            btn._style = "success"
            btn._draw()


# 保留 InfoPanel 占位以兼容旧 import (但实际不再使用)
class InfoPanel:
    """已弃用, 保留以兼容旧 import."""
    def __init__(self, parent: tk.Widget) -> None:
        self.frame: Optional[tk.Frame] = None

    def get_frame(self) -> Optional[tk.Frame]:
        return self.frame


__all__ = ["ControlPanel", "InfoPanel"]
