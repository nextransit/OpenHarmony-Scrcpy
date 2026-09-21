"""
OHScrcpy 品牌系统
=================

集中管理: Logo (Canvas自绘矢量) + 品牌色 + 渐变 + 启动画面 + 状态指示器

设计原则:
- 零外部依赖 (纯 Tk Canvas 绘制, 不引 Pillow SVG / cairosvg)
- 矢量绘制: 任意 DPI 缩放都清晰
- 暗色主题: 与 Slate/Zinc 配色体系融合
- 科技感: 蓝色青蓝渐变 + 流光动画

模块:
  - BrandColor   品牌色常量 (primary/accent/glow/error/success/warning)
  - BrandGradient Canvas 渐变生成 (HSL 插值)
  - Logo         Canvas 自绘 Logo (手机 + 投屏波纹)
  - StatusDot    Canvas 自绘状态指示器 (脉冲/旋转动画)
  - SplashScreen 启动画面 (Logo + 加载圆环)
  - GradientBar  渐变条 (标题栏装饰)
"""
import math
import tkinter as tk
from typing import Optional, Tuple, List


# ── 品牌色 (与 theme.py 协调, 但更鲜明) ──
class BrandColor:
    # 主色: 蓝青渐变 (科技感)
    PRIMARY = "#0ea5e9"        # sky-500
    PRIMARY_LIGHT = "#38bdf8"  # sky-400
    PRIMARY_DARK = "#0369a1"   # sky-700
    # 强调色: 青色 (活力)
    ACCENT = "#06b6d4"         # cyan-500
    ACCENT_LIGHT = "#22d3ee"   # cyan-400
    # 发光色: 紫色 (神秘)
    GLOW = "#a78bfa"           # violet-400
    GLOW_DARK = "#7c3aed"      # violet-600
    # 状态色
    SUCCESS = "#22c55e"        # green-500
    WARNING = "#f59e0b"        # amber-500
    ERROR = "#ef4444"          # red-500
    INFO = "#3b82f6"           # blue-500
    # 中性
    BG_DEEP = "#020617"        # slate-950 (启动画面背景)
    BG_PANEL = "#0f172a"       # slate-900
    TEXT_PRIMARY = "#f1f5f9"   # slate-100
    TEXT_MUTED = "#94a3b8"     # slate-400


# ── 颜色插值 (HSL 简单实现, 适合渐变条纹) ──
def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb).lower()


def interpolate_color(c1: str, c2: str, t: float) -> str:
    """在两个十六进制色之间按 t (0..1) 线性插值."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return _rgb_to_hex((r, g, b))


def make_horizontal_gradient(
    canvas: tk.Canvas,
    x1: int, y1: int, x2: int, y2: int,
    color_start: str, color_end: str,
    steps: int = 64,
    tag: Optional[str] = None,
) -> List[int]:
    """在画布上绘制水平渐变矩形 (用 steps 个细条模拟).

    Returns:
        创建的所有 canvas item id 列表.
    """
    items = []
    width = x2 - x1
    if width <= 0 or steps < 1:
        return items
    step_w = max(1, width // steps)
    for i in range(steps):
        t = i / max(1, steps - 1)
        c = interpolate_color(color_start, color_end, t)
        x_end = x1 + step_w if i < steps - 1 else x2
        rid = canvas.create_rectangle(
            x1 + i * step_w, y1, x_end, y2,
            fill=c, outline="", width=0, tags=tag,
        )
        items.append(rid)
    return items


def make_radial_glow(
    canvas: tk.Canvas,
    cx: int, cy: int, radius: int,
    color_core: str, color_edge: str,
    steps: int = 12,
    tag: Optional[str] = None,
) -> List[int]:
    """在画布上绘制径向发光 (同心圆从内向外插值).

    Returns:
        canvas item id 列表 (从内到外).
    """
    items = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        c = interpolate_color(color_core, color_edge, t)
        r_outer = radius * (i + 1) // steps
        if r_outer <= 0:
            continue
        rid = canvas.create_oval(
            cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
            fill=c, outline="", tags=tag,
        )
        items.append(rid)
    return items


# ── Logo: 矢量绘制 (手机 + 投屏波纹) ──
class Logo:
    """自绘 OHScrcpy Logo: 圆角矩形"手机"+ 屏幕内"投屏波纹"."""

    SIZE = 64  # 默认绘制边长

    @classmethod
    def draw(
        cls,
        canvas: tk.Canvas,
        x: int, y: int,
        size: int = SIZE,
        phone_color: str = BrandColor.PRIMARY,
        screen_color: str = BrandColor.BG_DEEP,
        ripple_color: str = BrandColor.ACCENT,
        tag: Optional[str] = None,
    ) -> List[int]:
        """在 (x, y) 绘制 size x size 的图标. 返回所有 item id."""
        items = []
        s = size
        # 1) 阴影 (右下偏移, 半透明近似深色)
        sh = 2
        items.append(canvas.create_rectangle(
            x + sh, y + sh, x + s + sh, y + s + sh,
            fill="#000000", outline="", tags=tag,
        ))
        # 2) 手机外框 (圆角矩形用 polygon 模拟)
        r = max(4, s // 8)  # 圆角
        x0, y0, x1, y1 = x, y, x + s, y + s
        inset = max(2, s // 12)
        phone = [
            x0 + r, y0,
            x1 - r, y0,
            x1, y0 + r,
            x1, y1 - r,
            x1 - r, y1,
            x0 + r, y1,
            x0, y1 - r,
            x0, y0 + r,
        ]
        items.append(canvas.create_polygon(
            phone, fill=phone_color, outline="", smooth=True, tags=tag,
        ))
        # 3) 屏幕 (内嵌矩形, 留出边框)
        screen = [
            x0 + inset + r, y0 + inset,
            x1 - inset - r, y0 + inset,
            x1 - inset, y0 + inset + r,
            x1 - inset, y1 - inset - r,
            x1 - inset - r, y1 - inset,
            x0 + inset + r, y1 - inset,
            x0 + inset, y1 - inset - r,
            x0 + inset, y0 + inset + r,
        ]
        items.append(canvas.create_polygon(
            screen, fill=screen_color, outline="", smooth=True, tags=tag,
        ))
        # 4) 投屏波纹 (3 条横线从左到右递进, 模拟"信号")
        cx_screen = (x0 + x1) // 2
        cy_screen = (y0 + y1) // 2
        n_ripples = 3
        for i in range(n_ripples):
            t = (i + 1) / (n_ripples + 1)
            line_w = int((s - 2 * inset) * 0.5 * t)
            line_x0 = cx_screen - line_w
            line_x1 = cx_screen + line_w
            line_y = cy_screen - (n_ripples // 2 - i) * max(2, s // 16)
            items.append(canvas.create_line(
                line_x0, line_y, line_x1, line_y,
                fill=ripple_color, width=max(1, s // 24),
                tags=tag,
            ))
        return items


# ── 状态指示器: 圆点 + 脉冲/旋转动画 ──
class StatusDot:
    """Canvas 自绘状态点.

    状态:
      - "idle"      : 灰色, 无动画
      - "connecting": 蓝色, 旋转弧线
      - "connected" : 绿色, 脉冲放大
      - "error"     : 红色, 闪烁
    """

    STATES = ("idle", "connecting", "connected", "error")

    def __init__(
        self,
        canvas: tk.Canvas,
        x: int, y: int,
        size: int = 14,
        initial: str = "idle",
    ):
        self.canvas = canvas
        self.cx = x
        self.cy = y
        self.size = size
        self.state = initial
        self._items: List[int] = []
        self._anim_after_id: Optional[str] = None
        self._anim_step = 0
        self._draw_static()

    def _state_color(self) -> str:
        return {
            "idle": BrandColor.TEXT_MUTED,
            "connecting": BrandColor.PRIMARY,
            "connected": BrandColor.SUCCESS,
            "error": BrandColor.ERROR,
        }.get(self.state, BrandColor.TEXT_MUTED)

    def _draw_static(self):
        for it in self._items:
            self.canvas.delete(it)
        self._items.clear()
        c = self._state_color()
        # 外环 (半透明效果用稍深颜色)
        r = self.size // 2
        self._items.append(self.canvas.create_oval(
            self.cx - r - 2, self.cy - r - 2,
            self.cx + r + 2, self.cy + r + 2,
            fill=BrandColor.BG_PANEL, outline="",
        ))
        # 圆点本体
        self._items.append(self.canvas.create_oval(
            self.cx - r, self.cy - r,
            self.cx + r, self.cy + r,
            fill=c, outline="",
        ))
        # 内点高光 (增加质感)
        hr = max(1, r // 2)
        self._items.append(self.canvas.create_oval(
            self.cx - hr, self.cy - hr,
            self.cx + hr, self.cy + hr,
            fill="#ffffff", outline="",
        ))

    def set_state(self, state: str) -> None:
        """切换状态. idle/connecting/connected/error."""
        if state not in self.STATES:
            return
        self.state = state
        self._anim_step = 0
        self._stop_anim()
        self._draw_static()
        if state in ("connecting", "connected", "error"):
            self._start_anim()

    def _start_anim(self) -> None:
        self._anim_step = 0
        self._tick()

    def _stop_anim(self) -> None:
        if self._anim_after_id:
            try:
                self.canvas.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None

    def _tick(self) -> None:
        self._anim_step += 1
        # 重画动态元素 (脉冲/旋转/闪烁)
        # 删掉之前的额外动画元素 (保留前 3 个 _items 是静态)
        for it in self._items[3:]:
            self.canvas.delete(it)
        self._items = self._items[:3]

        if self.state == "connecting":
            # 旋转弧线 (8 段, 间隔 45°, 透明度递减)
            r = self.size // 2 + 6
            n_segments = 8
            for i in range(n_segments):
                start_angle = (360 / n_segments) * i + (self._anim_step * 12) % 360
                # 简化: 画 8 条短线 (径向)
                a = math.radians(start_angle)
                a2 = math.radians(start_angle + 12)
                r1 = r - 2
                r2 = r + 2
                # 透明度靠颜色深浅模拟
                t = (i + self._anim_step) % n_segments / n_segments
                color = interpolate_color(BrandColor.PRIMARY_LIGHT, BrandColor.PRIMARY_DARK, t)
                self._items.append(self.canvas.create_arc(
                    self.cx - r, self.cy - r, self.cx + r, self.cy + r,
                    start=start_angle, extent=12,
                    style="arc", outline=color, width=2,
                ))
        elif self.state == "connected":
            # 脉冲: 外面画 1-2 个扩散环
            r_base = self.size // 2
            for k in range(2):
                phase = (self._anim_step + k * 15) % 30
                t = phase / 30.0
                r = r_base + 2 + int(8 * t)
                if r > r_base + 12:
                    continue
                c = interpolate_color(BrandColor.SUCCESS, BrandColor.BG_PANEL, t)
                self._items.append(self.canvas.create_oval(
                    self.cx - r, self.cy - r, self.cx + r, self.cy + r,
                    fill="", outline=c, width=1,
                ))
        elif self.state == "error":
            # 闪烁: 透明度由 sin 控制
            if self._anim_step % 12 < 6:
                self._items.append(self.canvas.create_oval(
                    self.cx - self.size, self.cy - self.size,
                    self.cx + self.size, self.cy + self.size,
                    fill=BrandColor.ERROR, outline="",
                ))

        self._anim_after_id = self.canvas.after(50, self._tick)

    def destroy(self) -> None:
        self._stop_anim()
        for it in self._items:
            try:
                self.canvas.delete(it)
            except Exception:
                pass
        self._items.clear()


# ── 启动画面 (Splash) ──
class SplashScreen:
    """启动画面: 渐显品牌 Logo + 加载圆环, 完成后自动关闭."""

    DURATION_MS = 1500  # 总时长 (足够看清 Logo 又不拖沓)
    FADE_IN_MS = 300

    def __init__(self, root: tk.Tk, on_finish: Optional[callable] = None):
        self.root = root
        self.on_finish = on_finish
        # 用 Toplevel 而不是直接画到 root (避免遮挡主窗口未就绪时)
        self.top = tk.Toplevel(root)
        self.top.title("OHScrcpy")
        self.top.configure(bg=BrandColor.BG_DEEP)
        # 移除窗口装饰 (无边框), 居中
        self.top.overrideredirect(True)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w, h = 420, 280
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        # 圆角外框 (Tk 不支持, 用深色矩形代替; 简洁科技感)
        self.canvas = tk.Canvas(
            self.top, width=w, height=h,
            bg=BrandColor.BG_DEEP, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        # 关键: pack 之后立即调 winfo_width() 还是 1 (canvas 未布局).
        # 强制 update_idletasks 让 canvas 拿到真实尺寸 (420x280).
        self.top.update_idletasks()
        # 绘制
        self._items: List[int] = []
        self._draw()
        self._step = 0
        self._tick()

    def _draw(self) -> None:
        w = self.canvas.winfo_width() or 420
        h = self.canvas.winfo_height() or 280
        cx, cy = w // 2, h // 2
        # 1) 渐变背景 (顶部青色 -> 底部深蓝)
        items_bg = make_horizontal_gradient(
            self.canvas, 0, 0, w, h,
            BrandColor.BG_PANEL, BrandColor.BG_DEEP,
            steps=64, tag="splash_bg",
        )
        self._items.extend(items_bg)
        # 2) Logo + 标题 + 副标题 整体垂直居中 (按视觉块高度算偏移)
        #    视觉块: logo 96 + 18 gap + title (~22pt 高) + 6 gap + sub (~11pt 高) ~= 155
        #    splash h=280 -> 顶部 offset = (280 - 155 - 28(ring区)) / 2 ~= 48
        block_top = (h - 155 - 32) // 2  # ring 区预留 32
        logo_size = 96
        logo_x = cx - logo_size // 2
        logo_y = block_top
        items_logo = Logo.draw(
            self.canvas, logo_x, logo_y, size=logo_size,
            phone_color=BrandColor.PRIMARY,
            screen_color=BrandColor.BG_DEEP,
            ripple_color=BrandColor.ACCENT_LIGHT,
            tag="splash_logo",
        )
        self._items.extend(items_logo)
        # 3) 文字 "OHScrcpy" (粗体, 在 logo 下方 18px)
        title_y = logo_y + logo_size + 18 + 11   # +11: title baseline 居中到文字高
        items_title = [self.canvas.create_text(
            cx, title_y, text="OHScrcpy",
            fill=BrandColor.TEXT_PRIMARY,
            font=("Microsoft YaHei", 22, "bold"),
            tag="splash_title",
        )]
        self._items.extend(items_title)
        # 4) 副标题 (在 title 下方 6px)
        sub_y = title_y + 17
        items_sub = [self.canvas.create_text(
            cx, sub_y, text="OpenHarmony 投屏工具",
            fill=BrandColor.TEXT_MUTED,
            font=("Microsoft YaHei", 11),
            tag="splash_sub",
        )]
        self._items.extend(items_sub)
        # 5) 加载圆环 (底部居中)
        self._ring_items: List[int] = []
        ring_r = 14
        ring_y = h - 26
        # 8 段灰色圆环 + 1 段高亮
        n_seg = 12
        for i in range(n_seg):
            color = BrandColor.BG_ELEVATED if hasattr(BrandColor, "BG_ELEVATED") else "#334155"
            self._ring_items.append(self.canvas.create_arc(
                cx - ring_r, ring_y - ring_r, cx + ring_r, ring_y + ring_r,
                start=(360 / n_seg) * i,
                extent=(360 / n_seg) - 4,
                style="arc", outline=color, width=2,
                tag="splash_ring",
            ))

    def _tick(self) -> None:
        self._step += 1
        # 旋转高亮段 (每隔 12 帧重画)
        if self._ring_items and self._step % 2 == 0:
            # 删除并重画
            for it in self._ring_items:
                self.canvas.delete(it)
            self._ring_items.clear()
            w = self.canvas.winfo_width() or 420
            h = self.canvas.winfo_height() or 280
            cx, cy = w // 2, h // 2
            ring_r = 14
            ring_y = h - 26
            n_seg = 12
            offset = (self._step // 2) % n_seg
            for i in range(n_seg):
                color = BrandColor.ACCENT_LIGHT if i == offset else "#334155"
                self._ring_items.append(self.canvas.create_arc(
                    cx - ring_r, ring_y - ring_r, cx + ring_r, ring_y + ring_r,
                    start=(360 / n_seg) * i,
                    extent=(360 / n_seg) - 4,
                    style="arc", outline=color, width=2,
                    tag="splash_ring",
                ))

        if self._step * 50 < self.DURATION_MS:
            self.top.after(50, self._tick)
        else:
            self._finish()

    def _finish(self) -> None:
        # 关闭顺序: 先调 on_finish (创建主窗口 widget), 再 destroy splash.
        # 避免在主窗口 widget 还没创建好之前 destroy splash -> 用户看到一闪黑底.
        if self.on_finish:
            try:
                self.on_finish()
            except Exception:
                pass
        try:
            self.top.destroy()
        except Exception:
            pass


__all__ = [
    "BrandColor", "interpolate_color",
    "make_horizontal_gradient", "make_radial_glow",
    "Logo", "StatusDot", "SplashScreen",
]
