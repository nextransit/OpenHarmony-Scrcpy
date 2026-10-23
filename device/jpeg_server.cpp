// jpeg_server.cpp - 设备端 JPEG 截图 HTTP 服务 (自包含, 仅依赖 musl)
//
// 用途: 精简版 OpenHarmOS (无 busybox/httpd/python) 上提供 MJPEG 截图服务。
// 设备端后台循环调用 snapshot_display 写 /data/local/tmp/<file>.jpeg,
// 本进程提供 HTTP 服务: GET /screen.jpeg -> 返回最新 JPEG; GET /screen.mjpeg -> MJPEG 流。
//
// 编译 (loongarch64):
//   clang++ --target=loongarch64-linux-ohos ...
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <string>
#include <vector>
#include <atomic>
#include <mutex>

static int32_t g_port = 27190;
static int32_t g_width = 1080;
static int32_t g_height = 1920;
static double g_interval = 0.15;
/* 抓帧 worker 数量.
 *
 * 实测 (真机 192.168.19.11:8710 loongarch, 8 核):
 *   单次 snapshot_display 约 174ms (进程启动为主, 与分辨率几乎无关:
 *   1920x1080 和 640x360 都是 ~340ms), 串行吞吐上限约 5.5fps.
 *   多路并发实测吞吐:
 *       1 路 5.5fps | 2 路 9.0fps | 4 路 13.6fps | 6 路 15.7fps | 8 路 16.0fps
 *   所以用 N 个 worker 各自持续抓帧, 各写各的文件, 客户端取"最新"的那张.
 *   帧率 = N / 174ms. */
static int32_t g_workers = 4;
static std::string g_jpeg_path = "/data/local/tmp/ohscrcpy_screen.jpeg";
static std::atomic<bool> g_running{true};

/* 最新一帧 JPEG (由抓帧线程写入) */
static std::vector<uint8_t> g_frame;
static std::mutex g_frame_mutex;
static std::atomic<uint64_t> g_seq{0};

/* 设备物理分辨率 (uinput 触摸注入用的坐标系).
   抓帧分辨率 (g_width/g_height) 可能小于它, 点击坐标必须换算到物理分辨率. */
static int32_t g_device_width = 0;
static int32_t g_device_height = 0;

/* 从 JPEG 字节流解析尺寸 (SOF0/SOF2 标记) */
static bool jpeg_size_from_bytes(const uint8_t* d, size_t n, int32_t* w, int32_t* h) {
    if (n < 4 || d[0] != 0xFF || d[1] != 0xD8) return false;
    size_t i = 2;
    while (i + 9 < n) {
        if (d[i] != 0xFF) { ++i; continue; }
        uint8_t marker = d[i + 1];
        if (marker == 0xD8 || marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) {
            i += 2; continue;
        }
        size_t seglen = ((size_t)d[i + 2] << 8) | d[i + 3];
        /* SOF0..SOF15, 排除 DHT(C4)/JPG(C8)/DAC(CC) */
        if (marker >= 0xC0 && marker <= 0xCF &&
            marker != 0xC4 && marker != 0xC8 && marker != 0xCC) {
            *h = (int32_t)(((size_t)d[i + 5] << 8) | d[i + 6]);
            *w = (int32_t)(((size_t)d[i + 7] << 8) | d[i + 8]);
            return *w > 0 && *h > 0;
        }
        i += 2 + seglen;
    }
    return false;
}

/* 探测设备真实分辨率: 用不带 -w/-h 的全分辨率截图, 解析 JPEG 头 */
static void detect_device_resolution() {
    const char* probe = "/data/local/tmp/ohscrcpy_probe.jpeg";
    pid_t pid = fork();
    if (pid == 0) {
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }
        execl("/system/bin/snapshot_display", "snapshot_display",
              "-i", "0", "-f", probe, (char*)nullptr);
        _exit(127);
    } else if (pid > 0) {
        int st = 0;
        waitpid(pid, &st, 0);
    }
    int fd = open(probe, O_RDONLY);
    if (fd >= 0) {
        uint8_t head[65536];
        ssize_t n = read(fd, head, sizeof(head));
        close(fd);
        if (n > 0) {
            int32_t pw = 0, ph = 0;
            if (jpeg_size_from_bytes(head, (size_t)n, &pw, &ph)) {
                g_device_width = pw;
                g_device_height = ph;
            }
        }
    }
    if (g_device_width > 0) {
        fprintf(stderr, "[jpeg_srv] 设备物理分辨率 %dx%d (抓帧 %dx%d)\n",
                g_device_width, g_device_height, g_width, g_height);
    } else {
        fprintf(stderr, "[jpeg_srv] 设备物理分辨率探测失败, 假定 = 抓帧 %dx%d\n",
                g_width, g_height);
        g_device_width = g_width;
        g_device_height = g_height;
    }
}

/* 抓帧 worker: 每个 worker 抓到帧后写入共享的最新帧.
 *
 * worker id 用于区分各自的临时文件 (避免多个 worker 同时写同一个文件
 * 造成读到半截 JPEG). */
static void* capture_thread(void* arg) {
    int worker_id = (int)(intptr_t)arg;
    std::string path = g_jpeg_path;
    if (g_workers > 1) {
        /* /data/local/tmp/ohscrcpy_screen.jpeg -> /data/local/tmp/ohscrcpy_screen.w0.jpeg */
        size_t dot = path.rfind(".jpeg");
        if (dot != std::string::npos) {
            char suffix[32];
            snprintf(suffix, sizeof(suffix), ".w%d.jpeg", worker_id);
            path = path.substr(0, dot) + suffix;
        }
    }
    char wbuf[16], hbuf[16];
    snprintf(wbuf, sizeof(wbuf), "%d", g_width);
    snprintf(hbuf, sizeof(hbuf), "%d", g_height);

    while (g_running.load()) {
        /* fork + exec snapshot_display (设备 shell 可能不支持 system()) */
        pid_t pid = fork();
        if (pid == 0) {
            int devnull = open("/dev/null", O_WRONLY);
            if (devnull >= 0) {
                dup2(devnull, STDOUT_FILENO);
                dup2(devnull, STDERR_FILENO);
                close(devnull);
            }
            execl("/system/bin/snapshot_display", "snapshot_display",
                  "-i", "0", "-w", wbuf, "-h", hbuf,
                  "-f", path.c_str(), (char*)nullptr);
            _exit(127);
        } else if (pid > 0) {
            int status = 0;
            waitpid(pid, &status, 0);
        }
        /* 读取文件内容 */
        int fd = open(path.c_str(), O_RDONLY);
        if (fd >= 0) {
            struct stat st{};
            if (fstat(fd, &st) == 0 && st.st_size > 0) {
                std::vector<uint8_t> buf((size_t)st.st_size);
                ssize_t n = read(fd, buf.data(), buf.size());
                if (n > 0) {
                    buf.resize((size_t)n);
                    std::lock_guard<std::mutex> lk(g_frame_mutex);
                    g_frame.swap(buf);
                    g_seq.fetch_add(1);
                }
            }
            close(fd);
        }
        /* interval 只在单worker 时有意义 (多worker 靠并发提吞吐) */
        if (g_workers <= 1) {
            usleep((useconds_t)(g_interval * 1000000));
        }
    }
    return nullptr;
}

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
    fprintf(stderr, "[jpeg_srv] listening on %d (file=%s)\n", port, g_jpeg_path.c_str());
    return fd;
}

static bool send_all(int fd, const void* buf, size_t len) {
    const uint8_t* p = (const uint8_t*)buf;
    size_t off = 0;
    while (off < len) {
        ssize_t n = send(fd, p + off, len - off, MSG_NOSIGNAL);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            return false;
        }
        off += (size_t)n;
    }
    return true;
}

static void serve_mjpeg(int cfd) {
    /* MJPEG multipart 流: 每帧 --BOUNDARY + Content-Type + 数据 */
    const char* BOUNDARY = "--OHScrcpyFrame\r\n";
    const char* hdr = "Content-Type: image/jpeg\r\nContent-Length: %zu\r\n\r\n";
    char head[256];
    uint32_t last_seq = 0;

    while (g_running.load()) {
        std::vector<uint8_t> frame;
        {
            std::lock_guard<std::mutex> lk(g_frame_mutex);
            if (g_seq == last_seq) {
                /* 无新帧, 稍等 */
            } else {
                last_seq = g_seq;
                frame = g_frame;
            }
        }
        if (frame.empty()) {
            usleep(30 * 1000);
            continue;
        }
        int n = snprintf(head, sizeof(head), hdr, frame.size());
        if (!send_all(cfd, BOUNDARY, strlen(BOUNDARY))) break;
        if (!send_all(cfd, head, (size_t)n)) break;
        if (!send_all(cfd, frame.data(), frame.size())) break;
    }
    close(cfd);
}

static void serve_once(int cfd) {
    char req[1024];
    size_t used = 0;
    while (used < sizeof(req) - 1) {
        ssize_t n = recv(cfd, req + used, sizeof(req) - 1 - used, 0);
        if (n <= 0) break;
        used += (size_t)n;
        req[used] = '\0';
        if (strstr(req, "\r\n\r\n")) break;
    }
    req[used] = '\0';

    if (strstr(req, "GET /screen.mjpeg")) {
        const char* h = "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=OHScrcpyFrame\r\nConnection: close\r\n\r\n";
        send_all(cfd, h, strlen(h));
        serve_mjpeg(cfd);
        return;
    }

    if (strstr(req, "GET /screen.info")) {
        char body[128];
        int n = snprintf(body, sizeof(body),
            "{\"width\":%d,\"height\":%d,\"captureWidth\":%d,\"captureHeight\":%d}",
            g_device_width, g_device_height, g_width, g_height);
        char h[160];
        int hn = snprintf(h, sizeof(h),
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
            n);
        send_all(cfd, h, (size_t)hn);
        send_all(cfd, body, (size_t)n);
        close(cfd);
        return;
    }

    if (strstr(req, "GET /screen.jpeg")) {
        std::vector<uint8_t> frame;
        {
            std::lock_guard<std::mutex> lk(g_frame_mutex);
            frame = g_frame;
        }
        if (frame.empty()) {
            const char* r = "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
            send_all(cfd, r, strlen(r));
            close(cfd);
            return;
        }
        char h[128];
        int n = snprintf(h, sizeof(h),
            "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n",
            frame.size());
        send_all(cfd, h, (size_t)n);
        send_all(cfd, frame.data(), frame.size());
        close(cfd);
        return;
    }

    const char* r = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
    send_all(cfd, r, strlen(r));
    close(cfd);
}

int main(int argc, char** argv) {
    int opt;
    while ((opt = getopt(argc, argv, "p:w:h:i:f:n:")) != -1) {
        switch (opt) {
            case 'p': g_port = atoi(optarg); break;
            case 'w': g_width = atoi(optarg); break;
            case 'h': g_height = atoi(optarg); break;
            case 'i': g_interval = atof(optarg); break;
            case 'f': g_jpeg_path = optarg; break;
            case 'n': g_workers = atoi(optarg); break;  /* 抓帧并发数 */
            default: break;
        }
    }
    if (g_workers < 1) g_workers = 1;
    if (g_workers > 16) g_workers = 16;
    fprintf(stderr, "[jpeg_srv] cfg %dx%d interval=%.2fs workers=%d file=%s\n",
            g_width, g_height, g_interval, g_workers, g_jpeg_path.c_str());
    detect_device_resolution();

    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, [](int){ g_running = false; });
    signal(SIGTERM, [](int){ g_running = false; });

    int fd = setup_listener(g_port);
    if (fd < 0) return 1;

    /* 启动 N 个抓帧 worker.
       实测并发可把吞吐从 5.5fps 提到 ~16fps (8 核设备). */
    for (int i = 0; i < g_workers; ++i) {
        pthread_t ct;
        pthread_create(&ct, nullptr, capture_thread, (void*)(intptr_t)i);
        pthread_detach(ct);
    }
    fprintf(stderr, "[jpeg_srv] %d 抓帧 worker 已启动\n", g_workers);

    /* 等第一帧就绪 */
    for (int i = 0; i < 50; ++i) {
        { std::lock_guard<std::mutex> lk(g_frame_mutex); if (!g_frame.empty()) break; }
        usleep(100 * 1000);
    }

    while (g_running.load()) {
        struct sockaddr_in cli{};
        socklen_t len = sizeof(cli);
        int cfd = accept(fd, (struct sockaddr*)&cli, &len);
        if (cfd < 0) { if (errno == EINTR) continue; break; }
        serve_once(cfd);
    }
    close(fd);
    fprintf(stderr, "[jpeg_srv] stopped\n");
    return 0;
}
