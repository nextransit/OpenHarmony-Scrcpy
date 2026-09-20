# OHScrcpy — OpenHarmony 投屏工具

[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-blue)]()

类似 Android `scrcpy`,但专为 **OpenHarmony** 设备设计(适配 RK3568 等)。
低延迟屏幕镜像 + 鼠标/键盘控制 + 全平台 GUI 客户端。

## ✨ 特性

- 📺 **实时屏幕镜像**:1080p @ 10-18 FPS(MJPEG 模式)
- 🖱️ **鼠标控制**:点击/滑动/拖拽, 坐标自动换算
- ⌨️ **键盘映射**: F1=主页 / F2=返回 / D-pad 选择图标 / 字母数字字符
- 🎨 **现代 UI**: Slate 暗色主题 + 品牌 Logo + 启动画面 + 状态指示器动画
- 🔌 **三平台支持**: Linux / macOS / Windows,统一启动脚本
- 🛠️ **零外部依赖**(除 Pillow / numpy / av / psutil),纯 Python + Tkinter

## 📦 系统要求

| | 最低 | 推荐 |
|---|---|---|
| 设备 | OpenHarmony 5.0+(root) | RK3568 + OH3.5 |
| Python | 3.9 | 3.12 |
| 内存 | 200 MB | 500 MB |
| 网络 | USB 或 TCP/IP | TCP/IP(局域网) |

## 🚀 快速开始

### 1. 克隆 / 下载项目

```bash
git clone <repo> ohscrcpy
cd ohscrcpy
```

### 2. 创建虚拟环境 + 装依赖

**Linux / macOS**:
```bash
python3 -m venv venv312
source venv312/bin/activate
pip install -r requirements.txt
```

**Windows**(PowerShell):
```powershell
python -m venv venv312
.\venv312\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 启动

**Linux**:
```bash
./run_linux.sh
```

**macOS**:
```bash
./run_macos.command
# 或命令行
bash run_macos.command
```

**Windows**:
- 双击 `run_windows.bat`
- 或命令行:`run_windows.bat`

启动脚本会自动:
1. 检测平台 + 选 hdc 二进制
2. 设置 PATH / DYLD_LIBRARY_PATH(macOS 需要 libusb)
3. 单实例保护(旧实例自动关闭)
4. 设置 MJPEG 模式(绕过 RK3568 HEVC bug)
5. 启动 1.5 秒品牌启动画面(Splash)

### 4. 自动化测试

```bash
bash tests/run_all.sh
```

跑全部 14 个测试套件(Pillow + PillBadge + UI回归 + 集成 + 跨平台 + 品牌)。

## 🔧 跨平台架构

```
ohscrcpy/
├── core/
│   ├── platform_utils.py    # ← 跨平台核心 (detect_platform / get_hdc_path / setup_environment)
│   ├── hdc_executor.py      # hdc 命令执行器 (自动 fallback 到 platform_utils)
│   ├── mjpeg_server_manager.py
│   └── ...
├── gui/
│   ├── branding.py          # ← 品牌系统 (Logo / Splash / StatusDot / 渐变)
│   ├── widgets.py           # PillBadge (淡入淡出) + ModernButton + PhoneFrame
│   ├── main_window.py       # 主窗口 (含 SplashScreen 集成)
│   └── ...
├── hdc/                     # hdc 二进制 (Linux/Darwin/Windows × x64/arm64)
├── run_linux.sh             # Linux 启动器
├── run_macos.command        # macOS 启动器 (双击即用)
├── run_windows.bat          # Windows 启动器 (双击即用)
└── tests/                   # 自动化测试
```

`core/platform_utils.py` 提供跨平台抽象:

```python
from core.platform_utils import (
    detect_platform,  # 'linux' / 'darwin' / 'windows'
    detect_arch,      # 'x64' / 'arm64' / 'arm'
    get_hdc_path,     # 自动选 hdc/Linux/x64/hdc 等
    setup_environment,  # 设 PATH / DYLD_LIBRARY_PATH
    print_platform_banner,  # 启动横幅
)
```

### Windows 平台特殊说明

- **hdc 路径**:`hdc\Windows\x64\hdc.exe`(项目自带)
- **libusb**:`hdc\Windows\x64\libusb_shared.dll`(项目自带)
- **PATH**:`run_windows.bat` 自动把 hdc 目录加入 PATH
- **hdc stdio**:Windows 上 hdc 偶尔有 stderr 噪音,不影响功能

### macOS 平台特殊说明

- **DYLD_LIBRARY_PATH**:必须设置才能找到 `libusb_shared.dylib`
- **Gatekeeper**:首次运行可能需要 `右键 → 打开` 绕过未签名警告
- **Apple Silicon (M1/M2)**:目前用 x64 版本(Rosetta 兼容),arm64 native 待支持

## 🎨 UI 品牌系统

`gui/branding.py` 提供:

- `BrandColor`:科技感蓝青配色(`#0ea5e9` / `#06b6d4` / `#22c55e` / `#ef4444`)
- `Logo`:Canvas 自绘矢量 logo(手机 + 投屏波纹)
- `SplashScreen`:1.5 秒渐显启动画面
- `StatusDot`:连接状态动画(connecting 旋转 / connected 脉冲 / error 闪烁)
- `interpolate_color`:渐变色插值工具

## ⌨️ 键盘快捷键

| 键 | 动作 |
|---|---|
| F1 | 主页 |
| F2 | 返回 |
| F3 / F4 | 音量 + / - |
| F5 | 电源键 |
| F6 | 截屏保存 |
| F8 | 切换 OSD 显示 (FPS/帧数/尺寸) |
| F9 | 强制 GC |
| F12 | 开关 键盘映射 |
| Shift+F8 | 调试窗口 |
| D-pad | 桌面图标选择 |
| 字母数字 | 字符输入 |

## ⚙️ 环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `OHCRCPY_MJPEG_MODE` | 1 | MJPEG 模式开关(绕过 RK3568 HEVC bug) |
| `OHCRCPY_MJPEG_WIDTH` | 1080 | 截图宽度 |
| `OHCRCPY_MJPEG_HEIGHT` | 1920 | 截图高度 |
| `OHCRCPY_MJPEG_PORT` | 27190 | MJPEG 服务端口 |
| `OHCRCPY_NO_SPLASH` | (unset) | 设为 1 跳过启动画面(单元测试用) |
| `DCEPR_HDC` | (unset) | 自定义 hdc 路径 |

## 🐛 故障排查

| 问题 | 解决 |
|---|---|
| 黑屏但日志显示 render 成功 | RDP 用户:加宽窗口(标题栏顶端光带消失即被 dock 遮挡) |
| 鼠标点击无反应 | 检查设备是否解锁,uitest daemon 是否启动 |
| hdc 未找到 | `ls hdc/<platform>/<arch>/` 应该有 hdc 二进制 |
| macOS `Library not loaded` | 检查 `hdc/Darwin/x64/libusb_shared.dylib` 是否存在 |
| Windows 启动闪退 | 启用虚拟环境:`venv312\Scripts\activate` |

## 📝 更新日志

### v2.3.x (跨平台 + UI 美化)
- ✅ 跨平台骨架: `core/platform_utils.py` + 三平台启动脚本
- ✅ 品牌 UI: Splash 启动画面 + Logo + StatusDot 动画
- ✅ PillBadge 淡入淡出
- ✅ 标题栏顶部光带
- ✅ 自适应窗口(RDP / 普通桌面 / 全屏)
- ✅ Click/key 路径优化(540ms → 220ms)
- ✅ MJPEG 服务稳定化(busybox httpd 监听重试)
- ✅ 单实例保护

### 详见 `logs/` 下的运行日志

## 📜 License

Apache License 2.0
