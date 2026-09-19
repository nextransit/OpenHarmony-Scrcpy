import sys
sys.path.insert(0, '/home/ben/ohscrcpy')
import tkinter as tk
from gui.widgets import PillBadge, PhoneFrame

root = tk.Tk()
root.geometry("600x400")
canvas = tk.Canvas(root, bg="#0a0f1c", highlightthickness=0)
canvas.pack(fill=tk.BOTH, expand=True)
root.update_idletasks()

# PhoneFrame
pf = PhoneFrame(canvas)
pf.draw()

# PillBadge
pill = PillBadge(canvas)
pill.set_text("FPS 28  •  帧 1234  •  1080×1920")
print(f"PillBadge item_bg: {pill._item_bg}")
print(f"PillBadge item_text: {pill._item_text}")
assert pill._item_bg is not None, "PillBadge bg should be drawn"
assert pill._item_text is not None, "PillBadge text should be drawn"

# 切换可见性
pill.set_visible(False)
assert pill._item_bg is None, "Should clear items when hidden"
pill.set_visible(True)
assert pill._item_bg is not None

# 测试长文字
pill.set_text("FPS 30 • 帧 5678 • 1080×1920")
pill.set_text("FPS 30")
pill.set_text("")

root.destroy()
print("PillBadge OK")
