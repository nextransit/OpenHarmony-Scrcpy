"""
ttkbootstrap 主题集成层
======================

职责:
  1. 一次性设置全局主题 ('darkly')
  2. 暴露 Style 实例, 供其他模块配置
  3. 提供统一的字号/间距常量, 避免硬编码
  4. 把 Bootstrap semantic colors 映射到现有 Theme.* (避免大改其他模块)

设计原则:
  - 不替换 ModernButton / PillBadge / PhoneFrame (都是 Canvas 自绘, 改造成本高)
  - 仅替换 tk.Label / tk.Frame / tk.Button 这类"包装"型 widget
  - Canvas 区域 (视频显示 + 极简顶栏) 保留 tk, 因为 ttk 不支持 Canvas
  - overrideredirect 顶栏继续用 tk.Label (ttk.Label 跨平台样式不一致)

为什么用 'darkly':
  - 与现有 Theme.BG_BASE (#0f172a) 风格接近 (darkly=#222)
  - 比 superhero 更稳重, 比 cyborg 更柔和
  - 完全暗色, 适合长时间投屏

依赖: pip install ttkbootstrap>=2.0.0
"""

from typing import Optional
import tkinter as tk

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import (
        PRIMARY, SECONDARY, SUCCESS, INFO, WARNING, DANGER, LIGHT, DARK,
        OUTLINE,
    )
    BOOTSTRAP_OUTLINE = OUTLINE  # 兼容别名
    _HAS_TTKBOOTSTRAP = True
except ImportError:
    # 容错: 没装就降级到标准 ttk
    _HAS_TTKBOOTSTRAP = False
    from tkinter import ttk


# 全局主题常量 — 所有 widget 都应该用这些
# 优先级: 环境变量 OHCRCPY_THEME > 默认 darkly
import os as _os
_THEME_FROM_ENV = _os.environ.get("OHCRCPY_THEME", "").strip()
THEME_NAME = _THEME_FROM_ENV or "darkly"   # 也可改 "superhero" / "cyborg" / "solar"
ACCENT_BOOTSTRAP = "info"    # 按钮 accent 风格 (蓝色, 与现有 ACCENT 一致)
SUCCESS_BOOTSTRAP = "success"   # 绿色, 连接成功 / 状态良好
DANGER_BOOTSTRAP = "danger"    # 红色, 关闭 / 危险操作
SECONDARY_BOOTSTRAP = "secondary"  # 灰色, 次要按钮
INFO_BOOTSTRAP = "info"            # 蓝色, 信息
WARNING_BOOTSTRAP = "warning"    # 黄色, 警告

# 全局 Style 单例 — 启动时初始化一次
_style_instance: Optional[object] = None


def get_style() -> object:
    """获取全局 ttkbootstrap Style 实例 (启动时已创建)."""
    global _style_instance
    if _style_instance is None:
        _style_instance = ttk.Style(theme=THEME_NAME)
    return _style_instance


def apply_theme(root: tk.Tk) -> object:
    """初始化 ttkbootstrap 主题并应用到 root 窗口.

    Args:
        root: tk.Tk() 实例 (由调用方创建)

    Returns:
        Style 实例, 供后续 configure 用
    """
    global _style_instance
    if not _HAS_TTKBOOTSTRAP:
        # 降级: 用标准 ttk
        _style_instance = ttk.Style(root)
        try:
            _style_instance.theme_use("clam")  # 比 default 平整
        except tk.TclError:
            pass
        return _style_instance

    # ttkbootstrap 路径: 把 root 转换成 ttkbootstrap.Window (可选, 也可直接用 tk.Tk)
    # 这里直接创建 Style 对象, 主题立即生效.
    _style_instance = ttk.Style(theme=THEME_NAME)

    # 自定义一些全局样式选项 — 让 ttk widget 与现有 Theme.BG_BASE 协调
    _style_instance.configure(
        "TFrame",
        background="#0f172a",     # Theme.BG_BASE
    )
    _style_instance.configure(
        "TLabelframe",
        background="#1e293b",     # Theme.BG_PANEL
        foreground="#f1f5f9",     # Theme.TEXT_PRIMARY
        bordercolor="#475569",    # Theme.BORDER
    )
    _style_instance.configure(
        "TLabel",
        background="#0f172a",
        foreground="#f1f5f9",
    )
    _style_instance.configure(
        "TNotebook",
        background="#0f172a",
    )
    _style_instance.configure(
        "TCombobox",
        fieldbackground="#1e293b",
        background="#1e293b",
        foreground="#f1f5f9",
        bordercolor="#475569",
    )

    return _style_instance


def make_button(
    parent: tk.Widget,
    text: Optional[str] = None,
    icon: Optional[str] = None,
    command=None,
    bootstyle: str = ACCENT_BOOTSTRAP,
    width: Optional[int] = None,
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Button (替代 tk.Button).

    Args:
        text: 按钮文字
        icon: 可选 unicode 图标 (放在 text 前面)
        command: 点击回调
        bootstyle: 风格 ("primary"/"info"/"success"/"danger"/"warning"/"secondary"/"outline-*"等)
        width: 按钮宽度 (字符)
    """
    label = f"{icon} {text}" if icon and text else (icon or text or "")
    if _HAS_TTKBOOTSTRAP:
        return ttk.Button(
            parent,
            text=label,
            command=command,
            bootstyle=bootstyle,
            width=width,
            **kwargs,
        )
    # 降级到标准 ttk
    btn = ttk.Button(parent, text=label, command=command, width=width, **kwargs)
    return btn


def make_label(
    parent: tk.Widget,
    text: str = "",
    bootstyle: str = "",
    font: Optional[tuple] = None,
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Label (替代 tk.Label)."""
    if _HAS_TTKBOOTSTRAP and bootstyle:
        return ttk.Label(parent, text=text, font=font, bootstyle=bootstyle, **kwargs)
    return ttk.Label(parent, text=text, font=font, **kwargs)


def make_frame(
    parent: tk.Widget,
    bootstyle: str = "",
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Frame (替代 tk.Frame).

    注意: ttk.Frame 不支持 bootstyle 参数 (bootstyle 只对带颜色的 widget 有意义,
    如 Button/Label/Entry/Combobox/Separator/Progressbar). 这里忽略 bootstyle.
    """
    return ttk.Frame(parent, **kwargs)


def make_entry(
    parent: tk.Widget,
    textvariable=None,
    bootstyle: str = "",
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Entry (替代 tk.Entry)."""
    if _HAS_TTKBOOTSTRAP and bootstyle:
        return ttk.Entry(parent, textvariable=textvariable, bootstyle=bootstyle, **kwargs)
    return ttk.Entry(parent, textvariable=textvariable, **kwargs)


def make_combobox(
    parent: tk.Widget,
    values,
    textvariable=None,
    bootstyle: str = "",
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Combobox (替代 ttk.Combobox)."""
    if _HAS_TTKBOOTSTRAP and bootstyle:
        return ttk.Combobox(
            parent,
            values=values,
            textvariable=textvariable,
            bootstyle=bootstyle,
            **kwargs,
        )
    return ttk.Combobox(parent, values=values, textvariable=textvariable, **kwargs)


def make_progressbar(
    parent: tk.Widget,
    bootstyle: str = SUCCESS_BOOTSTRAP,
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Progressbar (替代 ttk.Progressbar)."""
    if _HAS_TTKBOOTSTRAP and bootstyle:
        return ttk.Progressbar(parent, bootstyle=bootstyle, **kwargs)
    return ttk.Progressbar(parent, **kwargs)


def make_separator(
    parent: tk.Widget,
    bootstyle: str = "",
    **kwargs,
) -> object:
    """创建一个 ttkbootstrap.Separator (替代 ttk.Separator)."""
    if _HAS_TTKBOOTSTRAP and bootstyle:
        return ttk.Separator(parent, bootstyle=bootstyle, **kwargs)
    return ttk.Separator(parent, **kwargs)


__all__ = [
    "THEME_NAME",
    "ACCENT_BOOTSTRAP",
    "SUCCESS_BOOTSTRAP",
    "DANGER_BOOTSTRAP",
    "SECONDARY_BOOTSTRAP",
    "WARNING_BOOTSTRAP",
    "apply_theme",
    "get_style",
    "make_button",
    "make_label",
    "make_frame",
    "make_entry",
    "make_combobox",
    "make_progressbar",
    "make_separator",
]


def has_ttkbootstrap() -> bool:
    """是否真的加载了 ttkbootstrap (没装时降级到 ttk)."""
    return _HAS_TTKBOOTSTRAP
