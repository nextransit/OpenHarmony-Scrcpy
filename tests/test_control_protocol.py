"""单元测试: 键鼠控制二进制协议编码器.

覆盖文档第四节定义的所有报文类型和边界条件:
  - TYPE_TOUCH: 触摸事件 (14-16 bytes)
  - TYPE_KEY: 按键事件 (10-12 bytes)
  - TYPE_SCROLL: 滚轮事件 (5 bytes)
  - TYPE_TEXT: 文本输入
  - tk_to_device_coords: 黑边坐标转换 (文档公式)
"""
import struct
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.control_protocol import (
    ControlProtocol,
    TYPE_TOUCH, TYPE_KEY, TYPE_SCROLL, TYPE_TEXT,
    ACTION_DOWN, ACTION_MOVE, ACTION_UP,
    META_NONE, META_CTRL, META_SHIFT, META_ALT,
)


def test_touch_packet_format():
    """touch 报文: Type(0x01) + Action + PointerId + pad + X(I) + Y(I) + DevW(H) + DevH(H)"""
    cp = ControlProtocol()
    pkt = cp.pack_touch(ACTION_DOWN, 540, 960, dev_w=1080, dev_h=1920)
    assert len(pkt) == 16, f"expected 16 bytes, got {len(pkt)}"
    # 第 0 byte: Type
    assert pkt[0] == TYPE_TOUCH, f"Type != TYPE_TOUCH: {pkt[0]}"
    # 第 1 byte: Action
    assert pkt[1] == ACTION_DOWN, f"Action: {pkt[1]}"
    # 第 2-3 byte: PointerId + pad
    # 第 4-7 byte: X (big-endian unsigned int) = 540
    assert struct.unpack(">I", pkt[4:8])[0] == 540, f"X: {struct.unpack('>I', pkt[4:8])[0]}"
    # 第 8-11 byte: Y = 960
    assert struct.unpack(">I", pkt[8:12])[0] == 960
    # 第 12-13: DevW = 1080
    assert struct.unpack(">H", pkt[12:14])[0] == 1080
    # 第 14-15: DevH = 1920
    assert struct.unpack(">H", pkt[14:16])[0] == 1920
    print(f"[touch] ACTION_DOWN (540,960) -> 16B binary PASS")


def test_touch_boundary_clamp():
    """touch 报文边界裁剪: x/y 超过 dev_w/dev_h 时夹紧."""
    cp = ControlProtocol()
    pkt = cp.pack_touch(ACTION_MOVE, 99999, -5, dev_w=1080, dev_h=1920)
    # X 应该是 1079 (max), Y 应该是 0 (min)
    x = struct.unpack(">I", pkt[4:8])[0]
    y = struct.unpack(">I", pkt[8:12])[0]
    assert x == 1079, f"X 未夹紧到 1079, got {x}"
    assert y == 0, f"Y 未夹紧到 0, got {y}"
    print(f"[touch] 边界裁剪 (99999,-5) -> (1079,0) PASS")


def test_key_packet_format():
    """key 报文: Type(0x02) + Action + pad + Keycode(I) + MetaState(I)."""
    cp = ControlProtocol()
    pkt = cp.pack_key(ACTION_UP, 19, META_CTRL | META_SHIFT)
    assert len(pkt) == 12, f"expected 12 bytes, got {len(pkt)}"
    assert pkt[0] == TYPE_KEY
    assert pkt[1] == ACTION_UP
    # Keycode 在 offset 4-7 (Type+Action+pad(2) = 4 bytes)
    assert struct.unpack(">I", pkt[4:8])[0] == 19, f"keycode: {struct.unpack('>I', pkt[4:8])[0]}"
    # MetaState 在 offset 8-11
    assert struct.unpack(">I", pkt[8:12])[0] == (META_CTRL | META_SHIFT)
    print(f"[key] ACTION_UP keycode=19 meta=CTRL|SHIFT PASS")


def test_scroll_packet_format():
    """scroll 报文: Type(0x03) + HScroll(h signed) + VScroll(h signed)."""
    cp = ControlProtocol()
    # macOS 向上滚 = delta > 0, 我们映射为 vscroll=1
    pkt = cp.pack_scroll(vscroll=1)
    assert len(pkt) == 5, f"expected 5 bytes, got {len(pkt)}"
    assert pkt[0] == TYPE_SCROLL
    # 第 1-2: HScroll (signed short) = 0
    assert struct.unpack(">h", pkt[1:3])[0] == 0
    # 第 3-4: VScroll (signed short) = 1
    assert struct.unpack(">h", pkt[3:5])[0] == 1
    print(f"[scroll] vscroll=+1 PASS")


def test_scroll_boundary_clamp():
    """scroll 边界裁剪: 超出 short 范围夹紧."""
    cp = ControlProtocol()
    pkt = cp.pack_scroll(hscroll=99999, vscroll=-99999)
    h = struct.unpack(">h", pkt[1:3])[0]
    v = struct.unpack(">h", pkt[3:5])[0]
    assert h == 32767, f"H 未夹紧: {h}"
    assert v == -32768, f"V 未夹紧: {v}"
    print(f"[scroll] 边界裁剪 (99999,-99999) -> (32767,-32768) PASS")


def test_text_packet():
    """text 报文: Type(0x04) + Length(H) + UTF8 bytes."""
    cp = ControlProtocol()
    pkt = cp.pack_text("hi")
    assert pkt[0] == TYPE_TEXT
    assert struct.unpack(">H", pkt[1:3])[0] == 2, f"length: {pkt[1:3]}"
    assert pkt[3:] == b"hi"
    print(f"[text] 'hi' -> 5B PASS")


def test_text_cjk():
    """text 报文: CJK 字符按 UTF-8 编码."""
    cp = ControlProtocol()
    pkt = cp.pack_text("你好")
    assert pkt[0] == TYPE_TEXT
    assert pkt[3:].decode("utf-8") == "你好"
    print(f"[text] CJK '你好' -> {len(pkt)}B UTF-8 PASS")


def test_tk_to_device_coords_basic():
    """文档公式: X_target = (X_tk - X_0) / W_render * W_dev."""
    cp = ControlProtocol()
    # 渲染区域: (0,0,540,960), 设备: 1080x1920
    # 100,200 应映射到 200,400
    result = cp.tk_to_device_coords(100, 200, 0, 0, 540, 960, 1080, 1920)
    assert result == (200, 400), f"got {result}"
    print(f"[coords] (100,200) in (0,0,540,960) -> (200,400) PASS")


def test_tk_to_device_coords_with_letterbox():
    """带黑边 (letterbox) 坐标转换."""
    cp = ControlProtocol()
    # 渲染区域: (100,200,300,600) - 上下各有黑边
    # 画布坐标 (250, 500) 是渲染区中心, 映射到设备中心 (540, 960)
    result = cp.tk_to_device_coords(250, 500, 100, 200, 300, 600, 1080, 1920)
    # (250-100)/300*1080 = 540
    # (500-200)/600*1920 = 960
    assert result == (540, 960), f"got {result}"
    print(f"[coords] (250,500) in (100,200,300,600) letterbox -> (540,960) PASS")


def test_tk_to_device_coords_out_of_bounds():
    """黑边区域点击返回 None (不向设备发送)."""
    cp = ControlProtocol()
    # 画布坐标 50 在黑边 (渲染区从 100 开始)
    result = cp.tk_to_device_coords(50, 500, 100, 200, 300, 600, 1080, 1920)
    assert result is None, f"黑边区域应返回 None, got {result}"
    # x 超过渲染区右边界
    result = cp.tk_to_device_coords(450, 500, 100, 200, 300, 600, 1080, 1920)
    assert result is None
    print(f"[coords] 黑边点击 (50,500) -> None PASS")


def test_default_protocol_singleton():
    """get_protocol() 返回单例."""
    from core.control_protocol import get_protocol
    p1 = get_protocol()
    p2 = get_protocol()
    assert p1 is p2
    print(f"[singleton] get_protocol() 返回同一实例 PASS")


if __name__ == "__main__":
    tests = [
        test_touch_packet_format,
        test_touch_boundary_clamp,
        test_key_packet_format,
        test_scroll_packet_format,
        test_scroll_boundary_clamp,
        test_text_packet,
        test_text_cjk,
        test_tk_to_device_coords_basic,
        test_tk_to_device_coords_with_letterbox,
        test_tk_to_device_coords_out_of_bounds,
        test_default_protocol_singleton,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"[ERR ] {t.__name__}: {e!r}")
    print()
    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} tests failed")
        sys.exit(1)
    print(f"PASS: {len(tests)}/{len(tests)} protocol tests")
