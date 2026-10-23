// oh264_capture.cpp - OpenHarmony screen capture -> H.264 HTTP bytestream (v3)
//
// 设备端抓屏 + 硬件编码, 直接输出 H.264 码流 (HTTP GET /screen.h264, bytestream)。
// 与 oh264_streamer.cpp 的区别:
//   oh264_streamer  : 自己造 NV12 画面 (合成测试图), 只验证编码链路
//   oh264_capture   : 用 OH_AVScreenCapture 真实抓屏, 设备侧硬件编码
//
// 抓屏链路:
//   OH_AVScreenCapture_Create / _Init(ORIGINAL_STREAM)  -> 原始帧
//   OH_AVScreenCapture_AcquireVideoBuffer              -> 取一帧
//   OH_NativeBuffer_Map                               -> 映射像素
//   OH_VideoEncoder_CreateByMime(AVC) + PushInputData  -> H.264 码流
//   HTTP bytestream 推给客户端
//
// 编译 (loongarch64, 见 scripts/build_h264_streamer_loongarch.sh):
//   clang++ --target=loongarch64-linux-ohos ...
//
// ============================================================================
// ⚠️ 已知限制: 本文件在 loongarch64 设备 (192.168.19.11:8710, KaihongOS 6.1) 上
//    产不出码流, H.264 模式在这台设备上不可用. 已改用 MJPEG 路径.
//
//    根因: 设备 /system/etc/codec/codec_caps.xml 的 <VideoEncoder> 段只声明了
//    avenc_mpeg4, 没有 AVC. OH_VideoEncoder_CreateByMime(AVC) 仍返回非空句柄,
//    Configure / GetSurface / Prepare / Start 全部 rc=0, 但输出回调最多触发 1 帧.
//
//    完整的四种 API 组合实测记录见 docs/H264_LOONGARCH_LIMITATION.md.
//    本文件保留供支持 AVC 硬编码的其它 loongarch/OHOS 设备使用;
//    core/codec_detector.py 会读 codec_caps.xml, 明确没有 avc/h264 时不会选 H.264.
// ============================================================================
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <string>
#include <vector>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <algorithm>

/* 用 SDK 真实头文件, 避免手写结构体布局不一致导致越界崩溃 */
#include <multimedia/player_framework/native_avscreen_capture.h>
#include <multimedia/player_framework/native_avscreen_capture_base.h>
#include <multimedia/player_framework/native_avbuffer.h>
#include <multimedia/player_framework/native_avbuffer_info.h>
#include <native_buffer/native_buffer.h>
#include <native_window/external_window.h>

/* ================= OHOS 媒体 API 声明 =================
 *
 * 编码器回调用旧 API (OH_AVCodecAsyncCallback / OH_AVMemory*):
 * 实测本机 (KaihongOS 6.1, API 23) 上 OH_VideoEncoder_SetCallback 能正常触发输出回调,
 * 而 OH_VideoEncoder_RegisterCallback (API 11+ 新接口) 注册成功但不回调.
 *
 * 但释放输出缓冲必须用 OH_VideoEncoder_FreeOutputBuffer:
 * 设备 libnative_media_venc.so 没有导出 OH_VideoEncoder_FreeOutputData,
 * 之前用弱符号调它会静默失败 -> 输出缓冲永不释放 -> 编码器管线堵死 -> 只出 30 帧就停.
 */
typedef struct OH_AVCodec OH_AVCodec;
typedef struct OH_AVMemory OH_AVMemory;
typedef struct OH_AVFormat OH_AVFormat;
typedef void (*OH_AVCodecOnError)(OH_AVCodec*, int32_t, void*);
typedef void (*OH_AVCodecOnStreamChanged)(OH_AVCodec*, OH_AVFormat*, void*);
typedef void (*OH_AVCodecOnNeedInputData)(OH_AVCodec*, uint32_t, OH_AVMemory*, OH_AVCodecBufferAttr*, void*);
typedef void (*OH_AVCodecOnNewOutputData)(OH_AVCodec*, uint32_t, OH_AVMemory*, OH_AVCodecBufferAttr*, void*);
typedef struct OH_AVCodecAsyncCallback {
    OH_AVCodecOnError onError;
    OH_AVCodecOnStreamChanged onStreamChanged;
    OH_AVCodecOnNeedInputData onNeedInputData;
    OH_AVCodecOnNewOutputData onNeedOutputData;
} OH_AVCodecAsyncCallback;

extern "C" {
/* 编码 */
extern OH_AVCodec* OH_VideoEncoder_CreateByMime(const char* mime);
extern OH_AVErrCode OH_VideoEncoder_SetCallback(OH_AVCodec*, OH_AVCodecAsyncCallback, void*);
extern OH_AVErrCode OH_VideoEncoder_Configure(OH_AVCodec*, OH_AVFormat*);
extern OH_AVErrCode OH_VideoEncoder_Prepare(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Start(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Stop(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Destroy(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_FreeOutputBuffer(OH_AVCodec*, uint32_t);
/* 旧接口 (API 9): 本机 SetCallback 路径必须用它释放, 用 FreeOutputBuffer 会只出 1 帧就停 */
extern OH_AVErrCode OH_VideoEncoder_FreeOutputData(OH_AVCodec*, uint32_t) __attribute__((weak));
extern OH_AVErrCode OH_VideoEncoder_NotifyEndOfStream(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_PushInputData(OH_AVCodec*, uint32_t, OH_AVCodecBufferAttr) __attribute__((weak));
extern uint8_t* OH_AVMemory_GetAddr(OH_AVMemory*);
extern int32_t OH_AVMemory_GetSize(OH_AVMemory*);
extern OH_AVFormat* OH_AVFormat_Create(void);
extern OH_AVErrCode OH_VideoEncoder_GetSurface(OH_AVCodec*, OHNativeWindow**);
extern bool OH_AVFormat_SetIntValue(OH_AVFormat*, const char*, int32_t);
extern bool OH_AVFormat_SetLongValue(OH_AVFormat*, const char*, int64_t);
extern bool OH_AVFormat_SetDoubleValue(OH_AVFormat*, const char*, double);
extern void OH_AVFormat_Destroy(OH_AVFormat*);
extern const char* OH_MD_KEY_WIDTH;
extern const char* OH_MD_KEY_HEIGHT;
extern const char* OH_MD_KEY_PIXEL_FORMAT;
extern const char* OH_MD_KEY_FRAME_RATE;
extern const char* OH_MD_KEY_BITRATE;
extern const char* OH_AVCODEC_MIMETYPE_VIDEO_AVC;
extern const char* OH_MD_KEY_VIDEO_ENCODE_BITRATE_MODE;
extern const char* OH_MD_KEY_PROFILE;
extern const char* OH_MD_KEY_I_FRAME_INTERVAL;

}

/* ================= 全局状态 ================= */

static int32_t g_port = 27193;
static int32_t g_width = 1920;
static int32_t g_height = 1080;
static int32_t g_fps = 15;
static int32_t g_bitrate = 8000000;
static int32_t g_display_id = 0;
static int32_t g_home_mode = 0;  /* 1=OH_CAPTURE_HOME_SCREEN */
static int32_t g_rgba = 1;        /* 1=RGBA (官方默认), 0=YUV */
static int32_t g_surface_mode = 1;  /* 1=Surface 零拷贝 (官方做法), 0=CPU 路径 */
static std::atomic<bool> g_running{true};

/* 编码输出: SPS/PPS 常驻 + 逐帧推送给已连接的客户端.
   参考官方 ohscrcpy_server.cpp::handleEncodedData — 每一帧编码完就立刻 send,
   不能等 GOP, 否则客户端在下一个关键帧到来前什么都收不到. */
static std::vector<uint8_t> g_config;
static std::mutex g_gop_mutex;
static std::condition_variable g_gop_cv;
static bool g_config_ready = false;

/* 最近一个关键帧 (含前置 SPS/PPS), 新客户端接入时立即补发一帧,
   不用等下一个 I 帧间隔 (实测能立即出画面) */
static std::vector<uint8_t> g_last_keyframe;

/* 已连接客户端 (最多 4 个), 每个客户端一个待发队列 */
static std::mutex g_client_mutex;
static std::vector<int> g_clients;
static constexpr size_t MAX_CLIENTS = 4;
static constexpr size_t CLIENT_QUEUE_MAX = 8 * 1024 * 1024;

/* 抓屏 -> 编码 的当前帧 (由抓屏回调写入, 由 need_input 读取) */
static std::vector<uint8_t> g_frame;
static std::mutex g_frame_mutex;
static int64_t g_frame_pts = 0;
static uint32_t g_frame_counter = 0;
static std::atomic<uint32_t> g_captured{0};

/* ================= 编码器回调 ================= */

static std::atomic<int> g_in_total{0};
static inline int in_total() { return g_in_total.load(); }

static void on_error(OH_AVCodec*, int32_t err, void*) {
    fprintf(stderr, "[capture] encoder onError err=%d\n", err);
}

static void on_stream_changed(OH_AVCodec*, OH_AVFormat*, void*) {}

static void on_need_input(OH_AVCodec* c, uint32_t index, OH_AVMemory* mem, OH_AVCodecBufferAttr*, void*) {
    if (!mem) return;
    uint8_t* dst = OH_AVMemory_GetAddr(mem);
    int32_t cap = OH_AVMemory_GetSize(mem);
    if (!dst || cap <= 0) return;

    int32_t y_size = g_width * g_height;
    int32_t frame_len = y_size + y_size / 2;  /* NV12 */
    if (frame_len > cap) frame_len = cap;

    /* Surface 模式: 抓屏已直接渲染到编码器 Surface, CPU 侧拿不到像素.
       但这台设备 (KaihongOS 6.1 / loongarch) 的编码器必须持续喂输入帧才会推进:
       完全不填 → 编码器一直等输入 → 零输出回调 (实测).
       所以喂中性灰占位; 它不进码流 (码流来自 Surface). */
    memset(dst, 128, (size_t)frame_len);

    OH_AVCodecBufferAttr a{};
    a.pts = 1000000LL * (int64_t)(g_captured.load() / (g_fps > 0 ? g_fps : 15));
    a.size = frame_len;
    int rc = -1;
    if (OH_VideoEncoder_PushInputData) rc = OH_VideoEncoder_PushInputData(c, index, a);
    g_in_total.fetch_add(1);
    static int in_cnt = 0;
    in_cnt++;
    if (in_cnt % 30 == 1) {
        fprintf(stderr, "[capture] need_input#%d len=%d cap=%d rc=%d\n",
                in_cnt, frame_len, cap, rc);
    }
}

static void on_new_output(OH_AVCodec* c, uint32_t index, OH_AVMemory* mem, OH_AVCodecBufferAttr* attr_in, void*) {
    if (!mem || !attr_in) {
        if (c) {
            if (OH_VideoEncoder_FreeOutputData) OH_VideoEncoder_FreeOutputData(c, index);
            else OH_VideoEncoder_FreeOutputBuffer(c, index);
        }
        return;
    }
    OH_AVCodecBufferAttr attr = *attr_in;
    uint8_t* src = OH_AVMemory_GetAddr(mem);
    int32_t len = attr.size;
    const int32_t FLAG_CODEC_DATA = 1 << 3;  /* SPS/PPS */
    const int32_t FLAG_SYNC_FRAME = 1 << 1;  /* keyframe */

    if (src && len > 0) {
        if (attr.flags & FLAG_CODEC_DATA) {
            /* SPS/PPS: 缓存起来, 供后续关键帧前置 */
            std::lock_guard<std::mutex> lk(g_gop_mutex);
            if (g_config.size() != (size_t)len || memcmp(g_config.data(), src, len) != 0) {
                g_config.assign(src, src + len);
                g_config_ready = true;
            }
        } else {
            /* 关键帧: 先带 SPS/PPS 再带帧, 保证客户端随时接入都能解码 */
            std::vector<uint8_t> payload;
            if (attr.flags & FLAG_SYNC_FRAME) {
                std::lock_guard<std::mutex> lk(g_gop_mutex);
                if (g_config_ready) payload = g_config;
            }
            payload.insert(payload.end(), src, src + len);
            if (attr.flags & FLAG_SYNC_FRAME) {
                std::lock_guard<std::mutex> lk(g_gop_mutex);
                g_last_keyframe = payload;
            }

            /* 广播给所有客户端 */
            std::lock_guard<std::mutex> lk2(g_client_mutex);
            for (int cfd : g_clients) {
                ssize_t sent = send(cfd, payload.data(), payload.size(), MSG_NOSIGNAL);
                if (sent <= 0) {
                    if (errno == EINTR) continue;
                    fprintf(stderr, "[capture] client send failed errno=%d\n", errno);
                }
            }
        }
    }
    /* API 9 (SetCallback) 路径: 释放必须用 FreeOutputData.
       本机实测: 用 FreeOutputBuffer 释放只出 1 帧, 编码器管线随即堵死. */
    if (OH_VideoEncoder_FreeOutputData) OH_VideoEncoder_FreeOutputData(c, index);
    else OH_VideoEncoder_FreeOutputBuffer(c, index);
    static int out_cnt = 0;
    out_cnt++;
    if (out_cnt % 30 == 1) {
        fprintf(stderr, "[capture] new_output#%d len=%d flags=%d\n", out_cnt, len, attr.flags);
    }
    if (out_cnt % 300 == 0) {
        fprintf(stderr, "[capture] STAT out=%d in=%d\n", out_cnt, in_total());
    }
}

/* ================= 抓屏回调 ================= */

static void handle_native_buffer(OH_NativeBuffer* buffer);

static void on_capture_error(OH_AVScreenCapture*, int32_t err) {
    fprintf(stderr, "[capture] screen capture onError err=%d\n", err);
}

static void on_capture_audio(OH_AVScreenCapture*, bool isReady, OH_AudioCaptureSourceType) {
    /* 不采集音频 */
}

static void on_capture_video(OH_AVScreenCapture*, bool isReady) {
    /* 视频 buffer 就绪状态 (数据走 SetDataCallback) */
    static int ready_cnt = 0;
    if (isReady && (++ready_cnt % 60 == 1)) {
        fprintf(stderr, "[capture] video buffer ready (x%d)\n", ready_cnt);
    }
}

/* 编码器输入格式: NV12 (pixel format 2) */
/* RGBA — Surface 模式必须用 RGBA (官方 ohscrcpy_server 同).
   OHOS native_avformat.h: AV_PIXEL_FORMAT_RGBA = 5 */
static const int32_t PIXEL_FORMAT_RGBA = 5;
static const int32_t PIXEL_FORMAT_NV12 = 2;

static void on_buffer_available(OH_AVScreenCapture*, OH_AVBuffer* avbuffer,
                                OH_AVScreenCaptureBufferType, int64_t, void*) {
    if (avbuffer) {
        handle_native_buffer(OH_AVBuffer_GetNativeBuffer(avbuffer));
    }
}

static void handle_native_buffer(OH_NativeBuffer* buffer) {
    if (!buffer) return;

    void* virAddr = nullptr;
    int rc = OH_NativeBuffer_Map(buffer, &virAddr);
    static int map_fail = 0;
    if (rc != 0 || !virAddr) {
        /* Map 失败常见于 GPU buffer: 改用 MapPlanes */
        OH_NativeBuffer_Planes planes{};
        int rc2 = OH_NativeBuffer_MapPlanes(buffer, &virAddr, &planes);
        if (rc2 == 0 && virAddr && planes.planeCount > 0) {
            fprintf(stderr, "[capture] MapPlanes ok planes=%d\n", planes.planeCount);
        } else if ((++map_fail % 30) == 1) {
            OH_NativeBuffer_Config cfg{};
            OH_NativeBuffer_GetConfig(buffer, &cfg);
            fprintf(stderr, "[capture] Map failed rc=%d / MapPlanes rc=%d "
                    "(%dx%d stride=%d format=%d usage=%d)\n",
                    rc, rc2, cfg.width, cfg.height, cfg.stride, cfg.format, cfg.usage);
        }
        return;
    }

    /* 用 GetConfig 取实际宽高/stride 算帧大小 */
    OH_NativeBuffer_Config cfg{};
    OH_NativeBuffer_GetConfig(buffer, &cfg);
    int32_t w = cfg.width > 0 ? cfg.width : g_width;
    int32_t h = cfg.height > 0 ? cfg.height : g_height;
    int32_t stride = cfg.stride > 0 ? cfg.stride : w;
    int32_t y_size = stride * h;
    int32_t cap_size = y_size + y_size / 2;   /* NV12 */
    if (cfg.width != g_width || cfg.height != g_height) {
        /* 分辨率与配置不符, 用实际值 (编码器按实际尺寸解析) */
        static int warned = 0;
        if (!warned) {
            warned = 1;
            fprintf(stderr, "[capture] buffer 分辨率 %dx%d (配置 %dx%d), 按实际使用\n",
                    cfg.width, cfg.height, g_width, g_height);
        }
    }

    {
        std::lock_guard<std::mutex> lk(g_frame_mutex);
        g_frame.assign((uint8_t*)virAddr, (uint8_t*)virAddr + cap_size);
        g_frame_counter++;
        g_frame_pts = 1000000LL * (int64_t)(g_frame_counter - 1) / g_fps;
    }
    g_captured++;

    OH_NativeBuffer_Unmap(buffer);
    OH_NativeBuffer_Unreference(buffer);

    static int last = 0;
    int now = (int)g_captured.load();
    if (now - last >= 30) {
        last = now;
        fprintf(stderr, "[capture] frames captured=%d last_size=%zu\n", now, g_frame.size());
    }
}

/* ================= HTTP bytestream 服务 ================= */

static int listen_sock = -1;

static int setup_listener(int32_t port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return -1; }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) { perror("bind"); close(fd); return -1; }
    if (listen(fd, 8) < 0) { perror("listen"); close(fd); return -1; }
    fprintf(stderr, "[capture] listening on %d\n", port);
    return fd;
}

static void serve_client(int cfd) {
    /* 读 HTTP 请求头 (直到 \r\n\r\n 或收到 GET 行) */
    char req[1024];
    size_t used = 0;
    bool got_get = false;
    while (used < sizeof(req) - 1) {
        ssize_t n = recv(cfd, req + used, sizeof(req) - 1 - used, 0);
        if (n <= 0) break;
        used += (size_t)n;
        req[used] = '\0';
        if (strstr(req, "\r\n\r\n")) break;
        if (strstr(req, "GET /screen.h264")) { got_get = true; break; }
    }
    req[used] = '\0';

    if (!got_get && !strstr(req, "GET /screen.h264")) {
        const char* resp = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        send(cfd, resp, strlen(resp), 0);
        close(cfd);
        return;
    }

    const char* hdr = "HTTP/1.1 200 OK\r\nContent-Type: video/h264\r\nConnection: close\r\n\r\n";
    send(cfd, hdr, strlen(hdr), 0);

    /* 注册到广播列表. 编码回调每帧 send 到这里.
       SPS/PPS 已经在关键帧里前置, 客户端接入后等下一个关键帧即可解码. */
    {
        std::lock_guard<std::mutex> lk(g_client_mutex);
        if (g_clients.size() >= MAX_CLIENTS) {
            const char* resp = "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
            send(cfd, resp, strlen(resp), 0);
            close(cfd);
            return;
        }
        g_clients.push_back(cfd);
    }
    fprintf(stderr, "[capture] client registered (fd=%d)\n", cfd);

    /* 立即补发最近一个关键帧: 客户端不必等下一个 I 帧 */
    {
        std::vector<uint8_t> kf;
        {
            std::lock_guard<std::mutex> lk(g_gop_mutex);
            kf = g_last_keyframe;
        }
        if (!kf.empty()) {
            ssize_t sent = send(cfd, kf.data(), kf.size(), MSG_NOSIGNAL);
            fprintf(stderr, "[capture] sent last keyframe %zu bytes -> fd=%d (ret=%zd)\n",
                    kf.size(), cfd, sent);
        } else {
            fprintf(stderr, "[capture] no keyframe yet, waiting\n");
        }
    }

    /* 阻塞等待客户端断开 (send 失败时由编码回调标记) */
    while (g_running.load()) {
        char probe[256];
        ssize_t n = recv(cfd, probe, sizeof(probe), 0);
        if (n == 0) break;                       /* 对端关闭 */
        if (n < 0 && errno != EINTR && errno != EAGAIN) break;
        if (n < 0) usleep(100 * 1000);
    }

    {
        std::lock_guard<std::mutex> lk(g_client_mutex);
        g_clients.erase(std::remove(g_clients.begin(), g_clients.end(), cfd), g_clients.end());
    }
    fprintf(stderr, "[capture] client disconnected (fd=%d)\n", cfd);
    close(cfd);
}

/* ================= main ================= */

static void* server_thread(void*) {
    while (g_running.load()) {
        struct sockaddr_in cli{};
        socklen_t len = sizeof(cli);
        int cfd = accept(listen_sock, (struct sockaddr*)&cli, &len);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            break;
        }
        fprintf(stderr, "[capture] client connected\n");
        serve_client(cfd);
    }
    return nullptr;
}

int main(int argc, char** argv) {
    int opt;
    while ((opt = getopt(argc, argv, "p:w:h:f:b:d:HY")) != -1) {
        switch (opt) {
            case 'p': g_port = atoi(optarg); break;
            case 'w': g_width = atoi(optarg); break;
            case 'h': g_height = atoi(optarg); break;
            case 'f': g_fps = atoi(optarg); break;
            case 'b': g_bitrate = atoi(optarg); break;
            case 'd': g_display_id = atoi(optarg); break;
            case 'H': g_home_mode = 1; break;
            case 'Y': g_rgba = 0; break;
            default: break;
        }
    }

    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, [](int){ g_running = false; });
    signal(SIGTERM, [](int){ g_running = false; });

    /* 1) 编码器 */
    OH_AVCodec* codec = OH_VideoEncoder_CreateByMime(OH_AVCODEC_MIMETYPE_VIDEO_AVC);
    if (!codec) { fprintf(stderr, "[capture] CreateByMime(AVC) failed\n"); return 1; }

    OH_AVCodecAsyncCallback cb{ on_error, on_stream_changed, on_need_input, on_new_output };
    int rc_reg = OH_VideoEncoder_SetCallback(codec, cb, nullptr);
    fprintf(stderr, "[capture] SetCallback rc=%d\n", rc_reg);
    if (rc_reg != 0) {
        fprintf(stderr, "[capture] SetCallback failed\n"); return 1;
    }

    OH_AVFormat* fmt = OH_AVFormat_Create();
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_WIDTH, g_width);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_HEIGHT, g_height);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_PIXEL_FORMAT, PIXEL_FORMAT_RGBA);
    OH_AVFormat_SetDoubleValue(fmt, OH_MD_KEY_FRAME_RATE, (double)g_fps);
    OH_AVFormat_SetLongValue(fmt, OH_MD_KEY_BITRATE, (int64_t)g_bitrate);
    /* 以下三项同官方 ohscrcpy_server; 枚举值来自 SDK native_avcodec_base.h:
       BITRATE_MODE_VBR = 1, AVC_PROFILE_MAIN = 8. 值填错会 Configure 返回
       AV_ERR_INVALID_VAL(3). */
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_VIDEO_ENCODE_BITRATE_MODE, 1 /* VBR */);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_PROFILE, 8 /* AVC_PROFILE_MAIN */);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_I_FRAME_INTERVAL, 500);
    fprintf(stderr, "[capture] cfg %dx%d@%d %dbps\n", g_width, g_height, g_fps, g_bitrate);
    int rc_cfg = OH_VideoEncoder_Configure(codec, fmt);
    fprintf(stderr, "[capture] Configure rc=%d\n", rc_cfg);
    if (rc_cfg != 0) {
        fprintf(stderr, "[capture] Configure failed\n"); return 1;
    }
    /* 关键顺序 (同官方 ohscrcpy_server): Configure -> GetSurface -> Prepare -> Start.
       GetSurface 必须在 Prepare 之前调用, 否则编码器还没分配 Surface, 返回 INVALID_VAL. */
    OHNativeWindow* enc_surface = nullptr;
    int rc_surf = OH_VideoEncoder_GetSurface(codec, &enc_surface);
    fprintf(stderr, "[capture] GetSurface rc=%d surface=%p\n", rc_surf, (void*)enc_surface);
    if (rc_surf != 0 || !enc_surface) {
        fprintf(stderr, "[capture] GetSurface 失败, 只能走 CPU 路径\n");
        g_surface_mode = 0;
    }
    if (OH_VideoEncoder_Prepare(codec) != 0) {
        fprintf(stderr, "[capture] Prepare failed\n"); return 1;
    }
    if (OH_VideoEncoder_Start(codec) != 0) {
        fprintf(stderr, "[capture] Start failed\n"); return 1;
    }
    OH_AVFormat_Destroy(fmt);
    fprintf(stderr, "[capture] encoder started\n");

    /* 2) 抓屏 */
    fprintf(stderr, "[capture] creating OH_AVScreenCapture...\n");
    OH_AVScreenCapture* cap = OH_AVScreenCapture_Create();
    fprintf(stderr, "[capture] OH_AVScreenCapture_Create = %p\n", (void*)cap);
    if (!cap) { fprintf(stderr, "[capture] OH_AVScreenCapture_Create failed\n"); return 1; }

    OH_AVScreenCaptureCallback cap_cb{ on_capture_error, on_capture_audio, on_capture_video };
    int rc_cb = OH_AVScreenCapture_SetCallback(cap, cap_cb);
    fprintf(stderr, "[capture] SetCallback rc=%d\n", rc_cb);
    int rc_data = OH_AVScreenCapture_SetDataCallback(cap, on_buffer_available, nullptr);
    fprintf(stderr, "[capture] SetDataCallback rc=%d\n", rc_data);

    OH_AVScreenCaptureConfig config{};
    config.captureMode = g_home_mode ? OH_CAPTURE_HOME_SCREEN : OH_CAPTURE_SPECIFIED_SCREEN;
    config.dataType = OH_ORIGINAL_STREAM;
    config.videoInfo.videoCapInfo.displayId = (uint64_t)g_display_id;
    config.videoInfo.videoCapInfo.missionIDs = nullptr;
    config.videoInfo.videoCapInfo.missionIDsLen = 0;
    config.videoInfo.videoCapInfo.videoFrameWidth = g_width;
    config.videoInfo.videoCapInfo.videoFrameHeight = g_height;
    config.videoInfo.videoCapInfo.videoSource = g_rgba ? OH_VIDEO_SOURCE_SURFACE_RGBA : OH_VIDEO_SOURCE_SURFACE_YUV;
    config.videoInfo.videoEncInfo.videoCodec = OH_H264;
    config.videoInfo.videoEncInfo.videoBitrate = g_bitrate;
    config.videoInfo.videoEncInfo.videoFrameRate = g_fps;
    /* audioInfo / recorderInfo 仅 OH_CAPTURE_FILE 时需要, ORIGINAL_STREAM 保持 0 */

    int rc_init = OH_AVScreenCapture_Init(cap, config);
    fprintf(stderr, "[capture] OH_AVScreenCapture_Init rc=%d\n", rc_init);
    if (rc_init != 0) {
        fprintf(stderr, "[capture] OH_AVScreenCapture_Init failed\n"); return 1;
    }
    fprintf(stderr, "[capture] screen capture initialized\n");

    /* 3) HTTP 服务 */
    listen_sock = setup_listener(g_port);
    if (listen_sock < 0) return 1;

    pthread_t st;
    pthread_create(&st, nullptr, server_thread, nullptr);
    pthread_detach(st);

    /* 4) 启动抓屏
       Surface 模式: 抓屏直接渲染到编码器的 Surface (GPU 直通, 零 CPU 拷贝).
       官方 ohscrcpy_server 用的就是这条路径. */
    if (g_surface_mode && enc_surface) {
        int rc_scs = OH_AVScreenCapture_StartScreenCaptureWithSurface(cap, enc_surface);
        fprintf(stderr, "[capture] StartScreenCaptureWithSurface rc=%d\n", rc_scs);
        if (rc_scs != 0) {
            fprintf(stderr, "[capture] StartScreenCaptureWithSurface 失败, 回退 CPU 路径\n");
            g_surface_mode = 0;
        } else {
            fprintf(stderr, "[capture] screen capture started (Surface 模式, GPU 直通)\n");
            goto running;
        }
    }

    {
        int rc_start = OH_AVScreenCapture_StartScreenCapture(cap);
        fprintf(stderr, "[capture] StartScreenCapture rc=%d\n", rc_start);
        if (rc_start != 0) {
            fprintf(stderr, "[capture] StartScreenCapture failed\n"); return 1;
        }
        fprintf(stderr, "[capture] screen capture started (CPU 模式)\n");
    }
running:;

    while (g_running.load()) usleep(200 * 1000);

    OH_AVScreenCapture_StopScreenCapture(cap);
    OH_AVScreenCapture_Release(cap);
    OH_VideoEncoder_NotifyEndOfStream(codec);
    OH_VideoEncoder_Stop(codec);
    OH_VideoEncoder_Destroy(codec);
    if (listen_sock >= 0) close(listen_sock);
    fprintf(stderr, "[capture] stopped\n");
    return 0;
}
