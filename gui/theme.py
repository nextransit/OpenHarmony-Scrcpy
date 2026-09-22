"""
OHScrcpy 统一主题
=================

设计目标:
- 现代扁平化, 中性 Slate/Zinc 配色
- 圆角 4~8px, 去掉复古凸起边框
- 统一 hover/pressed 动效
- 不引入第三方依赖 (CustomTkinter / PyQtDarkTheme 都不需要)

色彩体系 (参考 Tailwind Slate + Zinc):
- BG_BASE     : 整体背景, 接近 #0f172a (slate-900)
- BG_PANEL    : 面板背景, #1e293b (slate-800)
- BG_ELEVATED : 悬浮元素, #334155 (slate-700)
- BORDER      : 分割线/边框, #475569 (slate-600)
- TEXT_PRIMARY: 主文字, #f1f5f9 (slate-100)
- TEXT_MUTED  : 次文字, #94a3b8 (slate-400)
- ACCENT      : 强调色 (按钮/链接), #38bdf8 (sky-400)
- ACCENT_HOT  : 危险/电源, #f87171 (red-400)
- ACCENT_GOOD : 成功/连接, #4ade80 (green-400)
"""

from typing import Dict, Tuple


class Theme:
    # ── 色彩 ──
    BG_BASE = "#0f172a"
    BG_PANEL = "#1e293b"
    BG_ELEVATED = "#334155"
    BG_CANVAS = "#0a0f1c"        # 视频显示区背景, 比 BG_BASE 更深
    BORDER = "#475569"
    BORDER_SOFT = "#334155"

    TEXT_PRIMARY = "#f1f5f9"
    TEXT_MUTED = "#94a3b8"
    TEXT_ON_ACCENT = "#0f172a"

    ACCENT = "#38bdf8"
    ACCENT_HOVER = "#7dd3fc"
    ACCENT_PRESSED = "#0ea5e9"
    ACCENT_HOT = "#f87171"
    ACCENT_HOT_HOVER = "#fca5a5"
    ACCENT_GOOD = "#4ade80"
    ACCENT_GOOD_HOVER = "#86efac"

    # 视频浮层 (Pill Badge / 底部悬浮键) 用半透明
    PILL_BG = "#0f172a"
    PILL_BG_ALPHA = 0.62      # canvas.itemconfig 不支持 alpha, 用近似深色 + outline
    PILL_OUTLINE = "#475569"

    # ── 字体 ──
    FONT_FAMILY = "Microsoft YaHei"
    FONT_TITLE = (FONT_FAMILY, 12, "bold")
    FONT_SUBTITLE = (FONT_FAMILY, 10, "bold")
    FONT_BODY = (FONT_FAMILY, 10)
    FONT_SMALL = (FONT_FAMILY, 9)
    FONT_ICON = (FONT_FAMILY, 14, "bold")     # 紧凑工具条上的文字图标
    FONT_PILL = (FONT_FAMILY, 9)

    # ── 尺寸 ──
    RADIUS = 6               # 圆角半径
    PADDING = 8              # 通用内边距
    BUTTON_HEIGHT = 36       # 标准按钮高度
    TOOLBAR_WIDTH = 68       # 紧凑工具条宽度
    TITLE_BAR_HEIGHT = 36    # 标题栏高度
    FLOAT_BAR_HEIGHT = 52    # 底部悬浮物理键条高度

    # ── 动画 ──
    HOVER_FADE_MS = 80       # hover 渐变时长
    PRESS_FADE_MS = 60


# 常用 unicode 图标 (轻量, 不依赖图标字体)
ICONS: Dict[str, str] = {
    "power": "⏻",
    "home": "⌂",
    "back": "←",
    "recents": "▢",
    "menu": "≡",
    "help": "?",
    "info": "ℹ",
    "refresh": "↻",
    "connect": "⏵",
    "disconnect": "⏹",
    "connecting": "⏳",
    "screenshot": "◉",
    "record": "●",
    "rotate": "↻",          # 与 refresh 重用
    "screen_off": "◐",
    "volume_up": "+",
    "volume_down": "−",
    "unlock": "⤓",
    "keyboard": "⌨",
    "settings": "⚙",
    "close": "✕",
    "expand": "⤢",
    "collapse": "⤡",
}


__all__ = ["Theme", "ICONS"]
