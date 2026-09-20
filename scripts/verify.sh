#!/usr/bin/env bash
# ohscrcpy 跨平台快速验证脚本
# 一键检查三平台启动器 + hdc 二进制 + 单元测试是否就绪
set -e
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

pass=0; fail=0
ok()   { echo "  [OK] $1"; pass=$((pass+1)); }
bad()  { echo "  [FAIL] $1"; fail=$((fail+1)); }
section() { echo ""; echo "== $1 =="; }

section "Python 环境"
PY=$(command -v python3 || command -v python || true)
if [ -n "$PY" ]; then
    ok "Python: $($PY --version 2>&1) ($PY)"
else
    bad "未找到 python3 / python"
fi

if [ -x "venv312/bin/python" ]; then
    ok "venv312 存在: venv312/bin/python"
else
    bad "venv312 不存在, 请运行 python3 -m venv venv312 && pip install -r requirements.txt"
fi

section "hdc 二进制 (本平台)"
# 按当前平台/架构检查 hdc 子目录 (与 core/platform_utils.py detect_arch 一致)
case "$(uname -s)" in
  Darwin) PLAT="Darwin" ;;
  Linux)  PLAT="Linux" ;;
  *)      PLAT="$(uname -s)" ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH="x64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *) ARCH="$(uname -m)" ;;
esac
PLAT_DIR="hdc/$PLAT/$ARCH"
if [ -d "$PLAT_DIR" ]; then
    files=$(ls "$PLAT_DIR" 2>/dev/null | tr '\n' ' ')
    ok "$PLAT_DIR: $files"
    if [ -x "$PLAT_DIR/hdc" ] || [ -f "$PLAT_DIR/hdc" ]; then
        ok "$PLAT_DIR/hdc 存在"
    else
        bad "$PLAT_DIR/hdc 不存在"
    fi
else
    bad "$PLAT_DIR 不存在"
fi

section "启动脚本"
for script in run_linux.sh run_macos.command run_windows.bat; do
    if [ -f "$script" ]; then
        if [ -x "$script" ] || [[ "$script" == *.bat ]]; then
            ok "$script 存在"
        else
            bad "$script 不可执行: chmod +x $script"
        fi
    else
        bad "$script 不存在"
    fi
done

section "单元测试"
if [ -x "venv312/bin/python" ]; then
    if venv312/bin/python tests/test_platform.py > /dev/null 2>&1; then
        ok "test_platform.py 全部通过"
    else
        bad "test_platform.py 有失败"
    fi
    if command -v xvfb-run >/dev/null 2>&1; then
        if OHCRCPY_NO_SPLASH=1 xvfb-run -a venv312/bin/python tests/test_branding.py > /dev/null 2>&1; then
            ok "test_branding.py 全部通过"
        else
            bad "test_branding.py 有失败"
        fi
    else
        echo "  [SKIP] test_branding.py (无 xvfb-run)"
    fi
else
    bad "跳过 (venv312 不存在)"
fi

section "git 状态"
if [ -d ".git" ]; then
    head_sha=$(git rev-parse --short HEAD 2>/dev/null || echo "(none)")
    ok "git HEAD: $head_sha"
    if [ -z "$(git status --short 2>/dev/null)" ]; then
        ok "工作区干净"
    else
        echo "  [WARN] 有未提交变更:"
        git status --short | sed 's/^/    /'
    fi
else
    bad "非 git 仓库"
fi

echo ""
echo "================================"
echo "Pass: $pass  Fail: $fail"
echo "================================"
exit $([ $fail -eq 0 ] && echo 0 || echo 1)
