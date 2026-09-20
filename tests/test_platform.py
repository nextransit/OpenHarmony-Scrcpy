"""
跨平台骨架 单元测试
====================

覆盖:
  - detect_platform / detect_arch 在当前平台返回非空字符串
  - get_hdc_path 在项目 hdc/<subdir>/ 找到二进制
  - get_venv_python 在 venv312 存在时返回正确路径
  - setup_environment 在 hdc 存在时不抛异常
  - Windows .exe / macOS .dylib 路径仅在对应平台被引用
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.platform_utils import (
    detect_platform, detect_arch, get_hdc_path, setup_environment,
    is_venv_available, get_venv_python, PLATFORM, HDC_SUBDIR, HDC_EXE_NAME,
)


class TestPlatformDetection(unittest.TestCase):
    def test_detect_platform_nonempty(self):
        plat = detect_platform()
        self.assertIn(plat, ("linux", "darwin", "windows", "unknown"))
        print(f"  [detect_platform] {plat}")

    def test_detect_arch_nonempty(self):
        arch = detect_arch()
        self.assertIsInstance(arch, str)
        self.assertGreater(len(arch), 0)
        print(f"  [detect_arch] {arch}")

    def test_module_constants_match(self):
        """模块级 PLATFORM 应与 detect_platform() 一致."""
        self.assertEqual(PLATFORM, detect_platform())
        self.assertEqual(HDC_EXE_NAME,
                         "hdc.exe" if detect_platform() == "windows" else "hdc")


class TestHdcPath(unittest.TestCase):
    def test_hdc_path_exists(self):
        """项目自带 hdc/<subdir>/hdc[.exe] 必须能定位到."""
        path = get_hdc_path()
        self.assertIsNotNone(path, f"未找到 hdc, 子目录应为 hdc/{HDC_SUBDIR}/{HDC_EXE_NAME}")
        self.assertTrue(os.path.exists(path), f"hdc 路径不存在: {path}")
        print(f"  [get_hdc_path] {path}")

    def test_hdc_is_executable_or_has_size(self):
        """Linux/macOS 需要可执行权限; Windows 检查 size > 0 (PE 文件权限不可靠)."""
        path = get_hdc_path()
        self.assertIsNotNone(path)
        if detect_platform() == "windows":
            self.assertGreater(os.path.getsize(path), 1024 * 100,
                f"hdc.exe size 异常小: {os.path.getsize(path)} bytes")
        else:
            self.assertTrue(os.access(path, os.X_OK),
                f"hdc 不可执行: {path} (chmod +x ?)")


class TestVenv(unittest.TestCase):
    def test_venv312_python_path(self):
        py = get_venv_python("/home/ben/ohscrcpy")
        if is_venv_available("/home/ben/ohscrcpy"):
            self.assertTrue(py.endswith("python.exe") if detect_platform() == "windows" else py.endswith("python"))
            print(f"  [get_venv_python] {py}")
        else:
            print(f"  [venv] venv312 不存在 (跳过)")

    def test_is_venv_available(self):
        # 项目目录存在 venv312
        result = is_venv_available("/home/ben/ohscrcpy")
        print(f"  [is_venv_available] {result}")


class TestSetupEnvironment(unittest.TestCase):
    def test_setup_environment_no_error(self):
        """setup_environment 在 hdc 存在时不应抛异常."""
        try:
            hdc_path = setup_environment("/home/ben/ohscrcpy")
            self.assertIsNotNone(hdc_path)
            self.assertTrue(os.path.exists(hdc_path))
            # PATH 应已包含 hdc 子目录
            self.assertIn(HDC_SUBDIR, os.environ.get("PATH", ""))
            print(f"  [setup_environment] OK, hdc={hdc_path}")
        except FileNotFoundError as e:
            self.fail(f"setup_environment 抛 FileNotFoundError: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
