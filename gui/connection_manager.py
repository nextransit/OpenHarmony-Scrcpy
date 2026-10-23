#!/usr/bin/env python

# Copyright (c) 2026 BenZhou.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
连接管理器 - 负责设备连接的生命周期管理
"""

import os
import threading
import time
from typing import Optional, Callable, Any

from core import (
    HOST, HEARTBEAT_TIMEOUT, LogLevel, print_log,
    HDCCommandExecutor, DeviceManager,
)
from video import VideoStreamClient, MjpegStreamClient, H264StreamClient


class ConnectionState:
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"


class ConnectionManager:
    """连接管理器"""
    
    def __init__(
        self,
        device_manager: DeviceManager,
        hdc_executor: HDCCommandExecutor,
        on_frame_decoded: Callable,
        on_state_changed: Optional[Callable[[str], None]] = None,
        debug: bool = False,
    ) -> None:
        self.device_manager = device_manager
        self.hdc_executor = hdc_executor
        self.on_frame_decoded = on_frame_decoded
        self.on_state_changed = on_state_changed
        self.debug = debug
        
        self._state = ConnectionState.DISCONNECTED
        self._lock = threading.Lock()
        
        self.video_client: Optional[VideoStreamClient] = None
        self._server_manager = None
        self._forwarded_port: int = -1
        
        self.log_title = "连接管理器"
        
        self._init_video_client()
    
    def _resolve_codec(self) -> str:
        """解析 codec — 环境变量优先, 否则按设备能力自动探测.

        设备精简版 (如 loongarch / KaihongOS) 没有 busybox, MJPEG 不可用,
        必须自动切到 H.264, 否则表现为"无图像".
        """
        from core.codec_detector import resolve_codec
        sn = ""
        try:
            if self.device_manager is not None:
                sn = self.device_manager.get_current_device() or ""
        except Exception:
            sn = ""
        try:
            return resolve_codec(self.hdc_executor, sn)
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"codec 自动探测失败, 回退 mjpeg: {e!r}")
            return os.environ.get('OHCRCPY_CODEC', 'mjpeg').lower()

    def _attach_disconnect_callback(self) -> None:
        """给 video_client 挂断线回调 — 设备重启/hdc 断开时通知上层.

        没有这个回调时, video_client 断了但 connection_manager.state 仍是
        CONNECTED, GUI 画面卡死且状态栏仍显示"已连接".
        """
        vc = self.video_client
        if vc is None or not hasattr(vc, "on_disconnected"):
            return
        vc.on_disconnected = self._on_video_disconnected

    def _on_video_disconnected(self, reason: str = "") -> None:
        """视频流断开 (设备掉线/重启/心跳超时)."""
        print_log(LogLevel.WARN, self.log_title, f"视频流断开: {reason}")
        # 状态切到 DISCONNECTED, 触发 on_state_changed -> GUI 更新 UI
        self._set_state(ConnectionState.DISCONNECTED)

    def _init_video_client(self) -> None:
        """初始化视频流客户端.

        codec 选择优先级 (由环境变量 OHCRCPY_CODEC 决定):
          - mjpeg: MJPEG 模式 (绕过 RK3568 HEVC bug, 当前默认)
          - h264:  H.264 模式 (需要设备端 oh264_streamer, 见 docs/H264_NDK_BUILD.md)
          - h265:  HEVC 模式 (走原 VideoStreamClient, RK3568 上有马赛克 bug)
        OHCRCPY_CODEC 未设时, 默认 mjpeg (向后兼容).
        """
        codec = self._resolve_codec()
        if codec == 'h264':
            from video.config import H264_STREAM_PORT
            print_log(LogLevel.INFO, self.log_title, "检测到 OHCRCPY_CODEC=h264, 使用 H.264 模式")
            self.video_client = H264StreamClient(
                host='127.0.0.1',
                port=int(os.environ.get('OHCRCPY_H264_PORT', str(H264_STREAM_PORT))),
                on_frame_decoded=self.on_frame_decoded,
                debug=self.debug,
            )
        elif codec == 'mjpeg':
            print_log(LogLevel.INFO, self.log_title, "检测到 OHCRCPY_CODEC=mjpeg (默认), 使用 MJPEG 模式 (绕过 RK3568 HEVC bug)")
            self.video_client = MjpegStreamClient(
                host='127.0.0.1',
                port=int(os.environ.get('OHCRCPY_MJPEG_PORT', '27190')),
                on_frame_decoded=self.on_frame_decoded,
                debug=self.debug,
            )
        else:  # h265 / hevc / 默认
            print_log(LogLevel.INFO, self.log_title, f"使用 HEVC 模式 (codec={codec})")
            self.video_client = VideoStreamClient(
                device_manager=self.device_manager,
                on_frame_decoded=self.on_frame_decoded,
                debug=self.debug,
            )

        self._attach_disconnect_callback()
    
    @property
    def state(self) -> str:
        with self._lock:
            return self._state
    
    @property
    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED
    
    def set_server_manager(self, server_manager) -> None:
        """设置或更新服务端管理器（复用已有实例）"""
        if self._server_manager is None:
            self._server_manager = server_manager
        else:
            # 复用已有实例，只更新 manufacturer
            self._server_manager.update_manufacturer(server_manager.manufacturer)
    
    def ensure_server_manager(self, manufacturer: str, hdc_executor: HDCCommandExecutor) -> None:
        """确保服务端管理器存在（复用或创建）"""
        if self._server_manager is None:
            from core.server_manager import ServerManager
            self._server_manager = ServerManager(manufacturer, hdc_executor)
        else:
            self._server_manager.update_manufacturer(manufacturer)
    
    def connect(self, port: int) -> bool:
        """同步连接设备（返回是否成功）"""
        current_state = self.state
        if current_state == ConnectionState.CONNECTED:
            print_log(LogLevel.INFO, self.log_title, "已连接, 忽略重复连接请求")
            return True
        if current_state == ConnectionState.CONNECTING:
            print_log(LogLevel.INFO, self.log_title, "正在连接, 忽略重复连接请求")
            return False
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            print_log(LogLevel.DEBUG, self.log_title, f"连接视频流服务器...")
            if self.video_client.connect(HOST, port):
                self._forwarded_port = port
                self._set_state(ConnectionState.CONNECTED)
                print_log(LogLevel.INFO, self.log_title, f"连接成功")
                return True
            else:
                self._set_state(ConnectionState.DISCONNECTED)
                return False
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"连接异常: {e}")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
    
    def disconnect(self) -> None:
        """断开连接"""
        if self.state == ConnectionState.DISCONNECTED:
            return
        self._set_state(ConnectionState.DISCONNECTING)
        
        if self.video_client:
            self.video_client.disconnect()
        
        if self._server_manager:
            self._server_manager.stop_server()
        
        port = self._forwarded_port
        if port > 0:
            self.device_manager.remove_port_forwarding(port, port)
            self.device_manager.reset_port_forwarding()
            self._forwarded_port = -1
        
        self._set_state(ConnectionState.DISCONNECTED)
        print_log(LogLevel.INFO, self.log_title, f"断开连接已完成")
    
    def get_video_client(self) -> Optional[VideoStreamClient]:
        return self.video_client
    
    def get_server_manager(self):
        return self._server_manager
    
    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
        if self.on_state_changed:
            self.on_state_changed(state)


__all__ = ["ConnectionManager", "ConnectionState"]
