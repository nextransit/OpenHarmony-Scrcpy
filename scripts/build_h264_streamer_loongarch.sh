#!/usr/bin/env bash
# 在 ben@192.168.26.106 (x86_64 Ubuntu) 上编译 loongarch64 版 oh264_capture, 取回产物.
#
# 用法: bash scripts/build_h264_streamer_loongarch.sh [host]
#   host 默认 ben@192.168.26.106
#
# 关键点 (全部经真机 192.168.19.11:8710 验证):
#   1. SDK 里的 clang-15 是 aarch64 ELF, x86_64 主机跑不了 -> 用系统 /usr/lib/llvm-18/bin/clang
#      (LLVM 18 才内置 LoongArch 后端; llvm-15 的 --print-targets 无 loongarch)
#   2. 需 lld-18 (apt install lld-18); llvm-15 自带 lld 不认识 elf64loongarch
#   3. sysroot 头文件按架构分子目录, 必须显式 -isystem usr/include/loongarch64-linux-ohos
#   4. C++ 头在 native/llvm/include/libcxx-ohos/include/c++/v1 (含 __config_site)
#   5. libc++/libunwind 在 native/llvm/lib/loongarch64-linux-ohos
#   6. LOAD 段对齐必须 16KB (-Wl,-z,max-page-size=16384), 否则设备上 SIGSEGV
#   7. oh264_capture 依赖 libnative_avscreen_capture.so / libnative_buffer.so,
#      前者在设备上已有, 后者缺失需从 SDK sysroot 推一份到 /system/lib64/
set -euo pipefail

HOST="${1:-ben@192.168.26.106}"
REMOTE_DIR="oh264_build_loongarch"
SDK="/media/ben/work_2026/opt/toolchains/ohos-sdk-5.1.0.53-loongarch"
TARGET="loongarch64-linux-ohos"
SRC="oh264_capture.cpp"
BIN="oh264_streamer_loongarch"

cd "$(dirname "$0")/.."

echo "== 准备远端目录 + 工具链适配层 =="
ssh -o BatchMode=yes "${HOST}" "
  set -e
  SDK=${SDK}
  mkdir -p ~/${REMOTE_DIR}
  cd ~/${REMOTE_DIR}

  # clang 的 resource dir 需要 loongarch64-unknown-linux-ohos 目录名, SDK 里是 -linux-ohos
  # 建一个 resource dir 副本 + 软链接别名, 让 clang 能找到 rt.builtins / crtbegin
  if [ ! -d rd15 ]; then
    cp -r \$SDK/native/llvm/lib/clang/15.0.4 rd15
    cd rd15/lib
    ln -sfn loongarch64-linux-ohos loongarch64-unknown-linux-ohos
    ln -sfn loongarch64-linux-ohos loongarch64-unknown-linux-gnu
  fi
  test -x /usr/lib/llvm-18/bin/clang || { echo 'NEED clang-18'; exit 1; }
  test -x /usr/lib/llvm-18/bin/ld.lld || { echo 'NEED lld-18 (apt install lld-18)'; exit 1; }
  echo TOOLCHAIN_OK
"

echo "== 上传源码 =="
scp -o BatchMode=yes device/${SRC} ${HOST}:~/${REMOTE_DIR}/${SRC}

echo "== 远端编译 =="
ssh -o BatchMode=yes "${HOST}" "
  set -e
  SDK=${SDK}; cd ~/${REMOTE_DIR}
  SYSROOT=\$SDK/native/sysroot
  LIB=\$SYSROOT/usr/lib/${TARGET}
  CXX=\$SDK/native/llvm/include/libcxx-ohos/include/c++/v1
  LLVMLIB=\$SDK/native/llvm/lib/${TARGET}

  /usr/lib/llvm-18/bin/clang++ --target=${TARGET} \\
    --sysroot=\$SYSROOT \\
    -isystem \$SYSROOT/usr/include/${TARGET} \\
    -isystem \$CXX \\
    -resource-dir \$PWD/rd15 \\
    -B \$LIB -L \$LIB -L \$LLVMLIB \\
    -fuse-ld=/usr/lib/llvm-18/bin/ld.lld \\
    -Wl,-z,max-page-size=16384 \\
    -Wl,-z,common-page-size=16384 \\
    -O2 -std=c++17 -pthread \\
    -lnative_media_core -lnative_media_venc -lnative_media_codecbase \\
    -lnative_avscreen_capture -lnative_buffer \\
    -Wl,--allow-shlib-undefined \\
    ${SRC} -o ${BIN} \\
    \$LLVMLIB/libc++_static.a \$LLVMLIB/libc++abi.a \$LLVMLIB/libunwind.a
  echo BUILD_OK
"

echo "== 取回产物 =="
scp -o BatchMode=yes "${HOST}:~/${REMOTE_DIR}/${BIN}" ./device/${BIN}
file ./device/${BIN}
echo "== 完成: device/${BIN} =="

echo "== 提示: 设备缺 libnative_buffer.so 时需推送 =="
echo "   scp ${HOST}:${SDK}/native/sysroot/usr/lib/${TARGET}/libnative_buffer.so ./device/"
