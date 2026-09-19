"""端到端 FPS 测试: 模拟 poll 线程 + Tk 主线程渲染."""
import time, io, threading, http.server, queue, sys, os
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')
from PIL import Image, ImageTk
import numpy as np
import tkinter as tk
import urllib.request

arr_full = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
buf = io.BytesIO()
Image.fromarray(arr_full).save(buf, format='JPEG', quality=85)
jpeg_bytes = buf.getvalue()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(jpeg_bytes)))
        self.end_headers()
        self.wfile.write(jpeg_bytes)
    def log_message(self, *a): pass

server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

opener = urllib.request.build_opener()
DISPLAY_W, DISPLAY_H = 400, 700
frame_queue = queue.Queue(maxsize=2)
STOP = threading.Event()

def poll_thread():
    while not STOP.is_set():
        t0 = time.time()
        try:
            with opener.open(f'http://127.0.0.1:{port}/x', timeout=1) as r:
                data = r.read()
            img = Image.open(io.BytesIO(data))
            img.draft('RGB', (540, 960))
            img = img.convert('RGB').resize((DISPLAY_W, DISPLAY_H), Image.Resampling.BOX)
            arr = np.asarray(img)
            try:
                frame_queue.put_nowait(arr)
            except queue.Full:
                try: frame_queue.get_nowait()
                except queue.Empty: pass
                frame_queue.put_nowait(arr)
        except Exception:
            time.sleep(0.05)
        elapsed = time.time() - t0
        if elapsed < 0.033:
            time.sleep(0.033 - elapsed)

threading.Thread(target=poll_thread, daemon=True).start()
time.sleep(0.3)

root = tk.Tk()
canvas = tk.Canvas(root, width=DISPLAY_W, height=DISPLAY_H, bg='black')
canvas.pack(); root.update()
item = canvas.create_image(0, 0, anchor=tk.NW); canvas.update()

def render_one():
    try:
        arr = frame_queue.get(timeout=0.05)
    except queue.Empty:
        return False
    pil = Image.fromarray(arr)
    photo = ImageTk.PhotoImage(pil)
    canvas.itemconfig(item, image=photo)
    return True

t_start = time.time()
rendered = 0
while time.time() - t_start < 3.0:
    if render_one():
        rendered += 1

elapsed = time.time() - t_start
STOP.set()
server.shutdown()
root.destroy()

fps = rendered / elapsed
print(f"3s 内渲染 {rendered} 帧 -> {fps:.1f} fps")
assert fps >= 20, f"FPS 过低: {fps:.1f} (期望 >= 20)"
print("E2E FPS PASS")
