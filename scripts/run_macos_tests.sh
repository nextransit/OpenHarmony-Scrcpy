#!/usr/bin/env bash
# run_macos 自动化测试入口: 一键验证 黑屏 + 滚轮 + 控制协议 修复
# 运行: bash scripts/run_macos_tests.sh
set -e
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

PY="$PROJECT_ROOT/venv312/bin/python3"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3)"
fi

export PYTHONPATH="$PROJECT_ROOT"
export OHCRCPY_NO_SPLASH=1

echo "============================================================"
echo "run_macos 自动化测试"
echo "Python: $PY"
echo "Project: $PROJECT_ROOT"
echo "============================================================"

# 0. 控制协议编码器 (文档第四节 binary protocol, 触摸/按键/滚轮/文本)
echo ""
echo "== 0) tests/test_control_protocol.py (binary protocol encoder) =="
"$PY" tests/test_control_protocol.py

# 1. 核心 smoke 测试 (run_macos 启动链路 + 滚轮)
echo ""
echo "== 1) tests/test_run_macos_smoke.py (黑屏 + 滚轮) =="
"$PY" tests/test_run_macos_smoke.py

# 2. 鼠标滚轮详细回归
echo ""
echo "== 2) tests/test_mouse_wheel.py (滚轮绑定 + native mouse scroll) =="
"$PY" tests/test_mouse_wheel.py

# 3. 集成测试 (无黑屏 + 主线程 render 调度)
echo ""
echo "== 3) tests/test_integration_mainwindow.py (集成) =="
"$PY" tests/test_integration_mainwindow.py

# 4. 黑屏回归 (mjpeg_client 主线程调度)
echo ""
echo "== 4) tests/test_black_screen_repro.py (黑屏 bug 回归) =="
"$PY" tests/test_black_screen_repro.py

# 5. 点击边界 / 异步 (基础链路)
echo ""
echo "== 5) tests/test_click_async.py (点击链路) =="
"$PY" tests/test_click_async.py

echo ""
echo "============================================================"
echo "全部通过"
echo "============================================================"
