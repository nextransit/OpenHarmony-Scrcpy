# loongarch 设备 H.264 编码限制（真机实测结论）

设备：`192.168.19.11:8710`，OpenHarmony loongarch64（KaihongOS 6.1.0.04 / API 23，
Loongson-3A6000-HV，物理分辨率 1920x1080）

## 结论

**该设备的 `OH_VideoEncoder_CreateByMime(AVC)` 能返回非空句柄，但永远产不出码流。**
H.264 模式在这台设备上不可用，已改用 MJPEG（jpeg_server + snapshot_display）。

## 证据

1. **codec_caps.xml 里没有 AVC**

   ```
   $ hdc shell "grep -A12 VideoEncoder /system/etc/codec/codec_caps.xml"
   <VideoEncoder>
       <Item codecName="avenc_mpeg4"/>      ← 只有 MPEG-4 Part 2
       <Item codecType="VIDEO_ENCODER"/>
       <Item mimeType="video/mp4v-es"/>
       ...
   </VideoEncoder>
   ```

2. **创建 / 配置 / 启动全部返回 rc=0，但输出回调一次都不触发**

   ```
   [capture] SetCallback rc=0
   [capture] cfg 1280x720@30 8000000bps
   [capture] Configure rc=0
   [capture] GetSurface rc=0 surface=0x7ffff0f24a80
   [capture] encoder started
   [capture] StartScreenCaptureWithSurface rc=0
   [capture] screen capture started (Surface 模式, GPU 直通)
   ... 等待 30 秒 ...
   [capture] new_output#1 len=105105 flags=2     ← 只有第 1 帧，之后再无输出
   ```

   且 `on_need_input` 从未被调用（Surface 模式下编码器不请求 CPU 帧）。

3. **试过的新旧 API 都一样**

   | 组合 | 结果 |
   |---|---|
   | `SetCallback` (API 9) + `FreeOutputData` | 出 30 帧后堵死 |
   | `SetCallback` (API 9) + `FreeOutputBuffer` | 只出 1 帧 |
   | `RegisterCallback` (API 11) + `OH_AVBuffer*` | 注册 rc=0 但**完全不回调** |
   | `SetCallback` + Surface 模式不填输入帧 | 0 帧 |
   | `SetCallback` + 灰帧占位 (w*h*1.5) | 1 帧 |

   `OH_VideoEncoder_SetParameter(fmt, REQUEST_I_FRAME=1)` 调用后编码器直接停止输出，
   已移除（见 `device/oh264_capture.cpp`）。

## 因此 `core/codec_detector.py` 的判定改成"明确肯定"

只有 `codec_caps.xml` 的 `<VideoEncoder>` 段里**明确列出** `avc` / `h264`
才认为支持；读不到文件、找不到段、段里没有 codecName 一律判为不支持。

```python
# 读不到声明文件 -> 判为不支持（不是"不阻断"）
if not out:
    return False
```

理由：猜错的后果（连上后黑屏）远重于保守回退 MJPEG。

`detect_codec()` 也不再因为"设备上存在 oh264_streamer 二进制"就认为 H.264 可用 ——
二进制推得上去，但产不出码流。

## 环境要求（H.264 若要在别的设备上启用）

1. **SELinux**：`setenforce 0`，否则 `OH_AVScreenCapture_Init` 返回
   `OPERATE_NOT_PERMIT(2)`。
2. **`libnative_buffer.so`**：设备缺失时从 SDK sysroot 推一份到 `/system/lib64/`
   （否则 `OH_NativeBuffer_Map` 失败）。
   SDK 路径：`native/sysroot/usr/lib/loongarch64-linux-ohos/libnative_buffer.so`
3. **16KB 页对齐**（loongarch 特有）：`-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384`，
   否则设备上 SIGSEGV。
4. **抓屏参数**：`-H`（`OH_CAPTURE_HOME_SCREEN`）+ `videoSource=OH_VIDEO_SOURCE_SURFACE_RGBA`。
5. **编码器配置**：`BITRATE_MODE_VBR=1`、`AVC_PROFILE_MAIN=8`、`OH_MD_KEY_I_FRAME_INTERVAL=500`
   必设，否则 `Configure` 返回 `AV_ERR_INVALID_VAL(3)`。
6. **调用顺序**：`Configure → GetSurface → Prepare → Start`，`GetSurface` 必须在 `Prepare` 之前。

编译脚本：`scripts/build_h264_streamer_loongarch.sh`
（SDK 路径注意是 `/media/ben/work_2026/opt/**toolchains**/ohos-sdk-5.1.0.53-loongarch`）。
