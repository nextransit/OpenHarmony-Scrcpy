"""
MJPEG 服务端管理器 - 在设备端部署 busybox httpd + 后台截图循环

适配 RK3568 等无法使用 HEVC 硬件编码器的 OpenHarmony 设备。
"""
import time
import threading
from typing import Optional

from .constants import LogLevel
from .logger import print_log
from .hdc_executor import HDCCommandExecutor


class MjpegServerManager:
    """在设备端部署 MJPEG 服务 (替代 ohscrcpy_server)"""
    
    DEFAULT_PORT = 27190
    DEFAULT_WIDTH = 1080
    DEFAULT_HEIGHT = 1920
    DEFAULT_INTERVAL_S = 0.15  # 截图间隔 (~6fps)
    LOOP_PID_FILE = "/data/local/tmp/ohscrcpy_mjpeg_loop.pid"
    
    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc = hdc_executor
        self.port = self.DEFAULT_PORT
        self.width = self.DEFAULT_WIDTH
        self.height = self.DEFAULT_HEIGHT
        self.interval = self.DEFAULT_INTERVAL_S
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
            if env_w: width = int(env_w)
            if env_h: height = int(env_h)
            if env_port: port = int(env_port)
        self.port = port
        self.width = width
        self.height = height
        self.interval = interval_s
        
        print_log(LogLevel.INFO, self.log_title, 
            f"开始部署 MJPEG 服务 (port={port}, {width}x{height}, interval={interval_s}s)")
        
        # 1. 清理之前的进程
        self._stop_background()
        time.sleep(0.5)
        
        # 2. 启动 busybox httpd
        if not self._start_httpd():
            print_log(LogLevel.ERROR, self.log_title, "busybox httpd 启动失败")
            return False
        
        # 3. 端口转发
        if not self._setup_port_forward(port, port):
            print_log(LogLevel.ERROR, self.log_title, "端口转发建立失败")
            return False
        
        # 4. 启动后台循环
        if not self._start_snapshot_loop():
            print_log(LogLevel.ERROR, self.log_title, "后台截图循环启动失败")
            return False
        
        print_log(LogLevel.INFO, self.log_title, f"MJPEG 服务部署成功: http://127.0.0.1:{port}/screen.jpeg")
        return True
    
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
