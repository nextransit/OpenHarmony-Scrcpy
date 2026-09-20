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
export OHCRCPY_MJPEG_MODE=${OHCRCPY_MJPEG_MODE:-1}
export OHCRCPY_MJPEG_WIDTH=${OHCRCPY_MJPEG_WIDTH:-1080}
export OHCRCPY_MJPEG_HEIGHT=${OHCRCPY_MJPEG_HEIGHT:-1920}

# 单实例保护
if pgrep -f "$PY.*main.py" > /dev/null; then
    echo "[run_linux] 检测到旧实例, 自动关闭..."
    pkill -9 -f "$PY.*main.py" 2>/dev/null || true
    sleep 1
fi

echo "== OHScrcpy Linux 启动 (luodh 版 v2.3) =="
exec "$PY" "$DIR/main.py" "$@"
