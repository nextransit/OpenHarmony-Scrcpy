"""回归测试: MainWindow.__init__ 不再因为子线程 Tk 操作崩溃.

Bug 现象: _init_components_async 在子线程创建 video_display 时调
         device_controller.bind_video_canvas -> canvas.bind ->
         抛 RuntimeError("main thread is not in main loop")
         init 线程崩溃, video_display 永远 None, canvas 永远黑屏.

修复: 把 GUI 相关的 video_display 创建移到主线程 (defer_to_main),
      用 _gui_queue + _pump_gui_queue 跨线程派发.
"""
import sys, os, time, threading, unittest.mock
sys.path.insert(0, '/home/ben/ohscrcpy')
os.environ.setdefault('DISPLAY', ':10')

class FakeHDC:
    def __init__(self, *a, **kw): pass
    def execute(self, *a, **kw): return {'success': True, 'stdout': '', 'stderr': ''}
    def _find_hdc_path(self): return '/fake/hdc'
    def set_device(self, sn): pass
    def stop_async_processes(self): pass

with unittest.mock.patch('core.hdc_executor.HDCCommandExecutor', FakeHDC):
    with unittest.mock.patch('core.device_manager.HDCCommandExecutor', FakeHDC):
        with unittest.mock.patch('core.server_manager.HDCCommandExecutor', FakeHDC):
            import gui.main_window as mw_mod
            mw = mw_mod.MainWindow.__new__(mw_mod.MainWindow)
            mw.root = tk_root = __import__('tkinter').Tk()
            tk_root.geometry("1450x900")
            mw.video_display = None
            mw.is_connected = True
            mw.server_deploy_lock = threading.Lock()
            mw.server_deploy_state = None
            mw.video_client = None
            mw.connection_manager = None
            mw.server_deployer = None
            mw.device_controller = None

            mw.__init__()
            # 等 GUI pump 处理
            time.sleep(0.3)
            tk_root.update()

            assert mw.video_display is not None, "FAIL: video_display 未创建"
            assert mw.device_controller is not None, "FAIL: device_controller 未创建"
            assert hasattr(mw, '_main_tid'), "FAIL: 缺 _main_tid"
            assert hasattr(mw, '_gui_queue'), "FAIL: 缺 _gui_queue"
            print(f"PASS: video_display={mw.video_display.__class__.__name__}", flush=True)
            print(f"  device_controller={mw.device_controller.__class__.__name__}", flush=True)
            print(f"  _main_tid={mw._main_tid}", flush=True)

            tk_root.destroy()
