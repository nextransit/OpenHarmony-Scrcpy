#!/usr/bin/env bash
# ohscrcpy 投屏客户端（luodh0157/OpenHarmony_Scrcpy 版，适配 aarch64 设备）
# MJPEG 模式默认开启（绕过 RK3568 OpenHarmony 的 HEVC 硬件编码器 bug）。
# 若要切回原始 HEVC 模式，请将下面这行注释掉或改为 OHCRCPY_MJPEG_MODE=0。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$DIR/hdc/Linux/x64:$PATH"
export OHCRCPY_MJPEG_MODE=1
# 默认 1080p (设备原始分辨率),可改为 720 降分辨率提高帧率
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-1080}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-1920}

echo "== OHScrcpy 客户端（luodh 版 v2.3）=="
command -v hdc >/dev/null || { echo "hdc 不可用"; exit 1; }

# 单实例保护: 启动前杀掉已有 main.py 进程 (避免多实例残留导致 RDP 多个窗口堆叠)
if pgrep -f 'python.*main.py' > /dev/null; then
    echo "[run.sh] 检测到旧实例, 自动关闭..."
    pkill -9 -f 'python.*main.py' 2>/dev/null || true
    sleep 1
fi

hdc list targets -v 2>&1 | grep -v 'FreeChannel' || true
echo "------------------------------------------------------------"
cd "$DIR"
exec "$DIR/venv312/bin/python" "$DIR/main.py" "$@"
