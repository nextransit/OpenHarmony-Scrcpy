#!/usr/bin/env python

# Copyright (c) 2026 luodh.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""
H.264 视频流客户端 - 方案 D 实施.

传输协议: HTTP GET http://127.0.0.1:<port>/screen.h264 bytestream, Annex-B H.264 NAL 流.
设备端 oh264_streamer 把屏幕抓帧 (snapshot_display) 编码成 H.264 后写到 TCP socket,
由本客户端拉取并按 start code 切片, 喂给 VideoDecoder (PyAV) 解码出 RGB 帧.

设计参考 MjpegStreamClient 的 API 表面, 便于在 connection_manager 里无差异替换:
  - host / port / path / on_frame_decoded / debug
  - connect() / disconnect() / _start_workers()
  - is_connected / frame_count / last_data_time / config

区别:
  - MJPEG 客户端每次 read() 拿到一帧完整 JPEG; H.264 客户端拿到字节流, 必须
    按 start code 边界切 NAL, 按 NAL 类型分派 (SPS/PPS -> 参数缓存; IDR/P -> 解码).
  - 解码前需要先收到 SPS+PPS, 才能解码 IDR. 因此帧延迟到第一次 IDR 出现.
"""

import time
import threading
import queue
import http.client
import urllib.parse
from typing import Optional, Callable, Tuple

from core.constants import LogLevel
from core.logger import print_log
from .config import VideoStreamConfig, H264_STREAM_PORT
from .decoder import VideoDecoder


# H.264 NAL unit type (Annex B, 5 bits after 0001)
NALTYPE_SPS = 7
NALTYPE_PPS = 8
NALTYPE_IDR = 5       # IDR slice (keyframe)
NALTYPE_NON_IDR = 1   # P/B slice

# 用 bytes([...]) 构造避免字符串转义噩梦
_SC4 = bytes([0x00, 0x00, 0x00, 0x01])  # 4-byte start code
_SC3 = bytes([0x00, 0x00, 0x01])         # 3-byte start code


class H264StreamClient:
    """H.264 拉模式客户端 - 从设备端 HTTP 拉 Annex-B 字节流并解码."""

    POLL_INTERVAL_MS_MIN = 16
    POLL_INTERVAL_MS_MAX = 1000
    POLL_INTERVAL_MS = 50

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = H264_STREAM_PORT,
        path: str = "/screen.h264",
        on_frame_decoded: Optional[Callable] = None,
        debug: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.url = f"http://{host}:{port}{path}"
        self.on_frame_decoded = on_frame_decoded
        self.debug = debug
        self.log_title = "H264客户端"

        self.is_connected: bool = False
        self.is_streaming: bool = False
        self.frame_queue: "queue.Queue" = queue.Queue(maxsize=3)
        self.frame_count: int = 0
        self.decode_success: int = 0
        self.decode_failure: int = 0
        self.total_bytes: int = 0
        self.last_data_time: float = 0
        self.last_frame_time: float = 0
        self.bad_packet_bytes: int = 0

        self.config = VideoStreamConfig(codec="h264")
        self.decoder = VideoDecoder(self.config, debug=debug)

        self._stop_event = threading.Event()
        self.poll_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None

        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._target_size: Tuple[int, int] = (0, 0)
        self._target_size_lock = threading.Lock()

        print_log(LogLevel.INFO, self.log_title, f"初始化完成 url={self.url}")

    def set_target_size(self, width: int, height: int) -> None:
        with self._target_size_lock:
            self._target_size = (int(width), int(height))

    def connect(self, host: str, port: int, timeout: float = 5.0,
                max_retries: int = 3, retry_interval: float = 1.0) -> bool:
        for retry in range(max_retries):
            if retry > 0:
                time.sleep(retry_interval)
            if self._try_connect(host, port, timeout):
                return True
        print_log(LogLevel.ERROR, self.log_title, f"连接失败,已重试{max_retries}次")
        return False

    def _try_connect(self, host: str, port: int, timeout: float) -> bool:
        try:
            self.host = host
            self.port = port
            self.url = f"http://{host}:{port}{self.path}"
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.request("GET", self.path)
            resp = conn.getresponse()
            if resp.status not in (200, 206):
                print_log(LogLevel.WARN, self.log_title, f"HTTP {resp.status}")
                conn.close()
                return False
            probe = resp.read(65536)
            conn.close()
            if not probe or len(probe) < 8:
                print_log(LogLevel.WARN, self.log_title, "服务端响应为空或过短")
                return False
            if probe[:4] != _SC4 and probe[:3] != _SC3:
                print_log(LogLevel.WARN, self.log_title,
                    f"响应不是 Annex-B H.264 (头: {probe[:8].hex()})")
                return False
            self._feed_parameter_sets(probe)
            self.is_connected = True
            self.last_data_time = time.time()
            self.last_frame_time = time.time()
            self._stop_event.clear()
            self._start_workers()
            print_log(LogLevel.INFO, self.log_title, "连接成功")
            return True
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"连接异常: {e}")
            return False

    def _feed_parameter_sets(self, data: bytes) -> None:
        """从流首部提取 SPS/PPS 并配置解码器.

        设备端 GOP 轮换时会把 SPS/PPS 冲掉, 每轮拉到的数据往往只有 IDR/P.
        因此 connect 探测时缓存 SPS/PPS, 后续当轮缺参数时复用缓存.
        """
        sps = pps = None
        nals = self._split_nalus(data)
        for nal in nals:
            if len(nal) < 2:
                continue
            ntype = nal[0] & 0x1F
            if ntype == NALTYPE_SPS:
                sps = nal
            elif ntype == NALTYPE_PPS:
                pps = nal
        if sps:
            self._sps = sps
        if pps:
            self._pps = pps
        # 优先取当轮参数, 否则复用连接探测时的缓存
        use_sps = sps or self._sps
        use_pps = pps or self._pps
        if use_sps and use_pps:
            self.decoder.extradata = bytearray(use_sps + use_pps)
            self.decoder.sps_data = use_sps
            self.decoder.pps_data = use_pps
            self.decoder.sps_received = True
            self.decoder.pps_received = True
            if self.decoder.codec_ctx is None:
                self.decoder._init_codec()
            elif self.decoder.extradata and self.decoder.codec_ctx.extradata != bytes(self.decoder.extradata):
                # 参数集有变化才重建 codec
                self.decoder._init_codec()

    def _start_workers(self) -> None:
        self._stop_event.clear()
        self.is_streaming = True
        self.poll_thread = threading.Thread(target=self._poll_thread_func, daemon=True)
        self.poll_thread.start()
        self.monitor_thread = threading.Thread(target=self._monitor_thread_func, daemon=True)
        self.monitor_thread.start()
        print_log(LogLevel.INFO, self.log_title, "工作线程已启动")

    @staticmethod
    def _split_nalus(data: bytes) -> list:
        """按 Annex-B start code 拆分 NAL units."""
        if not data:
            return []
        starts = []
        i = 0
        n = len(data)
        while i < n - 2:
            if data[i:i + 3] == _SC3:
                if i + 3 < n and data[i + 3] == 0:
                    # 4-byte start code (00 00 00 01)
                    if i + 4 < n:
                        starts.append(i + 4)
                    i += 4
                else:
                    # 3-byte start code (00 00 01)
                    starts.append(i + 3)
                    i += 3
                continue
            i += 1
        if not starts:
            return [data]
        out = []
        for idx, s in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else n
            out.append(data[s:end])
        return out

    def _poll_thread_func(self) -> None:
        current_poll_ms = self.POLL_INTERVAL_MS
        try:
            while not self._stop_event.is_set() and self.is_connected:
                t_start = time.time()
                try:
                    decoded = self._fetch_and_decode_once()
                    elapsed_ms = (time.time() - t_start) * 1000
                    if elapsed_ms < 50:
                        current_poll_ms = max(self.POLL_INTERVAL_MS_MIN,
                                              int(current_poll_ms * 0.8))
                    elif elapsed_ms > 500:
                        current_poll_ms = min(self.POLL_INTERVAL_MS_MAX,
                                              int(current_poll_ms * 1.3))
                    if decoded:
                        self.last_data_time = time.time()
                except Exception as e:
                    print_log(LogLevel.WARN, self.log_title, f"拉取异常: {type(e).__name__}: {e}")
                    self.last_data_time = time.time()
                    self._stop_event.wait(0.5)
                    current_poll_ms = self.POLL_INTERVAL_MS_MAX

                elapsed = time.time() - t_start
                sleep_s = current_poll_ms / 1000.0 - elapsed
                if sleep_s > 0:
                    self._stop_event.wait(sleep_s)
                else:
                    self._stop_event.wait(0.05)
        except BaseException as e:
            import traceback
            print_log(LogLevel.ERROR, self.log_title,
                f"poll 顶层异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            self.is_connected = False
            raise
        finally:
            self.is_streaming = False
            print_log(LogLevel.INFO, self.log_title, "轮询线程结束")

    def _fetch_and_decode_once(self) -> bool:
        """一次拉取 + 解码循环. 返回 True 表示至少 1 帧解码成功."""
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
            conn.request("GET", self.path, headers={"Connection": "close"})
            resp = conn.getresponse()
            if resp.status not in (200, 206):
                conn.close()
                return False
            data = b""
            try:
                while len(data) < 256 * 1024:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    data += chunk
            except Exception:
                pass
            conn.close()
        except Exception:
            return False

        if not data:
            return False
        self.total_bytes += len(data)

        # 累计 SPS/PPS 到解码器 extradata (不重建 codec, 首次若无参数集由 decode_block 处理)
        self._feed_parameter_sets(data)
        # 整块含 start code 的 GOP 交给 PyAV 自解析
        frames = self.decoder.decode_block(data)
        any_decoded = False
        for arr in frames:
            if arr is None:
                continue
            self.frame_count += 1
            # resize 到目标尺寸
            try:
                from PIL import Image
                import numpy as np
                pil = Image.fromarray(arr)
                with self._target_size_lock:
                    tw, th = self._target_size
                if tw > 0 and th > 0 and pil.size != (tw, th):
                    pil = pil.resize((tw, th), Image.Resampling.BOX)
                arr2 = np.asarray(pil)
            except Exception:
                arr2 = arr
            self.last_frame_time = time.time()
            if self.on_frame_decoded:
                try:
                    self.on_frame_decoded(arr2)
                except Exception as ex:
                    print_log(LogLevel.WARN, self.log_title, f"on_frame_decoded 抛异常: {ex}")
            try:
                self.frame_queue.put_nowait(arr2)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait(arr2)
                except queue.Empty:
                    pass
            any_decoded = True
        return any_decoded

    def _monitor_thread_func(self) -> None:
        from core.constants import HEARTBEAT_TIMEOUT
        try:
            while not self._stop_event.is_set() and self.is_connected:
                if self.frame_count > 0 and time.time() - self.last_data_time > HEARTBEAT_TIMEOUT:
                    print_log(LogLevel.ERROR, self.log_title, "心跳超时,断开连接")
                    self.disconnect()
                    break
                self._stop_event.wait(1.0)
        except Exception as e:
            if not self._stop_event.is_set():
                print_log(LogLevel.WARN, self.log_title, f"监控异常: {e}")
        print_log(LogLevel.INFO, self.log_title,
            f"监控线程退出: stop_event={self._stop_event.is_set()} is_connected={self.is_connected}")

    def disconnect(self) -> None:
        self._stop_event.set()
        self.is_connected = False
        self.is_streaming = False
        def _join_async():
            if self.poll_thread and self.poll_thread.is_alive():
                self.poll_thread.join(timeout=1.0)
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=1.0)
        threading.Thread(target=_join_async, daemon=True).start()

    def get_current_frame(self, timeout: float = 0.001):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None


__all__ = ["H264StreamClient", "NALTYPE_SPS", "NALTYPE_PPS", "NALTYPE_IDR", "NALTYPE_NON_IDR"]
