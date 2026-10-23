#!/usr/bin/env python3

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
自动推送服务端到设备 (run_macos.command / run_linux.sh 调用).

流程:
  1. 列出已连接设备 (hdc list targets)
  2. 逐个探测设备架构 (uname -m)
  3. 按架构 + 厂商选择本地服务端二进制
  4. 校验二进制架构与设备一致 (不一致跳过, 避免 'not executable')
  5. 推送并设置可执行权限 (仅当设备上还不是同一份二进制时才推)

用法:
  python3 scripts/auto_push_server.py                 # 处理所有已连接设备
  python3 scripts/auto_push_server.py --device SN     # 只处理指定设备
  python3 scripts/auto_push_server.py --dry-run       # 只探测和选型, 不推送
  python3 scripts/auto_push_server.py --codec h264    # 推 oh264_streamer (H.264 模式)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hdc_executor import HDCCommandExecutor          # noqa: E402
from core.arch_detector import (                            # noqa: E402
    detect_device_arch,
    find_server_binary,
    local_binary_arch,
    verify_binary_arch,
)

# 服务端二进制的本地候选 (按架构)
_SERVER_CANDIDATES = {
    "aarch64": ["ohscrcpy_server"],
    "arm": ["ohscrcpy_server_rk3568"],
    "loongarch64": ["ohscrcpy_server_loongarch"],
    "x86_64": ["ohscrcpy_server"],
}

_H264_CANDIDATES = {
    "aarch64": ["oh264_streamer"],
    "arm": ["oh264_streamer_arm"],
    "loongarch64": ["oh264_streamer_loongarch"],
    "x86_64": ["oh264_streamer"],
}

# MJPEG 自建服务 (设备无 busybox 时使用, 见 core/mjpeg_server_manager.py)
_JPEG_CANDIDATES = {
    "aarch64": ["jpeg_server"],
    "arm": ["jpeg_server_arm"],
    "loongarch64": ["jpeg_server_loongarch"],
    "x86_64": ["jpeg_server"],
}

_REMOTE_SERVER = "/system/bin/ohscrcpy_server"
_REMOTE_H264 = "/system/bin/oh264_streamer"
_REMOTE_JPEG = "/data/local/tmp/ohscrcpy_jpeg_server"


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _list_targets(hdc: HDCCommandExecutor):
    """返回已连接设备 SN 列表.

    hdc 无设备时会输出 "[Empty]" (而不是空输出), 必须过滤掉,
    否则会被当成 SN 传给后续命令, 报 "架构探测失败".
    """
    result = hdc.execute(["list", "targets"], need_sn=False, timeout=10)
    if not result.get("success"):
        return []
    targets = []
    for line in (result.get("stdout") or "").splitlines():
        sn = line.strip()
        if not sn or sn.startswith("[") or sn.lower() in ("empty", "no devices"):
            continue
        targets.append(sn)
    return targets


def _pick_binary(arch: str, codec: str):
    """按架构挑选本地二进制 (支持厂商专属目录)."""
    root = _root()
    table = {"h264": _H264_CANDIDATES, "jpeg": _JPEG_CANDIDATES}.get(codec, _SERVER_CANDIDATES)
    candidates = table.get(arch, [])
    if not candidates:
        return None
    for manufacturer_dir in ("HUAWEI", "ShiMeta"):
        for name in candidates:
            path = os.path.join(root, manufacturer_dir, name)
            if os.path.isfile(path):
                return path
    for name in candidates:
        path = os.path.join(root, "device", name)
        if os.path.isfile(path):
            return path
    for name in candidates:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


# 设备端可能存在的 server 位置 (按 codec)
_DEVICE_SEARCH_PATHS = {
    "server": [
        "/system/bin/ohscrcpy_server",
        "/system/bin/screen_server",
        "/data/local/tmp/ohscrcpy_server",
        "/data/local/tmp/ohscrcpy_jpeg_server",
    ],
    "h264": ["/system/bin/oh264_streamer", "/data/local/tmp/oh264_streamer"],
    "jpeg": ["/data/local/tmp/ohscrcpy_jpeg_server", "/system/bin/ohscrcpy_jpeg_server"],
}


def find_device_binary(hdc: HDCCommandExecutor, sn: str, codec: str) -> str:
    """在设备上搜索已存在的 server 二进制.

    命中就直接复用 (不重复推送); 未命中返回空串.
    用于"ohscrcpy_server 能否自行搜索" — 避免每次启动都推一遍 2MB 二进制.
    """
    for path in _DEVICE_SEARCH_PATHS.get(codec, []):
        result = hdc.execute(["-t", sn, "shell", "ls", "-la", path], timeout=8)
        if result.get("success") and path.split("/")[-1] in (result.get("stdout") or ""):
            print(f"[auto-push] {sn}: 设备上已有 {path}, 复用")
            return path
    return ""


def _device_has_binary(hdc: HDCCommandExecutor, sn: str, remote: str) -> bool:
    """设备上是否已有该 server."""
    result = hdc.execute(["-t", sn, "shell", "ls", "-la", remote], timeout=8)
    return bool(result.get("success")) and remote.split("/")[-1] in (result.get("stdout") or "")


def push_to_device(hdc: HDCCommandExecutor, sn: str, codec: str = "server",
                   dry_run: bool = False) -> bool:
    """把匹配架构的服务端二进制推送到指定设备."""
    arch = detect_device_arch(hdc, sn)
    if not arch:
        print(f"[auto-push] {sn}: 架构探测失败, 跳过")
        return False

    local = _pick_binary(arch, codec)
    if not local:
        print(f"[auto-push] {sn}: 未找到架构 [{arch}] 的本地二进制, 跳过")
        return False

    actual = local_binary_arch(local)
    if not verify_binary_arch(local, arch):
        print(f"[auto-push] {sn}: 二进制架构 [{actual}] != 设备 [{arch}], 跳过")
        return False

    remote = {"h264": _REMOTE_H264, "jpeg": _REMOTE_JPEG}.get(codec, _REMOTE_SERVER)
    print(f"[auto-push] {sn}: arch={arch} local={local} (elf={actual}) remote={remote}")

    if dry_run:
        print(f"[auto-push] {sn}: dry-run, 跳过推送")
        return True

    # 设备端搜索: 已有同架构二进制则跳过推送 (内容一致判断靠大小)
    existing = find_device_binary(hdc, sn, codec)
    if existing:
        try:
            local_size = os.path.getsize(local)
        except OSError:
            local_size = -1
        result = hdc.execute(["-t", sn, "shell", "wc", "-c", existing], timeout=8)
        out = (result.get("stdout") or "").strip().split()
        dev_size = int(out[0]) if out and out[0].isdigit() else -1
        if local_size > 0 and dev_size == local_size:
            print(f"[auto-push] {sn}: 设备已有相同大小 ({dev_size} 字节), 跳过推送")
            hdc.execute(["-t", sn, "shell", "chmod", "755", existing], timeout=10)
            return True
        print(f"[auto-push] {sn}: 设备已有 {existing} 但大小不同 ({dev_size} vs {local_size}), 重新推送")

    hdc.execute(["-t", sn, "target", "mount"], timeout=15)
    result = hdc.execute(["-t", sn, "file", "send", local, remote], timeout=120)
    if not result.get("success"):
        print(f"[auto-push] {sn}: 推送失败: {result.get('stderr') or result.get('stdout')}")
        return False

    hdc.execute(["-t", sn, "shell", "chmod", "755", remote], timeout=10)
    print(f"[auto-push] {sn}: 推送成功 -> {remote}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="自动推送服务端到 OHOS 设备")
    parser.add_argument("--device", default="", help="只处理指定设备 SN")
    parser.add_argument("--codec", default="server",
                        choices=["server", "h264", "jpeg"],
                        help="server=ohscrcpy_server, h264=oh264_streamer, "
                             "jpeg=jpeg_server (无busybox 设备的 MJPEG 服务)")
    parser.add_argument("--dry-run", action="store_true", help="只探测选型, 不推送")
    args = parser.parse_args()

    hdc = HDCCommandExecutor()
    targets = [args.device] if args.device else _list_targets(hdc)
    if not targets:
        print("[auto-push] 没有已连接设备")
        return 0

    print(f"[auto-push] 待处理设备: {targets}")
    ok = 0
    for sn in targets:
        try:
            if push_to_device(hdc, sn, args.codec, args.dry_run):
                ok += 1
        except Exception as e:
            print(f"[auto-push] {sn}: 异常 {e!r}")
    print(f"[auto-push] 完成: {ok}/{len(targets)} 成功")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
