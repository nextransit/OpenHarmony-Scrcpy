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
# codec 由设备能力自动探测 (core/codec_detector.py):
#   优先 H.264 (原生抓屏 Surface 零拷贝, loongarch 实测 28fps)
#   无 H.264 能力时回退 MJPEG (busybox httpd / 自建 jpeg_server)
# 显式设置 OHCRCPY_CODEC=h264|mjpeg 可强制指定.
# H.264 抓屏分辨率/帧率
export OHCRCPY_H264_WIDTH=${OHCRCPY_H264_WIDTH:-1280}
export OHCRCPY_H264_HEIGHT=${OHCRCPY_H264_HEIGHT:-720}
export OHCRCPY_H264_FPS=${OHCRCPY_H264_FPS:-30}
export OHCRCPY_H264_BITRATE=${OHCRCPY_H264_BITRATE:-8000000}
# 960x540 + 0.05s 间隔: 实测 20fps (设备端 snapshot_display 约 13fps 上限,
# 加上 HTTP 传输与客户端解码后稳定在 18-20fps).
# 1280x720 + 0.1s 间隔只有 4-5fps, 因为间隔太长白白浪费了抓帧时间.
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-960}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-540}
export OHCRCPY_MJPEG_INTERVAL=${OHCRCPY_MJPEG_INTERVAL:-0.05}


# 自动推送服务端到已连接设备 (按设备架构自动选二进制)
# OHCRCPY_AUTO_PUSH=0 可禁用; OHCRCPY_AUTO_PUSH_CODEC=h264 推 oh264_streamer
if [ "${OHCRCPY_AUTO_PUSH:-1}" = "1" ]; then
    PUSH_CODEC="${OHCRCPY_AUTO_PUSH_CODEC:-server}"
    if [ -f "$DIR/scripts/auto_push_server.py" ]; then
        echo "[run_macos] 自动推送服务端 (codec=$PUSH_CODEC)..."
        "$PY" "$DIR/scripts/auto_push_server.py" --codec "$PUSH_CODEC" || \
            echo "[run_macos] 警告: 自动推送未完全成功, GUI 启动时会重试"
        # 同时预推 jpeg_server: 设备无 busybox 时由 MjpegServerManager 使用
        # (预推后 codec 探测即可直接判定 MJPEG 可用)
        if [ "$PUSH_CODEC" != "jpeg" ]; then
            "$PY" "$DIR/scripts/auto_push_server.py" --codec jpeg || true
        fi
        # 预推设备端常驻输入代理: 把点击延迟从 ~380ms 降到 ~71ms
        # (代理不可用时 GUI 自动回退到 hdc shell uinput)
        if [ "$PUSH_CODEC" != "input_agent" ]; then
            "$PY" "$DIR/scripts/auto_push_server.py" --codec input_agent || true
        fi
    fi
fi

# 单实例
if pgrep -f "$PY.*main.py" > /dev/null; then
    echo "[run_macos] 检测到旧实例, 自动关闭..."
    pkill -9 -f "$PY.*main.py" 2>/dev/null || true
    sleep 1
fi

echo "== OHScrcpy macOS 启动 (BenZhou 版 v2.3) =="
exec "$PY" "$DIR/main.py" "$@"
