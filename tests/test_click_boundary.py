"""回归测试: 鼠标点击边界检查 bug.

Bug 现象: mjpeg_client 模式下, frame.shape[1] 在 _do_render 里被赋给
         self.video_width, 把设备原始尺寸 (1080x1920) 覆盖成显示尺寸 (417x743).
         之后 mouse_up 的边界检查用 self.video_width (417) 判 dev_start_x (539),
         结果 dev_start_x > video_width, 点击被丢弃, "完全没反应".

修复: device_controller 新增 device_video_width/height (保存真设备尺寸),
      set_display_resolution 时填, mouse_up 边界检查用这个字段.
"""
import sys, os, time
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import tkinter as tk
from unittest.mock import MagicMock

from gui.device_controller import DeviceController


class FakeHDC:
    def __init__(self):
        self.calls = []
    def execute_async(self, args, **kw):
        self.calls.append(args); return True
    def execute(self, args, **kw): return {"success": True}
    def _find_hdc_path(self): return "/fake/hdc"


def make_dc(canvas, vw=1080, vh=1920, dw=417, dh=743):
    """构造 DeviceController 模拟 mjpeg_client 覆盖 video_width 的场景"""
    dc = DeviceController.__new__(DeviceController)
    dc.hdc = FakeHDC()
    dc.video_canvas = canvas
    dc._uitest_daemon_started = True
    dc.drag_start = None
    dc._raw_drag = False
    dc._last_tap_time = 0.0
    dc._last_tap_pos = None
    dc.log_title = "test"
    # 模拟 set_display_resolution
    dc.device_video_width = vw
    dc.device_video_height = vh
    dc.video_width = vw      # 一开始是真尺寸
    dc.video_height = vh
    dc.display_ratio = min(dw/vw, dh/vh)
    dc.display_width = dw
    dc.display_height = dh
    canvas_w = canvas.winfo_width()
    canvas_h = canvas.winfo_height()
    dc.left = (canvas_w - dw) // 2
    dc.right = dc.left + dw
    dc.top = (canvas_h - dh) // 2
    dc.bottom = dc.top + dh
    return dc


def simulate_mjpeg_overwrite(dc):
    """模拟 mjpeg_client.poll resize 后 _do_render 把 video_width 覆盖成显示尺寸"""
    dc.video_width = dc.display_width
    dc.video_height = dc.display_height


def test_click_center():
    """点击视频显示区中心, 不应被边界检查过滤"""
    root = tk.Tk(); root.geometry("1500x900")
    canvas = tk.Canvas(root, width=1337, height=830)
    canvas.pack(); root.update()

    dc = make_dc(canvas)
    simulate_mjpeg_overwrite(dc)  # mjpeg 模式下 video_width 被覆盖

    # 点击视频显示区中心 (canvas 668, 415)
    e_down = MagicMock(); e_down.x, e_down.y = 668, 415
    dc._on_mouse_down(e_down)
    dc._on_mouse_up(e_down)

    # 验证 send_tap 被调用 (device_x, device_y 应该是 (540, 960) 附近)
    # 通过看 dc.hdc.calls
    assert len(dc.hdc.calls) > 0, "FAIL: send_tap 未被调用 (点击被边界过滤掉了)"
    args = dc.hdc.calls[-1]
    # 解析 uitest click X Y
    if "click" in str(args):
        x_arg = int(args[args.index('click') + 1])
        y_arg = int(args[args.index('click') + 2])
        assert 500 < x_arg < 600 and 900 < y_arg < 1020, \
            f"FAIL: 坐标错 ({x_arg},{y_arg}), 期望 ~(540, 960)"
        print(f"PASS: 中心点击 -> 设备 ({x_arg}, {y_arg})")
    root.destroy()


def test_click_corner():
    """点击视频显示区右下角 (边界附近), 不应被过滤"""
    root = tk.Tk(); root.geometry("1500x900")
    canvas = tk.Canvas(root, width=1337, height=830)
    canvas.pack(); root.update()

    dc = make_dc(canvas)
    simulate_mjpeg_overwrite(dc)

    # 右下角: canvas (dc.right-1, dc.bottom-1)
    e_down = MagicMock()
    e_down.x, e_down.y = dc.right - 5, dc.bottom - 5
    dc._on_mouse_down(e_down)
    dc._on_mouse_up(e_down)
    assert len(dc.hdc.calls) > 0, "FAIL: 右下角点击被过滤"
    args = dc.hdc.calls[-1]
    if "click" in str(args):
        x_arg = int(args[args.index('click') + 1])
        y_arg = int(args[args.index('click') + 2])
        assert x_arg > 1000 and y_arg > 1800, \
            f"FAIL: 右下角坐标错 ({x_arg},{y_arg}), 应接近 (1080, 1920)"
        print(f"PASS: 右下角点击 -> 设备 ({x_arg}, {y_arg})")
    root.destroy()


def test_click_outside_still_filtered():
    """点击视频显示区外 (黑边), 应该被边界过滤 (不调 send_tap)"""
    root = tk.Tk(); root.geometry("1500x900")
    canvas = tk.Canvas(root, width=1337, height=830)
    canvas.pack(); root.update()

    dc = make_dc(canvas)
    simulate_mjpeg_overwrite(dc)
    dc.hdc.calls.clear()

    # 点击黑边 (canvas 100, 100) -> 在 left=460 之前, 应被过滤
    e_down = MagicMock(); e_down.x, e_down.y = 100, 100
    dc._on_mouse_down(e_down)
    dc._on_mouse_up(e_down)
    # _on_mouse_down 早 return, drag_start 不会被设
    assert len(dc.hdc.calls) == 0, "FAIL: 黑边点击不应触发 send_tap"
    print(f"PASS: 黑边点击被正确过滤")
    root.destroy()


test_click_center()
test_click_corner()
test_click_outside_still_filtered()
print("ALL CLICK BOUNDARY TESTS PASS")
