"""回归测试: poll 线程在网络抖动/慢 httpd 下保持稳定 30秒.

Bug: HEARTBEAT_TIMEOUT=15 + last_data_time 仅在成功 request 后更新.
     在 RK3568 上 busybox httpd 慢 + 网络抖动时, 可能 15s 内无成功 request,
     监控线程误判超时 -> disconnect() -> poll 线程退出.
     用户看到 "FPS 0 帧 1" 后 poll 线程卡死.

修复: HEARTBEAT_TIMEOUT=60, RequestException 后也更新 last_data_time.
"""
import sys, os, time, threading, random, socket, io, http.server
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import numpy as np
from PIL import Image

JPEG_FILE = '/tmp/ohscrcpy_test/screenR30.jpeg'
Image.fromarray(np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)).save(
    JPEG_FILE, format='JPEG', quality=85)

STOP = [False]
def update():
    while not STOP[0]:
        try:
            tmp = JPEG_FILE + '.tmp'
            arr = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
            buf = io.BytesIO()
            Image.fromarray(arr).save(buf, format='JPEG', quality=85)
            with open(tmp, 'wb') as f: f.write(buf.getvalue()); f.flush()
            os.replace(tmp, JPEG_FILE)
        except: pass
        time.sleep(0.15)

# httpd 偶尔慢 1-3 秒 (模拟 RK3568)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if random.random() < 0.3:
                time.sleep(random.uniform(1, 3))
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

from video.mjpeg_client import MjpegStreamClient
mc = MjpegStreamClient(host='127.0.0.1', port=port)
ok = mc.connect('127.0.0.1', port)
assert ok, "FAIL: connect failed"

# 30 秒, 每 5 秒检查
start = time.time()
checkpoints = []
while time.time() - start < 30:
    time.sleep(5)
    checkpoints.append(mc.is_connected)
    print(f"  t={time.time()-start:.0f}s fc={mc.frame_count} is_conn={mc.is_connected}", flush=True)

assert all(checkpoints), f"FAIL: poll 线程中途退出: {checkpoints}"
assert mc.frame_count > 5, f"FAIL: 只解码 {mc.frame_count} 帧"
print(f"\nPASS: 30秒全程保持连接, 解码 {mc.frame_count} 帧", flush=True)

STOP[0] = True
mc.disconnect()
server.shutdown()
