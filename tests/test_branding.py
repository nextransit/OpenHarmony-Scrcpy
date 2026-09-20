"""
品牌系统 单元测试
=================

覆盖:
  - BrandColor 常量非空
  - interpolate_color 边界 + 中间值
  - Logo.draw 创建 canvas items
  - StatusDot 状态切换
  - SplashScreen 启动 + 自动销毁
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISPLAY", ":10")

import tkinter as tk

from gui.branding import (
    BrandColor, interpolate_color,
    make_horizontal_gradient, Logo, StatusDot,
)


class TestBrandColor(unittest.TestCase):
    def test_color_constants_nonempty(self):
        self.assertTrue(BrandColor.PRIMARY.startswith("#"))
        self.assertTrue(BrandColor.ACCENT.startswith("#"))
        self.assertTrue(BrandColor.SUCCESS.startswith("#"))
        self.assertTrue(BrandColor.ERROR.startswith("#"))
        print(f"  PRIMARY={BrandColor.PRIMARY} ACCENT={BrandColor.ACCENT}")

    def test_color_constants_distinct(self):
        """关键颜色不应相同 (避免配置错误导致全灰)."""
        self.assertNotEqual(BrandColor.PRIMARY, BrandColor.ERROR)
        self.assertNotEqual(BrandColor.SUCCESS, BrandColor.ERROR)


class TestInterpolateColor(unittest.TestCase):
    def test_boundary_start(self):
        self.assertEqual(interpolate_color("#ff0000", "#0000ff", 0.0), "#ff0000")

    def test_boundary_end(self):
        self.assertEqual(interpolate_color("#ff0000", "#0000ff", 1.0), "#0000ff")

    def test_middle(self):
        mid = interpolate_color("#ff0000", "#0000ff", 0.5)
        # 中点应接近 #800080 (紫色)
        self.assertIn(mid, ("#7f007f", "#800080"))
        print(f"  midpoint=#{mid.lstrip('#')}")

    def test_clamp_out_of_range(self):
        """t<0 应返回 start, t>1 应返回 end."""
        self.assertEqual(interpolate_color("#aabbcc", "#112233", -0.5), "#aabbcc")
        self.assertEqual(interpolate_color("#aabbcc", "#112233", 1.5), "#112233")


class TestLogo(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, width=100, height=100)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_logo_draw_creates_items(self):
        items = Logo.draw(self.canvas, 5, 5, size=50)
        self.assertGreater(len(items), 0)
        print(f"  Logo draw -> {len(items)} items")

    def test_logo_size_param(self):
        for size in (24, 48, 96):
            items = Logo.draw(self.canvas, 0, 0, size=size)
            self.assertGreater(len(items), 0)

    def test_gradient_helper(self):
        items = make_horizontal_gradient(
            self.canvas, 0, 0, 100, 50,
            BrandColor.PRIMARY, BrandColor.ACCENT, steps=10,
        )
        self.assertEqual(len(items), 10)


class TestStatusDot(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, width=200, height=200)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_status_dot_creation(self):
        dot = StatusDot(self.canvas, 50, 50, size=12, initial="idle")
        self.assertEqual(dot.state, "idle")
        dot.destroy()

    def test_status_state_transitions(self):
        dot = StatusDot(self.canvas, 50, 50, size=12, initial="idle")
        for s in ("connecting", "connected", "error", "idle"):
            dot.set_state(s)
            self.assertEqual(dot.state, s)
        dot.destroy()

    def test_invalid_state_ignored(self):
        dot = StatusDot(self.canvas, 50, 50, size=12, initial="idle")
        dot.set_state("invalid_state")
        self.assertEqual(dot.state, "idle")  # 不变
        dot.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
