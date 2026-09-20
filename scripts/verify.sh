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

section "hdc 二进制 (三平台)"
for plat_dir in hdc/Linux/x64 hdc/Darwin/x64 hdc/Windows/x64; do
    if [ -d "$plat_dir" ]; then
        files=$(ls "$plat_dir" 2>/dev/null | tr '\n' ' ')
        ok "$plat_dir: $files"
    else
        bad "$plat_dir 不存在"
    fi
done

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
