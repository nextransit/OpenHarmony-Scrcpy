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
licenseMonitor 周期重启 patch 工具 (LoongArch / KaihongOS).

背景
----
设备 (Kaihong BotStation, loongarch64) 每约 59 分钟重启一次, 与是否投屏无关.
根因链 (已在真机 hilog + 反汇编中确认):

  1. 设备出厂 fstab 为 /vendor/etc/fstab.3a6000, 其中**没有 factory 分区挂载行**
  2. licenseMonitor 硬编码只认 /vendor/etc/fstab.{sky1_evb,d3000,d2000}
     -> 找不到含 '/factory' 的行 -> "Line containing '/factory' not found."
  3. factory 分区 (nvme0n1p7, 512MB, 内含完整许可数据) 未被挂载
     -> "Read license data from factory fail."  (license.cpp:570)
  4. licenseMonitor 每 10s 重试, 持续约 59 分钟 (persist.license.monitor.interval=59)
     -> "CheckLicense fail, reboot soon!"
  5. SetParameter("ohos.startup.powerctrl", "reboot")
     -> PowerMgr 执行有序关机 -> init StopAllServices -> 设备重启
  6. 重启后 factory 依然没挂载 -> 无限循环

Patch 方式 (本脚本)
-------------------
不修改系统分区, 只改**用户态拷贝** (放到 /data/local/tmp), 由 init cfg 指向它启动.

反汇编确认的代码 (device licenseMonitor, license_monitor_main.cpp):

  0x1bc08  ori   s6, zero, 0x1
  0x1bc0c  move  a0, zero
  0x1bc10  move  a1, s6
  0x1bc14  bl    VerifyLicense        ; 返回 1 = 校验通过
  0x1bc18  move  s5, a0
  0x1bc1c  beq   a0, s6, 0x2fc        ; 校验通过 -> 跳到成功路径(继续运行)
  0x1bc20  ...                         ; 校验失败 -> 打印 "CheckLicense fail, reboot soon!"
  ...
  0x1beb0  bl    SetParameter           ; SetParameter("ohos.startup.powerctrl","reboot")

把 0x1bc1c 的条件分支 beq 改成**无条件跳转** b, 让失败路径也走成功路径,
从而不再执行 SetParameter(...,"reboot").

  原指令:  beq a0, s6, 0x2fc   = 9dfc0258
  新指令:  b   0x2fc           = 00fc0250

LoongArch B 指令编码 (已用二进制内 6 处真实 b 指令反推验证):
    word = 0x50000000 | ((off >> 2) & 0xFFFF) << 10 | ((off >> 2) >> 16) & 0x1F

用法
----
  # 1) 仅分析, 不改设备 (默认)
  python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710

  # 2) 生成 patched 二进制到本地 out 目录
  python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \\
      --pull --output out/licenseMonitor.patched

  # 3) 推送到设备用户态目录 (不覆盖 /system/bin)
  python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \\
      --pull --push

  # 4) 启用 (需 root; 修改 init cfg 指向 patched 副本, 立即生效)
  python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \\
      --enable

  # 5) 恢复原状
  python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \\
      --disable

安全性
------
* 从不写入 /system (只读) 或任何系统分区, 只用 /data/local/tmp.
* --enable 只改 /system/etc/init/khlicense_monitor.cfg 里的 path 一项
  (该文件在只读 /system, 需先 remount rw; 脚本会自动处理并可 --restore-config 回滚).
* --disable 精确还原 patch 前的 cfg 内容 (备份在设备 /data/local/tmp/license_patch/).
* 可重复执行 (幂等): 已打过 patch 的文件会检测出来并跳过.
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- 常量

# 设备端路径
REMOTE_BIN = "/system/bin/licenseMonitor"
REMOTE_WORKDIR = "/data/local/tmp/license_patch"
REMOTE_PATCHED = f"{REMOTE_WORKDIR}/licenseMonitor.patched"
REMOTE_ORIG = f"{REMOTE_WORKDIR}/licenseMonitor.orig"
REMOTE_CFG_BACKUP = f"{REMOTE_WORKDIR}/khlicense_monitor.cfg.orig"

# 原地替换方案: 原系统二进制的备份 (用于 --disable 回滚)
REMOTE_BACKUP_BIN = f"{REMOTE_WORKDIR}/licenseMonitor.system.bak"

# init 服务配置 (在 /system, 只读)
REMOTE_CFG = "/system/etc/init/khlicense_monitor.cfg"

# patch 位置: 虚拟地址 0x1bc1c 处的 beq 指令
PATCH_VADDR = 0x1BC1C
# .text 段: vaddr 0x19000 -> paddr 0x15000 (相差 0x4000)
TEXT_VADDR = 0x19000
TEXT_PADDR = 0x15000

OLD_BYTES = bytes.fromhex("9dfc0258")   # beq a0, s6, 0x2fc
NEW_BYTES = bytes.fromhex("00fc0250")   # b   0x2fc
NEW_DISASM = "b 0x2fc"

# 用于校验的上下文指令 (patch 点前后), 期望在 patched 文件里匹配
CTX_BEFORE = bytes.fromhex("003cfd54")   # 0x1bc14 bl VerifyLicense
CTX_AFTER = bytes.fromhex("a4011a14")    # 0x1bc20 lu12i.w a0, 0xd00d


def log(msg: str) -> None:
    print(f"[license-patch] {msg}", flush=True)


def die(msg: str, code: int = 1):
    print(f"[license-patch] 错误: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- hdc

class Hdc:
    """极简 hdc 封装 (只做本脚本需要的操作)."""

    def __init__(self, target: str, hdc_path: str = "") -> None:
        self.target = target
        self.hdc_path = hdc_path or self._find_hdc()
        self.env = dict(os.environ)
        if self.hdc_path:
            d = os.path.dirname(os.path.abspath(self.hdc_path))
            libdir = os.path.join(d, "libusb_shared.dylib")
            if os.path.exists(libdir):
                self.env["DYLD_LIBRARY_PATH"] = (
                    d + os.pathsep + self.env.get("DYLD_LIBRARY_PATH", ""))

    @staticmethod
    def _find_hdc() -> str:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        arch = "arm64" if os.uname().machine in ("arm64", "aarch64") else "x64"
        cand = os.path.join(root, "hdc", "Darwin", arch, "hdc")
        if os.path.exists(cand):
            return cand
        cand2 = os.path.join(root, "hdc", "Darwin", "x64", "hdc")
        if os.path.exists(cand2):
            return cand2
        return ""

    def _run(self, args, timeout=120):
        cmd = [self.hdc_path] + args
        if not self.hdc_path:
            die("找不到 hdc, 请用 --hdc 指定")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, env=self.env)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"

    def shell(self, cmd: str, timeout=120):
        rc, out, err = self._run(["-t", self.target, "shell", cmd], timeout)
        return rc, (out or "") + (err or "")

    def connect(self):
        rc, out, err = self._run(["tconn", self.target], timeout=30)
        if rc != 0 and "already" not in (out + err).lower():
            log(f"tconn 失败: {(out + err).strip()[:200]}")

    def file_recv(self, remote: str, local: str):
        return self._run(["-t", self.target, "file", "recv", remote, local], 180)

    def file_send(self, local: str, remote: str):
        return self._run(["-t", self.target, "file", "send", local, remote], 180)


# ---------------------------------------------------------------- patch 核心

def paddr_of(vaddr: int) -> int:
    """虚拟地址 -> 文件偏移 (.text 段)."""
    return vaddr - TEXT_VADDR + TEXT_PADDR


def patch_bytes(data: bytearray) -> str:
    """对内存中的 ELF 做 patch. 返回状态字符串."""
    off = paddr_of(PATCH_VADDR)
    if off < 0 or off + 4 > len(data):
        die(f"patch 偏移越界: {hex(off)} (文件大小 {len(data)})")

    cur = bytes(data[off:off + 4])

    # 上下文校验: 确认这就是目标函数的分支指令
    before = bytes(data[off - 8:off - 4])
    after = bytes(data[off + 4:off + 8])
    if before != CTX_BEFORE:
        die(f"上下文不匹配 (bl VerifyLicense 期望 {CTX_BEFORE.hex()}, "
            f"实际 {before.hex()}) — 二进制版本可能不同")
    if after != CTX_AFTER:
        die(f"上下文不匹配 (patch 后指令 期望 {CTX_AFTER.hex()}, "
            f"实际 {after.hex()}) — 二进制版本可能不同")

    if cur == NEW_BYTES:
        return "already-patched"
    if cur != OLD_BYTES:
        die(f"目标指令不匹配: 期望 {OLD_BYTES.hex()} (beq a0, s6, 0x2fc), "
            f"实际 {cur.hex()} — 二进制版本可能不同")

    data[off:off + 4] = NEW_BYTES
    return "patched"


def verify_patched(path: str) -> dict:
    """检查本地文件是否已打 patch."""
    with open(path, "rb") as f:
        data = f.read()
    off = paddr_of(PATCH_VADDR)
    cur = bytes(data[off:off + 4])
    return {
        "offset": off,
        "bytes": cur.hex(),
        "patched": cur == NEW_BYTES,
        "original": cur == OLD_BYTES,
        "size": len(data),
    }


def check_elf(path: str) -> dict:
    """确认是 loongarch64 ELF."""
    with open(path, "rb") as f:
        head = f.read(20)
    if head[:4] != b"\x7fELF":
        die(f"{path} 不是 ELF 文件")
    machine = struct.unpack_from("<H", head, 18)[0]
    # EM_LOONGARCH = 258
    if machine != 258:
        die(f"{path} 架构不是 loongarch (e_machine={machine}), 本 patch 仅适用 loongarch64")
    return {"machine": machine, "class": head[4], "size": os.path.getsize(path)}


# ---------------------------------------------------------------- 设备操作

def device_prepare(hdc: Hdc) -> None:
    """确保设备端工作目录存在."""
    rc, _ = hdc.shell(f"mkdir -p {REMOTE_WORKDIR}")
    if rc != 0:
        log(f"创建 {REMOTE_WORKDIR} 失败 (可能已存在)")


def device_pull_original(hdc: Hdc, local: str) -> bool:
    """从设备拉取原始二进制."""
    rc, out = hdc.shell(f"ls -la {REMOTE_BIN}")
    if rc != 0 or REMOTE_BIN not in out:
        die(f"设备上找不到 {REMOTE_BIN}")
    # 先备份到可写目录, 再 recv
    hdc.shell(f"cp {REMOTE_BIN} {REMOTE_ORIG} 2>/dev/null")
    rc, out, err = hdc.file_recv(REMOTE_BIN, local)
    if rc != 0:
        # 回退: 从备份取
        rc2, _, _ = hdc.file_recv(REMOTE_ORIG, local)
        if rc2 != 0:
            die(f"拉取 {REMOTE_BIN} 失败: {(out + err).strip()[:300]}")
    return True


def device_push_patched(hdc: Hdc, local: str) -> None:
    """推送 patched 副本到 /data/local/tmp (不碰 /system)."""
    rc, out, err = hdc.file_send(local, REMOTE_PATCHED)
    if rc != 0:
        die(f"推送 {REMOTE_PATCHED} 失败: {(out + err).strip()[:300]}")
    hdc.shell(f"chmod 755 {REMOTE_PATCHED}")
    log(f"已推送 patched 副本 -> {REMOTE_PATCHED}")


def device_enable(hdc: Hdc) -> None:
    """用 patched 覆盖 /system/bin/licenseMonitor (原地替换, 保持 SELinux 上下文).

    不改 init cfg — cfg 里的 path 本来就是 /system/bin/licenseMonitor, 原地替换后
    init 照常拉起, 只是执行的是打过补丁的二进制.

    为什么不把 patched 副本放 /data/local/tmp 再改 cfg 指过去:
      1) /data/local/tmp 的 SELinux 标签是 data_local_tmp, init 无法切换到
         u:r:licenseMonitor:s0 域 (域切换要求标签为 licenseMonitor_exec),
         会导致服务反复启动失败
      2) init 服务的库搜索路径/工作目录按 /system/bin 设定
      3) 实测: 该做法会让设备启动卡死

    风险控制:
      - 原二进制备份到 /data/local/tmp/license_patch/licenseMonitor.system.bak
      - chcon 还原 licenseMonitor_exec 标签
      - --disable 可用备份一键还原
    """
    # 1) 备份原二进制 (只做一次) —— 备份失败必须中止, 否则无法回滚
    if not device_file_exists(hdc, REMOTE_BACKUP_BIN):
        rc, out = hdc.shell(f"cp {REMOTE_BIN} {REMOTE_BACKUP_BIN} 2>&1")
        if rc != 0 or not device_file_exists(hdc, REMOTE_BACKUP_BIN):
            die(f"备份原二进制失败, 已中止 (避免无法回滚): {out.strip()[:200]}")
        log(f"已备份原二进制 -> {REMOTE_BACKUP_BIN}")
    # 2) 备份 cfg (万一之前误改过 cfg, 一并还原)
    hdc.shell(f"[ -f {REMOTE_CFG_BACKUP} ] || cp {REMOTE_CFG} {REMOTE_CFG_BACKUP} 2>/dev/null")
    # 3) 根分区只读 -> remount rw
    hdc.shell("mount -o remount,rw / 2>/dev/null")
    # 4) 覆盖 (先写临时名再原子替换, 避免半截文件)
    rc, out = hdc.shell(
        f"cp {REMOTE_PATCHED} {REMOTE_BIN}.new 2>&1 && "
        f"chmod 755 {REMOTE_BIN}.new 2>&1 && "
        f"chcon u:r:licenseMonitor_exec:s0 {REMOTE_BIN}.new 2>&1; "
        f"mv -f {REMOTE_BIN}.new {REMOTE_BIN} 2>&1; "
        f"ls -laZ {REMOTE_BIN}"
    )
    log("覆盖结果:")
    for line in out.strip().splitlines():
        log(f"    {line}")
    if "licenseMonitor_exec" not in out:
        log("警告: 未看到 licenseMonitor_exec 标签, 请人工确认 (chcon 可能不可用)")
    # 5) 还原 cfg 为原样 (确保 init 路径不变)
    hdc.shell(f"cp {REMOTE_CFG_BACKUP} {REMOTE_CFG} 2>/dev/null")
    # 6) 重启 licenseMonitor (init 会按原 cfg 重启它)
    hdc.shell("kill -9 $(pidof licenseMonitor) 2>/dev/null")
    # 7) 恢复根分区只读
    hdc.shell("mount -o remount,ro / 2>/dev/null")
    log("启用完成. 验证: hdc shell 'pidof licenseMonitor' + 'ls -laZ /system/bin/licenseMonitor'")


def device_disable(hdc: Hdc) -> None:
    """还原原二进制 (用备份覆盖回去)."""
    hdc.shell("mount -o remount,rw / 2>/dev/null")
    # 1) 还原二进制
    if device_file_exists(hdc, REMOTE_BACKUP_BIN):
        hdc.shell(
            f"cp {REMOTE_BACKUP_BIN} {REMOTE_BIN}.new 2>&1 && "
            f"chmod 755 {REMOTE_BIN}.new 2>&1 && "
            f"chcon u:r:licenseMonitor_exec:s0 {REMOTE_BIN}.new 2>&1; "
            f"mv -f {REMOTE_BIN}.new {REMOTE_BIN} 2>&1"
        )
        log("已用备份还原 /system/bin/licenseMonitor")
    else:
        log(f"警告: 找不到备份 {REMOTE_BACKUP_BIN}, 无法还原二进制")
    # 2) 还原 cfg
    if device_file_exists(hdc, REMOTE_CFG_BACKUP):
        hdc.shell(f"cp {REMOTE_CFG_BACKUP} {REMOTE_CFG}")
        log("已从备份恢复 cfg")
    else:
        hdc.shell(f"sed -i 's|{REMOTE_PATCHED}|{REMOTE_BIN}|g' {REMOTE_CFG}")
        log("已把 cfg path 改回原值")
    hdc.shell("kill -9 $(pidof licenseMonitor) 2>/dev/null")
    hdc.shell("mount -o remount,ro / 2>/dev/null")
    log("已恢复原状")


def device_file_exists(hdc: Hdc, path: str) -> bool:
    rc, out = hdc.shell(f"ls {path} 2>/dev/null")
    return rc == 0 and path in out


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="licenseMonitor 周期重启 patch 工具 (不修改系统分区)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True,
                    help="设备地址, 形如 192.168.19.9:8710")
    ap.add_argument("--hdc", default="", help="hdc 可执行文件路径 (默认用工程内)")
    ap.add_argument("--pull", action="store_true",
                    help="从设备拉取原始二进制到 --output")
    ap.add_argument("--output", default="", help="pull 的本地输出路径")
    ap.add_argument("--push", action="store_true",
                    help="把 patched 副本推送到设备 /data/local/tmp")
    ap.add_argument("--enable", action="store_true",
                    help="改 init cfg 指向 patched 副本并重启 licenseMonitor")
    ap.add_argument("--disable", action="store_true",
                    help="恢复 init cfg 原值")
    ap.add_argument("--status", action="store_true",
                    help="只打印设备端当前状态")
    args = ap.parse_args()

    hdc = Hdc(args.device, args.hdc)
    if not hdc.hdc_path:
        die("找不到 hdc")
    log(f"设备: {args.device}   hdc: {hdc.hdc_path}")

    hdc.connect()
    device_prepare(hdc)

    if args.status:
        rc, out = hdc.shell(
            f"echo '--- patched 副本 ---'; ls -la {REMOTE_PATCHED} 2>/dev/null; "
            f"echo '--- 进程 ---'; pidof licenseMonitor; "
            f"echo '--- cfg path ---'; grep -A2 path {REMOTE_CFG} | head -4; "
            f"echo '--- 监控间隔 ---'; param get persist.license.monitor.interval")
        log(out.strip())
        return 0

    if args.disable:
        device_disable(hdc)
        return 0

    # 拉取 + patch
    if args.output:
        local = args.output
        os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
    else:
        local = os.path.join(tempfile.mkdtemp(prefix="licpatch_"),
                             "licenseMonitor")

    if not os.path.exists(local):
        log("从设备拉取原始二进制…")
        device_pull_original(hdc, local)

    info = check_elf(local)
    log(f"ELF 校验通过: loongarch64, {info['size']} 字节")

    with open(local, "rb") as f:
        data = bytearray(f.read())
    status = patch_bytes(data)
    if status == "patched":
        with open(local, "wb") as f:
            f.write(data)
        log(f"patch 完成: 偏移 {hex(paddr_of(PATCH_VADDR))} "
            f"{OLD_BYTES.hex()} -> {NEW_BYTES.hex()}  ({NEW_DISASM})")
    else:
        log("文件已是 patched 状态, 跳过")

    v = verify_patched(local)
    log(f"校验: offset={hex(v['offset'])} bytes={v['bytes']} patched={v['patched']}")

    if args.output:
        log(f"本地输出: {local}")

    if args.push:
        device_push_patched(hdc, local)

    if args.enable:
        if not args.push:
            device_push_patched(hdc, local)
        device_enable(hdc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
