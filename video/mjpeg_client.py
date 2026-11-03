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
import os
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
    # MJPEG multipart 流路径: 设备端保持一条连接持续推帧.
    # 实测 (loongarch 设备, 6 个抓帧 worker):
    #   单帧轮询 /screen.jpeg   -> ~3fps  (每次都要新建 HTTP 连接 + 等新帧)
    #   multipart 流 /screen.mjpeg -> ~9.6fps (一次连接, 持续推)
    STREAM_PATH = "/screen.mjpeg"
    # 轮询间隔 (ms) — 自适应范围 [MIN, MAX].
    # 真实设备 (RK3568 + busybox httpd + hdc fport): 单次完整 GET 实际需要 80-150ms,
    # 如果间隔太短 (< 100ms), busybox httpd 单线程会被多个并发请求堵死, hdc 转发端口
    # rx_queue 堆积几 MB, client 一直 socket.readinto 阻塞等响应头.
    # 实测: 50ms 间隔下 poll 线程在 socket.readinto 卡死; 改为 200ms 起步, 然后自适应
    # (上次请求快就降一点, 慢就升一点) 才能稳定拉到帧.
    POLL_INTERVAL_MS_MIN = 50    # 最快 50ms (尽量贴近设备截图节奏)
    POLL_INTERVAL_MS_MAX = 600   # 最慢 600ms (网络/设备很慢时让出)
    POLL_INTERVAL_MS = 100       # 起步 100ms, 然后自适应调整

    # ---- 自适应分辨率 ----
    # 真机实测 (loongarch 8 核, 4 workers, 1920x1080, MJPEG multipart 端到端):
    #
    #   档位        帧率      实测接收带宽
    #   1920x1080   1.20fps   8.8 Mbps
    #   1280x720    2.20fps   8.9 Mbps
    #   960x540     3.49fps   8.3 Mbps
    #   640x360     6.59fps   4.7 Mbps
    #
    # 关键结论: **带宽在各档位几乎恒定 (8-9 Mbps)**, 帧率随分辨率线性下降.
    # 说明瓶颈不是带宽, 而是**每帧的处理成本** (snapshot_display 抓屏 + JPEG 编码
    # + 传输 + 解码), 降分辨率确实能提帧率, 但会牺牲清晰度.
    #
    # 阈值必须按**实测带宽**标定, 不能按"满帧率所需带宽"标定.
    # 旧阈值 (1080p 需 25 Mbps) 是按 16fps 理论值算的, 而设备实际只有 1-8fps,
    # 实测带宽 8-9 Mbps 永远达不到 25 -> **一直误判"带宽不足" -> 一直降级到
    # 640x360 -> 画面永久模糊** (用户手动选 1080p 也会被拉回去).
    # 现按实测重标, 留 35% 余量, 避免抖动误判.
    AUTO_RES_ENABLED = True
    # 降级阶梯 (从高到低)
    AUTO_RES_LADDER = [(1920, 1080), (1280, 720), (960, 540), (640, 360)]
    # 低于该帧率 (fps) 判定太卡, 降一级.
    #
    # [为什么用帧率而不是带宽做判据]
    # 真机实测 (loongarch 8 核, 1080p, MJPEG multipart 端到端) 各档位接收带宽
    # **几乎恒定** (8.8 / 8.9 / 8.3 / 4.7 Mbps), 帧率却随分辨率线性下降
    # (1.20 / 2.20 / 3.49 / 6.59 fps). 带宽是**结果**, 帧率才是**体感**:
    # 用带宽判据时, 1080p 需要的 5Mbps 阈值被 8.8Mbps 的实测值满足 ->
    # 永远不会降级, 用户却一直卡在 1.2fps.
    # 唯一能区分档位的信号是帧率, 所以判据改成帧率.
    #
    # 阈值取 8fps: 实测各档位**稳定**帧率 (1080p 3.6 / 720p 7.4 / 540p 11.6 /
    # 360p 19.7 fps), 8fps 落在 720p 与 540p 之间 —— 720p 不达标会降级,
    # 540p 达标后稳定, 于是系统收敛到 960x540 (~11.6fps) 这个单点.
    #
    # 升档门槛必须**明显高于**降档门槛, 否则会在两档之间来回横跳:
    # 若升档门槛 = 降档门槛, 360p(19.7fps) 满足 -> 升到 540p, 540p(11.6fps)
    # 若低于降档阈值 -> 又降回 360p, 无限震荡. 这里取 1.8 倍 (14.4fps),
    # 540p 实测 11.6fps 达不到 -> 稳定停在 540p; 链路变快时 (>14.4fps)
    # 才会继续往上走.
    AUTO_RES_MIN_FPS = 8.0
    # 升档门槛 = MIN_FPS * 该系数
    AUTO_RES_UPGRADE_RATIO = 1.8
    # 连续多少个采样窗口低于阈值才降级 (避免抖动误判)
    AUTO_RES_SLOW_WINDOWS = 2
    # 连续多少个窗口帧率富余才升档 (比降级更保守, 避免来回横跳)
    AUTO_RES_FAST_WINDOWS = 4
    # 改档后的静默期 (秒): 切换瞬间管道里还混着旧分辨率的帧,
    # 这段时间内不做任何档位判断, 否则会被过渡帧误导.
    AUTO_RES_SETTLE_S = 8.0
    
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
        # 是否使用 MJPEG multipart 流模式 (一次连接持续推帧).
        # 实测流模式 ~9.6fps vs 单帧轮询 ~3fps.
        # OHCRCPY_MJPEG_STREAM=0 可禁用.
        self.use_stream: bool = os.environ.get("OHCRCPY_MJPEG_STREAM", "1") != "0"
        # 自适应分辨率开关 (OHCRCPY_MJPEG_AUTO_RES=0 关闭, 固定分辨率)
        self.auto_res_enabled: bool = os.environ.get("OHCRCPY_MJPEG_AUTO_RES", "1") != "0"
        # 用户手动选过分辨率后锁定: 自动降级不再改回来.
        # 否则用户选 1920x1080 会被自动降级立刻拉回 640x360, 表现为"选了没用/画面很糊".
        self.resolution_locked: bool = False
        # 断线回调 (设备重启/hdc 断开/心跳超时 时通知上层).
        # 不设则上层 (GUI) 无法感知断线 -> 画面卡死但状态仍显示"已连接".
        self.on_disconnected = None
        self._disconnect_notified = False
        # 连续拉取失败多少次判定链路断开 (设备重启/hdc 掉线)
        self.MAX_CONSECUTIVE_FAILURES = 15
        self.is_streaming: bool = False
        
        # 与 VideoStreamClient 兼容的字段
        self.frame_queue: "queue.Queue" = queue.Queue(maxsize=3)
        self.frame_count: int = 0
        self.decode_failure: int = 0
        self.total_bytes: int = 0
        # 自适应分辨率状态
        self._auto_res_index: int = 0       # 当前在阶梯上的位置
        self._auto_res_slow_count: int = 0   # 连续低速窗口计数
        self._auto_res_fast_count: int = 0   # 连续高速窗口计数 (升档用)
        self._auto_res_last_check: float = 0.0
        self._auto_res_bytes_mark: int = 0
        self._auto_res_frame_mark: int = 0
        # 最近一次改档时间 (静默期内不判定)
        self._auto_res_changed_at: float = 0.0
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
        self._lifecycle_lock = threading.Lock()
        self._active_conn = None
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
            with self._lifecycle_lock:
                if self.is_connected or self.is_streaming:
                    print_log(LogLevel.INFO, self.log_title, "视频流已连接, 忽略重复连接")
                    return True
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
            # 查询设备物理分辨率 (jpeg_server 提供 /screen.info).
            # 抓帧分辨率 != 设备分辨率时, 点击坐标需要换算, 否则点歪.
            try:
                from urllib.parse import urlparse as _up
                _u = _up(self.url)
                _info_url = f"{_u.scheme}://{_u.netloc}/screen.info"
                _ri = requests.get(_info_url, timeout=5)
                if _ri.status_code == 200:
                    _j = _ri.json()
                    self.config.device_width = int(_j.get("width", 0) or 0)
                    self.config.device_height = int(_j.get("height", 0) or 0)
                    cap_w = int(_j.get("captureWidth", 0) or 0)
                    cap_h = int(_j.get("captureHeight", 0) or 0)
                    print_log(LogLevel.INFO, self.log_title,
                        f"设备物理分辨率 {self.config.device_width}x{self.config.device_height} "
                        f"(抓帧 {cap_w or self.config.width}x{cap_h or self.config.height})")
                    # 自适应阶梯起点对齐到设备实际抓帧分辨率
                    if cap_w > 0 and cap_h > 0:
                        for i, (lw, lh) in enumerate(self.AUTO_RES_LADDER):
                            if lw == cap_w and lh == cap_h:
                                self._auto_res_index = i
                                break
                        else:
                            # 不在阶梯上 (设备用了个特殊分辨率) -> 插到最接近的位置
                            self._auto_res_index = 0
                            self.AUTO_RES_LADDER.insert(0, (cap_w, cap_h))
            except Exception as e:
                print_log(LogLevel.WARN, self.log_title, f"获取设备分辨率失败(忽略): {e}")
            self.is_connected = True
            self._disconnect_notified = False
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
        """拉取 JPEG 帧.

        优先用 MJPEG multipart 流 (一次连接持续推帧, 帧率高);
        不可用时回退到单帧轮询 (短连接 + 自适应间隔).
        """
        if self.use_stream:
            if self._stream_loop():
                return
            print_log(LogLevel.WARN, self.log_title,
                "MJPEG 流模式失败, 回退单帧轮询")
        self._poll_loop()

    def _stream_loop(self) -> bool:
        """MJPEG multipart 流模式: 一次连接, 持续读帧.

        设备端 jpeg_server 的 /screen.mjpeg 返回:
            Content-Type: multipart/x-mixed-replace; boundary=OHScrcpyFrame
            每帧: --OHScrcpyFrame\r\nContent-Length: N\r\n\r\n<JPEG bytes>\r\n

        返回 True 表示流模式正常结束 (应退出), False 表示应回退到轮询.
        """
        from urllib.parse import urlparse
        u = urlparse(self.url)
        http_host = u.hostname or "127.0.0.1"
        http_port = u.port or 80
        boundary = b"--OHScrcpyFrame"

        import http.client
        try:
            conn = http.client.HTTPConnection(http_host, http_port, timeout=10)
            with self._lifecycle_lock:
                self._active_conn = conn
            conn.putrequest("GET", self.STREAM_PATH, skip_host=False)
            conn.putheader("Host", f"{http_host}:{http_port}")
            conn.putheader("Connection", "close")
            conn.putheader("User-Agent", "ohscrcpy/1.0")
            conn.endheaders()
            resp = conn.getresponse()
            if resp.status != 200:
                print_log(LogLevel.WARN, self.log_title, f"流模式 HTTP {resp.status}")
                try: conn.close()
                except Exception: pass
                return False

            print_log(LogLevel.INFO, self.log_title,
                "MJPEG 流模式已连接 (multipart, 持续推帧)")
            buf = bytearray()
            # socket 读超时: 半开连接 (对端失联但 TCP 未断) 时靠它兜底,
            # 否则 read1 会一直阻塞, 永远等不到异常.
            try:
                resp.fp.raw._sock.settimeout(15)
            except Exception:
                pass
            last_rx = time.time()
            while not self._stop_event.is_set() and self.is_connected:
                try:
                    # 必须用 read1 而不是 read: read(n) 会阻塞直到读满 n 字节,
                    # 对持续推送的流式响应会一直卡住 (实测 0 帧).
                    # read1 最多读一次 socket 缓冲里已有的数据, 立即返回.
                    if hasattr(resp, "read1"):
                        chunk = resp.read1(65536)
                    else:
                        chunk = resp.read(65536)
                except Exception as e:
                    if self._stop_event.is_set() or not self.is_connected:
                        break
                    # 通信错误 (设备重启/网络断开/hdc 掉线) — 立即判定断开并通知上层.
                    # 旧实现直接 break, 既不置 last_data_time=0 也不通知,
                    # 结果要等满 HEARTBEAT_TIMEOUT (默认 60s) 才发现断线.
                    print_log(LogLevel.ERROR, self.log_title,
                        f"流读取失败: {type(e).__name__}: {e}")
                    self._fail_count = getattr(self, "_fail_count", 0) + 1
                    self.last_data_time = 0.0   # 心跳归零 -> 监控线程立即判定断开
                    self._notify_disconnected(f"通信错误: {type(e).__name__}: {e}")
                    break
                if not chunk:
                    # 服务端关闭连接 (设备端 jpeg_server 被杀/重启)
                    if not self._stop_event.is_set() and self.is_connected:
                        print_log(LogLevel.ERROR, self.log_title,
                            "视频流连接被服务端关闭 (设备端服务可能已停止)")
                        self.last_data_time = 0.0
                        self._notify_disconnected("视频流连接被服务端关闭")
                    break

                buf.extend(chunk)
                self.total_bytes += len(chunk)
                self.last_data_time = time.time()
                last_rx = time.time()
                self._fail_count = 0

                # 从缓冲区切出完整帧.
                # 注意: 数据不足时必须"保留现场等下一批", 不能丢弃已解析的头部 —
                # 否则后续再也拼不出完整帧 (实测 0 帧).
                while True:
                    pos = buf.find(boundary)
                    if pos < 0:
                        # 防止缓冲无限增长
                        if len(buf) > 4 * 1024 * 1024:
                            del buf[:-1024 * 1024]
                        break
                    # part 头部: boundary 之后到 \r\n\r\n
                    head_start = pos + len(boundary)
                    head_end = buf.find(b"\r\n\r\n", head_start)
                    if head_end < 0:
                        break  # 头部还没收全, 等下一批
                    header = bytes(buf[head_start:head_end]).decode("latin-1", "ignore")
                    length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try: length = int(line.split(":", 1)[1].strip())
                            except ValueError: length = 0
                            break
                    if length <= 0:
                        # 没有 Content-Length: 丢弃这个 part, 继续找下一个 boundary
                        del buf[:head_end + 4]
                        continue
                    body_start = head_end + 4
                    if len(buf) < body_start + length:
                        break  # 帧体还没收全, 等下一批 (头部保留在 buf 里)
                    jpeg_bytes = bytes(buf[body_start:body_start + length])
                    del buf[:body_start + length]
                    # 吃掉帧尾的 CRLF
                    if len(buf) >= 2 and buf[:2] == b"\r\n":
                        del buf[:2]
                    self._handle_jpeg(jpeg_bytes)
            try: conn.close()
            except Exception: pass
            return True
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"流模式异常: {type(e).__name__}: {e}")
            return False

    def _handle_jpeg(self, jpeg_bytes: bytes) -> bool:
        """解码一帧 JPEG 并交给上层 (轮询模式与流模式共用).

        返回 True 表示这帧处理成功.
        """
        arr = None
        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            # 优化: 跳过 draft (draft 只节省内存不省 CPU).
            # 直接 convert 解码 + resize 到目标尺寸, 实测 4ms/帧.
            img = img.convert("RGB")
            with self._target_size_lock:
                tw, th = self._target_size
            if tw > 0 and th > 0 and img.size != (tw, th):
                img = img.resize((tw, th), Image.Resampling.BILINEAR)
            arr = np.asarray(img)
        except Exception as e:
            self.bad_packet_bytes += len(jpeg_bytes)
            if self.bad_packet_bytes % 1000 < len(jpeg_bytes):
                print_log(LogLevel.WARN, self.log_title,
                    f"JPEG 解析失败, 跳过本帧: {e}")
            return False

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
        else:
            if self.bad_frame_count > 0:
                print_log(LogLevel.INFO, self.log_title,
                    f"帧恢复正常 (累计异常已清零)")
                self.bad_frame_count = 0

        self.frame_count += 1
        self.last_frame_time = time.time()
        # 自适应分辨率: 定期根据接收速率决定是否降级
        self._check_auto_resolution()

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
            except queue.Empty:
                pass
        return True

    def _poll_loop(self) -> None:
        """轮询拉取 JPEG 帧 (短连接 + 自适应间隔).

        关键修复 (真设备 RK3568 + busybox httpd + hdc fport):
        - 单次完整 GET (1080p JPEG ≈ 130KB) 实际需要 80-150ms, 因为 hdc fport 中转
        - 之前用 `urllib.request.urlopen(self.url, timeout=10)` 每次新建连接,
          hdc 端口 8710 ←→ 设备 5555 的转发链路 rx_queue 堆积, client 端 socket.readinto
          永远阻塞, 导致 "闪一下就黑屏" (只有最初 3 帧能拉到)
        - 目标设备 busybox httpd 对 keep-alive 支持不稳定, 每帧使用短连接,
          避免复用半关闭 socket 导致 IncompleteRead/RemoteDisconnected.
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
        try:
            while not self._stop_event.is_set() and self.is_connected:
                t_start = time.time()
                jpeg_bytes = None
                conn = None
                try:
                    # busybox httpd 在目标设备上经常主动关闭 keep-alive.
                    # 每帧使用一个短连接, 能避免复用半关闭 socket 导致
                    # IncompleteRead/RemoteDisconnected 后长期黑屏.
                    conn = http.client.HTTPConnection(http_host, http_port, timeout=5)
                    with self._lifecycle_lock:
                        self._active_conn = conn
                    conn.putrequest("GET", http_path, skip_host=False)
                    conn.putheader("Host", f"{http_host}:{http_port}")
                    conn.putheader("Connection", "close")
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
                    self._fail_count = 0
                    
                    # 设备端 snapshot_display 写 screen.jpeg.tmp + atomic mv 到 screen.jpeg,
                    # 与 busybox httpd 的 sendfile 之间有微小竞争窗口, 可能拿到截断字节.
                    # 旧实现用 `for retry in range(3)` + 不存在的 s.get 重拉: s 未定义会抛
                    # NameError 被吞掉, 导致该帧被丢弃、poll 间隔被拉大 (FPS 掉到 1-3).
                    # 截断是字节内容问题, 同 bytes 再解 3 次结果一样.
                    # 简化: 解一次, 失败就跳过本帧, 下一轮 poll 拉新帧 (设备端 ~150ms 覆写一次).
                    self._handle_jpeg(jpeg_bytes)
                except (requests.exceptions.RequestException,
                        urllib.error.URLError, ConnectionResetError,
                        TimeoutError, OSError,
                        http.client.RemoteDisconnected,
                        http.client.BadStatusLine,
                        http.client.IncompleteRead) as e:
                    # 连续失败计数: 达到阈值说明链路真的断了 (设备重启/hdc 掉线),
                    # 此时不能继续刷新 last_data_time — 否则心跳监控永远等不到超时,
                    # 表现为"画面卡死 + 状态仍显示已连接".
                    self._fail_count = getattr(self, "_fail_count", 0) + 1
                    if self._fail_count < self.MAX_CONSECUTIVE_FAILURES:
                        # 偶发失败 (设备端 atomic rename 竞争等) — 保持心跳新鲜, 继续重试
                        self.last_data_time = time.time()
                    else:
                        self.last_data_time = 0.0  # 心跳归零 -> 监控线程立即判定断开
                    # 拉取失败 -> 关闭连接让下次重建, 短退避后立即重试.
                    # 旧实现用 POLL_INTERVAL_MS_MAX (600ms) + sleep 0.5s, 慢,
                    # 看起来像"连接成功 -> 立即黑屏". 设备端 atomic rename 是 ~150ms
                    # 周期, 200ms 退避足以避开下一次 rename 竞争窗口.
                    try:
                        if conn is not None: conn.close()
                    except Exception: pass
                    current_poll_ms = min(self.POLL_INTERVAL_MS_MAX, 200)
                    time.sleep(0.05)  # 极短退避, 立即重试
                    # 关键: 即使 debug=False 也打印前 5 次失败 + 之后每 30 次, 避免静默卡死
                    if self._fail_count <= 5 or self._fail_count % 30 == 0:
                        print_log(LogLevel.WARN, self.log_title,
                            f"拉取失败 #{self._fail_count}: {type(e).__name__}: {e}")
                    time.sleep(0.1)
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    with self._lifecycle_lock:
                        if self._active_conn is conn:
                            self._active_conn = None
                
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
            self.is_connected = False
            raise
        finally:
            self.is_streaming = False
            print_log(LogLevel.INFO, self.log_title, "轮询线程结束")
        # 线程异常退出 = 链路已断, 通知上层 (GUI 会显示"连接已断开"并尝试恢复)
        self._notify_disconnected("视频流读取线程退出")
    
    def _notify_disconnected(self, reason: str) -> None:
        """通知上层连接已断开 (幂等, 只通知一次)."""
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        cb = self.on_disconnected
        if cb is not None:
            try:
                cb(reason)
            except Exception as e:
                print_log(LogLevel.WARN, self.log_title, f"断线回调异常: {e!r}")

    def _monitor_thread_func(self) -> None:
        """心跳超时监控"""
        print_log(LogLevel.INFO, self.log_title, "监控线程启动")
        from core.constants import HEARTBEAT_TIMEOUT
        try:
            while not self._stop_event.is_set() and self.is_connected:
                current_time = time.time()
                # last_data_time == 0 表示 poll 线程连续失败已达阈值 (链路确认断开)
                if self.frame_count > 0 and (
                    self.last_data_time == 0.0
                    or current_time - self.last_data_time > HEARTBEAT_TIMEOUT
                ):
                    reason = ("连续拉取失败" if self.last_data_time == 0.0
                              else f"心跳超时 {current_time - self.last_data_time:.0f}秒无数据")
                    print_log(LogLevel.ERROR, self.log_title, f"{reason},断开连接")
                    self._notify_disconnected(reason)
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

    def _check_auto_resolution(self) -> None:
        """按实测帧率自动调整抓帧分辨率.

        帧率过低 (低于 AUTO_RES_MIN_FPS) 且连续多个窗口都这样,
        就降一级分辨率. 设备端 jpeg_server 支持 GET /screen.res?w=&h= 运行时调整,
        不需要重启服务.

        判据用帧率而非带宽: 真机实测各档位带宽几乎恒定 (8-9 Mbps), 只有帧率
        能区分档位好坏. 详见 AUTO_RES_MIN_FPS 的注释.
        """
        if not self.AUTO_RES_ENABLED or not self.auto_res_enabled:
            return
        if self.resolution_locked:
            # 用户手动指定过分辨率 -> 不再自动调整 (用户意图优先).
            return
        now = time.time()
        if now - self._auto_res_last_check < 3.0:
            return
        window = now - self._auto_res_last_check
        self._auto_res_last_check = now

        # 窗口内的接收速率 + 帧率
        bytes_in_window = self.total_bytes - self._auto_res_bytes_mark
        self._auto_res_bytes_mark = self.total_bytes
        frames_in_window = self.frame_count - self._auto_res_frame_mark
        self._auto_res_frame_mark = self.frame_count
        mbps = bytes_in_window * 8 / window / 1_000_000.0
        fps = frames_in_window / window if window > 0 else 0.0

        # 改档后的静默期: 管道里混着旧分辨率的过渡帧, 此时判定不可信.
        if self._auto_res_changed_at and (now - self._auto_res_changed_at) < self.AUTO_RES_SETTLE_S:
            self._auto_res_slow_count = 0
            self._auto_res_fast_count = 0
            self._auto_res_frame_mark = self.frame_count
            return

        threshold = self.AUTO_RES_MIN_FPS
        if fps < threshold:
            self._auto_res_slow_count += 1
            self._auto_res_fast_count = 0
        else:
            self._auto_res_slow_count = 0
            # 帧率明显富余 (超过升档门槛) 且已降过档 -> 尝试升回去.
            # 升档门槛明显高于降档门槛 (AUTO_RES_UPGRADE_RATIO), 避免来回横跳.
            if self._auto_res_index > 0 and fps >= threshold * self.AUTO_RES_UPGRADE_RATIO:
                self._auto_res_fast_count += 1
            else:
                self._auto_res_fast_count = 0

        if self._auto_res_slow_count >= self.AUTO_RES_SLOW_WINDOWS:
            self._auto_res_slow_count = 0
            if self._auto_res_index + 1 < len(self.AUTO_RES_LADDER):
                self._auto_res_index += 1
                w, h = self.AUTO_RES_LADDER[self._auto_res_index]
                print_log(LogLevel.WARN, self.log_title,
                    f"帧率偏低 ({fps:.1f} fps < {threshold:.0f}, {mbps:.0f} Mbps), "
                    f"自动降级分辨率到 {w}x{h}")
                if self._request_resolution(w, h):
                    # 设备端已改档, 必须同步 config —— 它是点击坐标换算的基准,
                    # 也是 set_capture_resolution 判断"是否已是该分辨率"的依据.
                    # 不同步会导致: 手动切回高分辨率时被误判为"已是"而什么都不做.
                    self.config.width = w
                    self.config.height = h
                    self._auto_res_changed_at = now
            else:
                print_log(LogLevel.WARN, self.log_title,
                    f"帧率偏低 ({fps:.1f} fps < {threshold:.0f}) "
                    f"且已到最低分辨率 {self.AUTO_RES_LADDER[-1]}")
        elif self._auto_res_fast_count >= self.AUTO_RES_FAST_WINDOWS:
            self._auto_res_fast_count = 0
            self._auto_res_index -= 1
            w, h = self.AUTO_RES_LADDER[self._auto_res_index]
            print_log(LogLevel.INFO, self.log_title,
                f"帧率恢复 ({fps:.1f} fps, {mbps:.0f} Mbps), 自动升回分辨率 {w}x{h}")
            if self._request_resolution(w, h):
                self.config.width = w
                self.config.height = h
                self._auto_res_changed_at = now

    def _request_resolution(self, width: int, height: int) -> bool:
        """请求设备端调整抓帧分辨率 (GET /screen.res?w=&h=).

        注意: 必须以 /screen.res 的**返回值**为准, 不能用 /screen.info ——
        设备端 jpeg_server 的 /screen.info 返回的是**启动参数** g_width/g_height,
        不是运行时的 g_capture_width/g_capture_height (设备端源码 bug).
        实测: 设备端已降到 640x360 时, /screen.info 仍报 1920x1080,
              而 /screen.res 正确返回 captureWidth=640.
        """
        from urllib.parse import urlparse as _up
        u = _up(self.url)
        url = f"{u.scheme}://{u.netloc}/screen.res?w={int(width)}&h={int(height)}"
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                actual = self._parse_capture_size(r)
                if actual:
                    width, height = actual
                print_log(LogLevel.INFO, self.log_title,
                    f"设备端分辨率已调整为 {width}x{height}")
                return True
            print_log(LogLevel.WARN, self.log_title,
                f"设备端分辨率调整失败: HTTP {r.status_code}")
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"设备端分辨率调整异常: {e!r}")
        return False

    @staticmethod
    def _parse_capture_size(response) -> Optional[Tuple[int, int]]:
        """从 /screen.res 响应里解析设备端实际抓帧尺寸."""
        try:
            j = response.json()
            cw = int(j.get("captureWidth", 0) or 0)
            ch = int(j.get("captureHeight", 0) or 0)
            if cw > 0 and ch > 0:
                return (cw, ch)
        except Exception:
            pass
        return None

    def query_capture_resolution(self) -> Optional[Tuple[int, int]]:
        """查询设备端**当前实际**抓帧分辨率.

        用 /screen.res (不带参数) 而非 /screen.info —— 后者返回的是启动参数,
        不反映运行时实际值 (设备端 jpeg_server 的已知 bug).
        """
        from urllib.parse import urlparse as _up
        u = _up(self.url)
        url = f"{u.scheme}://{u.netloc}/screen.res"
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                size = self._parse_capture_size(r)
                if size:
                    return size
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title,
                f"查询设备端实际分辨率失败: {e!r}")
        return None

    def get_capture_resolution(self) -> Tuple[int, int]:
        """当前抓帧分辨率 (设备端实际生效的尺寸)."""
        return (int(self.config.width), int(self.config.height))

    def set_capture_resolution(self, width: int, height: int) -> bool:
        """手动切换抓帧分辨率 (用户主动选择, 不需要断流).

        设备端 jpeg_server 支持 GET /screen.res?w=&h= 运行时调整, worker 每轮
        读取新尺寸, 无需重启服务.

        注意: config.width/height 必须同步更新 —— 它是点击坐标换算的基准
        (device_controller 用 device_width / config.width 算 input_scale),
        不同步会导致切完分辨率后点击位置偏移.
        """
        width = int(width)
        height = int(height)
        if width <= 0 or height <= 0:
            print_log(LogLevel.WARN, self.log_title,
                f"无效的分辨率 {width}x{height}, 已忽略")
            return False

        # 以**设备端实际值**判断, 不能用本地 config ——
        # 自动降级只改设备端, 本地 config 可能过期, 导致误判"已是该分辨率"
        # 而什么都不做 (实测: 设备端已降到 640x360, 用户选 1920x1080 却提示"无需切换").
        actual = self.query_capture_resolution()
        if actual == (width, height):
            print_log(LogLevel.INFO, self.log_title,
                f"设备端分辨率已是 {width}x{height}, 无需切换")
            self.config.width = width
            self.config.height = height
            # 手动选择同样要锁定: 否则用户选了 1080p, 自动降级下一窗口就把它
            # 改回 540p (表现为"选了没用, 画面很糊").
            self.resolution_locked = True
            self.auto_res_enabled = False
            return True

        if not self._request_resolution(width, height):
            return False

        # 同步客户端侧的抓帧分辨率 (点击坐标换算基准).
        final = self.query_capture_resolution() or (width, height)
        self.config.width = final[0]
        self.config.height = final[1]
        # 手动切换后锁定分辨率: 否则下一带宽窗口又会把分辨率改回去,
        # 用户选了 1920x1080 却被拉回 640x360 (表现为"选了没用, 画面很糊").
        self.resolution_locked = True
        self.auto_res_enabled = False
        width, height = self.config.width, self.config.height
        # 同步自动降级阶梯的当前位置, 避免关闭自动降级后索引停留在旧档.
        for i, (lw, lh) in enumerate(self.AUTO_RES_LADDER):
            if (lw, lh) == (width, height):
                self._auto_res_index = i
                break
        print_log(LogLevel.INFO, self.log_title,
            f"分辨率已切换为 {width}x{height} (自动降级已关闭)")
        return True

    def set_target_size(self, width: int, height: int) -> None:
        """由 video_display 调用: 设置目标显示尺寸.

        poll 线程会立即 resize 到这个尺寸再入 frame_queue, 主线程的 PhotoImage 开销
        从 ~25ms 降到 ~2ms, 这是 FPS 提升的关键.
        """
        with self._target_size_lock:
            self._target_size = (int(width), int(height))
    
    def disconnect(self) -> None:
        print_log(LogLevel.INFO, self.log_title, "开始断开视频流")
        self._stop_event.set()
        self.is_connected = False
        self.is_streaming = False
        with self._lifecycle_lock:
            active_conn = self._active_conn
        if active_conn is not None:
            try:
                active_conn.close()
            except Exception:
                pass

        current = threading.current_thread()
        for worker in (self.poll_thread, self.monitor_thread):
            if worker and worker.is_alive() and worker is not current:
                worker.join(timeout=1.0)
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        print_log(LogLevel.INFO, self.log_title, "断开连接已完成")


__all__ = ["MjpegStreamClient"]
