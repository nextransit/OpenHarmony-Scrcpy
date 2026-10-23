#!/usr/bin/env bash
# 编译设备端 input_agent (常驻输入代理, 自包含, 仅依赖 musl).
#
# 用途: 把"每次点击 = 一次 hdc shell uinput"(实测 ~380ms, 抖动到 3.4s)
#       改成"客户端 -> 持久 TCP -> 常驻代理 -> uinput":
#       客户端往返仅 ~15ms, 代理内部批量执行 uinput 约 90ms/次.
#
# 用法: bash scripts/build_input_agent.sh [host] [target]
#   host   默认 ben@192.168.26.106 (x86_64 Ubuntu)
#   target 默认 loongarch64-linux-ohos
#
# 关键点 (全部经真机验证):
#   1. SDK 里的 clang 是 aarch64 ELF, x86_64 主机跑不了 -> 用系统 /usr/lib/llvm-18/bin/clang
#   2. 需 lld-18 (apt install lld-18); llvm-15 自带 lld 不认识 elf64loongarch
#   3. sysroot 头按架构分目录, 需 -isystem usr/include/<target>
#   4. C++ 头在 native/llvm/include/libcxx-ohos/include/c++/v1
#   5. libc++/libunwind 在 native/llvm/lib/<target>
#   6. [关键] LoongArch 需要 16KB 页对齐, 否则设备上直接 SIGSEGV:
#        -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384
set -euo pipefail

HOST="${1:-ben@192.168.26.106}"
TARGET="${2:-loongarch64-linux-ohos}"
SDK="/media/ben/work_2026/opt/toolchains/ohos-sdk-5.1.0.53-loongarch"
REMOTE_DIR="input_agent_build"
BIN="input_agent_${TARGET}"

cd "$(dirname "$0")/.."

echo "== 检查远端工具链 =="
ssh -o BatchMode=yes "${HOST}" "
  set -e
  SDK=${SDK}
  test -d \$SDK || { echo \"SDK 不存在: \$SDK\"; exit 1; }
  test -d \$SDK/native/sysroot/usr/lib/${TARGET} || { echo \"sysroot 无 ${TARGET}\"; exit 1; }
  test -x /usr/lib/llvm-18/bin/clang || { echo 'NEED clang-18 (apt install clang-18)'; exit 1; }
  test -x /usr/lib/llvm-18/bin/ld.lld || { echo 'NEED lld-18 (apt install lld-18)'; exit 1; }
  mkdir -p ~/${REMOTE_DIR}
  cd ~/${REMOTE_DIR}
  # clang 的 resource dir 需要 <target 三元组> 目录名 (loongarch64-unknown-linux-ohos),
  # SDK 里只有 loongarch64-linux-ohos. 建软链别名 (与 build_h264_streamer_loongarch.sh 一致).
  # 注意: 必须同时检查"实体目录里真的有文件" — 上次失败会留下空的
  # rd15/lib/loongarch64-linux-ohos/, 此时 [ -d rd15 ] 为真但内容缺失,
  # lld 会报 "cannot open libclang_rt.builtins.a".
  if [ ! -f rd15/lib/${TARGET}/libclang_rt.builtins.a ]; then
    rm -rf rd15
    cp -r \$SDK/native/llvm/lib/clang/15.0.4 rd15
    # clang 找的是 <arch>-unknown-linux-ohos (注意是 unknown, 不是 linux-ohos),
    # 而 SDK 里只有 <arch>-linux-ohos. 目录名必须写成字面量, 不能用 ${TARGET} 拼接
    # (那样会得到 loongarch64-linux-ohos-unknown-linux-ohos, 名字不对).
    cp -r rd15/lib/${TARGET} rd15/lib/loongarch64-unknown-linux-ohos
    cp -r rd15/lib/${TARGET} rd15/lib/loongarch64-unknown-linux-gnu
  fi
  echo TOOLCHAIN_OK
"

echo "== 上传源码 =="
scp -o BatchMode=yes device/input_agent.cpp ${HOST}:~/${REMOTE_DIR}/input_agent.cpp

echo "== 远端编译 =="
ssh -o BatchMode=yes "${HOST}" "
  set -e
  SDK=${SDK}; cd ~/${REMOTE_DIR}
  SYSROOT=\$SDK/native/sysroot
  LIB=\$SYSROOT/usr/lib/${TARGET}
  CXX=\$SDK/native/llvm/include/libcxx-ohos/include/c++/v1
  LLVMLIB=\$SDK/native/llvm/lib/${TARGET}

  /usr/lib/llvm-18/bin/clang++ --target=${TARGET} --sysroot=\$SYSROOT \
    -isystem \$SYSROOT/usr/include/${TARGET} \
    -isystem \$CXX \
    -resource-dir \$PWD/rd15 \
    -B \$LIB -L \$LIB -L \$LLVMLIB \
    -fuse-ld=/usr/lib/llvm-18/bin/ld.lld \
    -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
    -O2 -std=c++17 -pthread \
    input_agent.cpp -o ${BIN} \
    \$LLVMLIB/libc++_static.a \$LLVMLIB/libc++abi.a \$LLVMLIB/libunwind.a
  echo BUILD_OK
"

echo "== 取回产物 =="
scp -o BatchMode=yes "${HOST}:~/${REMOTE_DIR}/${BIN}" ./device/input_agent_${TARGET%%-linux-ohos}
file ./device/input_agent_${TARGET%%-linux-ohos}
echo "== 完成: device/input_agent_${TARGET%%-linux-ohos} =="
