#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
# Licensed under the Apache License, Version 2.0 (the "AS IS")
# 键鼠控制二进制协议 - Python 端编码器
"""
ohscrcpy 键鼠控制协议: Python -> ohscrcpy_server (设备端 Agent).

设计目标:
  - 与文档 "Communication Protocol Design (Binary Control Protocol)" 一致
  - 大端序 (网络字节序), struct.pack 跨语言易解析
  - 低开销: 每条控制消息 <= 16 字节
  - 替代 hdc shell uinput 命令 (避免 0ms down/up 间隔 bug 和 Popen 启动开销)

报文结构 (Type 1 byte + Payload):
  TYPE_TOUCH  (0x01): Action(1) + PointerId(1) + X(4) + Y(4) + DevW(2) + DevH(2) = 14 bytes
  TYPE_KEY    (0x02): Action(1) + Keycode(4) + MetaState(4) = 9 bytes
  TYPE_SCROLL (0x03): HScroll(2 signed) + VScroll(2 signed) = 4 bytes
  TYPE_TEXT   (0x04): Length(2) + UTF8 bytes

OH KeyCode 约定 (与 device_controller.KEY_MAPPINGS 对齐):
  home=1, back=2, volume_up=16, volume_down=17, power=18,
  dpad_up=19, dpad_down=20, dpad_left=21, dpad_right=22, camera=27

使用示例:
  from core.control_protocol import ControlProtocol, TYPE_TOUCH, ACTION_DOWN

  cp = ControlProtocol()
  frame = cp.pack_touch(ACTION_DOWN, 540, 960, dev_w=1080, dev_h=1920)
  sock.sendall(frame)  # 14 字节

注意:
  当前 MJPEG 模式默认仍走 hdc shell uinput 路径 (兼容 ohscrcpy_server 旧版).
  本模块为未来 ohscrcpy_server 二进制升级 (支持控制 socket 端口) 做准备.
  启用方法: 设环境变量 OHCRCPY_USE_CONTROL_PROTOCOL=1, 需配合端侧 ohscrcpy_server
  在 27191 端口监听控制消息.
"""
import struct
from typing import Optional, Tuple


# 消息类型
TYPE_TOUCH = 0x01
TYPE_KEY = 0x02
TYPE_SCROLL = 0x03
TYPE_TEXT = 0x04

# 触摸/按键动作
ACTION_DOWN = 0x00
ACTION_MOVE = 0x01
ACTION_UP = 0x02

# 修饰键位 (与 Linux input event 修饰键对齐)
META_NONE = 0x00000000
META_CTRL = 0x00000001
META_ALT = 0x00000002
META_SHIFT = 0x00000004
META_SUPER = 0x00000008  # Win / macOS Cmd


class ControlProtocol:
    """键鼠控制协议编码器 (Python 端).

    设计原则:
      - pack_* 返回 immutable bytes, 调用方可直接 socket.sendall
      - 不持有状态: 每个方法是纯函数, 易于 mock 测试
      - 大端序 (网络字节序): struct '>'
    """

    # 预编译 struct format, 避免每次 pack 时 parse format string
    # Touch 报文: Type(B) + Action(B) + PointerId(B) + pad(B) + X(I) + Y(I) + DevW(H) + DevH(H) = 16 bytes
    _FMT_TOUCH = ">BBBxIIHH"  # x 在 BBB 后跳过 1 byte pad; 7 format items
    # Key 报文: Type(B) + Action(B) + pad(H) + Keycode(I) + MetaState(I) = 12 bytes
    _FMT_KEY = ">BBHII"
    # Scroll 报文: Type(B) + HScroll(h signed) + VScroll(h signed) = 5 bytes
    _FMT_SCROLL = ">Bhh"
    # Text header: Type(B) + Length(H) = 3 bytes + UTF8 payload
    _FMT_TEXT_HEADER = ">BH"

    def pack_touch(self, action: int, x: int, y: int,
                   pointer_id: int = 0,
                   dev_w: int = 1080, dev_h: int = 1920) -> bytes:
        """打包触摸事件 (TYPE_TOUCH).

        Args:
            action: ACTION_DOWN / ACTION_MOVE / ACTION_UP
            x, y: 设备端绝对坐标 (像素, 已扣除黑边)
            pointer_id: 多指触控 ID (单鼠固定 0)
            dev_w, dev_h: 设备真实物理分辨率
        """
        # 边界裁剪: x/y 必须在设备分辨率内 (防止 uiinput 接受越界值)
        x = max(0, min(dev_w - 1, int(x)))
        y = max(0, min(dev_h - 1, int(y)))
        return struct.pack(self._FMT_TOUCH,
                           TYPE_TOUCH, action,
                           pointer_id & 0xFF,  # PointerId (1 byte)
                           # 1 byte pad 自动 (x in format)
                           x, y,
                           dev_w & 0xFFFF, dev_h & 0xFFFF)

    def pack_key(self, action: int, keycode: int,
                 meta_state: int = META_NONE) -> bytes:
        """打包按键事件 (TYPE_KEY).

        Args:
            action: ACTION_DOWN / ACTION_UP
            keycode: OH KeyCode (1=home, 2=back, 19=dpad_up 等)
            meta_state: 修饰键位 (META_CTRL | META_SHIFT 等)
        """
        return struct.pack(self._FMT_KEY,
                           TYPE_KEY, action,
                           0,  # pad (2 bytes)
                           keycode & 0xFFFFFFFF,
                           meta_state & 0xFFFFFFFF)

    def pack_scroll(self, hscroll: int = 0, vscroll: int = 0) -> bytes:
        """打包滚轮事件 (TYPE_SCROLL).

        Args:
            hscroll: 水平滚动量 (有符号 short)
            vscroll: 垂直滚动量 (有符号 short, 正=向上滚)
        """
        # 边界裁剪到 short 范围
        hscroll = max(-32768, min(32767, int(hscroll)))
        vscroll = max(-32768, min(32767, int(vscroll)))
        return struct.pack(self._FMT_SCROLL, TYPE_SCROLL, hscroll, vscroll)

    def pack_text(self, text: str) -> bytes:
        """打包文本输入事件 (TYPE_TEXT).

        Args:
            text: UTF-8 字符串 (IME 输入用)
        """
        if not text:
            return b""
        encoded = text.encode("utf-8")
        # 长度截断到 16-bit (最大 65535 字节)
        length = min(len(encoded), 0xFFFF)
        return struct.pack(self._FMT_TEXT_HEADER, TYPE_TEXT, length) + encoded[:length]

    # ---- 设备坐标转换 (与文档公式一致) ----

    def tk_to_device_coords(
        self,
        tk_x: int, tk_y: int,
        render_x: int, render_y: int,
        render_w: int, render_h: int,
        dev_w: int, dev_h: int,
    ) -> Optional[Tuple[int, int]]:
        """Tkinter 画布坐标 -> 设备物理坐标, 黑边区域返回 None.

        文档公式:
          X_target = (X_tk - X_0) / W_render * W_dev
          Y_target = (Y_tk - Y_0) / H_render * H_dev
        """
        if render_w <= 0 or render_h <= 0:
            return None
        if not (render_x <= tk_x <= render_x + render_w):
            return None
        if not (render_y <= tk_y <= render_y + render_h):
            return None
        dev_x = int((tk_x - render_x) / render_w * dev_w)
        dev_y = int((tk_y - render_y) / render_h * dev_h)
        return (max(0, min(dev_w - 1, dev_x)),
                max(0, min(dev_h - 1, dev_y)))


# 默认实例 (供 device_controller 复用, 避免重复创建)
_default_protocol = ControlProtocol()


def get_protocol() -> ControlProtocol:
    """获取默认协议实例."""
    return _default_protocol


__all__ = [
    "ControlProtocol",
    "get_protocol",
    "TYPE_TOUCH",
    "TYPE_KEY",
    "TYPE_SCROLL",
    "TYPE_TEXT",
    "ACTION_DOWN",
    "ACTION_MOVE",
    "ACTION_UP",
    "META_NONE",
    "META_CTRL",
    "META_ALT",
    "META_SHIFT",
    "META_SUPER",
]
