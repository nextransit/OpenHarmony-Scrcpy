"""
跨平台工具 - 检测平台 + hdc 路径 + 环境设置
============================================

支持: Linux / macOS / Windows
设计:
  - 不依赖第三方库 (纯 stdlib: sys, os, platform, shutil, subprocess)
  - 单例: 模块级 PLATFORM 变量在 import 时确定一次
  - 健壮: 找不到 hdc 时返回 None, 让上层决定 fallback (PATH 查找 / 提示安装)

典型用法:
    from core.platform_utils import detect_platform, get_hdc_path, setup_environment

    plat = detect_platform()                    # 'linux' / 'darwin' / 'windows'
    hdc_path = get_hdc_path()                    # '/abs/path/hdc' 或 None
    setup_environment()                          # 设 PATH / DYLD_LIBRARY_PATH
"""
import os
import sys
import platform
import shutil
from typing import Optional


# 模块级常量 (单例, import 时确定)
PLATFORM: str = ""
PLATFORM_ARCH: str = ""
HDC_SUBDIR: str = ""
HDC_EXE_NAME: str = ""
LIBUSB_DYLIB: Optional[str] = None  # macOS 需要 libusb_shared.dylib


def _init_once() -> None:
    """初始化模块级常量 (只调用一次)."""
    global PLATFORM, PLATFORM_ARCH, HDC_SUBDIR, HDC_EXE_NAME, LIBUSB_DYLIB
    if PLATFORM:
        return  # 已初始化
    sys_platform = sys.platform.lower()
    if sys_platform.startswith("linux"):
        PLATFORM = "linux"
    elif sys_platform.startswith("darwin"):
        PLATFORM = "darwin"
    elif sys_platform.startswith("win") or sys_platform.startswith("cygwin"):
        PLATFORM = "windows"
    else:
        PLATFORM = "unknown"

    # 架构: x86_64 / arm64 / aarch64
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        PLATFORM_ARCH = "x64"
    elif machine in ("arm64", "aarch64"):
        PLATFORM_ARCH = "arm64"
    elif machine.startswith("arm"):
        PLATFORM_ARCH = "arm"
    else:
        PLATFORM_ARCH = machine

    if PLATFORM == "linux":
        HDC_SUBDIR = f"Linux/{PLATFORM_ARCH}"
        HDC_EXE_NAME = "hdc"
    elif PLATFORM == "darwin":
        HDC_SUBDIR = f"Darwin/{PLATFORM_ARCH}"
        HDC_EXE_NAME = "hdc"
        # macOS 需要 libusb dylib 配合
        candidate = os.path.join("hdc", HDC_SUBDIR, "libusb_shared.dylib")
        if os.path.exists(candidate):
            LIBUSB_DYLIB = os.path.abspath(candidate)
    elif PLATFORM == "windows":
        HDC_SUBDIR = f"Windows/{PLATFORM_ARCH}"
        HDC_EXE_NAME = "hdc.exe"
    else:
        HDC_SUBDIR = ""
        HDC_EXE_NAME = ""


_init_once()


def detect_platform() -> str:
    """返回当前平台标识: 'linux' / 'darwin' / 'windows' / 'unknown'."""
    return PLATFORM


def detect_arch() -> str:
    """返回架构: 'x64' / 'arm64' / 'arm' / ...  ."""
    return PLATFORM_ARCH


def get_hdc_path(project_root: Optional[str] = None) -> Optional[str]:
    """返回 hdc 可执行文件的绝对路径.

    查找顺序:
      1. project_root/hdc/<subdir>/hdc[.exe]   (项目自带的二进制)
      2. 系统 PATH 中 (通过 shutil.which)

    Args:
        project_root: 项目根目录 (含 hdc/ 子目录).  None = 自动用当前工作目录的祖先定位.

    Returns:
        绝对路径字符串, 找不到返回 None.
    """
    if project_root is None:
        # 自动定位: 假定 core/platform_utils.py 在 <project_root>/core/
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    bundled = os.path.join(project_root, "hdc", HDC_SUBDIR, HDC_EXE_NAME)
    if os.path.exists(bundled) and os.access(bundled, os.X_OK):
        return os.path.abspath(bundled)

    # Windows 上 .exe 的 os.access 不可靠 (PE 文件), 直接 size > 0 即可
    if PLATFORM == "windows" and os.path.exists(bundled) and os.path.getsize(bundled) > 0:
        return os.path.abspath(bundled)

    # fallback: PATH
    found = shutil.which(HDC_EXE_NAME)
    return found


def get_libusb_path() -> Optional[str]:
    """macOS 专用: 返回 libusb_shared.dylib 绝对路径 (若存在)."""
    return LIBUSB_DYLIB


def setup_environment(project_root: Optional[str] = None) -> str:
    """设置跨平台环境 (PATH / DYLD_LIBRARY_PATH), 返回 hdc 路径.

    必须先于 HDCCommandExecutor 创建之前调用, 让 hdc 二进制能被 os.exec 找到.

    Returns:
        hdc 可执行文件绝对路径 (如果找不到会 raise FileNotFoundError).
    """
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    hdc_dir = os.path.join(project_root, "hdc", HDC_SUBDIR)

    # 1. 把 hdc 子目录加到 PATH, 让后续 subprocess 找得到
    if hdc_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = hdc_dir + os.pathsep + os.environ.get("PATH", "")

    # 2. macOS: DYLD_LIBRARY_PATH (防止 libusb 找不到)
    if PLATFORM == "darwin" and LIBUSB_DYLIB:
        current = os.environ.get("DYLD_LIBRARY_PATH", "")
        if os.path.dirname(LIBUSB_DYLIB) not in current:
            os.environ["DYLD_LIBRARY_PATH"] = os.path.dirname(LIBUSB_DYLIB) + os.pathsep + current

    # 3. Windows: PATHEXT 已含 .exe, 不需要额外设置

    hdc_path = get_hdc_path(project_root)
    if hdc_path is None:
        raise FileNotFoundError(
            f"hdc 未找到. 请确认项目目录下 hdc/{HDC_SUBDIR}/{HDC_EXE_NAME} 存在, "
            f"或将 hdc 加入系统 PATH."
        )
    return hdc_path


def get_python_executable() -> str:
    """返回当前 Python 解释器路径 (用于启动脚本调用)."""
    return sys.executable


def get_venv_python(project_root: str, venv_name: str = "venv312") -> str:
    """返回项目内 venv 的 python 路径 (如果存在).

    优先于系统 python, 因为 requirements 已在 venv 装好.
    """
    if PLATFORM == "windows":
        candidate = os.path.join(project_root, venv_name, "Scripts", "python.exe")
    else:
        candidate = os.path.join(project_root, venv_name, "bin", "python")
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    return get_python_executable()


def is_venv_available(project_root: str, venv_name: str = "venv312") -> bool:
    """检查项目内 venv 是否就绪."""
    py = get_venv_python(project_root, venv_name)
    return os.path.exists(py)


def print_platform_banner() -> None:
    """打印平台信息横幅 (启动时调用, 方便用户调试)."""
    import sys as _sys
    print(f"== OHScrcpy 跨平台启动 ==", flush=True)
    print(f"  Python : {_sys.version.split()[0]} ({_sys.executable})", flush=True)
    print(f"  Platform: {PLATFORM} ({PLATFORM_ARCH})", flush=True)
    print(f"  hdc target: hdc/{HDC_SUBDIR}/{HDC_EXE_NAME}", flush=True)
    if LIBUSB_DYLIB:
        print(f"  libusb: {LIBUSB_DYLIB}", flush=True)


__all__ = [
    "PLATFORM",
    "PLATFORM_ARCH",
    "HDC_SUBDIR",
    "HDC_EXE_NAME",
    "LIBUSB_DYLIB",
    "detect_platform",
    "detect_arch",
    "get_hdc_path",
    "get_libusb_path",
    "setup_environment",
    "get_python_executable",
    "get_venv_python",
    "is_venv_available",
    "print_platform_banner",
]
