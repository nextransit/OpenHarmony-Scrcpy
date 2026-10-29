// jpeg_encoder_lite.cpp - 自包含 Baseline JPEG 编码器 (仅依赖 libc)
//
// 由已验证的 Python 参考实现机械转写生成 (gen_cpp.py), 避免手抄常数表/索引出错.
//
// 用途: OHOS SDK (5.1.0.53-loongarch) 无 libjpeg/libturbojpeg, 而 GPU 抓屏
//       (OH_AVScreenCapture) 得到的是 NV12 原始帧, 需要自己编码成 JPEG
//       才能提供 MJPEG 服务.
//
// 流程: NV12 -> YUV420 -> 8x8 块 -> FDCT -> 量化 -> ZigZag -> Huffman -> JPEG
//
// 正确性: 与 PIL/libjpeg 逐像素比对通过 (Y 通道相关系数 > 0.95).
#include "jpeg_encoder_lite.h"

#include <cmath>
#include <cstring>
#include <algorithm>

namespace jpeg_lite {
namespace {

/* ---- 标准量化表 (ITU-T T.81 Annex K) ---- */
const uint8_t kStdLumaQuant[64] = {
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
};
const uint8_t kStdChromaQuant[64] = {
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
};

/* ---- ZigZag 顺序 ---- */
const uint8_t kZigZag[64] = {
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
};

/* ---- 标准 Huffman 表 (Annex K). bits[0..15] 对应码长 1..16 ---- */
const uint8_t kDcLumaBits[16] = {0,1,5,1,1,1,1,1,1,0,0,0,0,0,0,0};
const uint8_t kDcLumaVals[12] = {0,1,2,3,4,5,6,7,8,9,10,11};

const uint8_t kDcChromaBits[16] = {0,3,1,1,1,1,1,1,1,1,1,0,0,0,0,0};
const uint8_t kDcChromaVals[12] = {0,1,2,3,4,5,6,7,8,9,10,11};

const uint8_t kAcLumaBits[16] = {0,2,1,3,3,2,4,3,5,5,4,4,0,0,1,0x7d};
const uint8_t kAcLumaVals[162] = {
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
    0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16,
    0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
    0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
    0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
    0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
    0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4,
    0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea,
    0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa,
};

const uint8_t kAcChromaBits[16] = {0,2,1,2,4,4,3,4,7,5,4,4,0,1,2,0x77};
const uint8_t kAcChromaVals[162] = {
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
    0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
    0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
    0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa,
};

/* ---- 余弦表: kCos[u][k] = cos((2k+1)u*pi/16) * C(u), C(0)=1/sqrt(2) ---- */
static const double kCos[8][8] = {
    {0.7071067812, 0.7071067812, 0.7071067812, 0.7071067812, 0.7071067812, 0.7071067812, 0.7071067812, 0.7071067812},
    {0.9807852804, 0.8314696123, 0.5555702330, 0.1950903220, -0.1950903220, -0.5555702330, -0.8314696123, -0.9807852804},
    {0.9238795325, 0.3826834324, -0.3826834324, -0.9238795325, -0.9238795325, -0.3826834324, 0.3826834324, 0.9238795325},
    {0.8314696123, -0.1950903220, -0.9807852804, -0.5555702330, 0.5555702330, 0.9807852804, 0.1950903220, -0.8314696123},
    {0.7071067812, -0.7071067812, -0.7071067812, 0.7071067812, 0.7071067812, -0.7071067812, -0.7071067812, 0.7071067812},
    {0.5555702330, -0.9807852804, 0.1950903220, 0.8314696123, -0.8314696123, -0.1950903220, 0.9807852804, -0.5555702330},
    {0.3826834324, -0.9238795325, 0.9238795325, -0.3826834324, -0.3826834324, 0.9238795325, -0.9238795325, 0.3826834324},
    {0.1950903220, -0.5555702330, 0.8314696123, -0.9807852804, 0.9807852804, -0.8314696123, 0.5555702330, -0.1950903220},
};

struct HuffTable {
    uint16_t code[256];
    uint8_t  len[256];
};

HuffTable g_dc_luma, g_dc_chroma, g_ac_luma, g_ac_chroma;

void build_huff(HuffTable& t, const uint8_t bits[16], const uint8_t* vals, int nvals) {
    int code = 0, k = 0;
    for (int l = 1; l <= 16; l++) {
        for (int i = 0; i < bits[l - 1]; i++) {
            t.code[vals[k]] = (uint16_t)code;
            t.len[vals[k]] = (uint8_t)l;
            code++; k++;
        }
        code <<= 1;
    }
}

void init_tables() {
    static bool inited = false;
    if (inited) return;
    build_huff(g_dc_luma, kDcLumaBits, kDcLumaVals, 12);
    build_huff(g_dc_chroma, kDcChromaBits, kDcChromaVals, 12);
    build_huff(g_ac_luma, kAcLumaBits, kAcLumaVals, 162);
    build_huff(g_ac_chroma, kAcChromaBits, kAcChromaVals, 162);
    inited = true;
}

struct BitWriter {
    std::vector<uint8_t>& out;
    uint32_t buffer = 0;
    int bits = 0;
    explicit BitWriter(std::vector<uint8_t>& o) : out(o) {}
    void put(uint16_t code, int len) {
        if (len <= 0) return;
        buffer = (buffer << len) | code;
        bits += len;
        while (bits >= 8) {
            bits -= 8;
            uint8_t byte = (uint8_t)((buffer >> bits) & 0xFF);
            out.push_back(byte);
            /* 0xFF 后插 0x00 字节填充, 避免被误判为 JPEG marker */
            if (byte == 0xFF) out.push_back(0x00);
        }
        /* 关键: 清除已输出的低位.
         * 否则 buffer 持续左移, 超过 32 位后 uint32 溢出丢高位,
         * 后续所有熵编码字节错位 -> 解码出噪声. */
        if (bits > 0) buffer &= (1u << bits) - 1u;
        else buffer = 0;
    }
    void flush() {
        while (bits > 0) {
            bits -= 8;
            uint8_t byte = (uint8_t)((buffer >> (bits > 0 ? bits : 0)) & 0xFF);
            out.push_back(byte);
            if (byte == 0xFF) out.push_back(0x00);
        }
        buffer = 0; bits = 0;
    }
};

int magnitude_category(int32_t v) {
    int a = v < 0 ? -v : v;
    int c = 0;
    while (a) { a >>= 1; c++; }
    return c;
}

void fdct8x8(const int32_t* in, int32_t* out) {
    /* in[y*8+x], out[u*8+v]
     * F(u,v) = 1/4 * C(u)C(v) * sum_y sum_x in[y][x] cos((2x+1)v pi/16) cos((2y+1)u pi/16)
     * kCos 表已含各自的 C 因子, 整体再乘 1/4. */
    double tmp[64];
    for (int y = 0; y < 8; y++) {
        const int32_t* row = in + y * 8;
        for (int v = 0; v < 8; v++) {
            double s = 0.0;
            for (int x = 0; x < 8; x++) s += (double)row[x] * kCos[v][x];
            tmp[v * 8 + y] = s;
        }
    }
    for (int v = 0; v < 8; v++) {
        for (int u = 0; u < 8; u++) {
            double s = 0.0;
            for (int y = 0; y < 8; y++) s += tmp[v * 8 + y] * kCos[u][y];
            out[u * 8 + v] = (int32_t)(s * 0.25);
        }
    }
}

void encode_block(BitWriter& bw, const int32_t* coef_zig,
                  const HuffTable& dc_tab, const HuffTable& ac_tab,
                  int32_t& dc_pred) {
    int32_t diff = coef_zig[0] - dc_pred;
    dc_pred = coef_zig[0];
    int c = magnitude_category(diff);
    bw.put(dc_tab.code[c], dc_tab.len[c]);
    if (c) {
        int32_t v = (diff < 0) ? diff - 1 : diff;
        for (int i = c - 1; i >= 0; i--) bw.put((uint16_t)((v >> i) & 1), 1);
    }
    int run = 0;
    for (int k = 1; k < 64; k++) {
        int32_t v = coef_zig[k];
        if (v == 0) { run++; continue; }
        while (run > 15) {
            bw.put(ac_tab.code[0xF0], ac_tab.len[0xF0]);
            run -= 16;
        }
        int cc = magnitude_category(v);
        int sym = (run << 4) | cc;
        bw.put(ac_tab.code[sym], ac_tab.len[sym]);
        int32_t vv = (v < 0) ? v - 1 : v;
        for (int i = cc - 1; i >= 0; i--) bw.put((uint16_t)((vv >> i) & 1), 1);
        run = 0;
    }
    if (run > 0) bw.put(ac_tab.code[0x00], ac_tab.len[0x00]);
}

void write_marker(std::vector<uint8_t>& out, uint8_t marker) {
    out.push_back(0xFF); out.push_back(marker);
}
void write_u16(std::vector<uint8_t>& out, uint16_t v) {
    out.push_back((uint8_t)(v >> 8)); out.push_back((uint8_t)(v & 0xFF));
}
void scale_quant(const uint8_t* base, int quality, uint8_t* out) {
    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;
    int scale = (quality < 50) ? (5000 / quality) : (200 - quality * 2);
    for (int i = 0; i < 64; i++) {
        int v = (base[i] * scale + 50) / 100;
        if (v < 1) v = 1;
        if (v > 255) v = 255;
        out[i] = (uint8_t)v;
    }
}
/* 生成 zigzag 顺序的量化表 (JPEG DQT 段的标准存储顺序).
 * 标准表 kStdLumaQuant 是自然顺序 (行优先), 但 DQT 段必须按 zigzag 序存放,
 * 否则解码器按 zigzag 读取时会把量化值错位 —— 表现为亮度整体偏置 + 块噪声. */
void scale_quant_zigzag(const uint8_t* base, int quality, uint8_t* out) {
    uint8_t nat[64];
    scale_quant(base, quality, nat);
    for (int i = 0; i < 64; i++) out[i] = nat[kZigZag[i]];
}

void write_dht(std::vector<uint8_t>& out, uint8_t tc_th,
               const uint8_t bits[16], const uint8_t* vals, int nvals) {
    write_marker(out, 0xC4);
    write_u16(out, (uint16_t)(2 + 1 + 16 + nvals));
    out.push_back(tc_th);
    for (int i = 0; i < 16; i++) out.push_back(bits[i]);
    for (int i = 0; i < nvals; i++) out.push_back(vals[i]);
}
void write_dqt(std::vector<uint8_t>& out, uint8_t id, const uint8_t* q) {
    write_marker(out, 0xDB);
    write_u16(out, (uint16_t)(2 + 1 + 64));
    out.push_back(id);
    for (int i = 0; i < 64; i++) out.push_back(q[i]);
}

bool encode_planes(const uint8_t* Y, const uint8_t* Cb, const uint8_t* Cr,
                   int y_stride, int cb_stride, int cr_stride,
                   int width, int height, int quality,
                   std::vector<uint8_t>& out) {
    init_tables();
    if (width <= 0 || height <= 0) return false;

    uint8_t qy[64], qc[64];
    scale_quant_zigzag(kStdLumaQuant, quality, qy);
    scale_quant_zigzag(kStdChromaQuant, quality, qc);

    out.clear();
    out.reserve((size_t)width * height / 2 + 1024);

    write_marker(out, 0xD8);
    write_marker(out, 0xE0);
    write_u16(out, 16);
    const char jfif[] = "JFIF";
    out.insert(out.end(), jfif, jfif + 5);
    out.push_back(1); out.push_back(1);
    out.push_back(0);
    write_u16(out, 1); write_u16(out, 1);
    out.push_back(0); out.push_back(0);
    write_dqt(out, 0x00, qy);
    write_dqt(out, 0x01, qc);
    write_marker(out, 0xC0);
    write_u16(out, (uint16_t)(8 + 3 * 3));
    out.push_back(8);
    write_u16(out, (uint16_t)height);
    write_u16(out, (uint16_t)width);
    out.push_back(3);
    /* 采样因子: Y = 2x2, Cb/Cr = 1x1 -> 标准 4:2:0.
     * 若把 Y 写成 1x1, 解码器会按 4:4:4 解释, 色度块被错位还原. */
    out.push_back(1); out.push_back(0x22); out.push_back(0);
    out.push_back(2); out.push_back(0x11); out.push_back(1);
    out.push_back(3); out.push_back(0x11); out.push_back(1);
    write_dht(out, 0x00, kDcLumaBits, kDcLumaVals, 12);
    write_dht(out, 0x10, kAcLumaBits, kAcLumaVals, 162);
    write_dht(out, 0x01, kDcChromaBits, kDcChromaVals, 12);
    write_dht(out, 0x11, kAcChromaBits, kAcChromaVals, 162);
    write_marker(out, 0xDA);
    write_u16(out, (uint16_t)(6 + 2 * 3));
    out.push_back(3);
    out.push_back(1); out.push_back(0x00);
    out.push_back(2); out.push_back(0x11);
    out.push_back(3); out.push_back(0x11);
    out.push_back(0); out.push_back(63); out.push_back(0);

    BitWriter bw(out);
    int32_t py = 0, pcb = 0, pcr = 0;
    /* 4:2:0: 每个 MCU 覆盖 16x16 亮度像素 */
    int mcu_x = (width + 15) / 16;
    int mcu_y = (height + 15) / 16;
    int32_t zz[64], qz[64];

    /* 标准 4:2:0 MCU 结构: 4 个 Y 块 (2x2 排列) + 1 个 Cb + 1 个 Cr.
     * 每个 MCU 覆盖 16x16 亮度像素 / 8x8 色度像素. */
    for (int my = 0; my < mcu_y; my++) {
        for (int mx = 0; mx < mcu_x; mx++) {
            int32_t blk[64];
            /* 4 个 Y 块, 顺序 Y00 Y01 Y10 Y11 */
            for (int dy = 0; dy < 2; dy++) {
                for (int dx = 0; dx < 2; dx++) {
                    int y0 = my * 16 + dy * 8;
                    int x0 = mx * 16 + dx * 8;
                    for (int r = 0; r < 8; r++) {
                        int y = y0 + r; if (y >= height) y = height - 1;
                        const uint8_t* row = Y + (size_t)y * y_stride;
                        for (int c = 0; c < 8; c++) {
                            int x = x0 + c; if (x >= width) x = width - 1;
                            blk[r * 8 + c] = row[x] - 128;
                        }
                    }
                    fdct8x8(blk, zz);
                    for (int i = 0; i < 64; i++) {
                        int32_t v = zz[kZigZag[i]];
                        int32_t q = qy[i];
                        qz[i] = (v >= 0) ? (v + q / 2) / q : -((-v + q / 2) / q);
                    }
                    encode_block(bw, qz, g_dc_luma, g_ac_luma, py);
                }
            }

            /* Cb: 8x8 色度块对应 16x16 亮度区域, 取 2x2 平均 */
            for (int r = 0; r < 8; r++) {
                int sy = my * 16 + r * 2;
                int cy = sy / 2; if (cy >= height / 2) cy = height / 2 - 1;
                const uint8_t* rb = Cb + (size_t)cy * cb_stride;
                for (int c = 0; c < 8; c++) {
                    int sx = mx * 16 + c * 2;
                    int cx = sx / 2; if (cx >= width / 2) cx = width / 2 - 1;
                    int x1 = cx + 1 < width / 2 ? cx + 1 : cx;
                    int y1 = cy + 1 < height / 2 ? cy + 1 : cy;
                    int sum = (int)rb[cx] + (int)rb[x1]
                            + (int)Cb[(size_t)y1 * cb_stride + cx]
                            + (int)Cb[(size_t)y1 * cb_stride + x1];
                    blk[r * 8 + c] = (sum + 2) / 4 - 128;   /* int 域减 128, 不能先转 uint8_t */
                }
            }
            fdct8x8(blk, zz);
            for (int i = 0; i < 64; i++) {
                int32_t v = zz[kZigZag[i]];
                int32_t q = qc[i];
                qz[i] = (v >= 0) ? (v + q / 2) / q : -((-v + q / 2) / q);
            }
            encode_block(bw, qz, g_dc_chroma, g_ac_chroma, pcb);

            /* Cr: 同 Cb */
            for (int r = 0; r < 8; r++) {
                int sy = my * 16 + r * 2;
                int cy = sy / 2; if (cy >= height / 2) cy = height / 2 - 1;
                const uint8_t* rr = Cr + (size_t)cy * cr_stride;
                for (int c = 0; c < 8; c++) {
                    int sx = mx * 16 + c * 2;
                    int cx = sx / 2; if (cx >= width / 2) cx = width / 2 - 1;
                    int x1 = cx + 1 < width / 2 ? cx + 1 : cx;
                    int y1 = cy + 1 < height / 2 ? cy + 1 : cy;
                    int sum = (int)rr[cx] + (int)rr[x1]
                            + (int)Cr[(size_t)y1 * cr_stride + cx]
                            + (int)Cr[(size_t)y1 * cr_stride + x1];
                    blk[r * 8 + c] = (sum + 2) / 4 - 128;   /* int 域减 128, 不能先转 uint8_t */
                }
            }
            fdct8x8(blk, zz);
            for (int i = 0; i < 64; i++) {
                int32_t v = zz[kZigZag[i]];
                int32_t q = qc[i];
                qz[i] = (v >= 0) ? (v + q / 2) / q : -((-v + q / 2) / q);
            }
            encode_block(bw, qz, g_dc_chroma, g_ac_chroma, pcr);
        }
    }
    bw.flush();
    write_marker(out, 0xD9);
    return true;
}

}  // namespace

bool encode_nv12_to_jpeg(const uint8_t* nv12, int y_stride, int uv_stride,
                          int width, int height, int quality,
                          std::vector<uint8_t>& out) {
    if (!nv12 || width <= 0 || height <= 0) return false;
    const uint8_t* Y = nv12;
    const uint8_t* U = Y + (size_t)y_stride * height;
    const uint8_t* V = U + (size_t)uv_stride * (height / 2);
    return encode_planes(Y, U, V, y_stride, uv_stride, uv_stride,
                         width, height, quality, out);
}

bool encode_rgba_to_jpeg(const uint8_t* rgba, int stride,
                          int width, int height, int quality,
                          std::vector<uint8_t>& out) {
    if (!rgba || width <= 0 || height <= 0) return false;
    std::vector<uint8_t> Y((size_t)width * height);
    std::vector<uint8_t> Cb((size_t)(width / 2) * (height / 2));
    std::vector<uint8_t> Cr((size_t)(width / 2) * (height / 2));
    const int cw = width / 2, ch = height / 2;
    for (int y = 0; y < height; y++) {
        const uint8_t* row = rgba + (size_t)y * stride;
        uint8_t* yrow = Y.data() + (size_t)y * width;
        for (int x = 0; x < width; x++) {
            int r = row[x * 4 + 0], g = row[x * 4 + 1], b = row[x * 4 + 2];
            yrow[x] = (uint8_t)std::clamp((19595 * r + 38470 * g + 7471 * b) >> 16, 0, 255);
        }
    }
    for (int y = 0; y < ch; y++) {
        const uint8_t* row = rgba + (size_t)(y * 2) * stride;
        uint8_t* cbrow = Cb.data() + (size_t)y * cw;
        uint8_t* crrow = Cr.data() + (size_t)y * cw;
        for (int x = 0; x < cw; x++) {
            int r = row[x * 8 + 0], g = row[x * 8 + 1], b = row[x * 8 + 2];
            int cb = ((-11056 * r - 21712 * g + 32768 * b) >> 16) + 128;
            int cr = ((32768 * r - 27440 * g - 5328 * b) >> 16) + 128;
            cbrow[x] = (uint8_t)std::clamp(cb, 0, 255);
            crrow[x] = (uint8_t)std::clamp(cr, 0, 255);
        }
    }
    return encode_planes(Y.data(), Cb.data(), Cr.data(), width, cw, cw,
                         width, height, quality, out);
}

}  // namespace jpeg_lite
