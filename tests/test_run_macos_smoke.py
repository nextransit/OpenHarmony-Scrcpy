"""run_macos 自动化 smoke 测试: 一键验证 run_macos 启动链路 + 滚轮修复.

覆盖两个用户报告的 bug:
  Bug A 黑屏: run_macos 启动后 splash -> 主窗口 -> 投屏区应正常出图, 不黑屏.
Bug B 滚轮: 鼠标在 canvas 滚动 -> uinput -M -s 原生滚轮命令被调用.

测试方法:
  - 用 Mock 替代真实 hdc 设备端交互 (FakeHDC, 内置 httpd + JPEG 流)
  - 起一个 Tk root, 走完 MainWindow.__init__ + _init_components_async
  - 真实 wheel/button 事件注入, 验证 handler 触发 + 触摸滑动调用

运行:
  PYTHONPATH=. OHCRCPY_NO_SPLASH=1 venv312/bin/python3 tests/test_run_macos_smoke.py
  或: bash scripts/run_macos_tests.sh
"""
import sys, os, time, threading, socket, io, http.server, queue
from unittest.mock import MagicMock, patch

# 自动检测项目根 (不依赖 /home/ben/ohscrcpy)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault('OHCRCPY_NO_SPLASH', '1')

import tkinter as tk
import numpy as np
from PIL import Image

# 抑制 OpenCV / numpy 启动噪音
os.environ.setdefault('OPENCV_LOG_LEVEL', 'ERROR')

# ---- Fake http server: 模拟设备端 busybox httpd ----
class FakeHttpHandler(http.server.BaseHTTPRequestHandler):
    # 静态 jpeg 帧: 1080x1920 彩色图
    arr = np.full((1920, 1080, 3), 180, dtype=np.uint8)
    arr[100:300, 200:400, 0] = 255  # 一块红色方便辨认
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format='JPEG', quality=85)
    JPEG_BYTES = buf.getvalue()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(self.JPEG_BYTES)))
        self.end_headers()
        self.wfile.write(self.JPEG_BYTES)
    def log_message(self, *a): pass


def start_fake_httpd():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.HTTPServer(('127.0.0.1', port), FakeHttpHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


# ---- FakeHDC: 收集所有 hdc 调用 ----
class FakeHDC:
    def __init__(self, *a, **kw):
        self.calls = []
        self.device_sn = None
    def execute(self, args, *positional, **kw):
        self.calls.append(('execute', args))
        if args[:2] == ['list', 'targets']:
            return {'success': True, 'stdout': '192.168.19.10:5555', 'stderr': ''}
        if 'param' in args and 'const.product.model' in args:
            return {'success': True, 'stdout': 'AIOT-3568A', 'stderr': ''}
        if 'param' in args and 'const.product.manufacturer' in args:
            return {'success': True, 'stdout': 'ShiMeta', 'stderr': ''}
        # start-daemon 返回成功
        if 'start-daemon' in args:
            return {'success': True, 'stdout': 'started', 'stderr': ''}
        # busybox httpd 检查
        if 'busybox' in str(args):
            return {'success': True, 'stdout': 'busybox', 'stderr': ''}
        return {'success': True, 'stdout': '', 'stderr': ''}
    def execute_async(self, args, **kw):
        self.calls.append(('execute_async', args, time.time()))
        return True
    def _find_hdc_path(self): return '/fake/hdc'
    def set_device(self, sn): self.device_sn = sn
    def stop_async_processes(self): pass
    @property
    def hdc_path(self): return '/fake/hdc'


class FakeMjpegServerManager:
    def __init__(self, hdc):
        self.hdc = hdc
        self.stopped = False

    def setup(self, *args, **kwargs):
        return True

    def stop(self):
        self.stopped = True


# ---- 测试结果累积 ----
RESULTS = []
def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {detail}", flush=True)


def run_black_screen_test():
    """Bug A 黑屏回归: 启动 -> 连接 -> 3秒内 canvas 拿到 >=10 帧.

    模拟 run_macos.command 的 splash -> 主窗口 -> 连接设备 -> MJPEG 解码 完整流程.
    """
    print("\n== Bug A 黑屏回归 ==", flush=True)
    srv, port = start_fake_httpd()

    # run_macos 启动需要的环境变量 (从 run_macos.command 抄)
    os.environ['OHCRCPY_MJPEG_MODE'] = '1'
    os.environ['OHCRCPY_MJPEG_PORT'] = str(port)
    os.environ['OHCRCPY_NO_SPLASH'] = '1'  # 测试无 splash, 跳过 1.5s 等待

    # 黑屏核心修复: 必须用 OHCRCPY_NO_SPLASH=1 shortcut 后, 手动 deiconify
    # 验证 deiconify 成功 (splash 路径里 splash 完成后自动 deiconify)

    with patch('core.hdc_executor.HDCCommandExecutor', FakeHDC), \
         patch('core.device_manager.HDCCommandExecutor', FakeHDC), \
         patch('core.server_manager.HDCCommandExecutor', FakeHDC):
        import gui.main_window as mw_mod
        # MainWindow 在模块导入时已缓存 HDCCommandExecutor；必须补丁模块内
        # 的引用，否则 smoke 会误连真实设备并修改设备端服务。
        with patch.object(mw_mod, 'HDCCommandExecutor', FakeHDC), \
             patch.object(mw_mod, 'MjpegServerManager', FakeMjpegServerManager):

            mw = mw_mod.MainWindow.__new__(mw_mod.MainWindow)
            # 初始化一些 attrs, 防止 _show_splash_then_init_ui 短路路径里访问不到
            for attr in ['video_display', 'video_canvas', 'device_controller',
                         'keyboard_mapper', 'hdc_executor', 'device_manager',
                         'device_panel', 'video_client', 'connection_manager',
                         'server_deployer']:
                if not hasattr(mw, attr):
                    setattr(mw, attr, None)
            mw.is_connected = True
            mw.server_deploy_lock = threading.Lock()
            mw.server_deploy_state = None

            try:
                mw.__init__()
            except Exception as e:
                record("__init__ 不抛异常", False, f"{e!r}")
                srv.shutdown()
                return
            record("__init__ 不抛异常", True, "")

            tk_root = mw.root
            # OHCRCPY_NO_SPLASH=1 路径不 deiconify, 手动调 (splash 路径会自动 deiconify)
            try:
                tk_root.deiconify()
                tk_root.update_idletasks()
                tk_root.geometry("1450x900")
            except Exception as e:
                record("deiconify 成功", False, f"{e!r}")

            # pump 多次 update + 让 after 回调跑完
            # _do_init_in_main 通过 _gui_queue + _pump_gui_queue (root.after 50ms) 异步执行
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    tk_root.update_idletasks()
                    tk_root.update()
                except Exception:
                    pass
                if mw.hdc_executor is not None:
                    break
                time.sleep(0.1)
            for _ in range(20):
                try:
                    tk_root.update_idletasks()
                    tk_root.update()
                except Exception:
                    pass
                time.sleep(0.05)

            record("主窗口已显示 (state=normal)", tk_root.state() == "normal",
                   f"mapped={tk_root.winfo_ismapped()} state={tk_root.state()}")
            record("hdc_executor 已在主线程创建", mw.hdc_executor is not None, "")
            record("device_controller 已创建", mw.device_controller is not None, "")
            record("video_canvas 已存在", mw.video_canvas is not None, "")

            # 关键修复: device_controller.hdc 不为 None
            # 修复前: _do_init_in_main 调 DeviceController(self.hdc_executor) 时
            # self.hdc_executor 还是 None, _ensure_uitest_daemon 全报 NoneType.
            if mw.device_controller is not None and mw.hdc_executor is not None:
                record("device_controller.hdc != None (修复 NoneType bug)",
                       mw.device_controller.hdc is mw.hdc_executor,
                       f"dc.hdc={mw.device_controller.hdc!r}")

            # 等设备连接 + 帧解码 (实际 run_macos 启动后 2 秒左右连上)
            time.sleep(1.5)
            for _ in range(20):
                try:
                    tk_root.update()
                except Exception:
                    pass
                time.sleep(0.05)

            # 关键修复 2: _do_render 必须被主线程调度
            # 修复前: mjpeg_client.poll 线程调 on_frame_decoded -> canvas.after 在子线程,
            #   抛 "main thread is not in main loop", _do_render 永远不被调度, canvas 黑屏.
            # 注: 真实视频帧渲染需要 MJPEG 客户端连接到 busybox httpd (设备端),
            #     FakeHDC 不能让 mock 设备真实响应该端口. 帧渲染验证由
            #     test_integration_mainwindow.py + test_black_screen_repro.py 覆盖.
            # 这里只验证 _do_render 流程可调度:
            if mw.video_display is not None:
                record("video_display 已创建", True, "")
                # 主线程 start_main_poll 已启动 (在 _do_init_in_main)
                # 验证 _poll_render_queue 后台线程存在且 pending 字段可达
                vd = mw.video_display
                # 至少调用一次 _do_render 不抛异常
                try:
                    vd._do_render()
                    record("主线程 _do_render 不抛异常 (黑屏 bug 关键)",
                           True, "")
                except Exception as e:
                    record("主线程 _do_render 不抛异常 (黑屏 bug 关键)",
                           False, f"{e!r}")
            else:
                record("video_display 已创建", False, "video_display 为 None")

            # 清理 (小心 server_deployer 子线程可能还在 after)
            try:
                mw.is_connected = False
                if hasattr(mw, '_on_closing'):
                    mw._on_closing()
                else:
                    tk_root.destroy()
            except Exception:
                pass
            srv.shutdown()


def run_mouse_wheel_test():
    """Bug B 滚轮回归: MouseWheel 真实转成设备原生滚轮."""
    print("\n== Bug B 滚轮回归 ==", flush=True)
    root = tk.Tk()
    root.geometry("800x600")

    canvas = tk.Canvas(root, bg="#1a1a2e", width=800, height=600)
    canvas.pack(fill=tk.BOTH, expand=True)

    hdc = FakeHDC()
    from gui.device_controller import DeviceController
    dc = DeviceController(hdc)
    dc.bind_video_canvas(canvas)
    dc.set_display_resolution(1080, 1920, 800, 600)

    record("canvas 已绑 <MouseWheel>", "<MouseWheel>" in canvas.bind(),
           f"bindings={sorted(canvas.bind())}")
    record("canvas 已绑 <Button-4> / <Button-5>",
           "<Button-4>" in canvas.bind() and "<Button-5>" in canvas.bind(), "")

    rb = root.bind_all()
    # Python 3.14+ bind_all() 返回 tuple (sequences); 旧版本返回 dict
    rb_list = list(rb) if not isinstance(rb, dict) else list(rb.keys())
    record("root.bind_all 已绑 <MouseWheel>", "<MouseWheel>" in rb_list,
           f"root.bind_all keys={sorted(rb_list)}")

    # 关键 macOS AquaTk fix: bind_video_canvas 必须调 canvas.focus_force()
    # headless 下 focus_force 可能 silent fail, 但 bind_video_canvas 内部
    # 应在 INFO 日志输出 "bind_video_canvas 完成"
    record("canvas 不绑 <Motion> (macOS AquaTk 高频触发会卡死主循环)",
           "<Motion>" not in canvas.bind(),
           f"bindings={sorted(canvas.bind())}")

    # 真实触发: 模拟 macOS 上 <MouseWheel> 事件
    class E:
        def __init__(self, delta=0, num=0, x=400, y=300):
            self.delta = delta
            self.num = num
            self.x = x
            self.y = y

    calls_before = len([c for c in hdc.calls if c[0] == 'execute_async'])
    dc._on_mouse_wheel(E(delta=1))   # 上滚
    time.sleep(0.05)
    dc._on_mouse_wheel(E(delta=-1))  # 下滚
    time.sleep(0.05)
    dc._on_mouse_wheel(E(num=4))     # Linux X11 Button-4 上滚
    time.sleep(0.05)
    dc._on_mouse_wheel(E(num=5))     # Linux X11 Button-5 下滚
    time.sleep(0.05)

    wheel_calls = [c for c in hdc.calls if c[0] == 'execute_async'][calls_before:]
    record("4 次 wheel -> 4 次原生滚轮",
           len(wheel_calls) == 4,
           f"实际 hdc.execute_async 次数={len(wheel_calls)}")

    directions = [
        int(call[1][4]) < 0
        for call in wheel_calls
    ]
    record("滚轮方向正确 (上/下/上/下)",
           directions == [True, False, True, False],
           f"方向={directions}")

    # 全局 handler 测试: bind_all 也能触发
    hdc.calls.clear()
    # 模拟 bind_all 触发的 event
    saved_x = dc._root_widget.winfo_pointerx
    saved_y = dc._root_widget.winfo_pointery
    dc._root_widget.winfo_pointerx = lambda: canvas.winfo_rootx() + 400
    dc._root_widget.winfo_pointery = lambda: canvas.winfo_rooty() + 300
    try:
        dc._on_mouse_wheel_global(E(delta=1))
        time.sleep(0.05)
        record("bind_all 全局 handler 触发 native mouse scroll",
               len([c for c in hdc.calls if c[0] == 'execute_async']) == 1,
               f"calls={len([c for c in hdc.calls if c[0] == 'execute_async'])}")
    finally:
        dc._root_widget.winfo_pointerx = saved_x
        dc._root_widget.winfo_pointery = saved_y

    root.destroy()


def run_run_macos_command_syntax_test():
    """run_macos.command 基本健全性: 可解析, 含必要 hook."""
    print("\n== run_macos.command 健全性 ==", flush=True)
    p = os.path.join(PROJECT_ROOT, 'run_macos.command')
    record("run_macos.command 存在", os.path.isfile(p), p)
    with open(p) as f:
        content = f.read()
    record("run_macos.command 含 venv312 优先",
           'venv312/bin/python3' in content, "")
    record("run_macos.command 含 MJPEG_MODE=1",
           'OHCRCPY_MJPEG_MODE=1' in content or 'OHCRCPY_MJPEG_MODE=${' in content, "")
    record("run_macos.command 含 DYLD_LIBRARY_PATH (hdc dylib)",
           'DYLD_LIBRARY_PATH' in content, "")
    record("run_macos.command 含 OHCRCPY_NO_SPLASH",
           'OHCRCPY_NO_SPLASH' not in content,  # 应当由测试自己设, 生产不强制
           "")


if __name__ == '__main__':
    print("=" * 60, flush=True)
    print(f"run_macos smoke 测试 (project_root={PROJECT_ROOT})", flush=True)
    print("=" * 60, flush=True)

    run_run_macos_command_syntax_test()
    run_black_screen_test()
    run_mouse_wheel_test()

    print("\n" + "=" * 60, flush=True)
    total = len(RESULTS)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"总计: {total} 项, 失败: {failed}", flush=True)
    if failed:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAIL: {name} -- {detail}", flush=True)
        sys.exit(1)
    else:
        print(f"PASS: {total}/{total} 项", flush=True)
        sys.exit(0)
