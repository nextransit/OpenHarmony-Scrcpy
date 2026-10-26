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
OpenHarmony_Scrcpy 常量与枚举定义
"""

from enum import IntEnum, auto

# ==================== 项目信息 ====================
AUTHOR = "BenZhou"
PROJECT_URL = "https://github.com/nextransit/OpenHarmony-Scrcpy"
VERSION = "v2.3.2"

# ==================== 网络配置 ====================
DEFAULT_PORT = 27183
HOST = "127.0.0.1"

# ==================== 心跳配置 ====================
# 心跳超时 (秒): 超过这个时间没收到任何视频数据就判定断线.
# 默认 15s — 之前是 60s, 网络断开时用户要等整整一分钟才看到提示.
# 流模式下设备端是持续推帧, 正常时每帧间隔 <200ms, 15s 足够宽松;
# 环境变量可按网络状况调整 (弱网可调大).
HEARTBEAT_TIMEOUT = float(__import__('os').environ.get('OHCRCPY_HEARTBEAT_TIMEOUT', '15'))
HEARTBEAT_INTERVAL = 2.0  # 心跳发送间隔（秒）

# ==================== 数据包配置 ====================
PACKET_HEADER_SIZE = 8

# ==================== 数据包类型定义 ====================
class PacketType(IntEnum):
    PACKET_HEARTBEAT = 0
    PACKET_SPS = 1
    PACKET_PPS = 2
    PACKET_KEYFRAME = 3
    PACKET_FRAME = 4
    PACKET_CONFIG = 5
    PACKET_VPS = 6
    PACKET_LOG = 7

# ==================== 日志级别定义 ====================
class LogLevel(IntEnum):
    DEBUG = auto()
    INFO = auto()
    WARN = auto()
    ERROR = auto()
    FATAL = auto()

# ==================== 服务部署状态定义 ====================
class ServerDeployState(IntEnum):
    IDLE = auto()
    INSTALLING = auto()
    STARTING = auto()
    FINISHED = auto()

# ==================== 默认日志级别 ====================
ENABLE_LOG_LEVEL = LogLevel.INFO