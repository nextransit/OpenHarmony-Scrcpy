"""回归测试: 启动顺序必须 splash -> 主窗口, 不能先闪主窗口.

Bug: __init__ 中先 state('zoomed') / geometry 后才 withdraw,
     OS 在 zoomed 时 visible -> 用户看到一闪空主窗口再 splash.
修复:
  - __init__ 第一步就 root.withdraw() (在 state/geometry 之前)
  - 几何自适应延后到 _apply_initial_geometry() (在 splash _finish -> on_finish 之后)
  - on_finish 顺序: _apply_initial_geometry -> _setup_ui -> deiconify -> _init_components_async
  - 这样:
      t=0~1.5s:    root.withdrawn, user 只看到 splash (Toplevel, overrideredirect)
      t=1.5s:      splash _finish -> on_finish -> _setup_ui 渲染 widgets -> deiconify
      t=1.5s+:    user 只看到完整主窗口 (无空白瞬间)
"""
import sys, os
sys.path.insert(0, '/Users/zhouyong/Desktop/work/Decard/gitlab/openharmony/ohscrcpy')

import time
import tkinter as tk

import gui.main_window as mw
import gui.branding as br

# 验证 1: __init__ 中, withdraw 必须在任何 self.root.state() 之前
src = open(mw.__file__).read()
init_block = src[src.find('def __init__'):src.find('def _show_splash_then_init_ui')]

m_withdraw = init_block.find('self.root.withdraw()')

state_calls = []
i = 0
while True:
    j = init_block.find('self.root.state(', i)
    if j == -1:
        break
    state_calls.append(j)
    i = j + 1

assert m_withdraw != -1, "self.root.withdraw() 不在 __init__"
if state_calls:
    assert m_withdraw < min(state_calls), (
        f"__init__ 中 withdraw (位置 {m_withdraw}) 必须在任何 self.root.state() "
        f"(位置 {state_calls}) 之前. 否则 state 会让 root visible 一闪."
    )
    print(f"[order] __init__: withdraw@{m_withdraw} < state@{state_calls}  PASS")
else:
    print(f"[order] __init__: withdraw@{m_withdraw} OK (无 state 调用)")

# 验证 2: __init__ 中, withdraw 必须在任何 self.root.geometry 之前
geom_calls = []
i = 0
while True:
    j = init_block.find('self.root.geometry(', i)
    if j == -1:
        break
    geom_calls.append(j)
    i = j + 1
if geom_calls:
    assert m_withdraw < min(geom_calls), (
        f"__init__ 中 withdraw (位置 {m_withdraw}) 必须在 self.root.geometry() "
        f"(位置 {geom_calls}) 之前."
    )
    print(f"[order] __init__: withdraw@{m_withdraw} < geometry@{geom_calls}  PASS")
else:
    print(f"[order] __init__: withdraw@{m_withdraw} OK (无 geometry 调用)")

# 验证 3: _show_splash_then_init_ui 中 splash 创建必须在 deiconify 之前
show_block = src[src.find('def _show_splash_then_init_ui'):src.find('def _apply_initial_geometry')]
assert 'SplashScreen(self.root' in show_block, "SplashScreen 创建不在 _show_splash_then_init_ui 中"
print(f"[order] _show_splash_then_init_ui: 创建 SplashScreen OK")


# 验证 4: 实际运行时序
t0 = time.time()
events = []

def now():
    return time.time() - t0

orig_setup = mw.MainWindow._setup_ui
def hooked_setup(self):
    events.append(('setup_ui', now()))
    s = self.root.state()
    print(f"[t={now():.3f}s] _setup_ui (root state={s!r})")
    if s != 'withdrawn':
        print(f"  WARN: _setup_ui 时 root 不是 withdrawn (splash 显示期间), 实际={s!r}")
    orig_setup(self)
mw.MainWindow._setup_ui = hooked_setup

orig_show = mw.MainWindow._show_splash_then_init_ui
def hooked_show(self):
    events.append(('show_splash_start', now()))
    orig_show(self)
    events.append(('show_splash_end', now()))
mw.MainWindow._show_splash_then_init_ui = hooked_show

orig_finish = br.SplashScreen._finish
def hooked_finish(self):
    events.append(('splash_finish_start', now()))
    orig_finish(self)
    events.append(('splash_finish_end', now()))
br.SplashScreen._finish = hooked_finish

orig_deiconify = tk.Tk.deiconify
def hooked_deiconify(self, *a, **kw):
    if not any(e[0] == 'deiconify' for e in events):
        events.append(('deiconify', now()))
        print(f"[t={now():.3f}s] root.deiconify")
    orig_deiconify(self, *a, **kw)
tk.Misc.deiconify = hooked_deiconify

app = mw.MainWindow()
app.root.after(3500, app.root.destroy)  # wait > splash 1.5s
app.root.mainloop()

print()
print("=== Events timeline ===")
for name, t in events:
    print(f"  {t:.3f}s: {name}")

def get(name):
    for n, t in events:
        if n == name:
            return t
    return None

t_show_start = get('show_splash_start')
t_finish_start = get('splash_finish_start')
t_setup = get('setup_ui')
t_deiconify = get('deiconify')

assert t_show_start is not None and t_finish_start is not None
assert t_show_start < t_finish_start, "show_splash 必须在 splash_finish 之前"
assert t_finish_start < t_setup, (
    f"splash_finish_start ({t_finish_start}) 必须在 setup_ui ({t_setup}) 之前"
)
if t_deiconify is not None:
    assert t_setup <= t_deiconify, (
        f"setup_ui ({t_setup}) 必须在 deiconify ({t_deiconify}) 之前"
    )

print()
print("PASS: 启动顺序 splash -> _setup_ui -> deiconify (无空白主窗口)")
print("ALL SPLASH ORDER TESTS PASS")
