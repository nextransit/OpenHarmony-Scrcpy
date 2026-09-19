"""回归测试: 验证 video_display 用 video_client.config.width/height (真设备尺寸)
而不是 frame.shape[1] (mjpeg draft 后是 1/2 尺寸).

Bug 现象: mjpeg_client.poll 返回第一帧 shape=(540, 960) (draft 1/2),
         _do_render 把 self.video_width=540, video_height=960.
         set_display_resolution(540, 960, canvas_w, canvas_h) 用错尺寸计算 ratio,
         显示画面被严重拉伸, 看起来像"黑屏".

修复: _do_render 用 self.connection_manager.get_video_client().config.width/height,
      这是 mjpeg_client.connect 时探测到的真设备尺寸 (1080x1920).
"""
import sys, os, time, queue, socket, io, http.server, threading, unittest.mock
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import numpy as np
from PIL import Image
import tkinter as tk
import gui.video_display as vd_mod
import gui.device_controller as dc_mod
import gui.connection_manager as cm_mod

# Mock http server 发送 1080x1920 jpeg
JPEG_FILE = '/tmp/ohscrcpy_test/real_dim.jpeg'
arr = np.zeros((1920, 1080, 3), dtype=np.uint8)
arr[0:80, :, :] = [30, 60, 120]
Image.fromarray(arr).save(JPEG_FILE, format='JPEG', quality=85)
STOP = [False]
def update():
    while not STOP[0]:
        try:
            tmp = JPEG_FILE + '.tmp'
            a = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
            buf = io.BytesIO()
            Image.fromarray(a).save(buf, format='JPEG', quality=85)
            with open(tmp, 'wb') as f: f.write(buf.getvalue()); f.flush()
            os.replace(tmp, JPEG_FILE)
        except: pass
        time.sleep(0.15)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            sz = os.path.getsize(JPEG_FILE)
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(sz))
            self.end_headers()
            with open(JPEG_FILE, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
        except: pass
    def log_message(self, *a): pass
s = socket.socket(); s.bind(('', 0)); port = s.getsockname()[1]; s.close()
server = http.server.HTTPServer(('127.0.0.1', port), H)
threading.Thread(target=server.serve_forever, daemon=True).start()
threading.Thread(target=update, daemon=True).start()

# Real VideoDisplay
root = tk.Tk(); root.geometry("1450x900")
canvas = tk.Canvas(root, width=1337, height=830, bg='#0a0f1c')
canvas.pack(); root.update()

dc = dc_mod.DeviceController.__new__(dc_mod.DeviceController)
dc.hdc = type('F', (), {'execute_async': lambda *a,**k: True, 'execute': lambda *a,**k:{'success':True}, '_find_hdc_path': lambda: '/'})()
dc.video_canvas = canvas
dc._uitest_daemon_started = True
dc.drag_start = None; dc._raw_drag = False
dc.log_title = 't'
dc.device_video_width = 1080; dc.device_video_height = 1920
dc.video_width = 1080; dc.video_height = 1920
dc.display_ratio = 417/1080
dc.display_width = 417; dc.display_height = 743
dc.left = (1337-417)//2; dc.right = dc.left + 417
dc.top = 0; dc.bottom = 743
# 关键: 修复 _do_render -> device_controller.set_display_resolution 调用.
# mock 跳过 __init__, 所以 set_display_resolution 不存在, _do_render 静默失败导致 video_width=0.
# 这里手动装一个简单实现 (与真 DeviceController.set_display_resolution 一致).
def _set_display_resolution(vw, vh, cw, ch):
    dc.video_width = vw; dc.video_height = vh
    dc.device_video_width = vw; dc.device_video_height = vh
    dc.display_ratio = min(cw / vw, ch / vh)
    dc.display_width = int(vw * dc.display_ratio)
    dc.display_height = int(vh * dc.display_ratio)
    return dc.display_width, dc.display_height, dc.display_ratio
dc.set_display_resolution = _set_display_resolution

vd = vd_mod.VideoDisplay(root=root, canvas=canvas, device_controller=dc, performance_label=None)
class _MockCM:
    is_connected = True
    @staticmethod
    def get_video_client():
        return mc
vd.connection_manager = _MockCM()

from video.mjpeg_client import MjpegStreamClient
mc = MjpegStreamClient(host='127.0.0.1', port=port, on_frame_decoded=vd.on_frame_decoded)
mc.connect('127.0.0.1', port)
# mc.config 现在是 1080x1920 (真实设备尺寸)
assert mc.config.width == 1080, f"mjpeg probe 没存到 1080: {mc.config.width}"
print(f"mjpeg config: {mc.config.width}x{mc.config.height}", flush=True)

vd.start_main_poll()

# 跑 3 秒让 mjpeg pull 几帧, video_display 应该用 1080x1920 而不是 draft 540x960
start = time.time()
while time.time() - start < 3:
    time.sleep(0.3); root.update()

print(f"video_display.video_width={vd.video_width} video_height={vd.video_height}", flush=True)
print(f"video_display.display_width={vd.display_width} display_height={vd.display_height}", flush=True)
print(f"video_display.video_ratio={vd.video_ratio}", flush=True)
print(f"device_controller.video_width={dc.video_width} video_height={dc.video_height}", flush=True)
print(f"device_controller.display_ratio={dc.display_ratio}", flush=True)
print(f"mjpeg_client.frame_count={mc.frame_count}", flush=True)
print(f"video_display.image_item_id={vd.image_item_id}", flush=True)

# 关键断言: video_width/height 必须是真设备尺寸 1080x1920, 不是 draft 后 540x960
assert vd.video_width == 1080, f"FAIL: video_width={vd.video_width} 应为 1080"
assert vd.video_height == 1920, f"FAIL: video_height={vd.video_height} 应为 1920"
print(f"\nPASS: video_width/height 用真设备尺寸 (1080x1920)", flush=True)

STOP[0] = True
mc.disconnect()
server.shutdown()
root.destroy()
