#!/usr/bin/env bash
# 编译设备端 jpeg_server (MJPEG 截图服务, 自包含, 仅依赖 musl).
#
# 用途: 精简版 OpenHarmony (无 busybox / toybox httpd, 如 loongarch/KaihongOS)
#       上提供 MJPEG 服务。设备端循环 snapshot_display 写 JPEG, 本进程提供
#       HTTP 服务 (/screen.jpeg 单帧, /screen.mjpeg multipart 流)。
#
# 用法: bash scripts/build_jpeg_server.sh [host] [target]
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
REMOTE_DIR="jpeg_build_${TARGET}"
BIN="jpeg_server_${TARGET}"

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
  # SDK 里只有 loongarch64-linux-ohos. 目录名必须写成字面量 —
  # 用 ${TARGET} 拼接会得到 loongarch64-linux-ohos-unknown-linux-ohos, 名字不对.
  # 同时要检查实体目录里真的有文件 (失败残留的空目录会让 lld 报
  # "cannot open libclang_rt.builtins.a").
  if [ ! -f rd/lib/${TARGET}/libclang_rt.builtins.a ]; then
    rm -rf rd
    cp -r \$SDK/native/llvm/lib/clang/15.0.4 rd
    cp -r rd/lib/${TARGET} rd/lib/loongarch64-unknown-linux-ohos
    cp -r rd/lib/${TARGET} rd/lib/loongarch64-unknown-linux-gnu
  fi
  echo TOOLCHAIN_OK
"

echo "== 上传源码 =="
scp -o BatchMode=yes device/jpeg_server.cpp ${HOST}:~/${REMOTE_DIR}/jpeg_server.cpp

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
    -resource-dir \$PWD/rd \
    -B \$LIB -L \$LIB -L \$LLVMLIB \
    -fuse-ld=/usr/lib/llvm-18/bin/ld.lld \
    -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
    -O2 -std=c++17 -pthread \
    jpeg_server.cpp -o ${BIN} \
    \$LLVMLIB/libc++_static.a \$LLVMLIB/libc++abi.a \$LLVMLIB/libunwind.a
  echo BUILD_OK
"

echo "== 取回产物 =="
scp -o BatchMode=yes "${HOST}:~/${REMOTE_DIR}/${BIN}" ./device/jpeg_server_loongarch
file ./device/jpeg_server_loongarch
echo "== 完成: device/jpeg_server_loongarch =="
