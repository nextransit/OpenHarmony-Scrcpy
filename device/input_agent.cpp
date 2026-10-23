// input_agent.cpp - 设备端常驻输入代理 (OpenHarmony)
//
// 存在的理由 (真机 192.168.19.11:8710 loongarch 实测):
//   客户端每次点击都执行一次 `hdc shell uinput ...`, 完整链路耗时:
//       hdc 往返            ~15ms
//       uinput 进程启动    ~104ms  (musl 动态链接)
//       uinput 注入动作    ~276ms  (默认 -i interval)
//       ------------------------------------------
//       单次点击合计        ~380ms, 且实测抖动到 3.4s
//
//   本代理常驻设备端, 客户端通过一条持久 TCP 连接下发指令,
//   代理内部调用 uinput 执行注入:
//       - 去掉每次点击的 hdc 进程启动开销
//       - 支持一条指令内批量执行多个动作 (uinput -T -d/-u -d/-u),
//         实测两个 tap 合并后约 90ms/次 (对比单独调用 208ms/次)
//       - 客户端到设备的往返只有 ~15ms
//
// 协议 (文本行, \n 结尾):
//   T <x> <y>              触摸 tap (down+up)
//   M <x> <y>              鼠标移动 (绝对坐标)
//   D <x> <y>              触摸 down
//   U <x> <y>              触摸 up
//   S <x1> <y1> <x2> <y2> [ms]   滑动
//   K <keycode>            按键 down
//   R <keycode>            按键 up
//   C <x> <y>              右键 (OHOS 语义: 触摸定位 + KEY_BACK)
//   B <keycode>            直接下发按键 down+up
//   P                      ping (回 "OK")
//   Q                      退出
//
// 响应: 每条指令回一行 "OK" 或 "ERR <msg>"
//
// 编译 (loongarch64): 见 scripts/build_input_agent.sh
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/wait.h>
#include <string>
#include <vector>

static volatile sig_atomic_t g_running = 1;
static int g_listen_sock = -1;

/* 注入串行锁: 多个客户端同时下发时, uinput 调用必须按到达顺序执行,
   否则 tap/swipe 的先后会乱序 (用户看到点击位置错乱). */
static pthread_mutex_t g_inject_lock = PTHREAD_MUTEX_INITIALIZER;

static void on_signal(int) { g_running = 0; if (g_listen_sock >= 0) close(g_listen_sock); }

/* 执行一条 uinput 命令 (fork+exec, 不阻塞调用方太久).
   返回 true 表示 uinput 进程正常启动. */
static bool run_uinput(const std::vector<std::string>& args) {
    if (args.empty()) return false;
    pid_t pid = fork();
    if (pid < 0) return false;
    if (pid == 0) {
        /* 子进程: 静默执行, 避免污染代理的 stdout (会破坏协议) */
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            if (devnull > STDERR_FILENO) close(devnull);
        }
        std::vector<char*> argv;
        argv.reserve(args.size() + 1);
        for (const auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
        argv.push_back(nullptr);
        execv("/system/bin/uinput", argv.data());
        _exit(127);
    }
    /* 父进程: 不 wait, 避免 uinput 内部延迟阻塞指令响应.
       但要等 uinput 真正执行完再放行下一条, 保证注入顺序.
       最多等 500ms, 超时也放行 (不能让一条卡死整个代理). */
    int status = 0;
    for (int i = 0; i < 500; i++) {
        pid_t r = waitpid(pid, &status, WNOHANG);
        if (r == pid) return true;
        if (r < 0) break;
        usleep(1000);
    }
    return true;
}

/* 把 "T 100 200" 之类的动作翻译成 uinput 参数, 合并到同一个 uinput 调用里.
   uinput 一次调用可带多个动作, 实测比多次调用快一倍以上. */
static bool handle_command(const std::string& line, std::string& reply) {
    /* 整个指令处理串行化: 保证注入顺序 */
    pthread_mutex_lock(&g_inject_lock);
    struct UnlockGuard {
        ~UnlockGuard() { pthread_mutex_unlock(&g_inject_lock); }
    } unlock_guard;

    std::vector<std::string> tok;
    size_t i = 0;
    while (i < line.size()) {
        while (i < line.size() && (line[i] == ' ' || line[i] == '\t' || line[i] == '\r')) i++;
        if (i >= line.size()) break;
        size_t start = i;
        while (i < line.size() && line[i] != ' ' && line[i] != '\t' && line[i] != '\r') i++;
        tok.push_back(line.substr(start, i - start));
    }
    if (tok.empty()) { reply = "OK"; return true; }

    const std::string& cmd = tok[0];

    if (cmd == "P") { reply = "OK"; return true; }
    if (cmd == "Q") { reply = "OK"; g_running = 0; if (g_listen_sock >= 0) { close(g_listen_sock); g_listen_sock = -1; } return true; }

    if (cmd == "T" && tok.size() >= 3) {
        /* 触摸 tap: down + up 合并进一次 uinput 调用 */
        run_uinput({"uinput", "-T", "-i", "1", "-d", tok[1], tok[2], "-u", tok[1], tok[2]});
        reply = "OK";
        return true;
    }
    if (cmd == "D" && tok.size() >= 3) {
        run_uinput({"uinput", "-T", "-i", "1", "-d", tok[1], tok[2]});
        reply = "OK"; return true;
    }
    if (cmd == "U" && tok.size() >= 3) {
        run_uinput({"uinput", "-T", "-i", "1", "-u", tok[1], tok[2]});
        reply = "OK"; return true;
    }
    if (cmd == "M" && tok.size() >= 3) {
        run_uinput({"uinput", "-M", "-m", tok[1], tok[2]});
        reply = "OK"; return true;
    }
    if (cmd == "S" && tok.size() >= 5) {
        std::vector<std::string> a = {"uinput", "-T", "-m", tok[1], tok[2], tok[3], tok[4]};
        if (tok.size() >= 6) a.push_back(tok[5]);
        run_uinput(a);
        reply = "OK"; return true;
    }
    if (cmd == "K" && tok.size() >= 2) {
        run_uinput({"uinput", "-K", "-d", tok[1]});
        reply = "OK"; return true;
    }
    if (cmd == "R" && tok.size() >= 2) {
        run_uinput({"uinput", "-K", "-u", tok[1]});
        reply = "OK"; return true;
    }
    if (cmd == "C" && tok.size() >= 3) {
        /* 右键 (OHOS 语义): 触摸定位 + KEY_BACK (keycode=2).
           OHOS 没有传统右键菜单, 鼠标右键被映射为 KEY_BACK, 与
           gui/device_controller.py::send_mouse_right_click 保持一致. */
        run_uinput({"uinput", "-T", "-i", "1", "-d", tok[1], tok[2], "-u", tok[1], tok[2]});
        run_uinput({"uinput", "-K", "-d", "2", "-u", "2"});
        reply = "OK"; return true;
    }
    if (cmd == "B" && tok.size() >= 2) {
        /* KEY_BACK 单独下发 (keycode) */
        run_uinput({"uinput", "-K", "-d", tok[1], "-u", tok[1]});
        reply = "OK"; return true;
    }

    reply = "ERR unknown";
    return false;
}

/* 每个客户端一个线程: GUI 独占连接的同时, 调试脚本也能另开连接,
   不会因为前一个客户端不读回复而阻塞. */
struct ClientCtx {
    int fd;
};

static void* client_thread(void* arg) {
    int cfd = (int)(intptr_t)arg;
    std::string buf;
    char chunk[1024];
    while (g_running) {
        ssize_t n = recv(cfd, chunk, sizeof(chunk), 0);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            break;
        }
        buf.append(chunk, (size_t)n);
        /* 逐行处理 */
        size_t pos;
        while ((pos = buf.find('\n')) != std::string::npos) {
            std::string line = buf.substr(0, pos);
            buf.erase(0, pos + 1);
            if (line.empty()) continue;
            std::string reply;
            handle_command(line, reply);
            reply.push_back('\n');
            ssize_t w = send(cfd, reply.data(), reply.size(), MSG_NOSIGNAL);
            (void)w;
        }
    }
    close(cfd);
    return nullptr;
}

int main(int argc, char** argv) {
    int port = 27195;
    if (argc > 1) port = atoi(argv[1]);

    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) { perror("bind"); return 1; }
    if (listen(fd, 8) < 0) { perror("listen"); return 1; }
    g_listen_sock = fd;
    fprintf(stderr, "[input_agent] listening on %d\n", port);

    while (g_running) {
        struct sockaddr_in cli{};
        socklen_t len = sizeof(cli);
        int cfd = accept(fd, (struct sockaddr*)&cli, &len);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            break;
        }
        /* 每连接一个线程 (detach), 主线程继续 accept */
        pthread_t th;
        pthread_create(&th, nullptr, client_thread, (void*)(intptr_t)cfd);
        pthread_detach(th);
    }
    fprintf(stderr, "[input_agent] stopped\n");
    return 0;
}
