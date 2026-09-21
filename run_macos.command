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

# 依赖自检 (venv 重建后常见 requests 缺失, 否则会在 GUI 里误报 "无法连接到服务端")
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
    echo "[run_macos] 检测到 Python 环境缺少 requests, 正在自动安装..."
    if "$PY" -m pip install --quiet requests; then
        echo "[run_macos] requests 安装完成"
    else
        echo "[run_macos] 错误: requests 安装失败, 请手动执行: $PY -m pip install requests"; exit 1
    fi
fi

# macOS 关键: 让 hdc 找到 libusb_shared.dylib
# 注意: hdc 子目录按 CPU 架构区分 (x64 / arm64), 不能硬编码 x64.
# 与 core/platform_utils.py 的 detect_arch() 保持一致:
#   x86_64 -> x64 ; arm64/aarch64 -> arm64
MAC_ARCH="$(uname -m)"
case "$MAC_ARCH" in
  x86_64|amd64) HDC_ARCH_DIR="x64" ;;
  arm64|aarch64) HDC_ARCH_DIR="arm64" ;;
  *) HDC_ARCH_DIR="$MAC_ARCH" ;;
esac
if [ -f "$DIR/hdc/Darwin/$HDC_ARCH_DIR/libusb_shared.dylib" ]; then
    export DYLD_LIBRARY_PATH="$DIR/hdc/Darwin/$HDC_ARCH_DIR:${DYLD_LIBRARY_PATH:-}"
    echo "[run_macos] hdc arch: Darwin/$HDC_ARCH_DIR"
else
    echo "[run_macos] 警告: 未找到 hdc/Darwin/$HDC_ARCH_DIR/libusb_shared.dylib"
fi

# MJPEG 模式
export OHCRCPY_MJPEG_MODE=${OHCRCPY_MJPEG_MODE:-1}
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-1080}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-1920}

# 关键修复: 启动前主动清理设备端残留的 ohscrcpy_server (HEVC 模式产物).
# 如果设备端残留 ohscrcpy_server, 它会占用 27183 端口并响应 SCREEN_INFO banner,
# 导致 mjpeg_client 在 27190 端口拿到错误 banner -> 黑屏.
# 注: MjpegServerManager._stop_background 也会清, 这里提前一层以减少首次连接失败.
HDC="${DIR}/hdc/Darwin/${HDC_ARCH_DIR}/hdc"
if [ -x "$HDC" ]; then
    export DYLD_LIBRARY_PATH="$DIR/hdc/Darwin/${HDC_ARCH_DIR}:${DYLD_LIBRARY_PATH:-}"
    # 异步清理所有在线设备, 不阻塞 main.py 启动
    # set +e 临时关闭: list targets 在没设备时返回非零 exit code
    set +e
    for dev_target in $("$HDC" list targets 2>/dev/null | grep -E "^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$"); do
        "$HDC" -t "$dev_target" shell \
            "pkill -9 -f ohscrcpy_server; pkill -9 -f busybox; pkill -9 -f snapshot_display" \
            >/dev/null 2>&1 &
    done
    set -e
fi

# 单实例
if pgrep -f "$PY.*main.py" > /dev/null; then
    echo "[run_macos] 检测到旧实例, 自动关闭..."
    pkill -9 -f "$PY.*main.py" 2>/dev/null || true
    sleep 1
fi

echo "== OHScrcpy macOS 启动 (luodh 版 v2.3) =="
exec "$PY" "$DIR/main.py" "$@"
