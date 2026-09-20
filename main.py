#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
OpenHarmony_Scrcpy 客户端入口
"""

import os
import sys
import traceback
from typing import NoReturn

from core import VERSION, LogLevel, print_log, init_logger, get_log_file
from core.platform_utils import (
    detect_platform, setup_environment, print_platform_banner,
    get_hdc_path, is_venv_available,
)
from gui import MainWindow


def _bootstrap_cross_platform() -> str:
    """跨平台引导: 设置环境 + 选 venv + 打印横幅.

    必须在 init_logger 之前调用 (确保 hdc 路径已设到 PATH).

    Returns:
        hdc 绝对路径 (setup_environment 已确保存在).
    """
    print_platform_banner()

    plat = detect_platform()
    project_root = os.path.dirname(os.path.abspath(__file__))

    # Windows 上 .bat 已经设了部分环境, 但 PATH 需要 Python 内部再加一次
    try:
        hdc_path = setup_environment(project_root)
    except FileNotFoundError as e:
        print(f"[main] 错误: {e}", file=sys.stderr)
        # 不 raise, 后面 GUI 启动时 hdc_executor 会再报错, 用户能看到具体原因

    # venv 提示 (用户体验)
    if not is_venv_available(project_root):
        print(f"[main] 提示: 未检测到项目内 venv312. 如果依赖缺失请执行:")
        print(f"        python3 -m venv venv312 && source venv312/bin/activate")
        print(f"        pip install -r requirements.txt")

    return get_hdc_path(project_root) or ""


def main() -> NoReturn:
    # 1. 跨平台环境 (必须在 init_logger 之前, 让 logger 能写到正确的日志目录)
    hdc_path = _bootstrap_cross_platform()

    # 2. 初始化日志
    init_logger()

    print("="*84)
    print(" "*15, f"OpenHarmony_Scrcpy Client - {VERSION} (author: luodh0157)", " "*15)
    print("="*84)

    log_file = get_log_file()
    if log_file:
        print_log(LogLevel.INFO, "主函数", f"日志文件: {log_file}")
    if hdc_path:
        print_log(LogLevel.INFO, "主函数",
            f"hdc: {hdc_path} (平台={detect_platform()})")

    try:
        app = MainWindow()
        app.run()
    except Exception as e:
        print_log(LogLevel.ERROR, "主函数", f"GUI启动失败: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()