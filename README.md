# OHScrcpy — OpenHarmony 投屏工具

[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-blue)]()

类似 Android `scrcpy`,但专为 **OpenHarmony** 设备设计
(适配 RK3568 / loongarch64 龙芯 等)。
低延迟屏幕镜像 + 鼠标/键盘控制 + 全平台 GUI 客户端。

## ✨ 特性

- 📺 **实时屏幕镜像**:设备原生分辨率(1080p)@ 20 FPS(MJPEG 模式,实测)
- 🖱️ **低延迟鼠标控制**:点击延迟 ~90ms(设备端常驻输入代理)
- 🏗️ **多架构支持**: aarch64 / arm / **loongarch64** / x86_64,自动识别 + 自动部署
- 🔄 **断线自恢复**:网络中断秒级检测,自动重连 + 重新部署
- 🎚️ **分辨率自动配置**:按设备物理分辨率抓帧,带宽不足时自动降级/升档
- ⌨️ **键盘映射**: F1=主页 / F2=返回 / D-pad 选择图标 / 字母数字字符
- 🎨 **现代 UI**: ttkbootstrap 暗色主题 + 品牌 Logo + 启动画面 + 状态指示器动画
- 🔌 **三平台支持**: Linux / macOS / Windows,统一启动脚本
- 🛠️ **零外部依赖**(除 Pillow / numpy / av / psutil),纯 Python + Tkinter

### 实测数据(loongarch64 设备,8 核)

| 指标 | 数值 |
|---|---|
| 分辨率 | 1920x1080(设备物理分辨率,自动配置) |
| 帧率 | **20 FPS**(峰值 25) |
| 点击延迟 | **~90ms** |
| 断线检测 | **0.25s**(服务被杀) / 15s(网络半开) |
| 断线恢复 | 自动重连 + 重新部署,约 10s |

## 📦 系统要求

| | 最低 | 推荐 |
|---|---|---|
| 设备 | OpenHarmony 5.0+(root) | RK3568 + OH3.5 / loongarch64 + KaihongOS 6.1 |
| 架构 | aarch64 / arm / loongarch64 / x86_64 | — |
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
│   ├── arch_detector.py     # ← 设备架构探测 (aarch64/arm/loongarch64/x86_64) + tconn 自动重连
│   ├── codec_detector.py    # ← 设备编码能力探测 (读 codec_caps.xml 自动选 H.264/MJPEG)
│   ├── selinux_helper.py    # ← SELinux 运行时切换 + 持久化 (只读根分区处理)
│   ├── input_agent_manager.py # ← 设备端常驻输入代理 (低延迟点击)
│   ├── mjpeg_server_manager.py
│   └── ...
├── device/                  # 设备端服务端源码 + 各架构二进制
│   ├── jpeg_server.cpp      # MJPEG 截图服务 (多 worker 并发抓帧)
│   ├── input_agent.cpp      # 常驻输入代理
│   └── oh264_capture.cpp    # H.264 原生抓屏
├── scripts/
│   ├── auto_push_server.py  # ← 启动时自动推送服务端到设备
│   ├── build_jpeg_server.sh # 编译 loongarch 版 jpeg_server
│   ├── build_input_agent.sh
│   └── verify_perf.sh       # ← 一键验证分辨率/帧率/断线检测延迟
├── gui/
│   ├── branding.py          # ← 品牌系统 (Logo / Splash / StatusDot / 渐变)
│   ├── ttk_bootstrap.py     # ← ttkbootstrap 组件封装
│   ├── main_window.py       # 主窗口 (含 SplashScreen 集成)
│   └── ...
├── docs/
│   └── H264_LOONGARCH_LIMITATION.md  # loongarch H.264 限制实测记录
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

### loongarch64 平台(龙芯)支持

自 v2.3.2 起支持 **loongarch64** 设备(如 KaihongOS 6.1 / 龙芯 3A6000),
主控端 macOS / Linux / Windows 均可。

#### 自动识别与部署

启动脚本会自动完成,无需手工干预:

1. `uname -m` 探测设备架构
2. 按架构选择对应的服务端二进制并校验 ELF 架构(`e_machine`)
3. 推送设备端服务并建立端口通道
4. 客户端**直连设备 IP**(免 hdc 端口转发,延迟更低且更稳定)

网络设备注意:设备的 hdc 连接**不持久**,设备重启或 hdc 进程重启后会丢失.
客户端会记住上次成功连接的设备(`~/.ohscrcpy_devices`),启动时自动 `tconn` 重连.

#### 设备端服务

loongarch 设备通常没有 busybox / toybox httpd,因此内置了自包含的 C++ 服务:

| 服务 | 作用 |
|---|---|
| `device/jpeg_server.cpp` | MJPEG 截图服务(多 worker 并发抓帧 + HTTP 服务) |
| `device/input_agent.cpp` | 常驻输入代理(消除每次点击的进程启动开销) |
| `device/oh264_capture.cpp` | H.264 原生抓屏(见下方限制说明) |

#### 编译 loongarch 版本

需要一台 x86_64 Linux 编译机 + loongarch SDK:

```bash
# SDK 路径在脚本里按需修改:
#   /media/ben/work_2026/opt/toolchains/ohos-sdk-5.1.0.53-loongarch
bash scripts/build_jpeg_server.sh              # -> device/jpeg_server_loongarch
bash scripts/build_input_agent.sh             # -> device/input_agent_loongarch64
bash scripts/build_h264_streamer_loongarch.sh # -> device/oh264_streamer_loongarch
```

编译要点:

- SDK 自带的 clang 是 aarch64 ELF,x86_64 主机跑不了 → 用系统 `/usr/lib/llvm-18/bin/clang`
  (只有 LLVM 18+ 内置 LoongArch 后端),链接器用 `/usr/lib/llvm-18/bin/ld.lld`
- **LoongArch 需要 16KB 页对齐**,否则设备上直接 SIGSEGV:
  `-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384`

#### SELinux

部分设备在 Enforcing 下会拒绝抓屏 / 事件注入. 程序会自动处理:

1. 运行时 `setenforce 0`
2. 持久化 `/system/etc/selinux/config`(设备重启后仍生效)

> 注意: 根分区常为只读挂载,直接 `sed -i` 会报 `sed: no temp file`
> (toybox 的 `sed -i` 需要在同目录建临时文件).
> 程序会先 `mount -o remount,rw /` 再改,改完恢复只读.

可用 `OHCRCPY_NO_SETENFORCE=1` / `OHCRCPY_NO_SELINUX_PERSIST=1` 关闭.

#### 已知限制:H.264 不可用

**loongarch64 设备的 H.264 硬件编码不可用**,已改用 MJPEG 路径.

根因: 设备的 `/system/etc/codec/codec_caps.xml` 里 `<VideoEncoder>` 段只声明了
`avenc_mpeg4`,**没有 AVC**. 而 `OH_VideoEncoder_CreateByMime(AVC)` 仍会返回
非空句柄,`Configure` / `GetSurface` / `Prepare` / `Start` 全部 `rc=0`,
看起来完全成功,但输出回调最多只触发 1 帧 —— 表现为"连上后无图像".

程序会读 `codec_caps.xml` 判断,**明确列出 avc/h264 时才选 H.264**,
否则自动走 MJPEG. 完整的四种 API 组合实测记录见
[`docs/H264_LOONGARCH_LIMITATION.md`](docs/H264_LOONGARCH_LIMITATION.md).

### Windows 平台特殊说明

- **hdc 路径**:`hdc\Windows\x64\hdc.exe`(项目自带)
- **libusb**:`hdc\Windows\x64\libusb_shared.dll`(项目自带)
- **PATH**:`run_windows.bat` 自动把 hdc 目录加入 PATH
- **hdc stdio**:Windows 上 hdc 偶尔有 stderr 噪音,不影响功能

### macOS 平台特殊说明

- **DYLD_LIBRARY_PATH**:必须设置才能找到 `libusb_shared.dylib`
- **Gatekeeper**:首次运行可能需要 `右键 → 打开` 绕过未签名警告
- **Apple Silicon (M1/M2/M3)**:原生 arm64 版 hdc(`hdc/Darwin/arm64/`),自动选用,无需 Rosetta

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
| `OHCRCPY_CODEC` | (自动探测) | 强制 codec:`mjpeg` / `h264`,默认按设备能力自动选 |
| `OHCRCPY_MJPEG_WIDTH` | 0 | 抓帧宽度,0 = 按设备物理分辨率(自动) |
| `OHCRCPY_MJPEG_HEIGHT` | 0 | 抓帧高度,0 = 按设备物理分辨率(自动) |
| `OHCRCPY_MJPEG_PORT` | 27190 | MJPEG 服务端口 |
| `OHCRCPY_MJPEG_WORKERS` | 6 | 设备端抓帧并发数(8 核实测 6 最优) |
| `OHCRCPY_MJPEG_INTERVAL` | 0.05 | 单 worker 模式下的截图间隔(秒) |
| `OHCRCPY_MJPEG_STREAM` | 1 | 用 MJPEG multipart 流(比单帧轮询快 3 倍) |
| `OHCRCPY_MJPEG_AUTO_RES` | 1 | 带宽不足时自动降级/升档分辨率 |
| `OHCRCPY_HEARTBEAT_TIMEOUT` | 15 | 心跳超时(秒),超过判定断线 |
| `OHCRCPY_TCONN` | (unset) | 手动指定设备地址,逗号分隔(优先于自动发现) |
| `OHCRCPY_AUTO_PUSH` | 1 | 启动时自动推送服务端到设备 |
| `OHCRCPY_AUTO_PUSH_CODEC` | server | 推送哪种服务端:`server`/`h264`/`jpeg`/`input_agent` |
| `OHCRCPY_NO_INPUT_AGENT` | (unset) | 设为 1 禁用输入代理(点击延迟会变高) |
| `OHCRCPY_INPUT_AGENT_PORT` | 27195 | 输入代理端口 |
| `OHCRCPY_NO_SETENFORCE` | (unset) | 设为 1 不动 SELinux |
| `OHCRCPY_NO_SELINUX_PERSIST` | (unset) | 设为 1 只改运行时,不写配置文件 |
| `OHCRCPY_NO_SPLASH` | (unset) | 设为 1 跳过启动画面(单元测试用) |
| `OHCRCPY_THEME` | darkly | ttkbootstrap 主题 |
| `DCEPR_HDC` | (unset) | 自定义 hdc 路径 |

## 🐛 故障排查

| 问题 | 解决 |
|---|---|
| **"无法连接到服务端"** | 可能有两个实例在跑互相踢设备端服务;启动脚本会跨目录自动清理旧实例. 也可跑 `scripts/verify_perf.sh` 诊断 |
| **网络断开后没反应** | 已修复: 断线秒级检测并自动恢复. 心跳超时可用 `OHCRCPY_HEARTBEAT_TIMEOUT` 调 |
| **设备连不上** | 网络设备的 hdc 连接不持久,设备重启后 IP 可能变化. 程序会自动 `tconn` 上次连接的设备;也可 `OHCRCPY_TCONN=host:port` 指定 |
| 画面卡住不动 | 检查设备端 `jpeg_server` 是否存活: `hdc shell pidof ohscrcpy_jpeg_server` |
| 画面分辨率不对 | 默认按设备物理分辨率抓帧;可用 `OHCRCPY_MJPEG_WIDTH/HEIGHT` 固定 |
| 点击很慢 | 已修复: 设备端常驻输入代理, ~380ms → ~90ms. 确认代理在跑: `hdc shell pidof ohscrcpy_input_agent` |
| `sed: no temp file` | 根分区只读,改 SELinux 配置需先 `mount -o remount,rw /`. 程序已自动处理 |
| loongarch 上 H.264 无图像 | 该设备 codec_caps.xml 无 AVC,已自动回退 MJPEG. 详见 `docs/H264_LOONGARCH_LIMITATION.md` |
| 黑屏但日志显示 render 成功 | RDP 用户:加宽窗口(标题栏顶端光带消失即被 dock 遮挡) |
| 鼠标点击无反应 | 检查设备是否解锁 |
| hdc 未找到 | `ls hdc/<platform>/<arch>/` 应该有 hdc 二进制 |
| macOS `Library not loaded` | 检查 `hdc/Darwin/<arch>/libusb_shared.dylib` 是否存在 |
| Windows 启动闪退 | 启用虚拟环境:`venv312\Scripts\activate` |

## 📝 更新日志

### v2.3.2 (loongarch64 支持 + 性能与稳定性)

**loongarch64(龙芯)支持**
- ✅ 架构感知: `core/arch_detector.py` 探测 `uname -m`,校验 ELF 架构后推送
- ✅ 自包含设备端服务: `jpeg_server`(无 busybox 设备可用)、`input_agent`
- ✅ 启动时自动推送 + 设备端二进制搜索复用
- ✅ 客户端直连设备 IP(免 hdc 端口转发,更快更稳)
- ✅ SELinux 自动处理(运行时 + 持久化,含只读根分区 remount)
- ✅ H.264 能力按 `codec_caps.xml` 真实声明判断(见下方限制)

**性能**
- ✅ 帧率 11.2 → **20 FPS**(1920x1080,多 worker 并发抓帧)
- ✅ 分辨率提升到**设备物理分辨率**(实测抓帧耗时与分辨率几乎无关)
- ✅ MJPEG multipart 流模式(比单帧轮询快 3 倍)
- ✅ 自适应分辨率: 带宽不足自动降级,恢复后自动升档

**交互**
- ✅ 点击延迟 380ms → **~90ms**(设备端常驻输入代理)
- ✅ 断线检测 60s → **0.25s**(服务被杀)/ 15s(网络半开)
- ✅ 断线自动恢复 + 明确的失败原因提示

**修复**
- ✅ 多实例并存导致"无法连接到服务端"(跨目录单实例保护)
- ✅ 点击坐标偏移(抓帧分辨率 ≠ 设备物理分辨率时的二次换算)
- ✅ ttkbootstrap 迁移导致的启动崩溃 `unknown option "-bootstyle"`

### v2.3.1 (跨平台 + UI 美化)
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
