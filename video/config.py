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
OpenHarmony_Scrcpy 视频流配置
"""

from dataclasses import dataclass


@dataclass
class VideoStreamConfig:
    """视频流配置"""
    width: int = 720
    height: int = 1280
    fps: int = 30
    bitrate: int = 1500000
    codec: str = "h264"  # h264 / h265 / mjpeg (由 OHCRCPY_CODEC 决定)
    # 设备物理分辨率 (uinput 触摸注入的坐标系).
    # 抓帧分辨率可能小于它 (如抓 720p 但设备是 1080p), 点击坐标必须换算.
    # 0 = 未知, 此时退化为与 width/height 相同.
    device_width: int = 0
    device_height: int = 0


# H.264 流端口 (设备端 oh264_streamer 默认监听端口).
# 端口独立于 MJPEG/H.265, 避免冲突. 选择 27191 是为了与 MJPEG (27190) 相邻便于排查.
H264_STREAM_PORT = 27191


__all__ = ["VideoStreamConfig", "H264_STREAM_PORT"]