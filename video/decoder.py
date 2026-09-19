#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
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
OpenHarmony_Scrcpy 视频解码器（支持H.264和H.265）
"""

import time
import traceback
from typing import Optional, Any

from core.constants import LogLevel
from core.logger import print_log
from .config import VideoStreamConfig


class VideoDecoder:
    """通用视频解码器（支持H.264和H.265）"""
    
    def __init__(self, config: VideoStreamConfig, debug: bool = False) -> None:
        self.config = config
        self.codec_name = config.codec
        self.debug = debug
        self.log_title = "解码器"
        
        self.codec_ctx: Optional[Any] = None
        
        self.sps_data: Optional[bytes] = None
        self.pps_data: Optional[bytes] = None
        self.vps_data: Optional[bytes] = None
        
        self.extradata: bytearray = bytearray()
        
        self.vps_received: bool = False
        self.sps_received: bool = False
        self.pps_received: bool = False
        
        self.frame_count: int = 0
        self.decode_success: int = 0
        self.decode_failure: int = 0
        self.last_decode_time: float = 0
        
        self.consecutive_errors: int = 0
        self.max_consecutive_errors: int = 25
        
        self.waiting_for_keyframe: bool = False
        self.last_extradata_hash: Optional[str] = None
        self.keyframe_decode_failed: bool = False
        self.is_first_frame = True
        
        print_log(LogLevel.INFO, self.log_title, f"创建解码器实例: {config.width}x{config.height}, codec: {config.codec}")

    def update_resolution(self, width: int, height: int) -> None:
        self.config.width = width
        self.config.height = height

    def _get_codec_type(self) -> str:
        """获取PyAV解码器类型字符串"""
        if self.codec_name == "h265":
            return 'hevc'
        return 'h264'

    def _init_codec(self) -> bool:
        """初始化解码器"""
        try:
            if self.codec_ctx is not None:
                self.codec_ctx = None
            
            import av
            codec_type = self._get_codec_type()
            self.codec_ctx = av.CodecContext.create(codec_type, 'r')
            
            self.codec_ctx.width = self.config.width
            self.codec_ctx.height = self.config.height
            self.codec_ctx.pix_fmt = 'rgba'
            
            if self.extradata:
                try:
                    self.codec_ctx.extradata = self.extradata
                    print_log(LogLevel.DEBUG, self.log_title, f"设置extradata: {len(self.extradata)}字节")
                except Exception as e:
                    print_log(LogLevel.ERROR, self.log_title, f"设置extradata失败: {e}")
            
            print_log(LogLevel.INFO, self.log_title, f"CodecContext初始化结果: {codec_type}, {self.codec_ctx.width}x{self.codec_ctx.height}")
            return True
            
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"初始化失败: {e}")
            traceback.print_exc()
            return False

    def is_ready(self) -> bool:
        """检查解码器是否就绪"""
        if not self.codec_ctx:
            return False
        if self.codec_name == "h265":
            return self.vps_received and self.sps_received and self.pps_received
        else:
            return self.sps_received and self.pps_received

    def set_vps(self, vps_data: bytes) -> bool:
        """设置VPS（H.265）"""
        if not vps_data:
            return False
        self.vps_data = vps_data
        self.vps_received = True
        print_log(LogLevel.DEBUG, self.log_title, f"接收VPS: {len(vps_data)}字节")
        return self._try_build_extradata()

    def set_sps(self, sps_data: bytes) -> bool:
        """设置SPS"""
        if not sps_data:
            return False
        self.sps_data = sps_data
        self.sps_received = True
        print_log(LogLevel.DEBUG, self.log_title, f"接收SPS: {len(sps_data)}字节")
        return self._try_build_extradata()

    def set_pps(self, pps_data: bytes) -> bool:
        """设置PPS"""
        if not pps_data:
            return False
        self.pps_data = pps_data
        self.pps_received = True
        print_log(LogLevel.DEBUG, self.log_title, f"接收PPS: {len(pps_data)}字节")
        return self._try_build_extradata()

    def _try_build_extradata(self) -> bool:
        """尝试构建extradata"""
        if not self._all_params_received():
            return False
        
        new_extradata = bytearray()
        
        if self.codec_name == "h265":
            if self.vps_data:
                if not self._has_start_code(self.vps_data):
                    new_extradata.extend(b'\x00\x00\x00\x01')
                new_extradata.extend(self.vps_data)
        
        if self.sps_data:
            if not self._has_start_code(self.sps_data):
                new_extradata.extend(b'\x00\x00\x00\x01')
            new_extradata.extend(self.sps_data)
        
        if self.pps_data:
            if not self._has_start_code(self.pps_data):
                new_extradata.extend(b'\x00\x00\x00\x01')
            new_extradata.extend(self.pps_data)
        
        import hashlib
        new_hash = hashlib.md5(bytes(new_extradata)).hexdigest()
        
        if self.last_extradata_hash != new_hash:
            self.extradata = new_extradata
            self.last_extradata_hash = new_hash
            print_log(LogLevel.INFO, self.log_title, f"extradata已更新: {len(self.extradata)}字节 (hash={new_hash[:8]})")
            return self._init_codec()
        else:
            print_log(LogLevel.DEBUG, self.log_title, f"extradata未变化，跳过重新初始化")
            return True

    def _all_params_received(self) -> bool:
        """检查是否已接收所有必要参数集"""
        if self.codec_name == "h265":
            return self.vps_received and self.sps_received and self.pps_received
        else:
            return self.sps_received and self.pps_received

    def _has_start_code(self, data: bytes) -> bool:
        """检查是否有起始码"""
        if len(data) < 4:
            return False
        return data[0:4] == b'\x00\x00\x00\x01' or data[0:3] == b'\x00\x00\x01'

    def _iter_nalus(self, data: bytes):
        """遍历 Annex-B 起始码分隔的 NALU，产出 (nalu_header_first_byte, payload)"""
        i = 0
        n = len(data)
        while i < n:
            # 找起始码 0x000001 或 0x00000001
            if i + 3 <= n and data[i] == 0 and data[i+1] == 0 and data[i+2] == 1:
                sc = 3
            elif i + 4 <= n and data[i] == 0 and data[i+1] == 0 and data[i+2] == 0 and data[i+3] == 1:
                sc = 4
            else:
                i += 1
                continue
            start = i + sc
            # 找下一个起始码
            j = start
            while j + 3 <= n:
                if data[j] == 0 and data[j+1] == 0 and data[j+2] == 1:
                    break
                if j + 4 <= n and data[j] == 0 and data[j+1] == 0 and data[j+2] == 0 and data[j+3] == 1:
                    break
                j += 1
            payload = data[start:j]
            if payload:
                yield payload[0], payload
            i = j if j > i else i + 1


    @staticmethod
    def _assess_frame_quality(rgb_array) -> tuple:
        """评估解码出来的 RGB 帧是否异常（设备 HEVC 编码器故障时常产生
        "垂直条纹/色块马赛克" 帧，Y 平面 edge_y/edge_x 比值会显著大于 1）。

        返回 (is_bad, edge_ratio, score)：
          is_bad=True 时表示帧高度怀疑是损坏的（应被上层丢弃/重连）。
          score 越大越正常；< 0.5 通常意味着马赛克伪影。

        性能:对 1080x1920 整帧做 np.diff 约 10-15ms。
        这里做 stride=8 下采样, 缩到 ~270x240, 时间降到 ~0.3ms (30x+)。
        """
        try:
            import numpy as np
            arr = rgb_array
            if arr is None or arr.size == 0:
                return True, 0.0, 0.0
            # 下采样到 ~270x240 (按 8 步长)
            s = 8
            if arr.ndim == 3 and arr.shape[2] >= 3:
                gray = arr[::s, ::s, :3].mean(axis=-1)
            else:
                gray = arr[::s, ::s]
            gy = float(abs(np.diff(gray, axis=0)).mean())
            gx = float(abs(np.diff(gray, axis=1)).mean())
            if gx <= 0.0:
                return gy > 0.5, 0.0, 0.0
            ratio = gy / gx
            # 经验阈值:正常自然图像 ratio 0.7~1.5;
            # RK3568 HEVC 损坏帧的 ratio 普遍 > 2.5 (垂直方向被"放大")。
            is_bad = ratio > 2.5
            return is_bad, ratio, 0.0
        except Exception:
            return False, 1.0, 0.0


    def _is_parameter_set_only(self, data: bytes) -> bool:
        """判断数据是否只含参数集（无 slice）。
        设备端在关键帧前会多发一个 VPS+SPS+PPS 拼接帧（不含图像数据），
        若直接送解码器会污染解码状态导致马赛克，必须跳过。"""
        has_slice = False
        has_any = False
        for first_byte, _ in self._iter_nalus(data):
            has_any = True
            if self.codec_name == "h265":
                nalu_type = (first_byte & 0x7E) >> 1
            else:
                nalu_type = first_byte & 0x1F
            # H.265:32/33/34 = VPS/SPS/PPS; H.264:7/8 = SPS/PPS
            if self.codec_name == "h265":
                if nalu_type not in (32, 33, 34):
                    has_slice = True
                    break
            else:
                if nalu_type not in (7, 8):
                    has_slice = True
                    break
        # 没有任何可识别 NALU（可能被 start code 前缀干扰）→不跳过，走原逻辑
        if not has_any:
            return False
        return not has_slice

    def decode_frame(self, frame_data: bytes, is_keyframe: bool = False):
        """解码视频帧"""
        if not frame_data or len(frame_data) == 0:
            print_log(LogLevel.WARN, self.log_title, f"空帧数据")
            return None
        
        if self.codec_ctx is None:
            print_log(LogLevel.ERROR, self.log_title, f"codec_ctx未初始化，无法解码")
            return None
        
        if self.waiting_for_keyframe and not is_keyframe:
            return None
        
        if is_keyframe:
            if self.waiting_for_keyframe:
                print_log(LogLevel.INFO, self.log_title, f"收到关键帧，恢复正常解码")
            self.waiting_for_keyframe = False
            self.keyframe_decode_failed = False
            self.consecutive_errors = 0
        
        self.frame_count += 1
        current_time = time.time()
        
        # 修复马赛克：设备端在关键帧前会发一个仅含 VPS/SPS/PPS 的参数帧，
        # 它本身不产生图像。直接跳过，避免污染解码器状态。
        if not is_keyframe and self._is_parameter_set_only(frame_data):
            print_log(LogLevel.DEBUG, self.log_title, f"跳过纯参数集帧 ({len(frame_data)} 字节)")
            return None
        
        try:
            processed_data = frame_data
            
            if is_keyframe and self.extradata and len(self.extradata) > 0:
                if not self._has_start_code(processed_data):
                    processed_data = self.extradata + b'\x00\x00\x00\x01' + frame_data
                elif not processed_data.startswith(self.extradata):
                    processed_data = self.extradata + processed_data
            
            if not self._has_start_code(processed_data):
                processed_data = b'\x00\x00\x00\x01' + processed_data
            
            if self.frame_count <= 3:
                print_log(LogLevel.DEBUG, self.log_title, f"帧数据前16字节: {processed_data[:16].hex()}")
            
            try:
                import av
                packets = self.codec_ctx.parse(processed_data)
                
                if len(packets) == 0:
                    if is_keyframe:
                        print_log(LogLevel.DEBUG, self.log_title, f"parse返回空，尝试直接decode")
                        packets = [av.Packet(processed_data)]
                    else:
                        self.consecutive_errors += 1
                        self.decode_failure += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            print_log(LogLevel.ERROR, self.log_title, f"连续解析失败{self.consecutive_errors}次，等待关键帧")
                            self.waiting_for_keyframe = True
                            self.consecutive_errors = 0
                        return None
            except Exception as parse_error:
                self.consecutive_errors += 1
                self.decode_failure += 1
                if is_keyframe:
                    print_log(LogLevel.ERROR, self.log_title, f"关键帧解析异常: {parse_error}, 数据前16字节: {processed_data[:16].hex()}")
                    self.keyframe_decode_failed = True
                    self.waiting_for_keyframe = True
                elif self.consecutive_errors >= self.max_consecutive_errors:
                    print_log(LogLevel.ERROR, self.log_title, f"连续解析异常{self.consecutive_errors}次，等待关键帧")
                    self.waiting_for_keyframe = True
                    self.consecutive_errors = 0
                return None
            
            decoded_frame = None
            for packet in packets:
                if packet is not None:
                    try:
                        frames = self.codec_ctx.decode(packet)
                        for frame in frames:
                            if isinstance(frame, av.VideoFrame):
                                if self.is_first_frame:
                                    print_log(LogLevel.INFO, self.log_title, f"解码帧: {frame.width}x{frame.height}, format={frame.format.name}")
                                    for i, plane in enumerate(frame.planes):
                                        print_log(LogLevel.INFO, self.log_title, f" plane[{i}] line_size={plane.line_size}, buffer_size={plane.buffer_size}")
                                
                                # rgb_array = frame.to_ndarray(format='rgb24')
                                rgb_array = frame.to_ndarray(width=self.config.width, height=self.config.height, format='rgb24')
                                if rgb_array is not None:
                                    if self.is_first_frame:
                                        self.is_first_frame = False
                                        print_log(LogLevel.INFO, self.log_title, f"RGB数组: shape={rgb_array.shape}, dtype={rgb_array.dtype}")
                                        print_log(LogLevel.INFO, self.log_title, f"RGB数组 strides: {rgb_array.strides}")
                                        print_log(LogLevel.INFO, self.log_title, f"colorspace={frame.colorspace}, color_range={frame.color_range}")
                                    
                                    self.decode_success += 1
                                    self.consecutive_errors = 0
                                    self.keyframe_decode_failed = False
                                    self.last_decode_time = current_time
                                    # 新增:帧质量评估 — HEVC 编码器故障时检测"垂直条纹马赛克"
                                    try:
                                        _bad, _ratio, _score = self._assess_frame_quality(rgb_array)
                                        if _bad:
                                            self.bad_frame_count = getattr(self, "bad_frame_count", 0) + 1
                                            self.last_bad_frame_ratio = _ratio
                                            if self.bad_frame_count % 5 == 1:
                                                print_log(LogLevel.WARN, self.log_title,
                                                    f"帧质量异常: edge_y/edge_x={_ratio:.2f} 累计异常={self.bad_frame_count}")
                                            # 异常帧不入 display queue；返回 None 让上层继续
                                            rgb_array = None
                                            decoded_frame = None
                                            break
                                        else:
                                            if getattr(self, "bad_frame_count", 0) > 0:
                                                print_log(LogLevel.INFO, self.log_title,
                                                    f"帧恢复正常 (累计异常已清零, 此前={self.bad_frame_count})")
                                            self.bad_frame_count = 0
                                    except Exception as _qe:
                                        pass
                                    decoded_frame = rgb_array
                                    break
                    except Exception as decode_error:
                        print_log(LogLevel.ERROR, self.log_title, f"decode失败: {decode_error}")
                        self.consecutive_errors += 1
                        self.decode_failure += 1
            
            if decoded_frame is not None:
                return decoded_frame
            else:
                self.decode_failure += 1
                if is_keyframe:
                    print_log(LogLevel.ERROR, self.log_title, f"关键帧解码失败！数据长度={len(processed_data)}字节")
                    self.keyframe_decode_failed = True
                    self.waiting_for_keyframe = True
                elif self.consecutive_errors >= self.max_consecutive_errors:
                    print_log(LogLevel.ERROR, self.log_title, f"连续解码失败{self.consecutive_errors}次，等待关键帧")
                    self.waiting_for_keyframe = True
                    self.consecutive_errors = 0
                return None
            
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"解码异常: {e}")
            self.decode_failure += 1
            self.consecutive_errors += 1
            if is_keyframe:
                self.keyframe_decode_failed = True
                self.waiting_for_keyframe = True
                print_log(LogLevel.ERROR, self.log_title, f"关键帧解码异常！")
            elif self.consecutive_errors >= self.max_consecutive_errors:
                self.waiting_for_keyframe = True
                self.consecutive_errors = 0
            return None
    
    def cleanup(self) -> None:
        """清理解码器资源"""
        print_log(LogLevel.INFO, self.log_title, f"清理资源...")
        self.codec_ctx = None
        self.sps_data = None
        self.pps_data = None
        self.vps_data = None
        self.extradata = None
        self.waiting_for_keyframe = False
        self.keyframe_decode_failed = False
        self.last_extradata_hash = None
        self.bad_frame_count = 0
        self.last_bad_frame_ratio = 0.0
        self.consecutive_errors = 0
        self.is_first_frame = True


__all__ = ["VideoDecoder"]