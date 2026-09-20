#!/usr/bin/env python

# Copyright (c) 2026 luodh.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
H.264 设备端 streamer 部署管理 (方案 D).

职责:
  1. 推送 oh264_streamer 可执行文件到设备 /system/bin/
  2. 启动 oh264_streamer -p <port> -w <w> -h <h> -f <fps>
  3. 设置 hdc fport 端口转发 127.0.0.1:<port> -> 设备 <port>
  4. 提供 stop() 关闭进程, 清理端口转发

设备端二进制 oh264_streamer 需要在 OH NDK 工具链下编译, 见 docs/H264_NDK_BUILD.md.
客户端不需要此二进制也能解码 (PC 上用 PyAV + libx264 即可生成测试流), 但真机部署必须.
"""

import os
import time
import subprocess
from typing import Optional

from .constants import LogLevel
from .logger import print_log
from .hdc_executor import HDCCommandExecutor


class H264ServerManager:
    """在设备端部署 oh264_streamer (H.264 Annex-B HTTP bytestream 服务)."""

    DEFAULT_PORT = 27191
    DEFAULT_WIDTH = 720
    DEFAULT_HEIGHT = 1280
    DEFAULT_FPS = 30
    DEFAULT_BITRATE = 1500000

    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc = hdc_executor
        self.port = self.DEFAULT_PORT
        self.width = self.DEFAULT_WIDTH
        self.height = self.DEFAULT_HEIGHT
        self.fps = self.DEFAULT_FPS
        self.bitrate = self.DEFAULT_BITRATE
        self.log_title = "H264服务"
        self.server_process_pid: Optional[int] = None
        self.installed: bool = False

    def setup(self, port: int = DEFAULT_PORT, width: int = DEFAULT_WIDTH,
              height: int = DEFAULT_HEIGHT, fps: int = DEFAULT_FPS,
              bitrate: int = DEFAULT_BITRATE, env_override: bool = True) -> bool:
        """部署 H.264 streamer 到设备端并建立端口转发."""
        if env_override:
            env_w = os.environ.get("OHCRCPY_H264_WIDTH")
            env_h = os.environ.get("OHCRCPY_H264_HEIGHT")
            env_p = os.environ.get("OHCRCPY_H264_PORT")
            env_fps = os.environ.get("OHCRCPY_H264_FPS")
            env_br = os.environ.get("OHCRCPY_H264_BITRATE")
            if env_w: width = int(env_w)
            if env_h: height = int(env_h)
            if env_p: port = int(env_p)
            if env_fps: fps = int(env_fps)
            if env_br: bitrate = int(env_br)

        self.port = port
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate

        print_log(LogLevel.INFO, self.log_title,
            f"开始部署 H.264 streamer (port={port}, {width}x{height}, {fps}fps, {bitrate}bps)")

        # 1) 清理旧进程
        self._stop_background()
        time.sleep(0.5)

        # 2) 推送二进制
        if not self._install_binary():
            print_log(LogLevel.ERROR, self.log_title, "oh264_streamer 安装失败")
            return False

        # 3) 启动 streamer
        if not self._start_streamer():
            print_log(LogLevel.ERROR, self.log_title, "oh264_streamer 启动失败")
            return False

        # 4) 端口转发
        if not self._setup_port_forward(port, port):
            print_log(LogLevel.ERROR, self.log_title, "端口转发建立失败")
            return False

        print_log(LogLevel.INFO, self.log_title, f"H.264 streamer 就绪: http://127.0.0.1:{port}/screen.h264")
        return True

    def _exec(self, *args, timeout: int = 15):
        return self.hdc.execute(list(args), timeout=timeout)

    def _local_binary_path(self) -> str:
        """本地 (PC) 上的 oh264_streamer 二进制路径."""
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "device", "oh264_streamer")

    def _install_binary(self) -> bool:
        """推送 oh264_streamer 到设备 /system/bin/."""
        local = self._local_binary_path()
        if not os.path.exists(local):
            print_log(LogLevel.ERROR, self.log_title,
                f"未找到本地二进制: {local}. 需要先按 docs/H264_NDK_BUILD.md 编译.")
            return False

        # 挂载系统为读写 (与 MJPEG 部署一致)
        self._exec("target", "mount")

        # 推送
        result = self._exec("file", "send", local, "/system/bin/oh264_streamer")
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"推送失败: {result.get("stderr", "")}")
            return False

        # 加可执行位
        self._exec("shell", "chmod 755 /system/bin/oh264_streamer")

        self.installed = True
        return True

    def _start_streamer(self) -> bool:
        """在设备端启动 oh264_streamer 后台进程."""
        # 清理可能残留的进程
        self._exec("shell", "pkill -9 -f oh264_streamer 2>/dev/null", timeout=5)
        time.sleep(0.5)

        # 后台启动 (-p port -w width -h height -f fps -b bitrate)
        cmd = f"nohup /system/bin/oh264_streamer -p {self.port} -w {self.width} -h {self.height} -f {self.fps} -b {self.bitrate} > /data/local/tmp/oh264_streamer.log 2>&1 &"
        self._exec("shell", cmd, timeout=5)
        time.sleep(1.5)  # 给设备端启动时间

        # 验证进程在跑
        r = self._exec("shell", "pidof oh264_streamer", timeout=5)
        pid_text = (r.get("stdout") or "").strip()
        if not pid_text or not pid_text.split():
            # 读日志排查
            r2 = self._exec("shell", "cat /data/local/tmp/oh264_streamer.log 2>/dev/null | tail -20", timeout=5)
            print_log(LogLevel.ERROR, self.log_title,
                f"oh264_streamer 未启动. 日志: {(r2.get("stdout") or "")[-500:]}")
            return False

        self.server_process_pid = int(pid_text.split()[0])
        print_log(LogLevel.INFO, self.log_title, f"oh264_streamer 已启动, PID={self.server_process_pid}")
        return True

    def _setup_port_forward(self, local_port: int, device_port: int) -> bool:
        """建立 hdc fport 端口转发: 127.0.0.1:<local_port> -> 设备 <device_port>."""
        # 先清理可能残留的
        self._exec("fport", "rm", f"tcp:{local_port}", f"tcp:{device_port}", timeout=5)
        time.sleep(0.3)
        r = self._exec("fport", f"tcp:{local_port}", f"tcp:{device_port}", timeout=10)
        if not r["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"fport 失败: {r.get("stderr", "")}")
            return False
        print_log(LogLevel.INFO, self.log_title, f"端口转发建立: tcp:{local_port} → tcp:{device_port}")
        return True

    def _stop_background(self) -> None:
        """停止设备端进程并清理端口转发."""
        self._exec("shell", "pkill -9 -f oh264_streamer 2>/dev/null", timeout=5)
        self._exec("fport", "rm", f"tcp:{self.port}", f"tcp:{self.port}", timeout=5)
        self.server_process_pid = None

    def stop(self) -> None:
        """对外公开: 停止服务."""
        self._stop_background()
        print_log(LogLevel.INFO, self.log_title, "H.264 streamer 已停止")


__all__ = ["H264ServerManager"]
