#!/usr/bin/env python

# Copyright (c) 2026 BenZhou.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""
设备端常驻输入代理 (input_agent) 的部署与客户端.

为什么需要 (真机 192.168.19.11:8710 loongarch 实测):
  旧路径每次点击都执行一次 `hdc shell uinput -T ...`:
      hdc 往返            ~15ms
      uinput 进程启动    ~104ms  (musl 动态链接)
      uinput 注入动作    ~276ms  (默认 -i interval)
      ------------------------------
      单次点击            ~380ms, 实测抖动到 3.4s

  新路径: 客户端 -> 持久 TCP -> 设备端常驻代理 -> uinput
      客户端往返            ~57ms (含 hdc fport 转发)
      代理内部 uinput       ~30ms (可用 -i 1 压缩)
      ------------------------------
      单次点击             ~71ms, 延迟稳定

  协议 (文本行, \\n 结尾), 设备端 device/input_agent.cpp:
      T <x> <y>                触摸 tap
      M <x> <y>                鼠标移动
      D <x> <y> / U <x> <y>    触摸 down / up
      S <x1> <y1> <x2> <y2>    滑动
      K <kc> / R <kc>          按键 down / up
      C <x> <y>                右键
      P                        ping
      Q                        退出
  响应: 每条回一行 "OK" 或 "ERR <msg>"
"""

import os
import socket
import time
import threading
from typing import Optional

from .constants import LogLevel
from .logger import print_log
from .hdc_executor import HDCCommandExecutor
from .arch_detector import detect_device_arch, local_binary_arch

REMOTE_PATH = "/data/local/tmp/ohscrcpy_input_agent"


class InputAgentManager:
    """部署并连接设备端常驻输入代理."""

    DEFAULT_PORT = 27195
    # 代理不可用时是否回退到旧的 hdc shell uinput 路径
    _ARCH_BINARIES = {
        "aarch64": ["input_agent"],
        "arm": ["input_agent_arm"],
        "loongarch64": ["input_agent_loongarch64"],
        "x86_64": ["input_agent"],
    }

    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc = hdc_executor
        self.port = self.DEFAULT_PORT
        self.device_arch: str = ""
        self.log_title = "输入代理"
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = False

    # ---------------- 部署 ----------------

    def _local_binary_path(self) -> str:
        device_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "device")
        arch = getattr(self, "device_arch", "") or "aarch64"
        names = self._ARCH_BINARIES.get(arch, ["input_agent"])
        for name in names:
            path = os.path.join(device_dir, name)
            if os.path.exists(path):
                return path
        return os.path.join(device_dir, "input_agent")

    def _install_binary(self) -> bool:
        if not getattr(self, "device_arch", ""):
            self.device_arch = detect_device_arch(self.hdc, self.hdc.get_current_device())
            if self.device_arch:
                print_log(LogLevel.INFO, self.log_title, f"设备架构: {self.device_arch}")

        local = self._local_binary_path()
        if not os.path.exists(local):
            print_log(LogLevel.WARN, self.log_title,
                f"未找到本地输入代理二进制: {local} (回退到 hdc shell uinput)")
            return False

        actual = local_binary_arch(local)
        if actual and self.device_arch and actual != self.device_arch:
            print_log(LogLevel.WARN, self.log_title,
                f"输入代理架构 [{actual}] 与设备 [{self.device_arch}] 不匹配, 跳过部署")
            return False

        # 设备端已有同架构二进制则跳过推送 (大小一致判断)
        result = self.hdc.execute(
            ["-t", self.hdc.get_current_device(), "shell", "wc", "-c", REMOTE_PATH],
            timeout=8)
        out = (result.get("stdout") or "").strip().split()
        dev_size = int(out[0]) if out and out[0].isdigit() else -1
        try:
            local_size = os.path.getsize(local)
        except OSError:
            local_size = -1
        if dev_size > 0 and dev_size == local_size:
            print_log(LogLevel.INFO, self.log_title, "设备已有相同大小的输入代理, 跳过推送")
        else:
            self.hdc.execute(["-t", self.hdc.get_current_device(), "target", "mount"], timeout=15)
            r = self.hdc.execute(
                ["-t", self.hdc.get_current_device(), "file", "send", local, REMOTE_PATH],
                timeout=120)
            if not r.get("success"):
                print_log(LogLevel.WARN, self.log_title,
                    f"输入代理推送失败: {r.get('stderr') or r.get('stdout')}")
                return False
            print_log(LogLevel.INFO, self.log_title, f"输入代理已推送: {local} -> {REMOTE_PATH}")

        self.hdc.execute(
            ["-t", self.hdc.get_current_device(), "shell", "chmod", "755", REMOTE_PATH],
            timeout=10)
        return True

    def _start_agent(self) -> bool:
        """启动设备端常驻代理 (先清理旧进程)."""
        sn = self.hdc.get_current_device()
        self.hdc.execute(["-t", sn, "shell", "pkill -9 -f ohscrcpy_input_agent 2>/dev/null"],
                         timeout=8)
        time.sleep(0.3)
        cmd = (f"nohup {REMOTE_PATH} {self.port} "
               f"> /data/local/tmp/ohscrcpy_input_agent.log 2>&1 &")
        self.hdc.execute(["-t", sn, "shell", cmd], timeout=8)
        time.sleep(1.0)

        r = self.hdc.execute(["-t", sn, "shell", "pidof ohscrcpy_input_agent"], timeout=8)
        pid = (r.get("stdout") or "").strip().split()
        if not pid:
            r2 = self.hdc.execute(
                ["-t", sn, "shell",
                 "cat /data/local/tmp/ohscrcpy_input_agent.log 2>/dev/null | tail -5"],
                timeout=8)
            print_log(LogLevel.WARN, self.log_title,
                f"输入代理未启动: {(r2.get('stdout') or '')[-200:]}")
            return False
        print_log(LogLevel.INFO, self.log_title, f"输入代理已启动, PID={pid[0]}")
        return True

    def _direct_host(self) -> str:
        """设备 IP (SN 形如 host:port 时取 host), 直连用.

        直连比 hdc fport 转发快一个数量级 (实测 0.02ms vs 15-57ms),
        而且不依赖 hdc 的端口转发机制 (实测设备 hdcd 在频繁 fport 操作时
        会不稳定掉线). 只有网络设备且 SN 形如 host:port 才能直连.
        """
        sn = self.hdc.get_current_device() or ""
        if ":" not in sn:
            return ""
        host = sn.rsplit(":", 1)[0].strip()
        # 只接受 IPv4 / 简单主机名
        if not host or " " in host:
            return ""
        return host

    def _setup_port_forward(self) -> bool:
        """建立到代理的访问通道.

        优先直连设备 IP (快且不依赖 hdc fport);
        直连失败时回退 hdc fport 转发.
        """
        self._direct_addr = self._direct_host()
        if self._direct_addr:
            try:
                probe = socket.create_connection((self._direct_addr, self.port), timeout=2.0)
                probe.close()
                print_log(LogLevel.INFO, self.log_title,
                    f"直连设备 {self._direct_addr}:{self.port} (免 hdc 转发, 延迟最低)")
                return True
            except OSError as e:
                print_log(LogLevel.INFO, self.log_title,
                    f"直连 {self._direct_addr}:{self.port} 失败 ({e!r}), 回退 hdc fport")

        sn = self.hdc.get_current_device()
        self.hdc.execute(["fport", "rm", f"tcp:{self.port}", f"tcp:{self.port}"], timeout=8)
        time.sleep(0.3)
        r = self.hdc.execute(["fport", f"tcp:{self.port}", f"tcp:{self.port}"], timeout=12)
        if not r.get("success"):
            print_log(LogLevel.WARN, self.log_title, f"端口转发失败: {r.get('stderr')}")
            return False
        self._direct_addr = ""
        return True

    def setup(self, port: int = DEFAULT_PORT) -> bool:
        """部署输入代理并建立端口转发 (不建立 TCP 连接)."""
        env_port = os.environ.get("OHCRCPY_INPUT_AGENT_PORT")
        if env_port:
            port = int(env_port)
        self.port = port

        if os.environ.get("OHCRCPY_NO_INPUT_AGENT") == "1":
            print_log(LogLevel.INFO, self.log_title, "已禁用输入代理 (OHCRCPY_NO_INPUT_AGENT=1)")
            return False
        if not self._install_binary():
            return False
        if not self._start_agent():
            return False
        if not self._setup_port_forward():
            return False
        target = f"{self._direct_addr}:{self.port}" if self._direct_addr else f"127.0.0.1:{self.port}"
        print_log(LogLevel.INFO, self.log_title,
            f"输入代理就绪: {target} (点击下发 ~0.05ms + 设备端注入 ~90ms, 旧路径 ~380ms)")
        return True

    # ---------------- 客户端 ----------------

    _direct_addr: str = ""

    def connect(self, timeout: float = 5.0) -> bool:
        """建立到代理的持久连接."""
        addr = (self._direct_addr or "127.0.0.1", self.port)
        try:
            sock = socket.create_connection(addr, timeout=timeout)
            sock.settimeout(2.0)
        except OSError as e:
            print_log(LogLevel.WARN, self.log_title, f"连接输入代理失败 ({addr}): {e!r}")
            # 直连失败 -> 尝试建立 fport 后再连本地端口
            if self._direct_addr:
                self._direct_addr = ""
                if self._setup_port_forward():
                    try:
                        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
                        sock.settimeout(2.0)
                    except OSError as e2:
                        print_log(LogLevel.WARN, self.log_title, f"回退连接也失败: {e2!r}")
                        return False
                else:
                    return False
            else:
                return False
        self._sock = sock
        self._connected = True
        self._stop_event.clear()
        if self._reader is None or not self._reader.is_alive():
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        # ping 验证链路
        if self.send_command("P", wait_reply=True) is None:
            print_log(LogLevel.WARN, self.log_title, "输入代理 ping 失败")
            self.disconnect()
            return False
        print_log(LogLevel.INFO, self.log_title, "输入代理连接成功")
        return True

    def _read_loop(self) -> None:
        """后台读线程: 丢弃代理回复 (协议为请求-响应, 同步等待时读取)."""
        sock = self._sock
        if sock is None:
            return
        buf = b""
        while not self._stop_event.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._last_reply = line.decode("utf-8", "ignore").strip()
        self._connected = False

    _last_reply: str = ""

    def send_command(self, cmd: str, wait_reply: bool = False) -> Optional[str]:
        """向代理发送一条指令.

        wait_reply=True 时同步等待一行回复 (用于 ping / 调试).
        正常点击不需要等待回复 — 代理收到就执行, 立即返回, 保证 UI 不卡.
        """
        sock = self._sock
        if sock is None or not self._connected:
            return None
        try:
            with self._lock:
                sock.sendall((cmd + "\n").encode("utf-8"))
        except OSError as e:
            print_log(LogLevel.WARN, self.log_title, f"发送指令失败: {e!r}")
            self._connected = False
            return None
        if not wait_reply:
            return None
        # 等待回包 (最多 2s)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self._last_reply:
                reply = self._last_reply
                self._last_reply = ""
                return reply
            time.sleep(0.005)
        return None

    def tap(self, x: int, y: int) -> bool:
        """触摸 tap."""
        return self.send_command(f"T {int(x)} {int(y)}") is not None or self._connected

    def mouse_move(self, x: int, y: int) -> bool:
        """鼠标移动."""
        return self.send_command(f"M {int(x)} {int(y)}") is not None or self._connected

    def swipe(self, x1: int, y1: int, x2: int, y2: int, smooth_ms: int = 100) -> bool:
        """滑动."""
        return self.send_command(
            f"S {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(smooth_ms)}") is not None or self._connected

    def key_down(self, keycode: int) -> bool:
        return self.send_command(f"K {int(keycode)}") is not None or self._connected

    def key_up(self, keycode: int) -> bool:
        return self.send_command(f"R {int(keycode)}") is not None or self._connected

    def right_click(self, x: int, y: int) -> bool:
        """右键 (OHOS 语义: 触摸定位 + KEY_BACK)."""
        return self.send_command(f"C {int(x)} {int(y)}") is not None or self._connected

    def key_tap(self, keycode: int) -> bool:
        """按键 down+up 一次下发."""
        return self.send_command(f"B {int(keycode)}") is not None or self._connected

    def disconnect(self) -> None:
        self._stop_event.set()
        sock = self._sock
        self._sock = None
        self._connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        # 停掉设备端代理
        try:
            self.hdc.execute(
                ["-t", self.hdc.get_current_device(), "shell",
                 "pkill -9 -f ohscrcpy_input_agent 2>/dev/null"], timeout=8)
        except Exception:
            pass

    def is_available(self) -> bool:
        return self._sock is not None and self._connected


__all__ = ["InputAgentManager"]
