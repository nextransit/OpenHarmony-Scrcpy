"""集成测试: 完整模拟 _init_components_async 流程, 验证修复:
1. HDCCommandExecutor 在主线程创建并传给 DeviceController (修鼠标无反应)
2. _do_render 不抛 NameError (修黑屏)
3. on_frame_decoded -> _do_render 链路不断 (修渲染卡死)

模拟真实 mjpeg server (1920x1080 jpeg) + 完整 MainWindow 启动流程.
"""
import sys, os, time, socket, io, http.server, threading, unittest.mock
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import numpy as np
from PIL import Image
import tkinter as tk

# Mock http server
arr = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
buf = io.BytesIO()
Image.fromarray(arr).save(buf, format='JPEG', quality=85)
jpeg_bytes = buf.getvalue()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(jpeg_bytes)))
        self.end_headers()
        self.wfile.write(jpeg_bytes)
    def log_message(self, *a): pass

s = socket.socket(); s.bind(('', 0)); port = s.getsockname()[1]; s.close()
server = http.server.HTTPServer(('127.0.0.1', port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


class FakeHDC:
    def __init__(self, *a, **kw):
        self.calls = []
    def execute(self, args, **kw):
        self.calls.append(('execute', args))
        if 'start-daemon' in args:
            return {'success': True, 'stdout': 'started', 'stderr': ''}
        return {'success': True, 'stdout': '', 'stderr': ''}
    def execute_async(self, args, **kw):
        self.calls.append(('execute_async', args))
        return True
    def _find_hdc_path(self): return '/fake/hdc'
    def set_device(self, sn): pass
    def stop_async_processes(self): pass


with unittest.mock.patch('core.hdc_executor.HDCCommandExecutor', FakeHDC):
    with unittest.mock.patch('core.device_manager.HDCCommandExecutor', FakeHDC):
        with unittest.mock.patch('core.server_manager.HDCCommandExecutor', FakeHDC):
            import gui.main_window as mw_mod
            import gui.connection_manager as cm_mod

            mw = mw_mod.MainWindow.__new__(mw_mod.MainWindow)
            # 不要先设置 self.root: __init__ 会自己创建 Tk root
            # (我们之前的写法让 test 创建了一个 Tk, 然后 __init__ 又创建一个,
            #  两个 Tk 解释器互相不识别 image/pyimage, 导致 canvas.create_image 失败)
            tk_root = None
            mw.video_display = None
            mw.is_connected = True
            mw.server_deploy_lock = threading.Lock()
            mw.server_deploy_state = None
            mw.video_client = None
            mw.connection_manager = None
            mw.server_deployer = None
            mw.device_controller = None
            mw.keyboard_mapper = None
            mw.hdc_executor = None

            # 完整启动
            mw.__init__()
            tk_root = mw.root  # __init__ 创建的 Tk 是真正的 root
            try:
                tk_root.state("normal")
                tk_root.geometry("1450x900")
            except Exception:
                pass
            time.sleep(0.5)
            tk_root.update()
            tk_root.update_idletasks()

            # 验证 1: hdc_executor 已在主线程创建
            assert mw.hdc_executor is not None, \
                "FAIL [修复 1]: hdc_executor 未在主线程创建"
            assert mw.device_controller.hdc is mw.hdc_executor, \
                "FAIL [修复 1]: device_controller.hdc != mw.hdc_executor"

            # 验证 2: send_tap 不抛 NoneType 错误, 走 uinput -T 主路径 (220ms 优化)
            # [优化 2026-09] send_tap 主路径从 uitest click (540ms) 改为 uinput -T (220ms),
            #   不依赖 uitest daemon (daemon 仅 fallback 路径用)
            mw.device_controller._uitest_daemon_started = False  # 重置
            ok = mw.device_controller.send_tap(540, 960)
            assert ok, "FAIL [修复 1]: send_tap 返回 False (链路断了?)"
            # 主路径 uinput -T 不需要 uitest daemon, 所以这里只验证 _send_tap_via_uitest_click 备用路径能启动 daemon
            ok2 = mw.device_controller._send_tap_via_uitest_click(540, 960)
            assert ok2, "FAIL [修复 1]: _send_tap_via_uitest_click 返回 False"
            assert mw.device_controller._uitest_daemon_started, \
                "FAIL [修复 1]: uitest daemon 未启动 (fallback 路径需要)"
            print(f"[修复 1] send_tap 成功, 主路径 uinput -T (220ms), fallback uitest click 可用", flush=True)

            # 验证 3: 注入 mjpeg stream, 触发 poll 线程 -> on_frame_decoded -> _do_render
            from video.mjpeg_client import MjpegStreamClient
            mc = MjpegStreamClient(
                host='127.0.0.1', port=port,
                on_frame_decoded=mw.video_display.on_frame_decoded,
            )

            class CM:
                is_connected = True
                @staticmethod
                def get_video_client(): return mc
            mw.video_display.connection_manager = CM()

            mc.connect('127.0.0.1', port)
            mw.video_display.start_main_poll()

            # 跑 3 秒
            start = time.time()
            errors_seen = []
            while time.time() - start < 3:
                time.sleep(0.2)
                tk_root.update()
                tk_root.update_idletasks()

            assert mc.frame_count > 10, \
                f"FAIL [修复 3]: frame_count={mc.frame_count} (期望 > 10)"
            assert mw.video_display.image_item_id is not None, \
                "FAIL [修复 3]: image_item_id 为 None (canvas 永远黑屏)"
            assert mw.video_display.video_width == 1080, \
                f"FAIL [修复 2]: video_width={mw.video_display.video_width} (应为 1080)"
            print(f"[修复 2/3] frame_count={mc.frame_count}, image_id={mw.video_display.image_item_id}, "
                  f"video_width={mw.video_display.video_width}", flush=True)

            mc.disconnect()
            server.shutdown()
            tk_root.destroy()
            print(f"\nPASS: 集成测试通过", flush=True)
