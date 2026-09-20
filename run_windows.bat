@echo off
REM ohscrcpy 跨平台启动 - Windows 版 (双击即可启动)
setlocal enabledelayedexpansion

REM 切到脚本所在目录
cd /d "%~dp0"

REM 优先用 venv312
if exist "venv312\Scripts\python.exe" (
    set "PY=%CD%\venv312\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY=%CD%\venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "delims=" %%i in ('where python') do set "PY=%%i" & goto :got_py
    ) else (
        echo [run_windows] 错误: 未找到 python. 请安装 Python 3.12+ 或运行 python -m venv venv312 ^&^& venv312\Scripts\activate ^&^& pip install -r requirements.txt
        pause
        exit /b 1
    )
)
:got_py
if not defined PY (
    echo [run_windows] 错误: 未找到 python
    pause
    exit /b 1
)

REM 单实例保护
tasklist /FI "IMAGENAME eq python.exe" /V | findstr /I "main.py" >nul 2>&1
if !errorlevel! equ 0 (
    echo [run_windows] 检测到旧实例, 自动关闭...
    taskkill /F /IM python.exe /FI "WINDOWTITLE eq *OHScrcpy*" >nul 2>&1
    timeout /t 1 /nobreak >nul
)

REM MJPEG 模式 (绕过 RK3568 HEVC bug)
if not defined OHCRCPY_MJPEG_MODE set OHCRCPY_MJPEG_MODE=1
if not defined OHCRCPY_MJPEG_WIDTH set OHCRCPY_MJPEG_WIDTH=1080
if not defined OHCRCPY_MJPEG_HEIGHT set OHCRCPY_MJPEG_HEIGHT=1920

REM Windows 上 PATH 已含 hdc 子目录 (hdc 在 Windows/x64/), 通过 set_environment() 在 Python 内设置
echo == OHScrcpy Windows 启动 (luodh 版 v2.3) ==
"%PY%" "%CD%\main.py" %*
