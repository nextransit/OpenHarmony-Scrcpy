#!/usr/bin/env bash
# 验证 loongarch 设备的两个改进: 网络断线提示速度 + 分辨率/帧率.
#
# 用法: bash scripts/verify_perf.sh [device_sn] [port]
#   device_sn 默认 192.168.19.11:8710
#   port       默认 27190 (jpeg_server)
#
# 检查项:
#   1. 设备端 jpeg_server 自动分辨率 (是否用物理分辨率抓帧)
#   2. 客户端实测帧率 + 接收带宽
#   3. /screen.res 运行时改分辨率是否生效
#   4. 断线检测延迟 (杀设备端服务 -> 客户端多久报断开)
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
SN="${1:-192.168.19.11:8710}"
PORT="${2:-27190}"
PY="$DIR/venv312/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
HDC="$DIR/hdc/Darwin/arm64/hdc"

echo "== 设备: $SN =="
$HDC tconn "$SN" >/dev/null 2>&1
sleep 1
$HDC -t "$SN" shell "uname -m; getenforce" 2>&1 | tail -2

echo
echo "== 1. 部署 jpeg_server (自动分辨率) =="
"$PY" - <<PYEOF
import sys, os
sys.path.insert(0, "$DIR")
os.environ['OHCRCPY_MJPEG_WIDTH'] = '0'
os.environ['OHCRCPY_MJPEG_HEIGHT'] = '0'
from core.hdc_executor import HDCCommandExecutor
from core.mjpeg_server_manager import MjpegServerManager
h = HDCCommandExecutor(); h.set_device("$SN")
m = MjpegServerManager(h)
print("setup:", m.setup())
print("抓帧分辨率:", m.width, "x", m.height)
PYEOF

echo
echo "== 2. 设备端实际抓帧分辨率 =="
$HDC -t "$SN" shell "grep -E '自动分辨率|设备物理分辨率' /data/local/tmp/ohscrcpy_jpeg_server.log | tail -2" 2>&1

echo
echo "== 3. 客户端实测帧率/带宽 =="
"$PY" - <<PYEOF
import sys, os, time
sys.path.insert(0, "$DIR")
from video.mjpeg_client import MjpegStreamClient
frames = []
c = MjpegStreamClient(host="192.168.19.11", port=$PORT,
                      on_frame_decoded=lambda a: frames.append((time.time(), len(a))))
if not c.connect("192.168.19.11", $PORT, timeout=8):
    print("连接失败"); sys.exit(1)
time.sleep(12)
c.disconnect()
if len(frames) > 2:
    span = frames[-1][0] - frames[0][0]
    fps = (len(frames) - 1) / span
    print(f"帧率: {fps:.1f} fps ({len(frames)} 帧 / {span:.1f}s)")
    print(f"接收带宽: {c.total_bytes*8/span/1_000_000:.0f} Mbps")
    print(f"帧尺寸: {frames[-1][1]}")
else:
    print("帧数不足:", len(frames))
PYEOF

echo
echo "== 4. /screen.res 运行时改分辨率 =="
$HDC -t "$SN" shell "curl -s 'http://127.0.0.1:$PORT/screen.res?w=640&h=360' 2>/dev/null || echo '(设备无 curl, 用客户端测)'" 2>&1 | tail -2
"$PY" - <<PYEOF
import sys
sys.path.insert(0, "$DIR")
import requests
r = requests.get("http://192.168.19.11:$PORT/screen.res?w=640&h=360", timeout=8)
print("调整后:", r.text)
PYEOF

echo
echo "== 5. 断线检测延迟 (杀设备端服务) =="
"$PY" - <<PYEOF
import sys, os, time, subprocess
sys.path.insert(0, "$DIR")
from video.mjpeg_client import MjpegStreamClient
disconnected = {}
def on_disc(reason=""):
    disconnected['t'] = time.time()
    disconnected['reason'] = reason
c = MjpegStreamClient(host="192.168.19.11", port=$PORT, on_frame_decoded=lambda a: None)
c.on_disconnected = on_disc
if not c.connect("192.168.19.11", $PORT, timeout=8):
    print("连接失败"); sys.exit(1)
time.sleep(2)
print("杀掉设备端 jpeg_server ...")
subprocess.run(["$DIR/hdc/Darwin/arm64/hdc", "-t", "$SN", "shell",
                "pkill -9 -f ohscrcpy_jpeg_server"], capture_output=True)
t0 = time.time()
while time.time() - t0 < 40:
    if 't' in disconnected:
        print(f"检测耗时: {disconnected['t']-t0:.1f} 秒")
        print(f"原因: {disconnected['reason']}")
        break
    time.sleep(0.2)
else:
    print("40 秒内未检测到断线 ❌")
c.disconnect()
PYEOF
