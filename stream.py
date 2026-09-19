#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ohscrcpy 一键投屏（直连模式，绕过 /system 只读安装）
用法: venv/bin/python stream.py [-t 192.168.26.160:5555]
适配设备: AIOT-3568A (aarch64, OpenHarmony/ShiMetaOS)
"""
import argparse, os, socket, struct, subprocess, sys, time

SERVER_BIN  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HUAWEI", "ohscrcpy_server")
DEVICE_PATH = "/data/local/tmp/ohscrcpy_server"
DEVICE_PORT = 27183
LOCAL_PORT  = 27183
HDC = "/media/ben/work_2021/toolchains/ohos-sdk/ohos-sdk/linux/toolchains/hdc"

def hdc(*args, timeout=15):
    cmd = [HDC, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", dest="target", default=None)
    args = ap.parse_args()

    prefix = ["-t", args.target] if args.target else []
    print(f"[1/5] 设备在线检查: {args.target or 'auto'}")
    r = hdc(*prefix, "shell", "echo ok")
    if r.returncode != 0 or "ok" not in (r.stdout or ""):
        print("  设备不可达"); sys.exit(1)
    print("  在线 OK")

    print("[2/5] 推送 aarch64 服务端 → /data/local/tmp")
    r = hdc(*prefix, "file", "send", SERVER_BIN, DEVICE_PATH)
    if r.returncode != 0:
        print("  推送失败", r.stderr); sys.exit(1)
    hdc(*prefix, "shell", "chmod 755 " + DEVICE_PATH)

    print(f"[3/5] 端口转发 tcp:{LOCAL_PORT} → tcp:{DEVICE_PORT}")
    hdc(*prefix, "fport", "rm", f"tcp:{LOCAL_PORT}", f"tcp:{DEVICE_PORT}")
    r = hdc(*prefix, "fport", f"tcp:{LOCAL_PORT}", f"tcp:{DEVICE_PORT}")
    if r.returncode != 0 and "Forwardport result:OK" not in (r.stdout or ""):
        print("  转发失败", r.stdout, r.stderr)

    print("[4/5] 启动设备端 ohscrcpy_server")
    hdc(*prefix, "shell", f"pkill -f {DEVICE_PATH}")
    hdc(*prefix, "shell", f"setsid {DEVICE_PATH} -p {DEVICE_PORT} -l > /data/local/tmp/ohsrv.log 2>&1 &")
    time.sleep(3)
    r = hdc(*prefix, "shell", f"grep -c ohscrcpy_video /proc/net/unix 2>/dev/null; pgrep -f {DEVICE_PATH} | head -1")
    print("  服务端:", (r.stdout or "").strip() or "未确认，继续尝试")

    print("[5/5] 连接 127.0.0.1:27183 并解码（模拟 luodh 客户端握手）")
    s = socket.socket(); s.settimeout(8)
    try:
        s.connect(("127.0.0.1", LOCAL_PORT))
    except Exception as e:
        print("  连接失败:", e); sys.exit(1)
    cfg = s.recv(1024).split(b"\n")[0].decode(errors="ignore")
    print("  服务端配置:", cfg)
    s.sendall(b"CONFIG_ACK\n")
    start = time.time(); frames = 0; bytes_sum = 0
    while time.time() - start < 5 and frames < 4:
        try:
            hdr = s.recv(8)
        except socket.timeout:
            break
        if len(hdr) < 8: break
        ptype, length = struct.unpack(">II", hdr)
        left = length
        while left > 0:
            c = s.recv(left)
            if not c: break
            left -= len(c)
        bytes_sum += length; frames += 1
    print(f"  收到 {frames} 包 / {bytes_sum} 字节 —— 投屏链路{'OK' if frames>=2 else '失败'}")
    s.close()

if __name__ == "__main__":
    main()
