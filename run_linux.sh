#!/usr/bin/env bash
# ohscrcpy 跨平台启动 - Linux 版
# 自动用项目 venv312 (推荐), fallback 到系统 python3
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 优先用 venv312 (requirements.txt 已装好)
if [ -x "$DIR/venv312/bin/python" ]; then
    PY="$DIR/venv312/bin/python"
elif [ -x "$DIR/venv/bin/python" ]; then
    PY="$DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
    echo "[run_linux] 警告: 未找到 venv, 使用系统 python3 ($PY). 如果缺依赖请: pip install -r requirements.txt"
else
    echo "[run_linux] 错误: 未找到 python3"; exit 1
fi

# 导出 MJPEG 模式 (绕过 RK3568 HEVC bug)
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
# 抓帧分辨率: 0 = 自动 (设备端探测物理分辨率, 最高画质).
# 实测 snapshot_display 耗时与分辨率几乎无关 (瓶颈是进程启动), 提分辨率几乎不损失帧率;
# 带宽不足时客户端会按实测速率自动降级 (video/mjpeg_client.py AUTO_RES_LADDER).
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-0}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-0}
export OHCRCPY_MJPEG_INTERVAL=${OHCRCPY_MJPEG_INTERVAL:-0.05}
# 抓帧并发数 (8 核设备实测: 6 路 ~9.6fps, 8 路 ~11.2fps)
export OHCRCPY_MJPEG_WORKERS=${OHCRCPY_MJPEG_WORKERS:-6}
# 自适应分辨率开关 (0 = 关闭, 固定用上面的分辨率)
export OHCRCPY_MJPEG_AUTO_RES=${OHCRCPY_MJPEG_AUTO_RES:-1}


# 自动推送服务端到已连接设备 (按设备架构自动选二进制)
# OHCRCPY_AUTO_PUSH=0 可禁用; OHCRCPY_AUTO_PUSH_CODEC=h264 推 oh264_streamer
if [ "${OHCRCPY_AUTO_PUSH:-1}" = "1" ]; then
    PUSH_CODEC="${OHCRCPY_AUTO_PUSH_CODEC:-server}"
    if [ -f "$DIR/scripts/auto_push_server.py" ]; then
        echo "[run_linux] 自动推送服务端 (codec=$PUSH_CODEC)..."
        "$PY" "$DIR/scripts/auto_push_server.py" --codec "$PUSH_CODEC" || \
            echo "[run_linux] 警告: 自动推送未完全成功, GUI 启动时会重试"
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

# 单实例保护
if pgrep -f "$PY.*main.py" > /dev/null; then
    echo "[run_linux] 检测到旧实例, 自动关闭..."
    pkill -9 -f "$PY.*main.py" 2>/dev/null || true
    sleep 1
fi

echo "== OHScrcpy Linux 启动 (BenZhou 版 v2.3) =="
exec "$PY" "$DIR/main.py" "$@"
