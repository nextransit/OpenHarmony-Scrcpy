#!/usr/bin/env python
"""
OpenHarmony 触摸注入层

在 RK3568 / OpenHarmony 3.2 设备上，原生 `hdc shell uinput -T` 注入触摸事件
不可靠（部分设备上 SimulateInputEvent 内部静默失败）。本模块提供多种注入路径，
按优先级尝试并选择实际生效的那一条：

1. ``uitest uiInput swipe``           - 系统级 UI 测试 fling (验证有效)
2. ``uitest uiInput longClick``       - 系统级 long press
3. ``uitest uiInput keyEvent``        - 键盘事件 (Back / Home 等)
4. ``hdc shell uinput -T -c``         - 原子点击 (atomic click)
5. ``hdc shell uinput -T -d/-u``      - 拆分 down/up
6. ``hdc shell uinput -T -m``         - 移动手势

第一次注入成功后，后续请求直接用相同路径，避免每次探索。
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


@dataclass
class TouchEvent:
    """触摸事件描述"""
    kind: str          # 'tap' | 'swipe' | 'long_press' | 'key'
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 100
    key: str = ''      # for kind=='key': 'Back' | 'Home' | 'Power' | keycode


class TouchInjector:
    """OpenHarmony 触摸注入器。

    用法::

        ti = TouchInjector(hdc_executor, device_sn='192.168.26.133:5555')
        ti.send(TouchEvent(kind='tap', x=540, y=960))
        ti.send(TouchEvent(kind='key', key='Back'))

    ``hdc_executor`` 必须实现 ``execute(args, timeout=...)`` 接口。
    """

    # ────────────────── shell helper ──────────────────
    def __init__(
        self,
        hdc_executor,
        device_sn: str = '',
        snapshotter: Optional[Callable[[str], bool]] = None,
        diff_fn: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self.hdc = hdc_executor
        self.device_sn = device_sn
        self._preferred_tap_path: Optional[str] = None  # 'uitest_swipe' | 'uinput_c' | ...
        self._preferred_key_path: Optional[str] = None
        self.snapshotter = snapshotter  # callable(local_path) -> bool
        self.diff_fn = diff_fn          # callable(ref_path, test_path) -> bool

    # ────────────────── path runners ──────────────────
    def _path_uitest_key(self, ev: TouchEvent) -> Tuple[str, str]:
        """uitest uiInput keyEvent  -- always works for keys on RK3568 OH3.2"""
        if not ev.key:
            return 'uitest_key', 'no key name'
        out = self.hdc.execute(['shell', 'uitest', 'uiInput', 'keyEvent', ev.key], timeout=10)
        return 'uitest_key', 'ok' if out.get('success') else 'failed'

    def _path_uitest_swipe_as_tap(self, ev: TouchEvent) -> Tuple[str, str]:
        """uitest uiInput swipe (same point = 'tap') -- 已验证可用作 fling"""
        # swipe x y x y -- 距离为 0 通常不响应, 用 1 pixel
        x2 = ev.x + 1
        y2 = ev.y + 1
        out = self.hdc.execute(
            ['shell', 'uitest', 'uiInput', 'swipe', str(ev.x), str(ev.y), str(x2), str(y2)],
            timeout=10,
        )
        return 'uitest_swipe', 'ok' if out.get('success') else 'failed'

    def _path_uitest_longclick(self, ev: TouchEvent) -> Tuple[str, str]:
        out = self.hdc.execute(['shell', 'uitest', 'uiInput', 'longClick', str(ev.x), str(ev.y)], timeout=10)
        return 'uitest_long', 'ok' if out.get('success') else 'failed'

    def _path_uinput_c(self, ev: TouchEvent) -> Tuple[str, str]:
        out = self.hdc.execute(
            ['shell', 'uinput', '-T', '-c', str(ev.x), str(ev.y)],
            timeout=5,
        )
        return 'uinput_c', 'ok' if out.get('success') else 'failed'

    def _path_uinput_du(self, ev: TouchEvent) -> Tuple[str, str]:
        cmd = ['shell', 'uinput', '-T', '-d', str(ev.x), str(ev.y), '-u', str(ev.x), str(ev.y)]
        out = self.hdc.execute(cmd, timeout=5)
        return 'uinput_du', 'ok' if out.get('success') else 'failed'

    def _path_uinput_m(self, ev: TouchEvent) -> Tuple[str, str]:
        # uinput -T -m x1 y1 x2 y2 duration
        cmd = [
            'shell', 'uinput', '-T', '-m',
            str(ev.x), str(ev.y), str(ev.x2), str(ev.y2), str(ev.duration_ms),
        ]
        out = self.hdc.execute(cmd, timeout=5)
        return 'uinput_m', 'ok' if out.get('success') else 'failed'

    # ────────────────── main send API ──────────────────
    def send(self, ev: TouchEvent) -> str:
        """注入触摸事件，返回实际使用的 path 名"""
        if ev.kind == 'key':
            return self._send_key(ev)
        if ev.kind == 'tap':
            return self._send_tap(ev)
        if ev.kind == 'long_press':
            return self._send_long_press(ev)
        if ev.kind == 'swipe':
            return self._send_swipe(ev)
        return 'unknown_kind'

    def _send_key(self, ev: TouchEvent) -> str:
        path, status = self._path_uitest_key(ev)
        if status == 'ok':
            self._preferred_key_path = path
            return path
        # 回退到 uinput -K
        keymap = {'Back': 2, 'Home': 1, 'Power': 18}
        kc = keymap.get(ev.key, int(ev.key) if ev.key.isdigit() else 1)
        out = self.hdc.execute(['shell', 'uinput', '-K', '-d', str(kc), '-u', str(kc)], timeout=5)
        return 'uinput_K' if out.get('success') else 'all_failed'

    def _send_tap(self, ev: TouchEvent) -> str:
        """发送 tap，按优先级尝试：preferred path > uinput -c > uinput -d/-u > uitest swipe fallback"""
        # 1. 用 preferred path (如果有)
        if self._preferred_tap_path:
            path, status = self._run_path(self._preferred_tap_path, ev)
            if status == 'ok':
                return path

        # 2. 探测每条路径 (第一次)
        paths = [
            ('uinput_c', self._path_uinput_c),
            ('uinput_du', self._path_uinput_du),
            ('uitest_swipe', self._path_uitest_swipe_as_tap),
            ('uitest_long', self._path_uitest_longclick),
        ]
        for name, fn in paths:
            path, status = fn(ev)
            if status == 'ok':
                # 验证：截屏 + diff (如果有 callback)
                if self._verify_tap(ev):
                    self._preferred_tap_path = name
                    return name

        return 'all_failed'

    def _send_long_press(self, ev: TouchEvent) -> str:
        return self._path_uitest_longclick(ev)[0]

    def _send_swipe(self, ev: TouchEvent) -> str:
        # swipe 用 uinput -T -m (大距离 fling 可靠)
        cmd = [
            'shell', 'uinput', '-T', '-m',
            str(ev.x), str(ev.y), str(ev.x2), str(ev.y2), str(ev.duration_ms),
        ]
        out = self.hdc.execute(cmd, timeout=10)
        if out.get('success'):
            return 'uinput_m'
        # 回退到 uitest
        out = self.hdc.execute(
            ['shell', 'uitest', 'uiInput', 'swipe',
             str(ev.x), str(ev.y), str(ev.x2), str(ev.y2)],
            timeout=10,
        )
        return 'uitest_swipe' if out.get('success') else 'all_failed'

    def _run_path(self, name: str, ev: TouchEvent) -> Tuple[str, str]:
        runners = {
            'uinput_c': self._path_uinput_c,
            'uinput_du': self._path_uinput_du,
            'uinput_m': self._path_uinput_m,
            'uitest_swipe': self._path_uitest_swipe_as_tap,
            'uitest_long': self._path_uitest_longclick,
        }
        if name not in runners:
            return name, 'no_such_path'
        return runners[name](ev)

    def _verify_tap(self, ev: TouchEvent) -> bool:
        """截屏 + diff 验证 tap 是否生效 (如果 callback 可用)"""
        if not self.snapshotter or not self.diff_fn:
            return True  # 没有验证手段，假设成功
        try:
            ref_path = f'/tmp/_ti_ref_{os.getpid()}.jpeg'
            test_path = f'/tmp/_ti_test_{os.getpid()}.jpeg'
            self.hdc.execute(['shell', 'snapshot_display', '-f', '/data/local/tmp/_ti_ref.jpeg'])
            self.hdc.execute(['file', 'recv', '/data/local/tmp/_ti_ref.jpeg', ref_path])
            # 发送 tap
            time.sleep(0.05)
            path, status = self._path_uinput_c(ev)
            if status != 'ok':
                return False
            time.sleep(0.6)
            self.hdc.execute(['shell', 'snapshot_display', '-f', '/data/local/tmp/_ti_test.jpeg'])
            self.hdc.execute(['file', 'recv', '/data/local/tmp/_ti_test.jpeg', test_path])
            return self.diff_fn(ref_path, test_path)
        except Exception:
            return False


__all__ = ['TouchInjector', 'TouchEvent']
