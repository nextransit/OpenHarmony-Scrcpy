# oh264_streamer NDK 编译与部署指南（H.264 方案 D）

RK3568 OpenHarmony 设备端 H.264 硬件编码视频流服务。编码走 OH `OH_VideoEncoder`
(MPP VPU 硬编)，通过简单 HTTP 按 GOP 输出 Annex-B H.264 字节流，客户端 PyAV 解码。

## 架构

```
RK3568 OH_VideoEncoder(H.264硬编,MJPEG模式)
  -> 回调按 GOP 聚合 (SPS/PPS + IDR + P)
  -> HTTP GET /screen.h264 (每请求返回 1 个 GOP, Connection: close)
  -> hdc fport tcp:27193 -> PCH264StreamClient
  -> PyAV decode_block -> RGB 帧 -> canvas
```

## 环境

远程编译机 ben@192.168.26.106 (Ubuntu 24.04)：

```bash
OHOS_SDK=/media/ben/work_2021/toolchains/ohos-sdk/ohos-sdk/linux/native/native
# 含 llvm/clang、sysroot、libnative_media_{core,venc,codecbase}.so stub
```

## 编译（一键）

```bash
bash scripts/build_h264_streamer.sh            # 默认 ben@192.168.26.106
bash scripts/build_h264_streamer.sh user@host  # 自定义
```

产物：`device/oh264_streamer`（aarch64-linux-ohos ELF，静态链 libc++）。

等价手工命令：

```bash
$OHOS_SDK/llvm/bin/clang++ --target=aarch64-linux-ohos --sysroot=$OHOS_SDK/sysroot -O2 \
  -std=c++17 -pthread -nostdlib++ \
  -L$OHOS_SDK/llvm/lib/aarch64-linux-ohos/c++ \
  -l:libc++_static.a -lc++abi -lunwind \
  -L$OHOS_SDK/sysroot/usr/lib/aarch64-linux-ohos \
  -lnative_media_core -lnative_media_venc -lnative_media_codecbase \
  -Wl,--allow-shlib-undefined \
  device/oh264_streamer.cpp -o device/oh264_streamer
```

关键点：
- `-nostdlib++` + 显式链接 `libc++_static.a`，避免运行时报 `libc++_shared.so 缺失`（真机无该文件）。
- `-Wl,--allow-shlib-undefined` 允许 stub 库里缺失的 `OH_VideoEncoder_PushInputData` 等符号延迟到真机解析。
- `OH_AVCodecBufferAttr.pts` 必须 `int64_t`；`PushInputData(codec, index, attr)` 第 3 参**按值**，无 OH_AVMemory* 参（对照 native_avcodec_videodecoder.h）。

## 部署 + 启动

```bash
hdc -t <device> file send device/oh264_streamer /data/local/tmp/oh264_streamer
hdc -t <device> shell chmod 755 /data/local/tmp/oh264_streamer
hdc -t <device> shell 'setsid sh -c "/data/local/tmp/oh264_streamer -p 27193 -w 320 -h 240 -f 15 -b 500000 > /data/local/tmp/oh264.log 2>&1" </dev/null >/dev/null 2>&1 &'
hdc -t <device> fport tcp:27193 tcp:27193
```

参数：`-p PORT -w WIDTH -h HEIGHT -f FPS -b BITRATE`。

## 客户端验证

```bash
export OHCRCPY_CODEC=h264
./run_macos.command
# 或命令行直接连
./venv312/bin/python3 - <<'EOF'
import sys; sys.path.insert(0,'.')
from core.platform_utils import setup_environment; setup_environment('.')
from video.h264_client import H264StreamClient
c = H264StreamClient(host='127.0.0.1', port=27193); c.connect('127.0.0.1',27193)
EOF
```

验证输出（真机实测）：

```
[解码器] CodecContext初始化结果: h264, 320x240
[解码器] 解码帧: 320x240, format=yuv420p
[frame#1] shape=(240,320,3) meanRGB=[129,129,129]   # 合成测试帧
```

## 关键排查点（已踩坑）

| 现象 | 原因 | 修复 |
|---|---|---|
| `AV_ERR_INVALID_STATE`(8) push 失败 | 用错 `OH_VideoEncoder_PushInputBuffer(4参带memptr)` | 改用 `PushInputData(codec,index,attr)` 3参按值 |
| `AV_ERR_INVALID_VAL`(3) | `attr.pts` 定义成 int32 错位 / attr.size 语义 | pts=int64_t; size=精确帧字节 |
| GOP 无 SPS/PPS → 客户端 codec_ctx 未初始化 | flags 常量错位，SPS/PPS 被当普通帧丢弃 | `AVCODEC_BUFFER_FLAGS_CODEC_DATA=1<<3` 常驻缓冲，每个 IDR GOP 首部拼接 |
| 客户端逐 NAL 喂 decode_frame 失败 | SPS/PPS 是纯参数集，不能当帧解 | 整 GOP 直接 `decode_block` 交给 PyAV parse |
| 真机 `libc++_shared.so` 缺失 | clang++ 默认链接共享 C++ runtime | `-nostdlib++` + `libc++_static.a` |

## 限制与后续

- 当前 PC 端 H264StreamClient 每轮 HTTP 拉一个 GOP 后断开重连，依赖设备端每请求返回 1 个带 SPS/PPS 的 GOP。
- 合成测试帧源在 `device/oh264_streamer.cpp` 的 `on_need_input`（NV12），可替换为 `snapshot_display`/OHNativeWindow surface 真屏采集。
- 端到端实测：320x240 H.264 硬编 ~15fps，GOP 含 SPS/PPS/IDR/P，PyAV 解码 124 帧/8s 全通过。
