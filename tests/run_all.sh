#!/bin/bash
set -e
cd "$(dirname "$0")/.."
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
echo "=== ALL PASS ==="
