#!/usr/bin/env bash
# 在 ben@192.168.26.106 (OH SDK) 上编译 oh264_streamer, 取回产物.
# 用法: bash scripts/build_h264_streamer.sh [host]   (host 默认 ben@192.168.26.106)
set -euo pipefail

HOST="${1:-ben@192.168.26.106}"
REMOTE_DIR="oh264_build"
SDK="/media/ben/work_2021/toolchains/ohos-sdk/ohos-sdk/linux/native/native"
BIN="oh264_streamer"

cd "$(dirname "$0")/.."

echo "== 上传源码 =="
scp -o BatchMode=yes device/oh264_streamer.cpp ${HOST}:~/${REMOTE_DIR}/oh264_streamer.cpp

echo "== 远端编译 =="
ssh -o BatchMode=yes "${HOST}" "
  SDK=${SDK}; cd ~/${REMOTE_DIR}
  \$SDK/llvm/bin/clang++ --target=aarch64-linux-ohos --sysroot=\$SDK/sysroot -O2 \\
    -std=c++17 -pthread -nostdlib++ \\
    -L\$SDK/llvm/lib/aarch64-linux-ohos/c++ \\
    -l:libc++_static.a -lc++abi -lunwind \\
    -L\$SDK/sysroot/usr/lib/aarch64-linux-ohos \\
    -lnative_media_core -lnative_media_venc -lnative_media_codecbase \\
    -Wl,--allow-shlib-undefined \\
    oh264_streamer.cpp -o ${BIN}
  echo BUILD_OK
"

echo "== 取回产物 =="
scp -o BatchMode=yes "${HOST}:~/${REMOTE_DIR}/${BIN}" ./device/${BIN}
file ./device/${BIN}
echo "== 完成: device/${BIN} =="
