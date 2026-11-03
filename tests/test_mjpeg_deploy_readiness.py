"""回归测试: MJPEG 服务部署就绪判定 + 帧率驱动的自适应分辨率.

覆盖两个已修复的缺陷:

1. 部署就绪判定 (core/mjpeg_server_manager.py)
   Bug: jpeg_server 在 worker > 1 时把帧写到 xxx.wN.jpeg 副文件, 主文件
        xxx.jpeg 永不生成. 而部署逻辑只检查主文件 -> 判定"暂无截图文件" ->
        部署必然失败 (真机 loongarch 192.168.19.9:8710 实测 setup() 返回 False).
   修复: 改用 HTTP 首帧探测 (权威判据), 文件探测仅作兜底.

2. 自适应分辨率判据 (video/mjpeg_client.py)
   Bug: 用**带宽**做降级判据, 但真机实测各档位带宽几乎恒定 (8-9 Mbps),
        只有帧率能区分档位 -> 1080p 卡在 1.2-3.6fps 却永不降级.
   修复: 改用**帧率**判据, 并加升档迟滞 + 改档静默期, 避免来回震荡.
"""
import os
import sys
import time
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mjpeg_server_manager import MjpegServerManager
from video.mjpeg_client import MjpegStreamClient


def _make_manager(workers=4, sn="192.168.19.9:8710"):
    m = MjpegServerManager.__new__(MjpegServerManager)
    m.hdc = unittest.mock.MagicMock()
    m.hdc.get_current_device.return_value = sn
    m.port = 27190
    m.width = 0
    m.height = 0
    m.interval = 0.05
    m.workers = workers
    m.client_host = "127.0.0.1"
    m.device_arch = "loongarch64"
    m.uses_jpeg_server = False
    m.log_title = "MJPEG服务"
    return m


def test_capture_glob_matches_worker_files():
    """多 worker 时 glob 必须覆盖 .wN.jpeg 副文件."""
    m4 = _make_manager(workers=4)
    m1 = _make_manager(workers=1)
    assert ".w*.jpeg" in m4._capture_file_glob(), m4._capture_file_glob()
    assert ".w*.jpeg" not in m1._capture_file_glob(), m1._capture_file_glob()
    assert m1._capture_file_glob().endswith(".jpeg")
    print("PASS: 抓帧文件 glob 覆盖 worker 副文件")


def test_probe_frame_over_http_accepts_jpeg_magic():
    """HTTP 探测: 200 + FFD8 魔数才算有帧."""
    m = _make_manager()

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b"\xff\xd8\xff\xe0"

    with unittest.mock.patch("urllib.request.urlopen", return_value=Resp()):
        assert m._probe_frame_over_http() is True

    class Bad(Resp):
        def read(self, n): return b"not-a-jpeg"
    with unittest.mock.patch("urllib.request.urlopen", return_value=Bad()):
        assert m._probe_frame_over_http() is False

    class NotFound(Resp):
        status = 503
    with unittest.mock.patch("urllib.request.urlopen", return_value=NotFound()):
        assert m._probe_frame_over_http() is False

    print("PASS: HTTP 首帧探测识别 JPEG 魔数")


def test_probe_capture_files_accepts_worker_files():
    """文件兜底探测必须能识别 .wN.jpeg 副文件."""
    m = _make_manager(workers=4)
    listing = ("-rw-r--r-- 1 root root 336408 2026-11-03 01:13 "
               "/data/local/tmp/ohscrcpy_screen.w0.jpeg")
    r = {"success": True, "stdout": listing}
    m._exec = lambda *a, **k: r
    assert m._probe_capture_files() is True

    empty = {"success": True, "stdout": ""}
    m._exec = lambda *a, **k: empty
    assert m._probe_capture_files() is False
    print("PASS: 文件兜底探测识别 worker 副文件")


def test_start_jpeg_server_succeeds_without_main_file():
    """核心回归: 主文件不存在时, 部署仍必须成功.

    旧逻辑只检查主文件 -> 必然失败. 新逻辑走 HTTP 首帧探测 -> 成功.
    """
    m = _make_manager(workers=4)

    def fake_exec(*args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if "netstat" in cmd:
            return {"success": True, "stdout": f"tcp 0 0 0.0.0.0:{m.port} 0.0.0.0:* LISTEN"}
        if "ls -l" in cmd:
            # 主文件不存在, 只有 worker 副文件
            return {"success": True,
                    "stdout": "-rw-r--r-- 1 root root 336408 2026-11-03 01:13 "
                              "/data/local/tmp/ohscrcpy_screen.w0.jpeg"}
        return {"success": True, "stdout": ""}

    m._exec = fake_exec
    m._direct_host = lambda: "192.168.19.9"

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b"\xff\xd8\xff\xe0"

    with unittest.mock.patch("urllib.request.urlopen", return_value=Resp()):
        # 旧逻辑会在这里死等 15 次然后返回 False
        assert m._start_jpeg_server() is True
    print("PASS: 主文件缺失时部署仍然成功")


def test_auto_resolution_uses_fps_not_bandwidth():
    """自适应分辨率必须用帧率判据: 带宽富余但帧率低 -> 仍要降级."""
    c = MjpegStreamClient(host="127.0.0.1", port=1)
    c.auto_res_enabled = True
    c.resolution_locked = False
    c._auto_res_index = 0          # 1920x1080
    c._auto_res_slow_count = 0
    c._auto_res_fast_count = 0
    c._auto_res_changed_at = 0.0
    c._auto_res_last_check = time.time() - 10.0
    c.total_bytes = 0
    c._auto_res_bytes_mark = 0
    c.frame_count = 0
    c._auto_res_frame_mark = 0

    requested = []

    def fake_request(w, h):
        requested.append((w, h))
        return True

    c._request_resolution = fake_request

    # 窗口内: 收到 30 Mbps (带宽很富余) 但只解出 1 帧 (帧率极低)
    c.total_bytes = 30_000_000
    c.frame_count = 1
    c._check_auto_resolution()
    # 第一个窗口只累计计数, 未达 SLOW_WINDOWS
    c._auto_res_last_check = time.time() - 10.0
    c.total_bytes += 30_000_000
    c.frame_count += 1
    c._check_auto_resolution()

    assert requested, "带宽富余但帧率低时应当降级 (带宽判据无法区分档位)"
    assert requested[0] == (1280, 720), requested
    print("PASS: 帧率判据生效 (带宽富余仍降级)")


def test_auto_resolution_no_oscillation():
    """升档门槛必须高于降档门槛, 否则在临界点来回横跳."""
    assert MjpegStreamClient.AUTO_RES_UPGRADE_RATIO > 1.0
    up = MjpegStreamClient.AUTO_RES_MIN_FPS * MjpegStreamClient.AUTO_RES_UPGRADE_RATIO
    assert up > MjpegStreamClient.AUTO_RES_MIN_FPS
    print(f"PASS: 升档门槛 {up:.1f}fps > 降档门槛 "
          f"{MjpegStreamClient.AUTO_RES_MIN_FPS:.1f}fps")


def test_auto_resolution_settle_period_blocks_decisions():
    """改档静默期内不得改档 (过渡帧会误导判定)."""
    c = MjpegStreamClient(host="127.0.0.1", port=1)
    c.auto_res_enabled = True
    c.resolution_locked = False
    c._auto_res_index = 0
    c._auto_res_slow_count = 0
    c._auto_res_fast_count = 0
    c._auto_res_changed_at = time.time()   # 刚刚改过档
    c._auto_res_last_check = time.time() - 10.0
    c.total_bytes = 0
    c._auto_res_bytes_mark = 0
    c.frame_count = 0
    c._auto_res_frame_mark = 0
    requested = []
    c._request_resolution = lambda w, h: requested.append((w, h)) or True

    c.frame_count = 1        # 帧率极低
    c._check_auto_resolution()
    assert not requested, "静默期内不应改档"
    print("PASS: 改档静默期内不触发降级")

def test_port_forward_failure_tolerated_for_direct_ip():
    """健壮性: 直连设备 IP 时, fport 失败不应中断部署.

    客户端在有设备 IP 时走直连, 根本不用 fport. 若 fport 失败就返回 False,
    会把"服务已起好"误报成"部署失败".
    """
    m = _make_manager(workers=4)
    m.width = 0
    m.height = 0
    m.uses_jpeg_server = True
    m.device_arch = "loongarch64"

    def fake_exec(*args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if "netstat" in cmd:
            return {"success": True,
                    "stdout": f"tcp 0 0 0.0.0.0:{m.port} 0.0.0.0:* LISTEN"}
        if "ls -l" in cmd:
            return {"success": True,
                    "stdout": "-rw-r--r-- 1 root root 336408 x "
                              "/data/local/tmp/ohscrcpy_screen.w0.jpeg"}
        return {"success": True, "stdout": ""}

    m._exec = fake_exec
    m._has_busybox = lambda: False
    m._install_jpeg_server = lambda: True
    m._setup_port_forward = lambda lp, dp: False    # fport 失败

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b"\xff\xd8\xff\xe0"

    with unittest.mock.patch("urllib.request.urlopen", return_value=Resp()):
        assert m.setup() is True, "直连模式下 fport 失败不应中断部署"
    assert m.client_host == "192.168.19.9", m.client_host
    print("PASS: 直连模式下 fport 失败仍可部署")


def test_port_forward_failure_fatal_without_direct_ip():
    """USB 场景: 无直连地址时 fport 失败必须失败 (无可用通路)."""
    m = _make_manager(workers=4, sn="emulator-5554")   # 无 ":port" -> 无直连 IP
    m.width = 0
    m.height = 0
    m.uses_jpeg_server = True
    m.device_arch = "loongarch64"

    def fake_exec(*args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if "netstat" in cmd:
            return {"success": True,
                    "stdout": f"tcp 0 0 0.0.0.0:{m.port} 0.0.0.0:* LISTEN"}
        if "ls -l" in cmd:
            return {"success": True,
                    "stdout": "-rw-r--r-- 1 root root 336408 x "
                              "/data/local/tmp/ohscrcpy_screen.w0.jpeg"}
        return {"success": True, "stdout": ""}

    m._exec = fake_exec
    m._has_busybox = lambda: False
    m._install_jpeg_server = lambda: True
    m._setup_port_forward = lambda lp, dp: False

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b"\xff\xd8\xff\xe0"

    with unittest.mock.patch("urllib.request.urlopen", return_value=Resp()):
        assert m.setup() is False, "无直连地址时 fport 失败必须中断部署"
    print("PASS: 无直连地址时 fport 失败中断部署")




def test_manual_resolution_selection_locks_auto_downgrade():
    """手动选分辨率 (含"已是该分辨率"分支) 必须锁定, 防止被自动降级改回.

    Bug: set_capture_resolution 的 early-return 分支 (设备端已是该分辨率)
         没有设置 resolution_locked -> 用户选 1080p 后, 自动降级下一窗口
         就把它改回 540p (表现为"选了没用, 画面很糊").
    """
    c = MjpegStreamClient(host="127.0.0.1", port=1)
    c.resolution_locked = False
    c.auto_res_enabled = True

    c.query_capture_resolution = lambda: (1920, 1080)
    assert c.set_capture_resolution(1920, 1080) is True
    assert c.resolution_locked is True, "已是该分辨率的分支也必须锁定"
    assert c.auto_res_enabled is False

    # 非 early-return 分支: 设备端当前是别的分辨率
    c2 = MjpegStreamClient(host="127.0.0.1", port=1)
    c2.resolution_locked = False
    c2.auto_res_enabled = True
    c2.query_capture_resolution = lambda: (1920, 1080)
    c2._request_resolution = lambda w, h: True
    assert c2.set_capture_resolution(640, 360) is True
    assert c2.resolution_locked is True
    assert c2.auto_res_enabled is False
    print("PASS: 手动分辨率选择锁定自动降级")

if __name__ == "__main__":
    test_capture_glob_matches_worker_files()
    test_probe_frame_over_http_accepts_jpeg_magic()
    test_probe_capture_files_accepts_worker_files()
    test_start_jpeg_server_succeeds_without_main_file()
    test_auto_resolution_uses_fps_not_bandwidth()
    test_auto_resolution_no_oscillation()
    test_auto_resolution_settle_period_blocks_decisions()
    test_port_forward_failure_tolerated_for_direct_ip()
    test_port_forward_failure_fatal_without_direct_ip()
    test_manual_resolution_selection_locks_auto_downgrade()
    print("\nALL PASS: MJPEG 部署就绪判定 + 帧率自适应分辨率")
