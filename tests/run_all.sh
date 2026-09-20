#!/bin/bash
# ohscrcpy 全测套件 - 自动化跑
# - 跳过 SplashScreen (OHCRCPY_NO_SPLASH=1, 单元测试不需要)
# - xvfb-run 提供虚拟 X display (Linux headless)
# - 所有测试 PASS 才算通过 (set -e)
set -e
cd "$(dirname "$0")/.."
export OHCRCPY_NO_SPLASH=1

echo "=== Pill Badge ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_pill_badge.py
echo "=== Button ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_button_interactive.py
echo "=== UI regression ==="
xvfb-run -a -s "-screen 0 1450x900x24" /home/ben/ohscrcpy/venv312/bin/python tests/test_ui_regression.py
echo "=== MainWindow thread-safe ==="
xvfb-run -a -s "-screen 0 1450x900x24" /home/ben/ohscrcpy/venv312/bin/python tests/test_mainwindow_thread_safe.py
echo "=== Real device dim ==="
xvfb-run -a -s "-screen 0 1450x900x24" /home/ben/ohscrcpy/venv312/bin/python tests/test_real_device_dim.py
echo "=== Click boundary ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_click_boundary.py
echo "=== Click async ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_click_async.py
echo "=== DeviceController HDC ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_device_controller_hdc.py
echo "=== Black screen repro ==="
xvfb-run -a -s "-screen 0 1450x900x24" /home/ben/ohscrcpy/venv312/bin/python tests/test_black_screen_repro.py
echo "=== Poll stable 30s ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_poll_stable_30s.py
echo "=== JPEG truncated ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_jpeg_truncated.py
echo "=== E2E FPS ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_e2e_fps.py
echo "=== Integration mainwindow ==="
xvfb-run -a -s "-screen 0 1450x900x24" /home/ben/ohscrcpy/venv312/bin/python tests/test_integration_mainwindow.py
echo "=== Platform cross-platform ==="
/home/ben/ohscrcpy/venv312/bin/python tests/test_platform.py
echo "=== Branding ==="
xvfb-run -a /home/ben/ohscrcpy/venv312/bin/python tests/test_branding.py

echo "=== ALL PASS ==="
