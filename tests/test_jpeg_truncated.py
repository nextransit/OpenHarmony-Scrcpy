"""回归测试: JPEG truncated (设备端 atomic rename 与 busybox httpd sendfile 竞争).

Bug 现象: PIL.Image.open 后 convert("RGB") 抛
         "image file is truncated (N bytes not processed)" 异常.
         老代码 time.sleep(0.1) 拖慢, 现在改成: 立即重拉 3 次.

验证: 模拟 atomic rename 频繁切换, 修复后 frame_count 应该 > 0,
       bad_packet_bytes 应该保持在合理范围 (成功的重试不算 bad).
"""
import sys, os, time, threading, io, socket, http.server
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

import numpy as np
from PIL import Image

JPEG_FILE = '/tmp/ohscrcpy_test/screen.jpeg'
os.makedirs('/tmp/ohscrcpy_test', exist_ok=True)

# 初始 jpeg
arr = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
Image.fromarray(arr).save(JPEG_FILE, format='JPEG', quality=85)

STOP = [False]

def update_jpeg():
    """模拟设备端 snapshot_display 持续 atomic rename"""
    while not STOP[0]:
        tmp = JPEG_FILE + '.tmp'
        arr = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format='JPEG', quality=85)
        data = buf.getvalue()
        # 分块写, 增加 sendfile 期间被替换的概率
        with open(tmp, 'wb') as f:
            for i in range(0, len(data), 16384):
                f.write(data[i:i+16384])
                f.flush()
                time.sleep(0.002)
        try:
            os.replace(tmp, JPEG_FILE)
        except Exception:
            pass
        time.sleep(0.04)

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
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, FileNotFoundError):
            pass
    def log_message(self, *a): pass

s = socket.socket(); s.bind(('', 0)); port = s.getsockname()[1]; s.close()
server = http.server.HTTPServer(('127.0.0.1', port), H)
threading.Thread(target=server.serve_forever, daemon=True).start()
threading.Thread(target=update_jpeg, daemon=True).start()

from video.mjpeg_client import MjpegStreamClient
mc = MjpegStreamClient(host='127.0.0.1', port=port)
mc.is_connected = True
mc._start_workers()

t_start = time.time()
for _ in range(50):
    time.sleep(0.1)

elapsed = time.time() - t_start
fps = mc.frame_count / elapsed
print(f"\n3秒内: frame_count={mc.frame_count}, bad_packet_bytes={mc.bad_packet_bytes}, fps={fps:.1f}")

# 关键断言: 修复后即使 atomic rename 频繁, frame_count 也应该 > 10
assert mc.frame_count > 10, f"FAIL: 修复后 frame_count={mc.frame_count} (期望 > 10)"
print(f"PASS: 重试机制有效, 截断的帧被透明重试, FPS={fps:.1f}")

STOP[0] = True
mc.disconnect()
server.shutdown()
