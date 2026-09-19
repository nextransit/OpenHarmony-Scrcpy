"""回归测试: 黑屏 bug 复现.

Bug 现象: MJPEG 模式下, mjpeg_client.poll 线程调 on_frame_decoded,
         触发 self.canvas.after() 在非主线程上调用, 抛
         "main thread is not in main loop" 异常, 导致 _do_render
         永远不被调度, canvas 永远黑屏.

修复: video_display 改用 _render_signal queue + 主线程 _poll_render_queue.
      main_window 调 start_main_poll 启动主线程 poll 循环.
"""
import sys, os, time, threading, queue, socket
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import tkinter as tk
import numpy as np
from PIL import Image
import http.server, urllib.request, io

# Mock server
arr_full = np.full((1920, 1080, 3), 200, dtype=np.uint8)
arr_full[100:, :, 0] = 50
buf = io.BytesIO()
Image.fromarray(arr_full).save(buf, format='JPEG', quality=85)
jpeg_bytes = buf.getvalue()

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(jpeg_bytes)))
        self.send_header('Content-Type', 'image/jpeg')
        self.end_headers()
        self.wfile.write(jpeg_bytes)
    def log_message(self, *a): pass

s = socket.socket(); s.bind(('', 0)); port = s.getsockname()[1]; s.close()
server = http.server.HTTPServer(('127.0.0.1', port), H)
threading.Thread(target=server.serve_forever, daemon=True).start()

import gui.main_window as mw_mod
import gui.video_display as vd_mod
import gui.device_controller as dc_mod
import gui.connection_manager as cm_mod
import unittest.mock

mw = mw_mod.MainWindow.__new__(mw_mod.MainWindow)
mw.root = tk.Tk(); mw.root.geometry("1450x900")
mw.video_display = None
mw.is_connected = True
mw.server_deploy_lock = threading.Lock()
mw.server_deploy_state = None
mw._setup_ui()
mw.root.update(); mw.root.update_idletasks()

class FakeHDC:
    def execute(self, *a, **kw): return {"success": True}
    def _find_hdc_path(self): return "/fake/hdc"
    def stop_async_processes(self): pass
mw.hdc_executor = FakeHDC()
mw.device_manager = type('DM', (), {
    'list_devices': lambda self: [], 'select_device': lambda self, sn: None})()
mw.device_controller = dc_mod.DeviceController(mw.hdc_executor)
mw.device_controller.bind_video_canvas(mw.video_canvas)
mw.device_controller._uitest_daemon_started = True

mw.video_display = vd_mod.VideoDisplay(
    root=mw.root, canvas=mw.video_canvas,
    device_controller=mw.device_controller, performance_label=mw.performance_label,
)

with unittest.mock.patch.object(cm_mod.ConnectionManager, '__init__', lambda self, **kw: None):
    cm = cm_mod.ConnectionManager()
    cm.device_manager = mw.device_manager
    cm.hdc_executor = mw.hdc_executor
    cm.on_frame_decoded = mw.video_display.on_frame_decoded
    cm.on_state_changed = None
    cm.debug = False
    cm._state = "connected"
    cm._lock = threading.Lock()
    cm._server_manager = None
    cm._forwarded_port = port
    from video.mjpeg_client import MjpegStreamClient
    cm.video_client = MjpegStreamClient(
        host='127.0.0.1', port=port,
        on_frame_decoded=mw.video_display.on_frame_decoded, debug=False)
    cm.get_video_client = lambda: cm.video_client

mw.video_display.connection_manager = cm
mw.connection_manager = cm

# 关键修复: 启动主线程 poll
mw.video_display.start_main_poll()

def connect_later():
    time.sleep(0.5)
    cm.video_client.connect('127.0.0.1', port)
threading.Thread(target=connect_later, daemon=True).start()

for _ in range(50):
    time.sleep(0.1)
    mw.root.update()

# 关键断言: 黑屏 bug 已修复 -> frame_count 应该 > 0, image_item_id 不为 None
assert cm.video_client.frame_count > 0, \
    f"FAIL: frame_count={cm.video_client.frame_count} (修复前会是 0)"
assert mw.device_controller.display_width > 0, \
    f"FAIL: display 未初始化"
assert mw.video_display.image_item_id is not None, \
    f"FAIL: image_item_id 为 None (修复前 _do_render 永远不被调度)"

print(f"PASS: frame_count={cm.video_client.frame_count} "
      f"display={mw.device_controller.display_width}x{mw.device_controller.display_height} "
      f"image_item_id={mw.video_display.image_item_id}")

cm.video_client.disconnect()
server.shutdown()
mw.root.destroy()
