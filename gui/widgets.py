"""
OHScrcpy 现代扁平化控件
========================

不依赖第三方库, 用 Tk 原生组件 + Canvas 模拟圆角 + 自绘 hover/pressed.

提供的控件:
- ModernButton    : 圆角 + hover/pressed 动效的按钮 (主/次/危险 3 种风格)
- Toolbar         : 紧凑 60-80px 工具条 (放 ModernButton 垂直堆叠)
- PillBadge       : 视频画面左上角半透明状态标签 (FPS / 帧数 / 尺寸)
- FloatingBar     : 视频画面底部半透明物理键条 (Back/Home/Recents), 鼠标移入淡入移出淡出
- PhoneFrame      : 投屏画布的"手机外壳"圆角边框 + 轻微投影
- ToplevelPanel   : 帮助/关于弹窗
"""

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, List, Tuple

from gui.theme import Theme, ICONS


# ─────────────────────────────────────────────
#  ModernButton : 圆角 + hover/pressed 动效
# ─────────────────────────────────────────────
class ModernButton(tk.Canvas):
    """现代扁平按钮, 自带 hover/pressed 动效.

    用法:
        btn = ModernButton(parent, text="主页", icon="⌂", command=cb,
                          style="primary")  # primary | secondary | danger | ghost
    """
    STYLE_COLORS = {
        "primary":   (Theme.ACCENT,       Theme.ACCENT_HOVER,      Theme.ACCENT_PRESSED, Theme.TEXT_ON_ACCENT),
        "secondary": (Theme.BG_ELEVATED,  Theme.BORDER,            Theme.ACCENT,        Theme.TEXT_PRIMARY),
        "danger":    (Theme.ACCENT_HOT,   Theme.ACCENT_HOT_HOVER,  "#dc2626",           Theme.TEXT_ON_ACCENT),
        "success":   (Theme.ACCENT_GOOD,  Theme.ACCENT_GOOD_HOVER, "#16a34a",           Theme.TEXT_ON_ACCENT),
        "ghost":     (Theme.BG_PANEL,     Theme.BG_ELEVATED,       Theme.BORDER,        Theme.TEXT_PRIMARY),
    }

    def __init__(
        self,
        parent: tk.Widget,
        text: str = "",
        icon: str = "",
        command: Optional[Callable] = None,
        style: str = "secondary",
        width: int = 96,
        height: int = Theme.BUTTON_HEIGHT,
        icon_only: bool = False,
        **kw,
    ) -> None:
        super().__init__(
            parent, width=width, height=height,
            bg=Theme.BG_PANEL,
            highlightthickness=0, bd=0, **kw,
        )
        self._command = command
        self._style = style
        self._width = width
        self._height = height
        self._text = text
        self._icon = icon
        self._icon_only = icon_only
        self._radius = Theme.RADIUS
        self._disabled = False
        self._current_colors = self._colors_for("normal")

        # 鼠标交互
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

        self._draw()

    def _colors_for(self, state: str) -> Tuple[str, str]:
        """根据状态返回 (背景, 边框). state: normal/hover/pressed"""
        c = self.STYLE_COLORS.get(self._style, self.STYLE_COLORS["secondary"])
        bg, bg_hover, bg_pressed, _fg = c
        if state == "hover":
            return bg_hover, Theme.BORDER
        if state == "pressed":
            return bg_pressed, Theme.BORDER
        return bg, Theme.BORDER_SOFT

    def _draw(self) -> None:
        """重绘按钮."""
        self.delete("all")
        bg, border = self._current_colors

        w, h = self._width, self._height
        r = self._radius

        # 圆角矩形 (用 4 个圆角 + 3 个矩形拼出; tkinter 没原生 rounded rect)
        self.create_polygon(
            r, 0,
            w - r, 0,
            w, r,
            w, h - r,
            w - r, h,
            r, h,
            0, h - r,
            0, r,
            fill=bg, outline=border, width=1, smooth=False,
        )
        # 文字 + 图标
        fg = Theme.TEXT_PRIMARY if self._style in ("secondary", "ghost") else self.STYLE_COLORS[self._style][3]
        if self._disabled:
            fg = Theme.TEXT_MUTED
        if self._icon and self._text and not self._icon_only:
            label = f"{self._icon}  {self._text}"
            font = Theme.FONT_BODY
        elif self._icon_only and self._icon:
            label = self._icon
            font = Theme.FONT_ICON
        elif self._text:
            label = self._text
            font = Theme.FONT_BODY
        else:
            label = ""
            font = Theme.FONT_BODY
        self.create_text(
            w // 2, h // 2, text=label, fill=fg,
            font=font, anchor="center",
        )

    def _on_enter(self, _e):
        if self._disabled:
            return
        self._current_colors = self._colors_for("hover")
        self._draw()

    def _on_leave(self, _e):
        if self._disabled:
            return
        self._current_colors = self._colors_for("normal")
        self._draw()

    def _on_press(self, _e):
        if self._disabled:
            return
        self._current_colors = self._colors_for("pressed")
        self._draw()

    def _on_release(self, e):
        if self._disabled:
            return
        # 若鼠标仍在按钮内, 视为 hover; 否则 normal
        x, y = e.x_root, e.y_root
        wx, wy = self.winfo_rootx(), self.winfo_rooty()
        in_bounds = (wx <= x < wx + self._width) and (wy <= y < wy + self._height)
        self._current_colors = self._colors_for("hover" if in_bounds else "normal")
        self._draw()
        if in_bounds and self._command:
            try:
                self._command()
            except Exception as ex:
                import traceback
                traceback.print_exc()

    # ── 公开 API ──
    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        if disabled:
            self._current_colors = (Theme.BG_PANEL, Theme.BORDER_SOFT)
        else:
            self._current_colors = self._colors_for("normal")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self._draw()


# ─────────────────────────────────────────────
#  Toolbar : 紧凑垂直工具条
# ─────────────────────────────────────────────
class Toolbar(tk.Frame):
    """紧凑工具条 (宽度 60-80px), 垂直堆叠 ModernButton.

    用法:
        tb = Toolbar(parent)
        tb.add_button("⌂", "主页", command=cb)
        tb.add_separator()
        tb.add_button("◉", "截屏", command=cb)
    """
    def __init__(self, parent: tk.Widget, width: int = Theme.TOOLBAR_WIDTH) -> None:
        super().__init__(parent, bg=Theme.BG_PANEL, width=width)
        self._width = width
        self.pack_propagate(False)
        self._buttons: List[ModernButton] = []

    def add_button(
        self,
        icon: str,
        tooltip: str,
        command: Optional[Callable] = None,
        style: str = "secondary",
    ) -> ModernButton:
        btn = ModernButton(
            self, icon=icon, command=command, style=style,
            width=self._width - 12, height=Theme.BUTTON_HEIGHT,
            icon_only=True,
        )
        btn.pack(pady=(8, 4), padx=6)
        # tooltip
        self._attach_tooltip(btn, tooltip)
        self._buttons.append(btn)
        return btn

    def add_separator(self) -> None:
        sep = tk.Frame(self, bg=Theme.BORDER_SOFT, height=1)
        sep.pack(fill=tk.X, padx=12, pady=8)
        self._buttons.append(sep)  # type: ignore

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        """鼠标悬停 0.6s 后弹出 tooltip."""
        tip = tk.Toplevel(widget)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        lbl = tk.Label(
            tip, text=f"  {text}  ",
            bg=Theme.BG_ELEVATED, fg=Theme.TEXT_PRIMARY,
            font=Theme.FONT_SMALL, padx=8, pady=3,
        )
        lbl.pack()
        state = {"shown": False, "after_id": None}

        def schedule():
            state["after_id"] = widget.after(600, show)

        def show():
            x = widget.winfo_rootx() + widget.winfo_width() + 6
            y = widget.winfo_rooty() + widget.winfo_height() // 2 - 12
            tip.geometry(f"+{x}+{y}")
            tip.deiconify()
            state["shown"] = True

        def cancel(_e=None):
            if state["after_id"]:
                widget.after_cancel(state["after_id"])
                state["after_id"] = None
            if state["shown"]:
                tip.withdraw()
                state["shown"] = False

        widget.bind("<Enter>", lambda e: (cancel(), schedule()), add="+")
        widget.bind("<Leave>", cancel, add="+")


# ─────────────────────────────────────────────
#  PillBadge : 视频画面左上角半透明状态标签
# ─────────────────────────────────────────────
class PillBadge:
    """半透明胶囊状态标签, 绘在 video canvas 之上 (用 canvas 的 create_*).

    用法:
        badge = PillBadge(video_canvas)
        badge.set_text("FPS 28 • 1080x1920")  # 更新内容
        badge.set_visible(False)             # 隐藏 (F8, 淡出)
        badge.fade_in()                       # 淡入显示 (300ms)

    动画:
        - 淡入: 6 帧 * 50ms 调整 fill 颜色亮度从 30% -> 100%
        - 淡出: 6 帧 * 50ms 调整 fill 颜色亮度从 100% -> 30%, 然后删除
        - Tk canvas 不支持 alpha, 通过调整 fill 颜色模拟 (深 -> 浅)
    """
    FADE_STEPS = 6
    FADE_MS = 50

    def __init__(self, canvas: tk.Canvas) -> None:
        self._canvas = canvas
        self._item_bg: Optional[int] = None
        self._item_text: Optional[int] = None
        self._visible = True
        self._text = ""
        self._fade_after_id: Optional[str] = None
        self._fade_step: int = 0
        self._fade_dir: int = 0  # 0=静止, 1=淡入, -1=淡出

    def _interp_color(self, c1: str, c2: str, t: float) -> str:
        """HSL 风格简化: 线性 RGB 插值."""
        from gui.branding import interpolate_color
        return interpolate_color(c1, c2, t)

    def _draw(self) -> None:
        self._canvas.delete("pill_badge")
        if not self._visible or not self._text:
            return
        # 半透明用稍微深一点 + 描边模拟 (tk 不支持 alpha)
        padding_x, padding_y = 10, 5
        font = Theme.FONT_PILL
        # 先创建文本测量宽度
        tmp = self._canvas.create_text(
            0, 0, text=self._text, font=font, anchor="nw", fill=Theme.TEXT_PRIMARY,
        )
        bbox = self._canvas.bbox(tmp)
        self._canvas.delete(tmp)
        if not bbox:
            return
        x0, y0, x1, y1 = bbox
        w = x1 - x0 + padding_x * 2
        h = y1 - y0 + padding_y * 2
        # 圆角矩形 (4 段)
        r = min(8, h // 2)
        x, y = 10, 10
        self._item_bg = self._canvas.create_polygon(
            x + r, y,
            x + w - r, y,
            x + w, y + r,
            x + w, y + h - r,
            x + w - r, y + h,
            x + r, y + h,
            x, y + h - r,
            x, y + r,
            fill=Theme.PILL_BG, outline=Theme.PILL_OUTLINE, width=1,
            tags=("pill_badge",),
        )
        self._item_text = self._canvas.create_text(
            x + w // 2, y + h // 2,
            text=self._text, fill=Theme.TEXT_PRIMARY,
            font=font, anchor="center",
            tags=("pill_badge",),
        )
        # 永远在最上层
        self._canvas.tag_raise(self._item_bg)
        if self._item_text:
            self._canvas.tag_raise(self._item_text)
        # 如果有未完成的淡入/淡出动画, 重启 (新的 text 重置动画)
        if self._fade_dir != 0:
            self._cancel_fade()
            self._start_fade(self._fade_dir)

    def _cancel_fade(self) -> None:
        if self._fade_after_id:
            try:
                self._canvas.after_cancel(self._fade_after_id)
            except Exception:
                pass
            self._fade_after_id = None

    def _start_fade(self, direction: int) -> None:
        """direction: 1=淡入, -1=淡出."""
        self._cancel_fade()
        self._fade_dir = direction
        self._fade_step = 0
        self._tick_fade()

    def _tick_fade(self) -> None:
        self._fade_step += 1
        t = self._fade_step / self.FADE_STEPS
        if self._fade_dir == 1:
            # 淡入: 深 -> 标准 PILL_BG
            color = self._interp_color("#020617", Theme.PILL_BG, t)
        else:
            # 淡出: 标准 -> 深
            color = self._interp_color(Theme.PILL_BG, "#020617", t)

        if self._item_bg is not None:
            try:
                self._canvas.itemconfig(self._item_bg, fill=color)
            except Exception:
                pass
        # 文字: 跟着变浅 (白 -> 灰)
        if self._item_text is not None:
            try:
                if self._fade_dir == 1:
                    text_color = self._interp_color("#475569", Theme.TEXT_PRIMARY, t)
                else:
                    text_color = self._interp_color(Theme.TEXT_PRIMARY, "#475569", t)
                self._canvas.itemconfig(self._item_text, fill=text_color)
            except Exception:
                pass

        if self._fade_step < self.FADE_STEPS:
            self._fade_after_id = self._canvas.after(self.FADE_MS, self._tick_fade)
        else:
            self._fade_after_id = None
            # 淡出完成: 删除
            if self._fade_dir == -1:
                self._canvas.delete("pill_badge")
                self._item_bg = None
                self._item_text = None
            self._fade_dir = 0

    def set_text(self, text: str) -> None:
        self._text = text
        # 如果当前可见, 重画并触发"重新淡入"(视觉反馈)
        if self._visible:
            self._draw()

    def set_visible(self, visible: bool) -> None:
        """直接显示/隐藏 (不带动画)."""
        self._cancel_fade()
        self._visible = visible
        self._draw()
        if not visible:
            self._item_bg = None
            self._item_text = None

    def fade_in(self) -> None:
        """淡入显示 (300ms, 替换硬显示, 用于 show after delay)."""
        self._visible = True
        self._draw()
        self._start_fade(1)

    def fade_out(self) -> None:
        """淡出隐藏 (300ms, 替换硬隐藏, 用于 hide)."""
        if self._item_bg is None and self._item_text is None:
            return
        self._start_fade(-1)


# ─────────────────────────────────────────────
#  FloatingBar : 底部半透明物理键条
# ─────────────────────────────────────────────
class FloatingBar(tk.Frame):
    """投屏画面正底部半透明物理键条 (Back / Home / Recents).

    - 默认半透明, 鼠标进入 canvas 区域时淡入 (alpha 模拟: 调 bg 明度)
    - 鼠标离开 1.5s 后淡出
    """
    def __init__(self, parent: tk.Widget, on_back: Callable, on_home: Callable, on_recents: Callable) -> None:
        super().__init__(parent, bg=Theme.PILL_BG, height=Theme.FLOAT_BAR_HEIGHT)
        self._on_back = on_back
        self._on_home = on_home
        self._on_recents = on_recents
        self._alpha_state = "fading_out"  # fading_in / visible / fading_out

        # 3 个按钮 (居中等分)
        for i, (icon, desc, cmd) in enumerate([
            (ICONS["back"],    "返回",    on_back),
            (ICONS["home"],    "主页",    on_home),
            (ICONS["recents"], "多任务",  on_recents),
        ]):
            btn = ModernButton(
                self, icon=icon, command=cmd, style="ghost",
                width=64, height=Theme.FLOAT_BAR_HEIGHT - 16,
                icon_only=True,
            )
            btn.grid(row=0, column=i, padx=24, pady=8, sticky="")
            self._attach_tooltip(btn, desc)
        for i in range(3):
            self.grid_columnconfigure(i, weight=1)

        # 半透明: ttk 不透明, 用 place_forget 实现"隐藏"
        self._fade_timer: Optional[str] = None
        self._hidden = True
        self._fade_out()

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        tip = tk.Toplevel(widget); tip.withdraw(); tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=f"  {text}  ", bg=Theme.BG_ELEVATED,
                 fg=Theme.TEXT_PRIMARY, font=Theme.FONT_SMALL,
                 padx=8, pady=3).pack()

        state = {"shown": False, "after_id": None}

        def schedule():
            state["after_id"] = widget.after(500, show)

        def show():
            x = widget.winfo_rootx() + widget.winfo_width() // 2 - 20
            y = widget.winfo_rooty() - 28
            tip.geometry(f"+{x}+{y}"); tip.deiconify(); state["shown"] = True

        def cancel(_e=None):
            if state["after_id"]:
                widget.after_cancel(state["after_id"]); state["after_id"] = None
            if state["shown"]:
                tip.withdraw(); state["shown"] = False

        widget.bind("<Enter>", lambda e: (cancel(), schedule()), add="+")
        widget.bind("<Leave>", cancel, add="+")

    def _fade_out(self) -> None:
        self._hidden = True
        self.place_forget()

    def fade_in(self) -> None:
        """被外部调用, 显示 bar."""
        if self._fade_timer:
            self.after_cancel(self._fade_timer)
            self._fade_timer = None
        self._hidden = False
        # 父组件需先 place 自身再 place bar
        # 实际位置由调用方 (MainWindow) 决定
        self._set_alpha(0.45)
        self.lift()

    def schedule_fade_out(self, delay_ms: int = 1500) -> None:
        """延迟淡出."""
        if self._fade_timer:
            self.after_cancel(self._fade_timer)
        self._fade_timer = self.after(delay_ms, self._fade_out)

    def _set_alpha(self, alpha: float) -> None:
        """模拟半透明 (通过调暗背景色)."""
        # alpha 越大越亮 -> 越接近 #334155
        # alpha=0.45 视觉接近 62% 不透明
        if alpha > 0.7:
            c = Theme.BG_ELEVATED
        else:
            c = Theme.PILL_BG
        try:
            self.configure(bg=c)
        except Exception:
            pass


# ─────────────────────────────────────────────
#  PhoneFrame : 投屏画布的"手机外壳"圆角边框
# ─────────────────────────────────────────────
class PhoneFrame:
    """给 video canvas 叠加手机外壳效果:
    - 圆角矩形高亮边框
    - 顶部细线模拟听筒
    - 轻微阴影 (用 outer outline 模拟)

    用法: 在 video canvas 创建后, PhoneFrame(canvas).draw()
    """
    def __init__(self, canvas: tk.Canvas) -> None:
        self._canvas = canvas
        self._items: List[int] = []

    def draw(self) -> None:
        """根据 canvas 当前尺寸重画外壳."""
        for it in self._items:
            self._canvas.delete(it)
        self._items.clear()
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w <= 10 or h <= 10:
            return
        # 圆角外框
        r = 14
        x0, y0, x1, y1 = 2, 2, w - 2, h - 2
        outer = self._canvas.create_polygon(
            x0 + r, y0,
            x1 - r, y0,
            x1, y0 + r,
            x1, y1 - r,
            x1 - r, y1,
            x0 + r, y1,
            x0, y1 - r,
            x0, y0 + r,
            fill="", outline=Theme.BORDER, width=1,
        )
        # 顶部听筒线条
        notch_w = 80
        notch_h = 4
        notch = self._canvas.create_rectangle(
            w // 2 - notch_w // 2, 6,
            w // 2 + notch_w // 2, 6 + notch_h,
            fill=Theme.BG_ELEVATED, outline="",
        )
        # 投影效果 (右下偏移 2px 的同形 outline)
        shadow = self._canvas.create_polygon(
            x0 + r + 2, y0 + 2,
            x1 - r + 2, y0 + 2,
            x1 + 2, y0 + r + 2,
            x1 + 2, y1 - r + 2,
            x1 - r + 2, y1 + 2,
            x0 + r + 2, y1 + 2,
            x0 + 2, y1 - r + 2,
            x0 + 2, y0 + r + 2,
            fill="", outline=Theme.BG_BASE, width=1,
        )
        self._items.extend([outer, notch, shadow])
        # 始终在最底, 不挡住视频与 OSD
        for it in self._items:
            self._canvas.tag_lower(it)


__all__ = ["ModernButton", "Toolbar", "PillBadge", "FloatingBar", "PhoneFrame"]
