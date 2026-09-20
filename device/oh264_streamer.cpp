// oh264_streamer.cpp - RK3568 H.264 hardware video streamer (v2)
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
#include <string>
#include <vector>
#include <atomic>
#include <mutex>
#include <condition_variable>

typedef struct OH_AVCodec OH_AVCodec;
typedef struct OH_AVMemory OH_AVMemory;
typedef struct OH_AVFormat OH_AVFormat;
typedef int32_t OH_AVErrCode;

/* OH_AVCodecBufferFlags (native_avcodec_base.h) */
static const int32_t AVCODEC_BUFFER_FLAGS_CODEC_DATA   = 1 << 3; /* SPS/PPS */
static const int32_t AVCODEC_BUFFER_FLAGS_SYNC_FRAME   = 1 << 1; /* keyframe */
static const int32_t AVCODEC_BUFFER_FLAGS_EOS          = 1 << 0;

typedef struct OH_AVCodecBufferAttr {
    int64_t pts;      /* presentation timestamp in microseconds */
    int32_t size;
    int32_t offset;
    int32_t flags;
} OH_AVCodecBufferAttr;

typedef void (*OH_AVCodecOnError)(OH_AVCodec*, int32_t, void*);
typedef void (*OH_AVCodecOnStreamChanged)(OH_AVCodec*, OH_AVFormat*, void*);
typedef void (*OH_AVCodecOnNeedInputData)(OH_AVCodec*, uint32_t, OH_AVMemory*, void*);
typedef void (*OH_AVCodecOnNewOutputData)(OH_AVCodec*, uint32_t, OH_AVMemory*, OH_AVCodecBufferAttr*, void*);
typedef struct OH_AVCodecAsyncCallback {
    OH_AVCodecOnError onError;
    OH_AVCodecOnStreamChanged onStreamChanged;
    OH_AVCodecOnNeedInputData onNeedInputData;
    OH_AVCodecOnNewOutputData onNeedOutputData;
} OH_AVCodecAsyncCallback;

extern "C" {
extern OH_AVCodec* OH_VideoEncoder_CreateByMime(const char* mime);
extern OH_AVErrCode OH_VideoEncoder_SetCallback(OH_AVCodec*, OH_AVCodecAsyncCallback, void*);
extern OH_AVErrCode OH_VideoEncoder_Configure(OH_AVCodec*, OH_AVFormat*);
extern OH_AVErrCode OH_VideoEncoder_Prepare(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Start(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Stop(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_Destroy(OH_AVCodec*);
extern OH_AVErrCode OH_VideoEncoder_FreeOutputData(OH_AVCodec*, uint32_t);
extern OH_AVErrCode OH_VideoEncoder_PushInputData(OH_AVCodec*, uint32_t, OH_AVCodecBufferAttr attr) __attribute__((weak));
extern void* OH_VideoEncoder_GetSurface(OH_AVCodec*) __attribute__((weak));
extern uint8_t* OH_AVMemory_GetAddr(OH_AVMemory*);
extern int32_t  OH_AVMemory_GetSize(OH_AVMemory*);
extern OH_AVFormat* OH_AVFormat_Create(void);
extern bool OH_AVFormat_SetIntValue(OH_AVFormat*, const char*, int32_t);
extern bool OH_AVFormat_SetDoubleValue(OH_AVFormat*, const char*, double);
extern void OH_AVFormat_Destroy(OH_AVFormat*);
extern const char* OH_MD_KEY_WIDTH;
extern const char* OH_MD_KEY_HEIGHT;
extern const char* OH_MD_KEY_PIXEL_FORMAT;
extern const char* OH_MD_KEY_FRAME_RATE;
extern const char* OH_MD_KEY_BITRATE;
extern const char* OH_AVCODEC_MIMETYPE_VIDEO_AVC;
}

static int32_t g_port = 27193;
static int32_t g_width = 320;
static int32_t g_height = 240;
static int32_t g_fps = 15;
static int32_t g_bitrate = 500000;
static std::atomic<bool> g_running{true};

static std::vector<uint8_t> g_config;  /* SPS/PPS (CODEC_CONFIG) 常驻 */
static std::vector<uint8_t> g_gop;
static std::mutex g_gop_mutex;
static std::condition_variable g_gop_cv;
static bool g_gop_ready = false;

static void on_error(OH_AVCodec*, int32_t err, void*) {
    fprintf(stderr, "[oh264] onError err=%d\n", err);
}

static void on_stream_changed(OH_AVCodec*, OH_AVFormat*, void*) {}

static void on_need_input(OH_AVCodec* c, uint32_t index, OH_AVMemory* mem, void*) {
    if (!mem) return;
    uint8_t* dst = OH_AVMemory_GetAddr(mem);
    int32_t cap = OH_AVMemory_GetSize(mem);
    if (!dst) return;
    int32_t y_size = g_width * g_height;
    int32_t frame_len = y_size + y_size / 2;  /* NV12 */
    if (cap < frame_len) {
        fprintf(stderr, "[oh264] input too small %d < %d\n", cap, frame_len);
        return;
    }
    static int counter = 0;
    counter++;
    uint8_t v = (uint8_t)(counter % 256);
    for (int32_t i = 0; i < y_size; ++i) dst[i] = (i % (g_width*16) < g_width*8) ? v : (uint8_t)(255-v);
    for (int32_t i = y_size; i < frame_len; i += 2) { dst[i]=128; dst[i+1]=128; }
    OH_AVCodecBufferAttr attr{};
    attr.pts = 1000000LL + (int64_t)(counter - 1) * 1000000LL / g_fps;
    attr.size = frame_len; /* 官方 4 参 PushInputData 用精确帧字节 */
    OH_AVErrCode rc = -1;
    if (OH_VideoEncoder_PushInputData) rc = OH_VideoEncoder_PushInputData(c, index, attr);
    static int in_cnt=0;
    if ((++in_cnt)%5==1) fprintf(stderr, "[oh264] need_input#%d len=%d cap=%d push_rc=%d pts=%lld size=%d flags=%d\n",
                                 in_cnt, frame_len, cap, rc, (long long)attr.pts, attr.size, attr.flags);
}

static void on_new_output(OH_AVCodec* c, uint32_t index, OH_AVMemory* mem, OH_AVCodecBufferAttr* attr, void*) {
    if (!mem || !attr) { if (c) OH_VideoEncoder_FreeOutputData(c, index); return; }
    uint8_t* src = OH_AVMemory_GetAddr(mem);
    int32_t len = attr->size;
    if (src && len > 0) {
        std::lock_guard<std::mutex> lk(g_gop_mutex);
        if (attr->flags & AVCODEC_BUFFER_FLAGS_CODEC_DATA) {
            /* SPS/PPS 常驻缓冲, 每个 GOP 首部都要带上, 否则解码器无法初始化 */
            if (g_config.size() != (size_t)len || memcmp(g_config.data(), src, len) != 0) {
                g_config.assign(src, src+len);
            }
            if (g_gop.empty()) { g_gop.insert(g_gop.end(), src, src+len); }
        } else if (attr->flags & AVCODEC_BUFFER_FLAGS_SYNC_FRAME) {
            g_gop.clear();
            g_gop.insert(g_gop.end(), g_config.begin(), g_config.end()); /* 先 SPS/PPS */
            g_gop.insert(g_gop.end(), src, src+len);
            g_gop_ready=true;
            g_gop_cv.notify_all();
        } else {
            g_gop.insert(g_gop.end(), src, src+len);
        }
    }
    OH_VideoEncoder_FreeOutputData(c, index);
    static int out_cnt=0;
    if ((++out_cnt)%5==1) fprintf(stderr, "[oh264] new_output#%d len=%d flags=%d gop=%zu\n", out_cnt, len, attr->flags, g_gop.size());
}

static void serve_one(int cfd) {
    char buf[1024];
    int n = (int)recv(cfd, buf, sizeof(buf)-1, 0);
    if (n <= 0) { close(cfd); return; }
    buf[n] = 0;
    if (!strstr(buf, "GET /screen.h264")) {
        const char* r = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        send(cfd, r, (int)strlen(r), MSG_NOSIGNAL); close(cfd); return;
    }
    std::vector<uint8_t> gop;
    {
        std::unique_lock<std::mutex> lk(g_gop_mutex);
        g_gop_cv.wait_for(lk, std::chrono::seconds(10), []{ return g_gop_ready; });
        if (!g_gop_ready) {
            const char* r = "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
            send(cfd, r, (int)strlen(r), MSG_NOSIGNAL); close(cfd); return;
        }
        gop = g_gop;
    }
    char hdr[256];
    int hl = snprintf(hdr, sizeof(hdr), "HTTP/1.1 200 OK\r\nContent-Type: video/h264\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", gop.size());
    send(cfd, hdr, hl, MSG_NOSIGNAL);
    size_t off=0;
    while (off < gop.size()) { ssize_t w = send(cfd, gop.data()+off, gop.size()-off, MSG_NOSIGNAL); if (w<=0) break; off+=(size_t)w; }
    close(cfd);
}

static void* server_thread(void*) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    int yes=1; setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr{}; addr.sin_family=AF_INET; addr.sin_addr.s_addr=htonl(INADDR_ANY); addr.sin_port=htons((uint16_t)g_port);
    if (bind(srv,(struct sockaddr*)&addr,sizeof(addr))<0) { fprintf(stderr,"[oh264] bind %d failed\n",g_port); return nullptr; }
    if (listen(srv,8)<0) { fprintf(stderr,"[oh264] listen failed\n"); return nullptr; }
    fprintf(stderr, "[oh264] listening on %d\n", g_port);
    while (g_running.load()) {
        struct sockaddr_in cli{}; socklen_t cl=sizeof(cli);
        int cfd = accept(srv,(struct sockaddr*)&cli,&cl);
        if (cfd<0) { if (errno==EINTR) continue; break; }
        serve_one(cfd);
    }
    return nullptr;
}

int main(int argc, char** argv) {
    int opt;
    while ((opt=getopt(argc,argv,"p:w:h:f:b:"))!=-1) {
        switch (opt) {
            case 'p': g_port=atoi(optarg); break;
            case 'w': g_width=atoi(optarg); break;
            case 'h': g_height=atoi(optarg); break;
            case 'f': g_fps=atoi(optarg); break;
            case 'b': g_bitrate=atoi(optarg); break;
            default: break;
        }
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, [](int){ g_running=false; });
    signal(SIGTERM, [](int){ g_running=false; });

    OH_AVCodec* codec = OH_VideoEncoder_CreateByMime(OH_AVCODEC_MIMETYPE_VIDEO_AVC);
    if (!codec) { fprintf(stderr, "[oh264] CreateByMime failed\n"); return 1; }
    OH_AVCodecAsyncCallback cb{ on_error, on_stream_changed, on_need_input, on_new_output };
    if (OH_VideoEncoder_SetCallback(codec, cb, nullptr) != 0) { fprintf(stderr,"[oh264] SetCallback failed\n"); return 1; }
    OH_AVFormat* fmt = OH_AVFormat_Create();
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_WIDTH, g_width);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_HEIGHT, g_height);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_PIXEL_FORMAT, 2);   /* NV12 */
    OH_AVFormat_SetDoubleValue(fmt, OH_MD_KEY_FRAME_RATE, (double)g_fps);
    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_BITRATE, g_bitrate);
    fprintf(stderr, "[oh264] cfg %dx%d@%d %dbps\n", g_width, g_height, g_fps, g_bitrate);
    if (OH_VideoEncoder_Configure(codec, fmt) != 0) { fprintf(stderr,"[oh264] Configure failed\n"); return 1; }
    if (OH_VideoEncoder_Prepare(codec) != 0) { fprintf(stderr,"[oh264] Prepare failed\n"); return 1; }
    if (OH_VideoEncoder_Start(codec) != 0) { fprintf(stderr,"[oh264] Start failed\n"); return 1; }
    OH_AVFormat_Destroy(fmt);
    fprintf(stderr, "[oh264] encoder started\n");
    pthread_t st; pthread_create(&st,nullptr,server_thread,nullptr); pthread_detach(st);
    while (g_running.load()) usleep(200*1000);
    OH_VideoEncoder_Stop(codec);
    OH_VideoEncoder_Destroy(codec);
    return 0;
}
