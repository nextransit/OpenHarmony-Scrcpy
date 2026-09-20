#!/usr/bin/env bash
# ohscrcpy 跨平台启动 - macOS 版 (双击 .command 即可启动)
# macOS 上 busbox httpd 通过 .dylib 注入触摸需要 DYLD_LIBRARY_PATH
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 优先用 venv312
if [ -x "$DIR/venv312/bin/python3" ]; then
    PY="$DIR/venv312/bin/python3"
elif [ -x "$DIR/venv/bin/python3" ]; then
    PY="$DIR/venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
    echo "[run_macos] 警告: 未找到 venv, 使用系统 python3"
else
    echo "[run_macos] 错误: 未找到 python3"; exit 1
fi

# macOS 关键: 让 hdc 找到 libusb_shared.dylib
if [ -f "$DIR/hdc/Darwin/x64/libusb_shared.dylib" ]; then
    export DYLD_LIBRARY_PATH="$DIR/hdc/Darwin/x64:${DYLD_LIBRARY_PATH:-}"
fi

# MJPEG 模式
export OHCRCPY_MJPEG_MODE=${OHCRCPY_MJPEG_MODE:-1}
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-1080}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-1920}

# 单实例
if pgrep -f "$PY.*main.py" > /dev/null; then
    echo "[run_macos] 检测到旧实例, 自动关闭..."
    pkill -9 -f "$PY.*main.py" 2>/dev/null || true
    sleep 1
fi

echo "== OHScrcpy macOS 启动 (luodh 版 v2.3) =="
exec "$PY" "$DIR/main.py" "$@"
