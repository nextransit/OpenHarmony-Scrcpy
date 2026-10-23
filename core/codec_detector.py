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
设备端编码能力探测 — 自动选择 MJPEG / H.264.

为什么需要自动探测:
  - MJPEG 模式依赖设备端 `busybox httpd` 提供 27190 端口 (循环 snapshot_display 写 jpeg).
    精简版 OHOS 设备 (如 loongarch / KaihongOS) 没有 busybox, 也没有 toybox httpd,
    MJPEG 直接不可用 -> 表现为"无图像".
  - H.264 模式依赖设备端 oh264_streamer (硬件编码), 只要 OH_VideoEncoder_CreateByMime(AVC)
    能成功就可用. 已验证 loongarch64 设备支持 H.264 硬件编码.

探测项:
  1. busybox 是否存在且能跑 httpd  -> 决定 MJPEG 可行性
  2. snapshot_display 是否存在    -> MJPEG 抓帧前提
  3. 设备端是否已有 oh264_streamer / ohscrcpy_server -> H.264 可行性前提

探测全部在设备上执行只读命令, 不改变设备状态.
"""

from typing import Dict, Optional

from .constants import LogLevel
from .logger import print_log
from .arch_detector import ensure_connected


def _run(hdc_executor, sn: str, args, timeout: int = 8) -> Dict:
    cmd = (["-t", sn] if sn else []) + list(args)
    try:
        return hdc_executor.execute(cmd, timeout=timeout)
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": repr(e)}


def _shell_out(hdc_executor, sn: str, command: str, timeout: int = 8) -> str:
    """执行设备端 shell 命令, 返回 stdout (失败返回空串)."""
    result = _run(hdc_executor, sn, ["shell", command], timeout=timeout)
    if not result.get("success"):
        return ""
    return result.get("stdout") or ""


def has_busybox_httpd(hdc_executor, sn: str = "") -> bool:
    """设备是否有可用的 busybox httpd (MJPEG 模式前提)."""
    out = _shell_out(hdc_executor, sn, "busybox httpd --help 2>&1 | head -1")
    if "httpd" in out.lower() or "BusyBox" in out:
        return True
    # toybox 也可能带 httpd applet
    out2 = _shell_out(hdc_executor, sn, "toybox 2>&1 | tr '\\n' ' '")
    if " httpd " in f" {out2} ":
        return True
    return False


def has_snapshot_display(hdc_executor, sn: str = "") -> bool:
    """设备是否有 snapshot_display (MJPEG 抓帧前提)."""
    out = _shell_out(hdc_executor, sn, "ls /system/bin/snapshot_display 2>/dev/null")
    if "snapshot_display" in out:
        return True
    out2 = _shell_out(hdc_executor, sn, "which snapshot_display 2>/dev/null")
    return "snapshot_display" in out2


def has_h264_encoder(hdc_executor, sn: str = "") -> bool:
    """设备是否有 H.264 硬件编码器 (H.264 模式前提).

    依据设备端 codec_caps.xml 的 <VideoEncoder> 段里声明的 codecName.

    这里做的是"明确肯定"判断而不是"明确否定": 只有文件里明确列出了
    avc / h264 才算支持. 理由 (真机 192.168.19.11:8710 loongarch 实测):
    该设备的 codec_caps.xml 里 VideoEncoder 只有 avenc_mpeg4, 没有 AVC,
    但 OH_VideoEncoder_CreateByMime(AVC) 仍会返回非空句柄 (不报错),
    表面上"配置成功", 实际永远产不出码流 — 表现为连上后画面全黑/无图像.
    按"明确肯定"判断可以避免这个坑.
    """
    out = _shell_out(hdc_executor, sn, "cat /system/etc/codec/codec_caps.xml 2>/dev/null")
    if not out:
        # 读不到声明文件 -> 判为不支持.
        # 理由: AVC 编码器在这类设备上"创建成功但永远产不出码流",
        # 猜错的后果 (连上后黑屏) 比保守回退 MJPEG 严重得多.
        print_log(LogLevel.WARN, "编解码探测",
            "读不到 codec_caps.xml, 保守判定为不支持 H.264")
        return False
    # 提取 <VideoEncoder> 段
    start = out.find("<VideoEncoder>")
    end = out.find("</VideoEncoder>")
    if start < 0 or end < 0 or end < start:
        print_log(LogLevel.WARN, "编解码探测",
            "codec_caps.xml 中没有 <VideoEncoder> 段, 判定为不支持 H.264")
        return False
    segment = out[start:end]
    encoders = []
    for line in segment.splitlines():
        line = line.strip()
        if "codecName=" in line:
            encoders.append(line)
    if not encoders:
        print_log(LogLevel.WARN, "编解码探测",
            "codec_caps.xml 的 <VideoEncoder> 段没有 codecName, 判定为不支持 H.264")
        return False
    joined = " ".join(encoders).lower()
    return "avc" in joined or "h264" in joined


def has_h264_streamer_binary(hdc_executor, sn: str = "") -> bool:
    """设备端是否已部署可用的 H.264 streamer 二进制."""
    for path in ("/system/bin/oh264_streamer", "/system/bin/ohscrcpy_server"):
        out = _shell_out(hdc_executor, sn, f"ls {path} 2>/dev/null")
        if path.split("/")[-1] in out:
            return True
    return False


def has_jpeg_server_binary(hdc_executor, sn: str = "") -> bool:
    """设备端是否已部署自建 jpeg_server (无 busybox 时的 MJPEG 服务)."""
    out = _shell_out(hdc_executor, sn, "ls /data/local/tmp/ohscrcpy_jpeg_server 2>/dev/null")
    return "ohscrcpy_jpeg_server" in out


def detect_codec(hdc_executor, sn: str = "") -> str:
    """自动探测设备应使用的 codec.

    返回 'h264' 或 'mjpeg'.
    规则:
      - MJPEG 需要 busybox httpd + snapshot_display, 两者齐备才可用
      - H.264 需要设备端有 H.264 编码器 (或已部署 streamer 二进制)
      - 两者都不可用时, 返回 'mjpeg' (保持既有默认, 由上层报明确错误)
    """
    log_title = "编解码探测"
    if sn:
        ensure_connected(hdc_executor, sn)

    # MJPEG 可行条件: 能抓帧 (snapshot_display) 且有 HTTP 服务
    #   - busybox httpd (常规设备)
    #   - 自建 jpeg_server (精简版设备, 由 MjpegServerManager 自动推送)
    mjpeg_ok = has_snapshot_display(hdc_executor, sn) and (
        has_busybox_httpd(hdc_executor, sn) or has_jpeg_server_binary(hdc_executor, sn)
    )
    # H.264 优先: 原生抓屏 (OH_AVScreenCapture + Surface 零拷贝) 无 CPU 拷贝,
    # 优于 MJPEG 的 snapshot_display 轮询 (16-20fps), 所以两者都可用时选 H.264.
    # 注意: 不能因为设备上存在 oh264_streamer 二进制就算可用 —
    # 设备硬件不支持 AVC 时该二进制照样推得上, 但产不出码流.
    h264_ok = has_h264_encoder(hdc_executor, sn)

    print_log(LogLevel.INFO, log_title,
        f"能力探测: MJPEG={'可用' if mjpeg_ok else '不可用'}, "
        f"H.264={'可用' if h264_ok else '不可用'}")

    if h264_ok:
        print_log(LogLevel.INFO, log_title, "自动选择 codec=h264 (原生抓屏, 更高帧率)")
        return "h264"
    if mjpeg_ok:
        print_log(LogLevel.INFO, log_title, "自动选择 codec=mjpeg")
        return "mjpeg"
    print_log(LogLevel.WARN, log_title,
        "两种 codec 均未探测到可用能力, 回退 mjpeg (上层会给出明确错误)")
    return "mjpeg"


def resolve_codec(hdc_executor, sn: str = "") -> str:
    """解析最终使用的 codec — 环境变量优先, 否则自动探测.

    OHCRCPY_CODEC 显式设置时直接采用 (用户意图优先);
    未设置时自动探测.
    """
    import os
    explicit = os.environ.get("OHCRCPY_CODEC", "").strip().lower()
    if explicit:
        print_log(LogLevel.INFO, "编解码探测", f"使用显式指定的 codec={explicit}")
        return explicit
    return detect_codec(hdc_executor, sn)


__all__ = [
    "has_busybox_httpd",
    "has_jpeg_server_binary",
    "has_snapshot_display",
    "has_h264_encoder",
    "has_h264_streamer_binary",
    "detect_codec",
    "resolve_codec",
]
