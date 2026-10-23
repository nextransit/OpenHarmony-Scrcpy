"""
MJPEG 服务端管理器 - 在设备端部署 busybox httpd + 后台截图循环

适配 RK3568 等无法使用 HEVC 硬件编码器的 OpenHarmony 设备。
"""
import time
import threading
from typing import Optional

import os

from .constants import LogLevel
from .logger import print_log
from .hdc_executor import HDCCommandExecutor
from .arch_detector import detect_device_arch, local_binary_arch, ensure_connected


class MjpegServerManager:
    """在设备端部署 MJPEG 服务 (替代 ohscrcpy_server)"""
    
    DEFAULT_PORT = 27190
    # 默认横屏 960x540: 实测 loongarch 设备 snapshot_display 抓帧上限约 6-13fps,
    # 降低分辨率 + 缩短间隔后客户端实测 16-20fps (原 1080x1920 是竖屏值, 会写反)
    DEFAULT_WIDTH = 960
    DEFAULT_HEIGHT = 540
    DEFAULT_INTERVAL_S = 0.05
    LOOP_PID_FILE = "/data/local/tmp/ohscrcpy_mjpeg_loop.pid"
    
    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc = hdc_executor
        self.port = self.DEFAULT_PORT
        self.width = self.DEFAULT_WIDTH
        self.height = self.DEFAULT_HEIGHT
        self.interval = self.DEFAULT_INTERVAL_S
        self.device_arch: str = ""
        self.uses_jpeg_server: bool = False
        self.log_title = "MJPEG服务"
    
    def setup(self, port: int = DEFAULT_PORT, width: int = DEFAULT_WIDTH, 
              height: int = DEFAULT_HEIGHT, interval_s: float = DEFAULT_INTERVAL_S, env_override: bool = True) -> bool:
        """
        部署 MJPEG 服务:
        1. 启动 busybox httpd (静态文件服务,端口 <port>)
        2. 启动后台循环 sh:每 <interval_s> 秒截图到 screen.jpeg (atomic rename)
        3. 端口转发: hdc fport tcp:<port> tcp:<port>
        
        Returns True if all steps succeeded.
        """
        self.port = port
        # 环境变量覆盖 (便于运行时切换)
        if env_override:
            import os
            env_w = os.environ.get('OHCRCPY_MJPEG_WIDTH')
            env_h = os.environ.get('OHCRCPY_MJPEG_HEIGHT')
            env_port = os.environ.get('OHCRCPY_MJPEG_PORT')
            env_int = os.environ.get('OHCRCPY_MJPEG_INTERVAL')
            if env_w: width = int(env_w)
            if env_h: height = int(env_h)
            if env_port: port = int(env_port)
            if env_int: interval_s = float(env_int)
        self.port = port
        self.width = width
        self.height = height
        self.interval = interval_s
        
        print_log(LogLevel.INFO, self.log_title, 
            f"开始部署 MJPEG 服务 (port={port}, {width}x{height}, interval={interval_s}s)")
        
        # 1. 清理之前的进程
        self._stop_background()
        time.sleep(0.5)

        # 2. 探测设备能力: 有 busybox 用 busybox httpd, 否则用自带的 jpeg_server
        #    (精简版 OpenHarmony 如 loongarch/KaihongOS 没有 busybox, 也没有 toybox httpd)
        self.device_arch = detect_device_arch(self.hdc, self.hdc.get_current_device())
        self.uses_jpeg_server = not self._has_busybox()

        if self.uses_jpeg_server:
            print_log(LogLevel.INFO, self.log_title,
                f"设备无 busybox (arch={self.device_arch}), 改用内置 jpeg_server")
            # 3a. 推送 jpeg_server 二进制
            if not self._install_jpeg_server():
                print_log(LogLevel.ERROR, self.log_title, "jpeg_server 部署失败")
                return False
            # 4a. 启动 jpeg_server (自带抓帧循环 + HTTP 服务)
            if not self._start_jpeg_server():
                # 抓屏被 SELinux 拦截时, 切 Permissive 再试一次
                if self._ensure_permissive() and self._start_jpeg_server():
                    pass
                else:
                    print_log(LogLevel.ERROR, self.log_title, "jpeg_server 启动失败")
                    return False
        else:
            # 3b. 启动 busybox httpd
            if not self._start_httpd():
                print_log(LogLevel.ERROR, self.log_title, "busybox httpd 启动失败")
                return False
            # 4b. 启动后台循环
            if not self._start_snapshot_loop():
                print_log(LogLevel.ERROR, self.log_title, "后台截图循环启动失败")
                return False

        # 5. 端口转发
        if not self._setup_port_forward(port, port):
            print_log(LogLevel.ERROR, self.log_title, "端口转发建立失败")
            return False

        print_log(LogLevel.INFO, self.log_title, f"MJPEG 服务部署成功: http://127.0.0.1:{port}/screen.jpeg")
        return True

    def _ensure_permissive(self) -> bool:
        """确保设备 SELinux 处于 Permissive (仅在必要时, 且失败不阻断).

        部分 OHOS 设备在 Enforcing 下会拒绝 snapshot_display / 抓屏 API,
        表现为抓帧失败或 jpeg_server 拿不到像素. 提前切到 Permissive 可避免.

        规则:
          - 已经是 Permissive -> 直接返回 True
          - 设备不允许 setenforce (非 root / 无权限) -> 返回 False, 不阻断主流程
          - OHCRCPY_NO_SETENFORCE=1 -> 跳过 (用户显式禁用)
        """
        if os.environ.get("OHCRCPY_NO_SETENFORCE", "") == "1":
            return False
        r = self._exec("shell", "getenforce 2>/dev/null", timeout=5)
        state = (r.get("stdout") or "").strip().lower()
        if not state:
            return False
        if state == "permissive":
            return True
        if state != "enforcing":
            return False
        r2 = self._exec("shell", "setenforce 0 2>/dev/null; getenforce 2>/dev/null", timeout=8)
        now = (r2.get("stdout") or "").strip().lower()
        if now == "permissive":
            print_log(LogLevel.WARN, self.log_title,
                "设备 SELinux 为 Enforcing, 已自动切换到 Permissive (抓屏需要)")
            return True
        return False

    def _has_busybox(self) -> bool:
        """设备是否有 busybox (且支持 httpd applet)."""
        r = self._exec("shell", "busybox --help 2>/dev/null | head -1", timeout=5)
        if r.get("success") and "BusyBox" in (r.get("stdout") or ""):
            return True
        return False

    def _jpeg_server_path(self) -> str:
        """按设备架构选本地 jpeg_server 二进制."""
        device_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "device")
        names = {
            "aarch64": "jpeg_server",
            "arm": "jpeg_server_arm",
            "loongarch64": "jpeg_server_loongarch",
        }
        name = names.get(self.device_arch, "jpeg_server")
        path = os.path.join(device_dir, name)
        if os.path.exists(path):
            return path
        return os.path.join(device_dir, "jpeg_server")

    def _install_jpeg_server(self) -> bool:
        """推送 jpeg_server 到设备 (按架构选二进制 + 架构一致性校验)."""
        local = self._jpeg_server_path()
        if not os.path.exists(local):
            print_log(LogLevel.ERROR, self.log_title,
                f"未找到本地 jpeg_server 二进制: {local} "
                f"(需先用 scripts/build_jpeg_server.sh 编译)")
            return False

        actual = local_binary_arch(local)
        if actual and self.device_arch and actual != self.device_arch:
            print_log(LogLevel.ERROR, self.log_title,
                f"jpeg_server 架构 [{actual}] 与设备 [{self.device_arch}] 不匹配, 拒绝推送")
            return False

        remote = "/data/local/tmp/ohscrcpy_jpeg_server"
        self._exec("shell", f"rm -f {remote}", timeout=5)
        r = self._exec("file", "send", local, remote, timeout=120)
        if not r.get("success"):
            print_log(LogLevel.ERROR, self.log_title,
                f"jpeg_server 推送失败: {r.get('stderr') or r.get('stdout')}")
            return False
        r = self._exec("shell", f"chmod 755 {remote}", timeout=10)
        if not r.get("success"):
            print_log(LogLevel.ERROR, self.log_title, "jpeg_server chmod 失败")
            return False
        print_log(LogLevel.INFO, self.log_title,
            f"jpeg_server 已推送: {local} (arch={actual or '未知'}) -> {remote}")
        return True

    def _start_jpeg_server(self) -> bool:
        """启动设备端 jpeg_server (自带抓帧循环 + HTTP 服务)."""
        remote = "/data/local/tmp/ohscrcpy_jpeg_server"
        self._exec("shell", f"pkill -9 -f {remote} 2>/dev/null", timeout=5)
        time.sleep(0.3)
        cmd = (f"nohup {remote} -p {self.port} -w {self.width} -h {self.height} "
               f"-i {self.interval} "
               f"-f /data/local/tmp/ohscrcpy_screen.jpeg "
               f"> /data/local/tmp/ohscrcpy_jpeg_server.log 2>&1 < /dev/null &")
        self._exec("shell", cmd, timeout=8)

        # 等待监听 + 首帧就绪
        for attempt in range(10):
            time.sleep(0.6)
            r = self._exec("shell",
                           f"netstat -an 2>/dev/null | grep 'tcp.*:{self.port}.*LISTEN'",
                           timeout=5)
            if (r.get("stdout") or "").strip():
                print_log(LogLevel.INFO, self.log_title,
                    f"jpeg_server 已监听 {self.port} (尝试 {attempt+1}/10)")
                # 再确认抓到帧
                r2 = self._exec("shell",
                                "ls -la /data/local/tmp/ohscrcpy_screen.jpeg 2>/dev/null",
                                timeout=5)
                if (r2.get("stdout") or "").strip():
                    return True
                print_log(LogLevel.WARN, self.log_title, "jpeg_server 已监听但暂无截图文件")
        log = self._exec("shell",
                         "cat /data/local/tmp/ohscrcpy_jpeg_server.log 2>/dev/null | tail -10",
                         timeout=5)
        print_log(LogLevel.ERROR, self.log_title,
            f"jpeg_server 未监听 {self.port}. 日志: {(log.get('stdout') or '')[-400:]}")
        return False
    
    def _exec(self, *args, timeout=10):
        return self.hdc.execute(list(args), timeout=timeout)
    
    def _stop_background(self) -> None:
        """清理后台进程

        MJPEG 模式下, 如果设备端残留 ohscrcpy_server (HEVC 模式产物),
        它会占用 27183 端口并响应 SCREEN_INFO banner,
        导致 mjpeg_client 连 27190 失败或拿到错误 banner -> 黑屏.
        """
        # 仅杀 snapshot_display 子进程不够: 外层 `sh -c while ...` 会在 sleep
        # 结束后再次拉起它, 多个循环会同时覆盖 screen.jpeg, 造成断流/黑帧.
        # 先按 PID 和唯一命令标记杀掉循环本身, 再清理旧版本遗留的子进程.
        try:
            self._exec(
                "shell",
                f"if [ -f {self.LOOP_PID_FILE} ]; then "
                f"kill -9 $(cat {self.LOOP_PID_FILE}) 2>/dev/null; "
                f"rm -f {self.LOOP_PID_FILE}; fi",
                timeout=3,
            )
        except Exception:
            pass
        try:
            # 兼容没有 PID 文件的旧循环; screen_new.jpeg 只出现在截图循环命令中.
            self._exec("shell", "pkill -9 -f screen_new.jpeg", timeout=3)
        except Exception:
            pass
        # 关键: 杀掉设备端残留的 ohscrcpy_server (HEVC 模式产物),
        # 释放 27183 端口并避免 banner 干扰 mjpeg_client.
        try:
            self._exec("shell", "pkill -9 -f ohscrcpy_server", timeout=3)
        except Exception:
            pass
        # 清理自建 jpeg_server (无 busybox 时的 MJPEG 服务)
        try:
            self._exec("shell", "pkill -9 -f ohscrcpy_jpeg_server", timeout=3)
        except Exception:
            pass
        try:
            self._exec("shell", "pkill -9 -f snapshot_display", timeout=3)
        except Exception:
            pass
        try:
            self._exec(
                "shell",
                f"pkill -9 -f 'busybox httpd.*-p {self.port}'",
                timeout=3,
            )
        except Exception:
            pass
    
    def _start_httpd(self) -> bool:
        """启动 busybox httpd"""
        # busybox httpd -p PORT -h /data/local/tmp
        # 注意:需要用 nohup 或 setsid 防止退出
        try:
            # 先测试 busybox 是否可用
            r = self._exec("shell", "busybox --help", timeout=3)
            if not r.get("success"):
                return False
            
            # 启动 httpd
            cmd = "shell"
            args_str = f"nohup busybox httpd -p {self.port} -h /data/local/tmp > /data/local/tmp/httpd.log 2>&1 < /dev/null &"
            r = self._exec("shell", args_str, timeout=5)
            
            # 验证: 多次重试 netstat 检查 (RK3568 busybox httpd 启动慢, 1-3s 不等)
            # 原代码只等 1s, 经常检查失败 → setup return False → MJPEG 部署失败 → 黑屏
            for attempt in range(6):  # 6 次 * 0.5s = 3s 总超时
                time.sleep(0.5)
                r2 = self._exec("shell", f"netstat -an 2>/dev/null | grep 'tcp.*:{self.port}.*LISTEN'", timeout=3)
                if r2.get("stdout", "").strip():
                    print_log(LogLevel.INFO, self.log_title, f"busybox httpd 已在端口 {self.port} 监听 (尝试 {attempt+1}/6)")
                    return True
            print_log(LogLevel.WARN, self.log_title, f"busybox httpd 监听检查失败 (3s 超时): stdout={r2.get('stdout')!r}")
            return False
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"启动 httpd 异常: {e}")
            return False
    
    def _setup_port_forward(self, local_port: int, device_port: int) -> bool:
        """hdc fport tcp:local tcp:device, 返回是否成功"""
        try:
            self._exec("fport", "rm", f"tcp:{local_port}", f"tcp:{device_port}", timeout=5)
        except Exception:
            pass
        r = self._exec("fport", f"tcp:{local_port}", f"tcp:{device_port}", timeout=5)
        if not r.get("success"):
            print_log(LogLevel.WARN, self.log_title, f"端口转发失败: {r.get('stdout')}")
            return False
        print_log(LogLevel.INFO, self.log_title, f"端口转发建立: tcp:{local_port} → tcp:{device_port}")
        # 等待端口转发稳定 (hdc fport 是异步生效,通常需要 0.5-1s)
        time.sleep(1.0)
        # 验证: 列出所有 fport
        r2 = self._exec("fport", "ls", timeout=3)
        if f"tcp:{local_port}" in r2.get("stdout", ""):
            return True
        print_log(LogLevel.WARN, self.log_title, f"端口转发未生效, 重试...")
        time.sleep(2.0)
        return True
    
    def _start_snapshot_loop(self) -> bool:
        """启动后台循环截图 sh"""
        # sh 脚本 (注意:busybox sh 支持 while+sleep)
        # snapshot_display 要求后缀 .jpeg,所以先用临时 .jpeg 再 atomic rename
        # 注意: screen.tmp → screen.jpeg 用 mv 实现 atomic swap
        interval_ms = int(self.interval * 1000)
        # 用 nohup + & 启动独立后台进程 (OHOS sh 不支持 setsid)
        sh_cmd = (
            f"nohup sh -c 'echo $$ > {self.LOOP_PID_FILE}; "
            f"while sleep {interval_ms/1000:.3f}; do "
            f"snapshot_display -i 0 -w {self.width} -h {self.height} "
            f"-f /data/local/tmp/screen_new.jpeg > /dev/null 2>&1 && "
            f"mv /data/local/tmp/screen_new.jpeg /data/local/tmp/screen.jpeg; "
            f"done' > /data/local/tmp/snap_loop.log 2>&1 < /dev/null &"
        )
        try:
            r = self._exec("shell", sh_cmd, timeout=5)
            time.sleep(2)
            # 验证: 是否 screen.jpeg 在变化
            r1 = self._exec("shell", "stat /data/local/tmp/screen.jpeg | grep Modify", timeout=3)
            t1 = r1.get("stdout", "")
            time.sleep(2)
            r2 = self._exec("shell", "stat /data/local/tmp/screen.jpeg | grep Modify", timeout=3)
            t2 = r2.get("stdout", "")
            if t1 and t2 and t1 != t2:
                print_log(LogLevel.INFO, self.log_title, "截图循环已运行 (screen.jpeg mtime 变化)")
                return True
            print_log(LogLevel.WARN, self.log_title, f"截图循环未运行 t1={t1!r} t2={t2!r}")
            return False
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"启动截图循环异常: {e}")
            return False
    
    def stop(self) -> None:
        """停止所有后台进程"""
        self._stop_background()
        # 端口转发清除
        try:
            self._exec("fport", "rm", f"tcp:{self.port}", f"tcp:{self.port}", timeout=3)
        except Exception:
            pass
        print_log(LogLevel.INFO, self.log_title, "MJPEG 服务已停止")


__all__ = ["MjpegServerManager"]
