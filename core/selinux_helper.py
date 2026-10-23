#!/usr/bin/env python

# Copyright (c) 2026 BenZhou.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""
设备端 SELinux 处理 (运行时切换 + 持久化配置).

为什么需要:
  部分 OHOS 设备在 SELinux Enforcing 下会拒绝:
    - snapshot_display 抓屏 (表现为抓帧失败 / jpeg_server 拿不到像素)
    - OH_AVScreenCapture_Init (返回 OPERATE_NOT_PERMIT)
    - uinput 触摸/鼠标事件注入

两层处理:
  1. 运行时: `setenforce 0` 立即生效, 设备重启后失效
  2. 持久化: 改 /system/etc/selinux/config, 设备重启后仍生效

持久化的坑 (真机 192.168.19.11:8710 loongarch 实测):
  直接执行
      sed -i 's/SELINUX=enforcing/SELINUX=permissive/g' /system/etc/selinux/config
  会失败并报 "sed: no temp file".
  原因: 根分区 `/` 是只读挂载 (ro), 设备上的 sed 是 toybox 的软链,
  toybox 的 `sed -i` 需要在目标文件同目录创建临时文件再 rename,
  只读文件系统上根本建不出来.
  解法: 先 `mount -o remount,rw /`, 改完再 `mount -o remount,ro /` 恢复原状.

环境变量:
  OHCRCPY_NO_SETENFORCE=1     跳过全部 SELinux 处理
  OHCRCPY_NO_SELINUX_PERSIST=1 只做运行时切换, 不改配置文件
"""

import os
from typing import Optional

from .constants import LogLevel
from .logger import print_log

SELINUX_CONFIG = "/system/etc/selinux/config"


def _run(hdc_executor, command: str, timeout: int = 10) -> str:
    """执行设备端命令, 返回 stdout (失败返回空串)."""
    try:
        result = hdc_executor.execute(["shell", command], timeout=timeout)
    except Exception:
        return ""
    if not result.get("success"):
        return ""
    return (result.get("stdout") or "").strip()


def get_state(hdc_executor) -> str:
    """返回 SELinux 状态: enforcing / permissive / disabled, 未知返回空串."""
    return _run(hdc_executor, "getenforce 2>/dev/null", timeout=5).strip().lower()


def set_permissive_runtime(hdc_executor) -> bool:
    """运行时切到 Permissive (setenforce 0)."""
    out = _run(hdc_executor, "setenforce 0 2>/dev/null; getenforce 2>/dev/null", timeout=8)
    return out.strip().lower() == "permissive"


def persist_permissive(hdc_executor, log_title: str = "SELinux") -> bool:
    """把配置文件改成 SELINUX=permissive (设备重启后仍生效).

    只读根分区必须先 remount, 否则 toybox sed 报 "no temp file".
    任何失败都只返回 False, 不抛异常 — 持久化是锦上添花,
    运行时 setenforce 0 已经够用.
    """
    if os.environ.get("OHCRCPY_NO_SELINUX_PERSIST", "") == "1":
        return False

    # 已经改过就不动
    cnt = _run(hdc_executor,
               f"grep -c 'SELINUX=permissive' {SELINUX_CONFIG} 2>/dev/null", timeout=5)
    if cnt.strip() not in ("", "0"):
        return True

    # 只读根分区: 必须先 remount 才能 sed -i
    remount = _run(hdc_executor, "mount -o remount,rw / 2>/dev/null; echo rc=$?", timeout=12)
    if "rc=0" not in remount:
        print_log(LogLevel.WARN, log_title,
            "remount 根分区为可写失败, SELinux 配置无法持久化 (不影响本次使用)")
        return False

    cmd = (f"sed -i 's/SELINUX=enforcing/SELINUX=permissive/g' {SELINUX_CONFIG} 2>/dev/null; "
           f"grep -c 'SELINUX=permissive' {SELINUX_CONFIG} 2>/dev/null")
    out = _run(hdc_executor, cmd, timeout=12)
    lines = [x for x in out.splitlines() if x.strip()]
    ok = bool(lines) and lines[-1].strip() not in ("", "0")
    if ok:
        print_log(LogLevel.INFO, log_title,
            "SELinux 配置已持久化为 permissive (设备重启后仍生效)")
    else:
        print_log(LogLevel.WARN, log_title,
            "SELinux 配置持久化失败 (不影响本次使用)")

    # 尽量恢复只读挂载, 保持设备原状
    _run(hdc_executor, "mount -o remount,ro / 2>/dev/null", timeout=10)
    return ok


def ensure_permissive(hdc_executor, log_title: str = "SELinux",
                       persist: bool = True) -> bool:
    """确保设备 SELinux 处于 Permissive.

    返回 True 表示当前已是 permissive (可用于抓屏/注入);
    False 表示无法切换 (非 root / 无权限), 调用方应继续原流程而不是中断.
    """
    if os.environ.get("OHCRCPY_NO_SETENFORCE", "") == "1":
        return False

    state = get_state(hdc_executor)
    if state == "permissive":
        if persist:
            persist_permissive(hdc_executor, log_title)
        return True
    if state != "enforcing":
        return False

    if set_permissive_runtime(hdc_executor):
        print_log(LogLevel.WARN, log_title,
            "设备 SELinux 为 Enforcing, 已自动切换到 Permissive (抓屏/输入注入需要)")
        if persist:
            persist_permissive(hdc_executor, log_title)
        return True
    return False


__all__ = [
    "SELINUX_CONFIG",
    "get_state",
    "set_permissive_runtime",
    "persist_permissive",
    "ensure_permissive",
]
