#!/usr/bin/env python3
"""自动截图脚本: 启动 ohscrcpy GUI, 截取启动界面和投屏界面.

用法:
  venv312/bin/python3 scripts/capture_screenshots.py

截图保存在项目根目录:
  客户端启动GUI.png  — 未连接状态
  客户端投屏GUI.png  — 已连接投屏状态 (需要设备已连接)
"""

import os
import sys
import time
import subprocess
import signal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(PROJECT_ROOT, "venv312", "bin", "python3")
if not os.path.exists(PY):
    PY = sys.executable

def capture_window(title_keyword, output_path, wait_seconds=8):
    """用 screencapture 截取包含指定标题的窗口."""
    time.sleep(wait_seconds)
    # macOS screencapture -x 静默截取全屏
    subprocess.run(
        ["screencapture", "-x", output_path],
        check=True, timeout=10
    )
    print(f"[截图] 已保存: {output_path}")

def main():
    # 清理旧实例
    subprocess.run(["pkill", "-9", "-f", "python.*main.py"],
                   capture_output=True)
    time.sleep(1)

    # 设置环境
    env = os.environ.copy()
    env["OHCRCPY_MJPEG_MODE"] = "1"
    env["OHCRCPY_MJPEG_WIDTH"] = "1080"
    env["OHCRCPY_MJPEG_HEIGHT"] = "1920"

    # macOS hdc dylib
    arch = os.uname().machine
    arch_dir = {"x86_64": "x64", "amd64": "x64",
                "arm64": "arm64", "aarch64": "arm64"}.get(arch, arch)
    dylib_path = os.path.join(PROJECT_ROOT, "hdc", "Darwin", arch_dir)
    if os.path.exists(os.path.join(dylib_path, "libusb_shared.dylib")):
        env["DYLD_LIBRARY_PATH"] = dylib_path + ":" + env.get("DYLD_LIBRARY_PATH", "")

    # 启动程序
    print("[截图] 启动 ohscrcpy...")
    proc = subprocess.Popen(
        [PY, os.path.join(PROJECT_ROOT, "main.py")],
        env=env, cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        # 截取启动界面 (未连接状态)
        print("[截图] 等待启动界面...")
        capture_window(
            "OHScrcpy",
            os.path.join(PROJECT_ROOT, "客户端启动GUI.png"),
            wait_seconds=10
        )

        # 截取投屏界面 (等待自动连接)
        print("[截图] 等待投屏画面 (15秒)...")
        capture_window(
            "OHScrcpy",
            os.path.join(PROJECT_ROOT, "客户端投屏GUI.png"),
            wait_seconds=15
        )
    finally:
        print("[截图] 关闭程序...")
        proc.terminate()
        time.sleep(1)
        subprocess.run(["pkill", "-9", "-f", "python.*main.py"],
                       capture_output=True)

    print("[截图] 完成!")

if __name__ == "__main__":
    main()
