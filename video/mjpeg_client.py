"""
MJPEG 视频流客户端 — 替代 HEVC 解码路径。
适用于 RK3568 等 OpenHarmony 设备无法使用 H.265 编码器的场景。

协议:HTTP GET http://127.0.0.1:<port>/screen.jpeg 循环拉取 JPEG 帧
- 设备端需运行 busybox httpd 提供静态文件服务
- 设备端需后台循环调用 snapshot_display 持续刷新 screen.jpeg
"""
import time
import threading
import queue
import struct
from typing import Optional, Callable, Tuple

from core.logger import print_log
from core.constants import LogLevel

try:
    import requests
except ImportError:
    requests = None
import urllib.request
import urllib.error

try:
    from PIL import Image
    import io
    import numpy as np
except ImportError:
    Image = None
    np = None


class MjpegStreamClient:
    """MJPEG 拉模式客户端 — 简单、稳定、不依赖硬件编码器"""
    
    DEFAULT_PORT = 27190
    DEFAULT_PATH = "/screen.jpeg"
    # 轮询间隔 (ms) — 自适应范围 [MIN, MAX].
    # 真实设备 (RK3568 + busybox httpd + hdc fport): 单次完整 GET 实际需要 80-150ms,
    # 如果间隔太短 (< 100ms), busybox httpd 单线程会被多个并发请求堵死, hdc 转发端口
    # rx_queue 堆积几 MB, client 一直 socket.readinto 阻塞等响应头.
    # 实测: 50ms 间隔下 poll 线程在 socket.readinto 卡死; 改为 200ms 起步, 然后自适应
    # (上次请求快就降一点, 慢就升一点) 才能稳定拉到帧.
    POLL_INTERVAL_MS_MIN = 50    # 最快 50ms (尽量贴近设备截图节奏)
    POLL_INTERVAL_MS_MAX = 600   # 最慢 600ms (网络/设备很慢时让出)
    POLL_INTERVAL_MS = 100       # 起步 100ms, 然后自适应调整
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
        on_frame_decoded: Optional[Callable] = None,
        debug: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.url = f"http://{host}:{port}{path}"
        self.on_frame_decoded = on_frame_decoded
        self.debug = debug
        
        self.is_connected: bool = False
        self.is_streaming: bool = False
        
        # 与 VideoStreamClient 兼容的字段
        self.frame_queue: "queue.Queue" = queue.Queue(maxsize=3)
        self.frame_count: int = 0
        self.decode_failure: int = 0
        self.total_bytes: int = 0
        self.last_data_time: float = 0
        self.last_frame_time: float = 0
        self.bad_packet_bytes: int = 0
        
        self.config = type("Config", (), {
            "width": 720, "height": 1280, "fps": 5,
            "bitrate": 0, "codec": "mjpeg"
        })()
        
        self._stop_event = threading.Event()
        self.poll_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None
        # 目标显示尺寸 (由 video_display 在 canvas resize 时设置)
        # poll 线程解码 JPEG 后立即 resize 到这个尺寸, 主线程 PhotoImage 开销降到 ~2ms.
        self._target_size: Tuple[int, int] = (0, 0)
        self._target_size_lock = threading.Lock()
        
        # 帧质量检测(与 VideoDecoder 同样的异常帧检测)
        self.bad_frame_count: int = 0
        self.last_bad_frame_ratio: float = 0.0
        
        self.log_title = "MJPEG客户端"
        print_log(LogLevel.INFO, self.log_title, f"初始化完成 url={self.url}")
    
    def connect(self, host: str, port: int, timeout: float = 5.0, 
                max_retries: int = 3, retry_interval: float = 1.0) -> bool:
        """连接 MJPEG 服务 (简单 HTTP 探测)"""
        for retry in range(max_retries):
            if retry > 0:
                print_log(LogLevel.INFO, self.log_title, f"连接失败,第{retry}次重试...")
                time.sleep(retry_interval)
            if self._try_connect(host, port, timeout):
                return True
        print_log(LogLevel.ERROR, self.log_title, f"连接失败,已重试{max_retries}次")
        return False
    
    def _try_connect(self, host: str, port: int, timeout: float) -> bool:
        try:
            if requests is None:
                print_log(LogLevel.ERROR, self.log_title, "需要安装 requests: pip install requests")
                return False
            self.host = host
            self.port = port
            self.url = f"http://{host}:{port}{self.path}"
            # 用 Range 请求避免下载整个文件, busybox httpd 支持 Range
            # 这样不用先 HEAD 探测 (HEAD 在 busybox httpd 上有时不稳定)
            headers = {"Range": "bytes=0-65535"}
            r = requests.get(self.url, timeout=10, stream=True, headers=headers)
            if r.status_code not in (200, 206):
                print_log(LogLevel.WARN, self.log_title, f"HTTP {r.status_code}")
                return False
            # 取前 64KB 探测 JPEG 尺寸
            content = next(r.iter_content(1024 * 64))
            try:
                img = Image.open(io.BytesIO(content))
                self.config.width, self.config.height = img.size
                print_log(LogLevel.INFO, self.log_title,
                    f"探测成功 {img.size[0]}x{img.size[1]}")
            except Exception as e:
                print_log(LogLevel.WARN, self.log_title, f"无法解析首张图片: {e}")
            # 关键: 探测完立刻关闭连接,避免占着 device busybox httpd
            r.close()
            self.is_connected = True
            self.last_data_time = time.time()
            self.last_frame_time = time.time()
            self._start_workers()
            print_log(LogLevel.INFO, self.log_title, "连接成功")
            return True
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"连接失败: {e}")
            return False
    
    def _start_workers(self) -> None:
        """启动轮询线程 + 监控线程"""
        self._stop_event.clear()
        self.is_streaming = True
        
        self.poll_thread = threading.Thread(target=self._poll_thread_func, daemon=True)
        self.poll_thread.start()
        
        self.monitor_thread = threading.Thread(target=self._monitor_thread_func, daemon=True)
        self.monitor_thread.start()
        
        print_log(LogLevel.INFO, self.log_title, "工作线程已启动")
    
    def _poll_thread_func(self) -> None:
        """轮询拉取 JPEG 帧 (复用长连接 + 自适应间隔).

        关键修复 (真设备 RK3568 + busybox httpd + hdc fport):
        - 单次完整 GET (1080p JPEG ≈ 130KB) 实际需要 80-150ms, 因为 hdc fport 中转
        - 之前用 `urllib.request.urlopen(self.url, timeout=10)` 每次新建连接,
          hdc 端口 8710 ←→ 设备 5555 的转发链路 rx_queue 堆积, client 端 socket.readinto
          永远阻塞, 导致 "闪一下就黑屏" (只有最初 3 帧能拉到)
        - 修复: 手动维护一个 http.client.HTTPConnection 长连接, 每次循环复用同一 socket
          (Connection: keep-alive), 让 hdc fport 的转发通道一直热, 减少延迟
        - 自适应间隔: 起步 200ms; 上次 request 耗时 < 80ms 就降一点 (最低 100ms);
                    上次 request 耗时 > 200ms 就升一点 (最高 1000ms).
                    卡死/超时则将间隔升到 MAX.
        """
        print_log(LogLevel.INFO, self.log_title, "轮询线程启动")
        import http.client
        current_poll_ms = self.POLL_INTERVAL_MS  # 当前间隔, 自适应调整
        # 解析 host:port (url: http://host:port/path)
        from urllib.parse import urlparse
        u = urlparse(self.url)
        http_host = u.hostname or "127.0.0.1"
        http_port = u.port or 80
        http_path = u.path or "/"
        conn = None  # 复用的长连接
        try:
            while not self._stop_event.is_set() and self.is_connected:
                t_start = time.time()
                jpeg_bytes = None
                try:
                    # 长连接复用: 第一次创建, 后续 putrequest 复用
                    if conn is None or not self._is_conn_alive(conn):
                        try:
                            if conn is not None:
                                conn.close()
                        except Exception:
                            pass
                        conn = http.client.HTTPConnection(
                            http_host, http_port, timeout=5)
                    conn.connect()
                    conn.putrequest("GET", http_path, skip_host=False)
                    conn.putheader("Host", f"{http_host}:{http_port}")
                    conn.putheader("Connection", "keep-alive")
                    conn.putheader("User-Agent", "ohscrcpy/1.0")
                    conn.endheaders()
                    resp = conn.getresponse()
                    if resp.status != 200:
                        print_log(LogLevel.WARN, self.log_title,
                            f"HTTP {resp.status}")
                        try: conn.close()
                        except Exception: pass
                        conn = None
                        time.sleep(0.3)
                        continue
                    jpeg_bytes = resp.read()
                    elapsed_ms = (time.time() - t_start) * 1000
                    # 自适应间隔: 太快降一点, 太慢升一点.
                    # 注意: hdc fport 隧道单帧传输 200-300ms (145KB JPEG) 属正常,
                    # 阈值必须高于这个数, 否则会把轮询间隔一路推满, FPS 被压到 1-3.
                    if elapsed_ms < 120:
                        current_poll_ms = max(self.POLL_INTERVAL_MS_MIN,
                                              int(current_poll_ms * 0.8))
                    elif elapsed_ms > 500:
                        current_poll_ms = min(self.POLL_INTERVAL_MS_MAX,
                                              int(current_poll_ms * 1.3))
                    self.total_bytes += len(jpeg_bytes)
                    self.last_data_time = time.time()
                    
                    # 设备端 snapshot_display 写 screen.jpeg.tmp + atomic mv 到 screen.jpeg,
                    # 与 busybox httpd 的 sendfile 之间有微小竞争窗口, 可能拿到截断字节.
                    # 旧实现用 `for retry in range(3)` + 不存在的 s.get 重拉: s 未定义会抛
                    # NameError 被吞掉, 导致该帧被丢弃、poll 间隔被拉大 (FPS 掉到 1-3).
                    # 截断是字节内容问题, 同 bytes 再解 3 次结果一样.
                    # 简化: 解一次, 失败就跳过本帧, 下一轮 poll 拉新帧 (设备端 ~150ms 覆写一次).
                    arr = None
                    last_err = None
                    try:
                        img = Image.open(io.BytesIO(jpeg_bytes))
                        img.draft("RGB", (self.config.width // 2, self.config.height // 2))
                        img = img.convert("RGB")  # 真正解码在此, truncated 在这抛
                        with self._target_size_lock:
                            tw, th = self._target_size
                        if tw > 0 and th > 0 and img.size != (tw, th):
                            img = img.resize((tw, th), Image.Resampling.BOX)
                        arr = np.asarray(img)
                    except Exception as e:
                        last_err = e
                        self.bad_packet_bytes += len(jpeg_bytes)
                        if self.bad_packet_bytes % 1000 < len(jpeg_bytes):
                            print_log(LogLevel.WARN, self.log_title,
                                f"JPEG 解析失败, 跳过本帧: {last_err}")
                        # 跳过本帧, 直接下一轮 (下一轮 poll 会拿到新 bytes)
                        continue
                    
                    # 帧质量检测 (MJPEG 模式: 设备端直出 JPEG, 几乎不会有马赛克.
                    #   阈值已经放宽到 10.0, 仅拦截极端情况 (整帧垂直条纹).
                    #   真有异常也不 sleep (会拖慢整个 poll 轮次), 仅记录 ratio 给 GUI 参考.)
                    bad, ratio = self._assess_frame_quality(arr)
                    if bad:
                        self.bad_frame_count += 1
                        self.last_bad_frame_ratio = ratio
                        if self.bad_frame_count % 10 == 1:
                            print_log(LogLevel.WARN, self.log_title,
                                f"帧质量异常: edge_y/edge_x={ratio:.2f} 累计={self.bad_frame_count}")
                        # 仍然把帧入队 (不 sleep, 不 continue), 让用户能看到画面.
                        # HEVC 严重马赛克的真正检测在 decoder.py 里, mjpeg 模式不绕过.
                    else:
                        if self.bad_frame_count > 0:
                            print_log(LogLevel.INFO, self.log_title,
                                f"帧恢复正常 (累计异常已清零)")
                            self.bad_frame_count = 0
                    
                    self.frame_count += 1
                    self.last_frame_time = time.time()

                    # MJPEG 路径: 跳过 frame_queue (旧 HEVC 路径才用),
                    # 直接调 on_frame_decoded 回调. frame_queue 在 MJPEG 模式下没人消费,
                    # put_nowait 总会抛 queue.Full, 导致 on_frame_decoded 永远不被调用.
                    if self.on_frame_decoded:
                        try:
                            self.on_frame_decoded(arr)
                        except Exception as ex:
                            print_log(LogLevel.ERROR, self.log_title,
                                f"on_frame_decoded 抛异常: {ex}")
                            import traceback
                            traceback.print_exc()
                    # 仍然 put_nowait 给 frame_queue (兼容 HEVC 模式), 但失败不致命
                    try:
                        self.frame_queue.put_nowait(arr)
                    except queue.Full:
                        try:
                            self.frame_queue.get_nowait()
                            self.frame_queue.put_nowait(arr)
                        except queue.Empty:
                            pass
                except (requests.exceptions.RequestException,
                        urllib.error.URLError, ConnectionResetError,
                        TimeoutError, OSError,
                        http.client.RemoteDisconnected,
                        http.client.BadStatusLine,
                        http.client.IncompleteRead) as e:
                    # 拉取失败时也更新 last_data_time, 防止监控线程误判超时.
                    # 真正"心跳停止"的标志是 poll 线程退出, 不是 last_data_time.
                    self.last_data_time = time.time()
                    # 拉取失败 -> 关闭连接让下次重建, 拉大间隔让设备喘息
                    try:
                        if conn is not None: conn.close()
                    except Exception: pass
                    conn = None
                    current_poll_ms = self.POLL_INTERVAL_MS_MAX
                    # 关键: 即使 debug=False 也打印前 5 次失败 + 之后每 30 次, 避免静默卡死
                    self._fail_count = getattr(self, "_fail_count", 0) + 1
                    if self._fail_count <= 5 or self._fail_count % 30 == 0:
                        print_log(LogLevel.WARN, self.log_title,
                            f"拉取失败 #{self._fail_count}: {type(e).__name__}: {e}")
                    time.sleep(0.5)
                
                # 自适应间隔控制 (current_poll_ms 已根据上次耗时调整)
                elapsed = time.time() - t_start
                sleep_s = current_poll_ms / 1000.0 - elapsed
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    # 上次请求本身就超过了 current_poll_ms, 让出 50ms 给主循环
                    time.sleep(0.05)
        except BaseException as e:
            import traceback
            tb_text = traceback.format_exc()
            print_log(LogLevel.ERROR, self.log_title,
                f"poll 顶层异常: {type(e).__name__}: {e}  tb={tb_text}")
            try:
                if conn is not None: conn.close()
            except Exception: pass
            self.is_connected = False
            raise
        finally:
            self.is_streaming = False
            print_log(LogLevel.INFO, self.log_title, "轮询线程结束")
    
    def _monitor_thread_func(self) -> None:
        """心跳超时监控"""
        print_log(LogLevel.INFO, self.log_title, "监控线程启动")
        from core.constants import HEARTBEAT_TIMEOUT
        try:
            while not self._stop_event.is_set() and self.is_connected:
                current_time = time.time()
                if self.frame_count > 0 and current_time - self.last_data_time > HEARTBEAT_TIMEOUT:
                    print_log(LogLevel.ERROR, self.log_title,
                        f"心跳超时 ({current_time - self.last_data_time:.1f}秒),断开连接")
                    self.disconnect()
                    break
                # 用 wait 替代 sleep, 断开时能立即唤醒; 同时避免每次循环刷日志
                self._stop_event.wait(1.0)
        except Exception as e:
            if not self._stop_event.is_set():
                print_log(LogLevel.ERROR, self.log_title, f"监控异常: {e}")
        # 退出日志只在真正退出时打一次 (旧实现误写在 while 内, 每秒刷屏)
        print_log(LogLevel.INFO, self.log_title,
            f"监控线程退出: stop_event={self._stop_event.is_set()} is_connected={self.is_connected}")
    
    @staticmethod
    def _is_conn_alive(conn) -> bool:
        """检查 HTTPConnection 是否还能复用 (socket 未关闭).

        简化版: 只检查 conn.sock 是否存在, 不做探针 recv.
        真实健康检查交给 poll 循环的 getresponse()/read() 来完成,
        如果连接已死, read 会抛 RemoteDisconnected/BadStatusLine.
        """
        try:
            if conn is None:
                return False
            if conn.sock is None:
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _assess_frame_quality(rgb_array) -> tuple:
        """评估 RGB 帧是否异常(垂直条纹马赛克等)"""
        try:
            if rgb_array is None or rgb_array.size == 0:
                return True, 0.0
            if rgb_array.ndim == 3 and rgb_array.shape[2] >= 3:
                gray = rgb_array[..., :3].mean(axis=-1)
            else:
                gray = rgb_array
            gy = float(abs(np.diff(gray, axis=0)).mean())
            gx = float(abs(np.diff(gray, axis=1)).mean())
            if gx <= 0.0:
                return gy > 0.5, 0.0
            ratio = gy / gx
            # 阈值放宽到 10.0: 
            # - 设备端实际桌面 / 列表 / 动画, 加上 resize 后, gy/gx 正常 1-4
            # - 原阈值 4.0 太严苛, 导致几乎所有帧被误判为 bad, 用户看到 FPS 0
            # - HEVC 严重马赛克的特征是 gy/gx > 10 (整帧垂直条纹), 这里只兜底这种极端情况
            # - 真正需要的坏帧检测在 HEVC 路径 (decoder.py 独立处理)
            return ratio > 10.0, ratio
        except Exception:
            return False, 1.0
    
    def get_current_frame(self, timeout: float = 0.001):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def set_target_size(self, width: int, height: int) -> None:
        """由 video_display 调用: 设置目标显示尺寸.

        poll 线程会立即 resize 到这个尺寸再入 frame_queue, 主线程的 PhotoImage 开销
        从 ~25ms 降到 ~2ms, 这是 FPS 提升的关键.
        """
        with self._target_size_lock:
            self._target_size = (int(width), int(height))
    
    def disconnect(self) -> None:
        self._stop_event.set()
        self.is_connected = False
        self.is_streaming = False
        
        def disconnect_async():
            if self.poll_thread and self.poll_thread.is_alive():
                self.poll_thread.join(timeout=1.0)
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=1.0)
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
            print_log(LogLevel.INFO, self.log_title, "断开连接已完成")
        
        threading.Thread(target=disconnect_async, daemon=True).start()


__all__ = ["MjpegStreamClient"]
