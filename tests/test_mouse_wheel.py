"""回归测试: macOS/Linux 鼠标滚轮转 OpenHarmony 原生滚轮事件."""

import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OHCRCPY_NO_SPLASH", "1")

import tkinter as tk

import gui.device_controller as dc_module
from gui.device_controller import DeviceController


class FakeHDC:
    def __init__(self):
        self.calls = []

    def execute_async(self, args, timeout=3):
        self.calls.append((time.time(), args))
        return True

    def execute(self, *args, **kwargs):
        return {"success": True}


class Event:
    def __init__(self, delta=0, num=0, x=200, y=200):
        self.delta = delta
        self.num = num
        self.x = x
        self.y = y


def setup_controller(root):
    canvas = tk.Canvas(root, bg="#1a1a2e", width=400, height=400)
    canvas.pack(fill=tk.BOTH, expand=True)
    root.update_idletasks()
    hdc = FakeHDC()
    with mock.patch.object(dc_module, '_PYOBJC_AVAILABLE', False):
        controller = DeviceController(hdc)
        controller.bind_video_canvas(canvas)
    controller.set_display_resolution(1080, 1920, 400, 400)
    return canvas, controller, hdc


def assert_scroll(call, upward):
    args = call[1]
    assert args[:4] == ["shell", "uinput", "-M", "-s"], args
    if upward:
        assert int(args[4]) < 0, args
    else:
        assert int(args[4]) > 0, args


def test_mac_wheel_directions():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        _, controller, hdc = setup_controller(root)
        controller._on_mouse_wheel(Event(delta=1))
        time.sleep(0.06)
        controller._on_mouse_wheel(Event(delta=-1))
        time.sleep(0.06)
        controller._on_mouse_wheel(Event(delta=2))
        assert len(hdc.calls) == 3, hdc.calls
        assert_scroll(hdc.calls[0], upward=True)
        assert_scroll(hdc.calls[1], upward=False)
        assert_scroll(hdc.calls[2], upward=True)
        print("[macOS] wheel -> native mouse scroll direction PASS")
    finally:
        root.destroy()


def test_linux_button_4_5():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        _, controller, hdc = setup_controller(root)
        controller._on_mouse_wheel(Event(num=4))
        time.sleep(0.06)
        controller._on_mouse_wheel(Event(num=5))
        assert len(hdc.calls) == 2, hdc.calls
        assert_scroll(hdc.calls[0], upward=True)
        assert_scroll(hdc.calls[1], upward=False)
        print("[Linux] Button-4/5 -> native mouse scroll PASS")
    finally:
        root.destroy()


def test_debounce_same_direction():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        _, controller, hdc = setup_controller(root)
        controller._on_mouse_wheel(Event(delta=1))
        controller._on_mouse_wheel(Event(delta=1))
        controller._on_mouse_wheel(Event(delta=1))
        time.sleep(0.06)
        controller._on_mouse_wheel(Event(delta=-1))
        assert len(hdc.calls) == 2, hdc.calls
        assert_scroll(hdc.calls[0], upward=True)
        assert_scroll(hdc.calls[1], upward=False)
        print("[debounce] same direction merged PASS")
    finally:
        root.destroy()


def test_noop_when_disconnected():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        canvas = tk.Canvas(root, width=400, height=400)
        canvas.pack()
        hdc = FakeHDC()
        with mock.patch.object(dc_module, '_PYOBJC_AVAILABLE', False):
            controller = DeviceController(hdc)
            controller.bind_video_canvas(canvas)
        controller._on_mouse_wheel(Event(delta=1))
        assert not hdc.calls, hdc.calls
        print("[disconnected] no side effect PASS")
    finally:
        root.destroy()


def test_noop_on_zero_delta():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        _, controller, hdc = setup_controller(root)
        controller._on_mouse_wheel(Event(delta=0, num=0))
        assert not hdc.calls, hdc.calls
        print("[noop] delta=0 skipped PASS")
    finally:
        root.destroy()


def test_canvas_bindings_present():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        canvas, _, _ = setup_controller(root)
        bindings = canvas.bind()
        assert "<MouseWheel>" in bindings, bindings
        assert "<Button-4>" in bindings, bindings
        assert "<Button-5>" in bindings, bindings
        print("[bindings] wheel bindings PASS")
    finally:
        root.destroy()


def test_wheel_binding_strategy():
    """验证滚轮绑定策略: macOS+PyObjC 用 CGEventTap, 否则用递归绑定."""
    root = tk.Tk()
    root.geometry("400x400")
    try:
        from gui.device_controller import _IS_MACOS, _PYOBJC_AVAILABLE
        if _IS_MACOS and _PYOBJC_AVAILABLE:
            canvas = tk.Canvas(root, bg="#1a1a2e", width=400, height=400)
            canvas.pack(fill=tk.BOTH, expand=True)
            root.update_idletasks()
            hdc = FakeHDC()
            controller = DeviceController(hdc)
            controller.bind_video_canvas(canvas)
            controller.set_display_resolution(1080, 1920, 400, 400)
            root.update_idletasks()
            assert controller._cgevent_tap is not None, "macOS+PyObjC 应安装 CGEventTap"
            assert controller._cgevent_thread is not None, "应有 CFRunLoop 线程"
            print("[strategy] CGEventTap installed PASS")
            controller._remove_cgevent_tap()
        else:
            with mock.patch.object(dc_module, '_PYOBJC_AVAILABLE', False):
                canvas, controller, _ = setup_controller(root)
            root.update_idletasks()
            bound_count = 0
            def count_wheel(widget):
                nonlocal bound_count
                b = widget.bind()
                if "<MouseWheel>" in b:
                    bound_count += 1
                for child in widget.winfo_children():
                    count_wheel(child)
            count_wheel(root)
            assert bound_count >= 2, f"非macOS应递归绑定, 期望>=2, 实际{bound_count}"
            print(f"[strategy] recursive binding {bound_count} widgets PASS")
        assert "<MouseWheel>" in canvas.bind(), "canvas必须有MouseWheel绑定"
    finally:
        root.destroy()


def test_canvas_focus_binding():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        canvas, _, _ = setup_controller(root)
        bindings = canvas.bind()
        assert "<Enter>" in bindings, bindings
        assert "<Motion>" not in bindings, bindings
        print("[focus] Enter focus and no Motion binding PASS")
    finally:
        root.destroy()


def test_global_handler_outside_canvas_is_ignored():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        canvas, controller, hdc = setup_controller(root)
        controller._root_widget.winfo_pointerx = (
            lambda: canvas.winfo_rootx() + canvas.winfo_width() + 20
        )
        controller._root_widget.winfo_pointery = (
            lambda: canvas.winfo_rooty() + 20
        )
        controller._on_mouse_wheel_global(Event(delta=1))
        assert not hdc.calls, hdc.calls
        print("[outside] outside canvas ignored PASS")
    finally:
        root.destroy()


def test_global_handler_inside_canvas_sends_swipe():
    root = tk.Tk()
    root.geometry("400x400")
    try:
        canvas, controller, hdc = setup_controller(root)
        controller._root_widget.winfo_pointerx = (
            lambda: canvas.winfo_rootx() + canvas.winfo_width() // 2
        )
        controller._root_widget.winfo_pointery = (
            lambda: canvas.winfo_rooty() + canvas.winfo_height() // 2
        )
        controller._on_mouse_wheel_global(Event(delta=1))
        assert len(hdc.calls) == 1, hdc.calls
        assert_scroll(hdc.calls[0], upward=True)
        print("[global] inside canvas sends swipe PASS")
    finally:
        root.destroy()


def test_send_key_down_up_split():
    root = tk.Tk()
    root.geometry("200x200")
    try:
        canvas = tk.Canvas(root, width=200, height=200)
        canvas.pack()
        hdc = FakeHDC()
        with mock.patch.object(dc_module, '_PYOBJC_AVAILABLE', False):
            controller = DeviceController(hdc)
            controller.bind_video_canvas(canvas)
        assert controller.send_key("dpad_up")
        time.sleep(0.05)
        assert len(hdc.calls) == 2, hdc.calls
        down_ts, down_args = hdc.calls[0]
        up_ts, up_args = hdc.calls[1]
        assert down_args[3:5] == ["-d", "19"], down_args
        assert up_args[3:5] == ["-u", "19"], up_args
        assert (up_ts - down_ts) >= 0.02
        print("[key] down/up interval PASS")
    finally:
        root.destroy()


if __name__ == "__main__":
    tests = [
        test_mac_wheel_directions,
        test_linux_button_4_5,
        test_debounce_same_direction,
        test_noop_when_disconnected,
        test_noop_on_zero_delta,
        test_canvas_bindings_present,
        test_wheel_binding_strategy,
        test_canvas_focus_binding,
        test_global_handler_outside_canvas_is_ignored,
        test_global_handler_inside_canvas_sends_swipe,
        test_send_key_down_up_split,
    ]
    failed = []
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed.append((test.__name__, repr(exc)))
            print(f"[FAIL] {test.__name__}: {exc!r}")
    if failed:
        for name, message in failed:
            print(f"  {name}: {message}")
        sys.exit(1)
    print(f"PASS: {len(tests)}/{len(tests)} wheel tests")
