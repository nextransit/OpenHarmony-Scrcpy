"""回归测试: 鼠标滚轮 -> 设备 dpad up / down.

Bug: 之前 video_canvas 没绑定 <MouseWheel> / <Button-4/5>,
     鼠标滚轮在投屏区无反应.
修复: bind_video_canvas 增加 MouseWheel/Button-4/5 绑定,
     _on_mouse_wheel 解析 macOS (event.delta) 和 Linux X11 (event.num) 方向,
     调 send_key("dpad_up" / "dpad_down") 转发到设备.
     50ms 同方向防抖, 防止快速滚轮把设备端按死.
"""
import sys, os, time, threading
sys.path.insert(0, '/Users/zhouyong/Desktop/work/Decard/gitlab/openharmony/ohscrcpy')
os.environ.setdefault('OHCRCPY_NO_SPLASH', '1')

import tkinter as tk
from gui.device_controller import DeviceController


class FakeHDC:
    """收集所有 execute_async 调用"""
    def __init__(self):
        self.calls = []
    def execute_async(self, args, timeout=3):
        self.calls.append(args)
        return True
    def execute(self, *a, **kw):
        return {'success': True}
    def stop_async_processes(self): pass


def test_mac_wheel_directions():
    """macOS / Windows: event.delta 正值=向上滚, 负值=向下滚"""
    root = tk.Tk()
    root.geometry("400x400")
    canvas = tk.Canvas(root, bg="#1a1a2e", width=400, height=400)
    canvas.pack()

    hdc = FakeHDC()
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    dc.display_width = 300
    dc.display_height = 500

    class E:
        def __init__(self, delta=0, num=0):
            self.delta = delta
            self.num = num

    # 上滚 (delta > 0)
    dc._on_mouse_wheel(E(delta=1))
    time.sleep(0.06)
    # 下滚 (delta < 0)
    dc._on_mouse_wheel(E(delta=-1))
    time.sleep(0.06)
    # 上滚
    dc._on_mouse_wheel(E(delta=2))
    time.sleep(0.06)

    assert len(hdc.calls) == 3, f"expected 3 calls, got {len(hdc.calls)}"
    # 上滚 -> dpad_up (keycode 19)
    assert hdc.calls[0][4] == '19', f"up: {hdc.calls[0]}"
    # 下滚 -> dpad_down (keycode 20)
    assert hdc.calls[1][4] == '20', f"down: {hdc.calls[1]}"
    # 上滚 -> 19
    assert hdc.calls[2][4] == '19', f"up: {hdc.calls[2]}"
    print("[macOS] wheel up/down -> dpad up/down  PASS")
    root.destroy()


def test_linux_button_4_5():
    """Linux X11: event.num 4=上, 5=下"""
    root = tk.Tk()
    root.geometry("400x400")
    canvas = tk.Canvas(root, bg="#1a1a2e", width=400, height=400)
    canvas.pack()

    hdc = FakeHDC()
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    dc.display_width = 300
    dc.display_height = 500

    class E:
        def __init__(self, delta=0, num=0):
            self.delta = delta
            self.num = num

    # Linux 上滚
    dc._on_mouse_wheel(E(num=4))
    time.sleep(0.06)
    # Linux 下滚
    dc._on_mouse_wheel(E(num=5))
    time.sleep(0.06)

    assert len(hdc.calls) == 2, f"expected 2 calls, got {len(hdc.calls)}"
    assert hdc.calls[0][4] == '19', f"up: {hdc.calls[0]}"
    assert hdc.calls[1][4] == '20', f"down: {hdc.calls[1]}"
    print("[Linux] Button-4/5 -> dpad up/down  PASS")
    root.destroy()


def test_debounce_same_direction():
    """50ms 内同方向滚轮合并为一次按键"""
    root = time.time()
    app = tk.Tk()
    app.geometry("400x400")
    canvas = tk.Canvas(app, bg="#1a1a2e", width=400, height=400)
    canvas.pack()

    hdc = FakeHDC()
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    dc.display_width = 300
    dc.display_height = 500

    class E:
        def __init__(self, delta=0, num=0):
            self.delta = delta
            self.num = num

    # 快速连续同方向 (50ms 内)
    dc._on_mouse_wheel(E(delta=1))   # up (第 1 次, 触发)
    dc._on_mouse_wheel(E(delta=1))   # up (50ms 内, 应被合并)
    dc._on_mouse_wheel(E(delta=1))   # up (50ms 内, 应被合并)
    # 50ms 后反向
    time.sleep(0.06)
    dc._on_mouse_wheel(E(delta=-1))  # down (新方向, 触发)

    assert len(hdc.calls) == 2, \
        f"防抖失效: 期望 2 次 (1 up + 1 down), 实际 {len(hdc.calls)}: {hdc.calls}"
    assert hdc.calls[0][4] == '19'
    assert hdc.calls[1][4] == '20'
    print(f"[debounce] 50ms 同方向合并  PASS ({len(hdc.calls)} calls)")
    app.destroy()


def test_noop_when_disconnected():
    """未连接时滚轮无副作用"""
    app = tk.Tk()
    app.geometry("400x400")
    canvas = tk.Canvas(app, bg="#1a1a2e", width=400, height=400)
    canvas.pack()

    hdc = FakeHDC()
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    # display_width = 0 (未连接)

    class E:
        def __init__(self, delta=0, num=0):
            self.delta = delta
            self.num = num

    dc._on_mouse_wheel(E(delta=1))
    dc._on_mouse_wheel(E(delta=-1))
    assert len(hdc.calls) == 0, f"未连接不应有调用, got {hdc.calls}"
    print("[disconnected] 滚轮无副作用  PASS")
    app.destroy()


def test_noop_on_zero_delta():
    """delta=0 / num=0 都跳过"""
    app = tk.Tk()
    app.geometry("400x400")
    canvas = tk.Canvas(app, bg="#1a1a2e", width=400, height=400)
    canvas.pack()

    hdc = FakeHDC()
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    dc.display_width = 300
    dc.display_height = 500

    class E:
        def __init__(self, delta=0, num=0):
            self.delta = delta
            self.num = num

    dc._on_mouse_wheel(E(delta=0, num=0))
    dc._on_mouse_wheel(E(delta=10, num=0))  # 大 delta 也是 up
    time.sleep(0.06)
    assert len(hdc.calls) == 1, f"expected 1, got {len(hdc.calls)}"
    assert hdc.calls[0][4] == '19'
    print("[noop] delta=0 跳过  PASS")
    app.destroy()


def test_canvas_bindings_present():
    """bind_video_canvas 必须包含 MouseWheel / Button-4/5 绑定"""
    app = tk.Tk()
    app.geometry("400x400")
    canvas = tk.Canvas(app, bg="#1a1a2e", width=400, height=400)
    canvas.pack()
    dc = DeviceController(FakeHDC())
    dc.bind_video_canvas(canvas)

    bindings = canvas.bind()
    assert "<MouseWheel>" in bindings, f"<MouseWheel> 未绑定: {bindings}"
    assert "<Button-4>" in bindings, f"<Button-4> 未绑定: {bindings}"
    assert "<Button-5>" in bindings, f"<Button-5> 未绑定: {bindings}"
    print("[bindings] MouseWheel + Button-4/5 全部绑定  PASS")
    app.destroy()


if __name__ == "__main__":
    tests = [
        test_mac_wheel_directions,
        test_linux_button_4_5,
        test_debounce_same_direction,
        test_noop_when_disconnected,
        test_noop_on_zero_delta,
        test_canvas_bindings_present,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"[ERR ] {t.__name__}: {e!r}")
    print()
    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} tests failed")
        for name, msg in failed:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"PASS: {len(tests)}/{len(tests)} wheel tests")
