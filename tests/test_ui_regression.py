"""完整 MainWindow 启动测试 + 模拟设备连接."""
import sys, time, os
sys.path.insert(0, '/home/ben/ohscrcpy')

import tkinter as tk
from gui.theme import Theme

import gui.main_window as mw_mod

# 直接构造 MainWindow 但绕过 __init__ 里的 _init_components_async
# 因为那要 hdc
mw = mw_mod.MainWindow.__new__(mw_mod.MainWindow)
mw.root = tk.Tk()
mw.root.title("OHScrcpy - test")
mw.root.geometry("1450x900")
mw.root.update()

mw.video_display = None
mw._setup_ui()
mw.root.update()
mw.root.update_idletasks()

print(f"Video canvas:  {mw.video_canvas.winfo_width()}x{mw.video_canvas.winfo_height()}")
print(f"FloatingBar:   {'OK' if hasattr(mw, 'floating_bar') else 'MISSING'}")
print(f"ControlPanel:  {'OK' if mw.control_panel else 'MISSING'}")
print(f"DevicePanel:   {'OK' if mw.device_panel else 'MISSING'}")
print(f"Title lbl:     {'OK' if mw.device_status_label else 'MISSING'}")

# 模拟 device_controller + video_display
class FakeDC:
    def __init__(self): 
        self.drag_start = None; self._raw_drag = False
        self.video_canvas = mw.video_canvas
        self.display_width = 1080; self.display_height = 1920
        self.display_ratio = 0.5
        self.video_width = 1080; self.video_height = 1920
        self.left = 0; self.top = 0; self.right = 0; self.bottom = 0
    def set_display_resolution(self, vw, vh, cw, ch):
        r = min(cw/vw, ch/vh)
        self.display_width = int(vw*r); self.display_height = int(vh*r); self.display_ratio = r
        return self.display_width, self.display_height, r
    def bind_video_canvas(self, c): pass

mw.device_controller = FakeDC()
import gui.video_display as vd_mod
mw.video_display = vd_mod.VideoDisplay(
    root=mw.root, canvas=mw.video_canvas,
    device_controller=mw.device_controller,
    performance_label=mw.performance_label,
)
import numpy as np
frame = np.random.randint(0, 255, (1920, 1080, 3), dtype=np.uint8)
mw.video_display.on_frame_decoded(frame)
mw.root.update(); mw.root.update_idletasks()

print(f"PillBadge text: '{mw.video_display.pill_badge._text}'")
print(f"PillBadge visible: {mw.video_display._osd_visible}")
mw.video_display.toggle_osd()
print(f"After F8 OSD visible: {mw.video_display._osd_visible}")

try:
    mw._show_help_dialog()
    print("Help dialog: OK")
except Exception as e:
    print(f"Help dialog failed: {e}")

# FloatingBar 已删除. 验证 control_panel 按钮都可见
not_visible = [n for n, b in mw.control_panel._buttons.items() if not b.winfo_viewable()]
print(f"Buttons not visible: {not_visible} (should be empty)")
assert not not_visible, f"Some buttons are hidden: {not_visible}"
print(f"ControlPanel: {mw.control_panel.frame.winfo_width()}x{mw.control_panel.frame.winfo_height()}")

# 模拟 OSD 切换 3 次
for i in range(3):
    mw.video_display.toggle_osd()
print(f"After 3x toggle OSD visible: {mw.video_display._osd_visible}")

# 检查 ModernButton 是否正确处理点击
connect_btn = mw.control_panel.get_button('connect')
print(f"Connect button: {connect_btn}, text='{connect_btn._text}'")
power_btn = mw.control_panel.get_button('power')
print(f"Power button: {power_btn}, text='{power_btn._text}'")

# 模拟点击 power (调用 send_key 走 uinput -K, 应该调用 hdc.execute)
clicked = []
orig_key = mw._power_key
mw._power_key = lambda: clicked.append('power')
mw._power_key()
print(f"Power click -> {clicked}")

mw.root.after(300, mw.root.destroy)
mw.root.mainloop()
print("\n=== ALL MAIN WINDOW TESTS PASS ===")
