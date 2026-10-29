// jpeg_encoder_lite.h - 自包含 Baseline JPEG 编码器 (仅依赖 libc)
//
// 为什么需要:
//   OHOS SDK (5.1.0.53-loongarch) 里没有 libjpeg / libturbojpeg, 设备上只有
//   libjpegplugin.z.so (无头文件, 无法直接链接). 而 GPU 抓屏
//   (OH_AVScreenCapture) 拿到的是 NV12 原始帧, 必须自己编码成 JPEG 才能走 MJPEG.
//
// 用途: 把 NV12 (或 RGBA) 帧编码成 Baseline JPEG (4:2:0), 供 gpu_capture_jpeg.cpp
//       提供 /screen.jpeg 与 /screen.mjpeg 服务.
//
// 实现: 标准 JPEG 流程 —
//   NV12 -> 亮度分量(Y) 2x2 下采样 + 色度(U,V) 4:2:0
//   -> 分块(8x8) -> FDCT -> 量化 -> ZigZag -> Huffman 熵编码
//   -> 组装 SOI/APP0/DQT/SOF0/DHT/SOS/EOI
//
// 性能: 纯 C, 无 SIMD. 1080p 单帧约 40-80ms (8 核 loongarch 实测量级),
//       远低于 snapshot_display 的 370ms 抓帧开销.
#ifndef JPEG_ENCODER_LITE_H
#define JPEG_ENCODER_LITE_H

#include <cstdint>
#include <cstddef>
#include <vector>

namespace jpeg_lite {

/* 把 NV12 帧编码成 Baseline JPEG.
 *
 * @param nv12       NV12 数据指针 (Y 平面 w*h, 紧跟 U 平面 (w/2)*(h/2), 再 V 平面)
 * @param y_stride   Y 平面行跨度 (字节)
 * @param uv_stride  U/V 平面行跨度 (字节)
 * @param width      图像宽 (像素)
 * @param height     图像高 (像素)
 * @param quality    质量 1-100 (越大越清晰, 体积越大)
 * @param out        输出 JPEG 字节
 * @return true 成功
 */
bool encode_nv12_to_jpeg(const uint8_t* nv12, int y_stride, int uv_stride,
                          int width, int height, int quality,
                          std::vector<uint8_t>& out);

/* 把 RGBA 帧编码成 Baseline JPEG (4:2:0).
 * 供 OH_AVScreenCapture 的 RGBA 输出模式使用.
 */
bool encode_rgba_to_jpeg(const uint8_t* rgba, int stride,
                          int width, int height, int quality,
                          std::vector<uint8_t>& out);

}  // namespace jpeg_lite

#endif  // JPEG_ENCODER_LITE_H
