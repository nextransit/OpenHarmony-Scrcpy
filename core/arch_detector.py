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
设备端架构探测 + 服务端二进制选择.

设备架构来源 (按可靠性排序):
  1. `uname -m`  — 所有设备都有, 最可靠
  2. `param get const.ohos.cpu.abi` — OHOS 特有, 部分设备缺失
  3. `param get const.product.cpu.abi` — 兜底

支持的目标:
  aarch64   -> aarch64 (默认, 现有 ohscrcpy_server)
  arm       -> arm    (ohscrcpy_server_rk3568, 32 位)
  loongarch64 -> loongarch64 (ohscrcpy_server_loongarch)

二进制命名约定 (在 device/ 目录下):
  ohscrcpy_server              -> aarch64
  ohscrcpy_server_rk3568       -> arm (32 位)
  ohscrcpy_server_loongarch    -> loongarch64
"""

import os
import re
from typing import Dict, List, Optional

from .constants import LogLevel
from .logger import print_log


# 架构别名归一化: uname -m / abi 字符串 -> 规范架构名
_ARCH_ALIASES: Dict[str, str] = {
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv8l": "aarch64",
    "arm": "arm",
    "armv7l": "arm",
    "armv7a": "arm",
    "armeabi-v7a": "arm",
    "loongarch64": "loongarch64",
    "loong64": "loongarch64",
    "x86_64": "x86_64",
    "x64": "x86_64",
}

# 各架构对应的服务端二进制文件名 (按优先级)
_ARCH_SERVER_BINARIES: Dict[str, List[str]] = {
    "aarch64": ["ohscrcpy_server"],
    "arm": ["ohscrcpy_server_rk3568"],
    "loongarch64": ["ohscrcpy_server_loongarch"],
    "x86_64": ["ohscrcpy_server"],
}


# hdc 的错误输出特征 — 出现这些说明命令没真正执行, 不能当作架构值
_HDC_ERROR_MARKERS = (
    "[fail]", "[error]", "device not found", "not connected",
    "closed", "timeout", "connection refused",
)


def normalize_arch(raw: str) -> str:
    """把 uname -m / abi 输出归一化成规范架构名.

    hdc 失败时 stdout 里会混入 "[Fail][E001005] device not found or connected"
    这类错误文本, 必须先剔除, 否则会被误当成架构名.
    """
    if not raw:
        return ""
    value = raw.strip().lower()
    # 逐行剔除 hdc 错误行
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    kept = [ln for ln in lines
            if not any(m in ln for m in _HDC_ERROR_MARKERS)]
    if not kept:
        return ""
    value = kept[0]
    if value in _ARCH_ALIASES:
        return _ARCH_ALIASES[value]
    # 前缀匹配 (如 "aarch64-linux-ohos", "loongarch64-linux-ohos")
    for alias, canonical in _ARCH_ALIASES.items():
        if value.startswith(alias):
            return canonical
    return ""


def ensure_connected(hdc_executor, sn: str) -> bool:
    """确保 hdc 已连接目标设备.

    网络设备 (SN 形如 "host:port") 必须先 `hdc tconn host:port` 注册,
    否则后续 `-t host:port shell ...` 会返回
    "[Fail][E001005] Device not found or connected".

    USB 设备 (纯 SN) 无需此步, 直接返回 True.
    """
    if not sn:
        return False
    if ":" not in sn:
        return True  # USB 设备, 已自动连接
    try:
        result = hdc_executor.execute(["tconn", sn], need_sn=False, timeout=15)
        out = (result.get("stdout") or "") + (result.get("stderr") or "")
        # "Target is connected" / "Connect successfully" 都算成功
        ok = result.get("success") and (
            "connected" in out.lower() or "success" in out.lower()
            or result.get("returncode") == 0
        )
        if not ok:
            print_log(LogLevel.WARN, "架构探测",
                f"tconn {sn} 失败: {out.strip()[:200]}")
        return bool(ok)
    except Exception as e:
        print_log(LogLevel.WARN, "架构探测", f"tconn {sn} 异常: {e!r}")
        return False


def detect_device_arch(hdc_executor, sn: str = "") -> str:
    """探测设备架构, 返回 'aarch64' / 'arm' / 'loongarch64' / 'x86_64' / ''.

    探测失败返回空字符串 (调用方应回退到默认架构).
    """
    log_title = "架构探测"

    # 0) 网络设备需要先 tconn 注册, 否则后续命令都会失败
    if sn:
        ensure_connected(hdc_executor, sn)

    # 1) uname -m — 最可靠
    try:
        cmd = (["-t", sn] if sn else []) + ["shell", "uname", "-m"]
        result = hdc_executor.execute(cmd, timeout=8)
        if result.get("success"):
            arch = normalize_arch(result.get("stdout", ""))
            if arch:
                print_log(LogLevel.INFO, log_title, f"设备架构 (uname -m): {arch}")
                return arch
    except Exception as e:
        print_log(LogLevel.WARN, log_title, f"uname -m 探测失败: {e!r}")

    # 2) OHOS param — 部分设备没有 uname 权限时兜底
    for param in ("const.ohos.cpu.abi", "const.product.cpu.abi"):
        try:
            cmd = (["-t", sn] if sn else []) + ["shell", "param", "get", param]
            result = hdc_executor.execute(cmd, timeout=8)
            if result.get("success"):
                arch = normalize_arch(result.get("stdout", ""))
                if arch:
                    print_log(LogLevel.INFO, log_title, f"设备架构 ({param}): {arch}")
                    return arch
        except Exception:
            continue

    print_log(LogLevel.WARN, log_title, "无法探测设备架构")
    return ""


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_server_binary(arch: str, manufacturer: str = "default") -> Optional[str]:
    """按架构 + 厂商找到服务端二进制本地路径.

    查找顺序:
      1. <root>/<manufacturer>/<arch_binary>   (厂商专属目录)
      2. <root>/<manufacturer>/<fallback>      (厂商目录里的通用二进制)
      3. <root>/<arch_binary>                  (根目录, 架构专属)
      4. <root>/<fallback>                     (根目录, 通用二进制)

    找不到返回 None.
    """
    log_title = "服务端选择"
    root = _repo_root()
    candidates: List[str] = []

    arch_bins = _ARCH_SERVER_BINARIES.get(arch, [])
    fallback_bins = _ARCH_SERVER_BINARIES.get("aarch64", [])

    if manufacturer and manufacturer != "default":
        manu_dir = os.path.join(root, manufacturer)
        for name in arch_bins + fallback_bins:
            candidates.append(os.path.join(manu_dir, name))

    for name in arch_bins + fallback_bins:
        candidates.append(os.path.join(root, name))

    for path in candidates:
        if os.path.isfile(path):
            print_log(LogLevel.INFO, log_title,
                f"选中服务端二进制 (arch={arch or '未知'}, 厂商={manufacturer}): {path}")
            return path

    print_log(LogLevel.WARN, log_title,
        f"未找到匹配架构 [{arch}] 的服务端二进制, 已尝试: {candidates}")
    return None


def local_binary_arch(path: str) -> str:
    """读取本地 ELF 二进制的架构 (纯 Python 解析, 不依赖外部工具)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return ""
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return ""

    e_machine = int.from_bytes(head[18:20], "little")  # ELFCLASS32 下同偏移
    # ELF64: e_machine @ 0x12; ELF32: e_machine @ 0x12 也是 (2 bytes @ 0x12)
    machine_map = {
        0x28: "arm",
        0x3E: "x86_64",
        0xB7: "aarch64",
        0x102: "loongarch64",
    }
    return machine_map.get(e_machine, f"0x{e_machine:x}")


def verify_binary_arch(path: str, expected_arch: str) -> bool:
    """校验本地二进制架构与设备架构是否一致.

    架构不匹配时设备会报 'not executable: 64-bit ELF file' — 提前拦截.
    """
    actual = local_binary_arch(path)
    if not actual or not expected_arch:
        return True  # 无法判定时不拦截
    if actual != expected_arch:
        print_log(LogLevel.ERROR, "服务端选择",
            f"二进制架构不匹配: {path} 是 [{actual}], 设备是 [{expected_arch}]")
        return False
    return True


__all__ = [
    "normalize_arch",
    "ensure_connected",
    "detect_device_arch",
    "find_server_binary",
    "local_binary_arch",
    "verify_binary_arch",
]
