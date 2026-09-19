"""交互式 UI 测试: 点击按钮验证 hover/press 动效"""
import sys, time
sys.path.insert(0, '/home/ben/ohscrcpy')
import tkinter as tk
from gui.theme import Theme
from gui.widgets import ModernButton, Toolbar, PillBadge, FloatingBar

root = tk.Tk()
root.configure(bg=Theme.BG_BASE)
root.geometry("600x400")

clicks = []
def make_cb(name):
    return lambda: (clicks.append(name), print(f"click: {name}"))

# 测试 5 个不同 style 的按钮
for i, (style, label) in enumerate([
    ("primary",   "连接"),
    ("secondary", "刷新"),
    ("danger",    "电源"),
    ("success",   "主页"),
    ("ghost",     "?"),
]):
    btn = ModernButton(
        root, text=label, command=make_cb(label),
        style=style, width=120, height=40,
    )
    btn.pack(pady=4)

# 模拟点击 primary
def click_primary():
    btn = root.winfo_children()[0]
    btn._on_press(None)
    root.update()
    btn._on_release(type("E", (), {"x_root": btn.winfo_rootx()+10,
                                    "y_root": btn.winfo_rooty()+10})())
root.after(100, click_primary)

root.after(200, root.destroy)
root.mainloop()
print(f"Clicks recorded: {clicks}")
assert "连接" in clicks, f"primary button click should register"
print("INTERACTIVE TEST PASS")
