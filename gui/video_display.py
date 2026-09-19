#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
视频显示器 - 负责视频帧的渲染和显示管理
"""

import time
import gc
import threading
import queue as _queue
import tkinter as tk
from typing import Optional, Tuple

from PIL import Image  # 模块顶层, 避免 _do_render 内 import 抛 Image is None
import io

# 标记 Tk 主线程 ID
_MAIN_TID = threading.get_ident()

from gui.device_controller import DeviceController
from gui.theme import Theme
from gui.widgets import PillBadge, PhoneFrame
from core import HEARTBEAT_TIMEOUT, LogLevel, print_log


class VideoDisplay:
    """视频显示器"""
    
    def __init__(
        self,
        root: tk.Tk,
        canvas: tk.Canvas,
        device_controller: DeviceController,
        performance_label: tk.Label,
        connection_manager=None,
    ) -> None:
        self.root = root
        self.canvas = canvas
        self.device_controller = device_controller
        self.performance_label = performance_label
        self.connection_manager = connection_manager
        # 新增:PillBadge (左上角半透明状态) + PhoneFrame (外壳)
        self.pill_badge = PillBadge(canvas)
        self.phone_frame = PhoneFrame(canvas)
        self._osd_visible = True
        self._device_aspect: Tuple[int, int] = (9, 16)  # 默认竖屏 9:16, 连接后更新
        
        self.video_width = 0
        self.video_height = 0
        self.video_ratio = 0.0
        self.display_width = 0
        self.display_height = 0
        
        self.fps = 0
        self.frame_counter = 0
        self.last_fps_time = time.time()
        self.displayed_frames = 0
        self.last_print_frames = 0
        
        self.last_gc_frame_count = 0
        self.gc_interval_frames = 500
        self.image_refs = []
        
        self.current_frame = None
        self.tk_image = None
        self.status_text_id = None
        self.running_status_text_id = None
        # 新增:image item id (避免每次 delete+create_image, 改用 itemconfig)
        self.image_item_id = None
        # 新增:最近一帧的设备像素数据 (避免 on_frame_decoded -> 渲染 直接冲突)
        self.pending_frame = None
        self._last_rendered_frame_id = None  # 用 id(frame) 判重复
        
        self._render_scheduled = False
        self.last_display_time = 0.0
        self._frame_seq = 0
        # 跨线程渲染信号队列: 非主线程 on_frame_decoded push, 主线程 _poll_render_queue 消费.
        self._render_signal: "queue.Queue" = _queue.Queue(maxsize=4)
        # 主线程 poll 由 main_window 在 _setup_ui 后启动; 这里只标记
        
        self.log_title = "视频显示器"
        
        # 新增: 监听 canvas resize 强制重算 video display resolution
        self.last_canvas_size: Tuple[int, int] = (0, 0)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        # 新增:HEVC 编码器异常提示状态
        self.bad_frame_streak = 0
        self.last_bad_ratio = 0.0
        self.error_overlay_id = None
        self.error_overlay_text_id = None
        self.error_overlay_shown_at = 0.0
    
    def _on_canvas_configure(self, event):
        '''Canvas 尺寸变化 (窗口 resize) → 立即重算 display_resolution'''
        try:
            canvas_w = self.canvas.winfo_width()
            canvas_h = self.canvas.winfo_height()
            if canvas_w <= 10 or canvas_h <= 10:
                return
            # 主动调 set_display_resolution
            if self.video_width > 0 and self.video_height > 0:
                self.display_width, self.display_height, self.video_ratio = (
                    self.device_controller.set_display_resolution(
                        self.video_width, self.video_height, canvas_w, canvas_h
                    )
                )
                print_log(LogLevel.INFO, self.log_title, f"canvas resize -> {canvas_w}x{canvas_h} -> display {self.display_width}x{self.display_height}")
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"canvas configure error: {e}")

    def on_frame_decoded(self, frame) -> None:
        """帧解码回调"""
        import threading as _thr
        if not hasattr(self, '_ofd_count'):
            self._ofd_count = 0
        self._ofd_count += 1
        if self._ofd_count <= 3 or self._ofd_count % 30 == 0:
            print_log(LogLevel.INFO, self.log_title,
                f"on_frame_decoded #{self._ofd_count} tid={_thr.get_ident()} "
                f"frame={'None' if frame is None else frame.shape}")
        if frame is None:
            # 解码器检测到异常帧：累加 streak,触发可见的提示
            self.bad_frame_streak += 1
            # 由 stream_client 的 _decode_thread_func 设置 last_bad_ratio
            try:
                vc = self.connection_manager.get_video_client() if self.connection_manager else None
                if vc and vc.decoder:
                    self.last_bad_ratio = getattr(vc.decoder, "last_bad_frame_ratio", 0.0) or self.last_bad_ratio
            except Exception:
                pass
            return
        # 正常帧：重置计数（保留 1 个连续段以避免反复 toggle）
        if self.bad_frame_streak > 0:
            self.bad_frame_streak = 0
            self._hide_error_overlay()
        
        self.current_frame = frame
        self.pending_frame = frame
        self.frame_counter += 1


        current_time = time.time()
        if current_time - self.last_fps_time >= 1.0:
            self.fps = self.frame_counter
            from core import LogLevel as _LL
            from core.logger import print_log as _pl
            _pl(_LL.INFO, self.log_title,
                f"FPS 实测: {self.fps} fps (1秒内 {self.frame_counter} 帧)")
            self.frame_counter = 0
            self.last_fps_time = current_time

        # 触发 render (线程安全).
        self._schedule_render_if_needed()
    
    def _show_error_overlay(self, message: str) -> None:
        """在画布上覆盖一个错误提示层（不影响底层数据流）。"""
        try:
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            if canvas_width <= 10 or canvas_height <= 10:
                canvas_width, canvas_height = 800, 600
            if self.error_overlay_id is None:
                self.error_overlay_id = self.canvas.create_rectangle(
                    0, 0, canvas_width, canvas_height,
                    fill="#000000", stipple="gray50", outline="")
            if self.error_overlay_text_id is None:
                self.error_overlay_text_id = self.canvas.create_text(
                    canvas_width // 2, canvas_height // 2,
                    text=message,
                    fill="#ff5252",
                    font=("Microsoft YaHei", 13, "bold"),
                    width=canvas_width - 80,
                    justify="center",
                )
            else:
                self.canvas.itemconfig(self.error_overlay_text_id, text=message)
            self.error_overlay_shown_at = time.time()
        except Exception as e:
            print_log(LogLevel.WARN, self.log_title, f"显示错误覆盖层失败: {e}")
    
    def _hide_error_overlay(self) -> None:
        try:
            if self.error_overlay_id is not None:
                self.canvas.delete(self.error_overlay_id)
                self.error_overlay_id = None
            if self.error_overlay_text_id is not None:
                self.canvas.delete(self.error_overlay_text_id)
                self.error_overlay_text_id = None
        except Exception:
            pass
    
    def _schedule_render_if_needed(self) -> None:
        """仅当 self._render_scheduled=False 时调度一次。

        主入口: on_frame_decoded 把帧写到 pending_frame, 然后调度一次。
        _do_render 自身不会再自我递归 (避免解码回调与渲染循环相互抢占)。

        线程安全: on_frame_decoded 可能被 mjpeg_client.poll 线程调用
        (非主线程), Tk 的 root.after() 在子线程调用会抛
        "main thread is not in main loop" 异常. 这里用两种调度:
          - 主线程: 直接 root.after(0, ...) -> _do_render
          - 子线程: push signal 到 _render_signal queue,
                    主线程 _poll_render_queue 负责消费并调 _do_render.
        """
        if self._render_scheduled:
            return
        self._render_scheduled = True
        if threading.get_ident() == _MAIN_TID:
            # 主线程: 直接 after
            try:
                self.root.after(0, self._do_render)
                return
            except Exception:
                pass
        # 非主线程 (或 after 失败): 用 queue 让主线程 poll 拉走
        try:
            self._render_signal.put_nowait(1)
        except _queue.Full:
            pass  # 已有信号在排队, 不重复

    def start_main_poll(self) -> None:
        """在主线程启动 poll 循环, 消费非主线程 push 的渲染信号.

        由 main_window 在 _setup_ui 后调一次. 必须在主线程执行!
        """
        if getattr(self, '_main_poll_started', False):
            return
        self._main_poll_started = True
        self.root.after(8, self._poll_render_queue)

    def _poll_render_queue(self) -> None:
        """主线程 poll 渲染信号队列, 触发 _do_render."""
        import threading as _thr
        if not hasattr(self, '_prq_count'):
            self._prq_count = 0
        self._prq_count += 1
        if self._prq_count <= 3 or self._prq_count % 200 == 0:
            print_log(LogLevel.INFO, self.log_title,
                f"_poll_render_queue #{self._prq_count} tid={_thr.get_ident()} "
                f"pending={self.pending_frame is not None}")
        try:
            while True:
                self._render_signal.get_nowait()
                if self._render_scheduled:
                    self._do_render()
                break
        except _queue.Empty:
            pass
        except Exception as e:
            if self.connection_manager and self.connection_manager.is_connected:
                print_log(LogLevel.WARN, self.log_title, f"poll_render_queue err: {e}")
        self.root.after(8, self._poll_render_queue)

    def _do_render(self) -> None:
        self._render_scheduled = False
        current_time = time.time()
        # 诊断: 每 100 次打印一次, 看是否真的跑到这里
        import threading as _thr
        if not hasattr(self, '_render_call_count'):
            self._render_call_count = 0
        self._render_call_count += 1
        if self._render_call_count <= 3 or self._render_call_count % 50 == 0:
            print_log(LogLevel.INFO, self.log_title,
                f"_do_render #{self._render_call_count} tid={_thr.get_ident()} "
                f"pending={self.pending_frame is not None} "
                f"conn_mgr={self.connection_manager is not None}")

        # 连接断开时降低调度频率
        if not self.connection_manager or not self.connection_manager.is_connected:
            self.root.after(100, self._schedule_render_if_needed)
            return

        # 心跳超时保护
        try:
            video_client = self.connection_manager.get_video_client()
            if video_client and hasattr(video_client, "last_data_time"):
                if current_time - video_client.last_data_time > HEARTBEAT_TIMEOUT:
                    print_log(LogLevel.WARN, self.log_title,
                              f"检测到心跳超时 ({current_time - video_client.last_data_time:.1f}秒),断开连接")
                    return
        except Exception:
            video_client = None

        # ---- 取最新帧: 优先 pending_frame (刚解码完的最鲜) ----
        frame = self.pending_frame if self.pending_frame is not None else self.current_frame
        if frame is None:
            # 还没拿到第一帧,继续等
            self.root.after(33, self._schedule_render_if_needed)
            return

        # 同一个 numpy 数组对象去重 (id 一致表示还没换新帧)
        if id(frame) == self._last_rendered_frame_id:
            self.root.after(16, self._schedule_render_if_needed)
            return
        self._last_rendered_frame_id = id(frame)
        self.current_frame = frame


        try:
            # 1) canvas 尺寸 (只读一次)
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            if canvas_width <= 10: canvas_width = 800
            if canvas_height <= 10: canvas_height = 600

            # 2) 首次拿到帧时, 主动同步 device_controller 的显示参数.
            #    关键: 不要用 frame.shape 作为设备尺寸!
            #    MJPEG 模式 draft 后 frame 是 540x960 (1/2), resize 后是 417x743,
            #    都不是真设备尺寸. 用 video_client.config (真设备尺寸).
            target_vw, target_vh = 0, 0
            vc = self.connection_manager.get_video_client() if self.connection_manager else None
            if vc:
                cfg = getattr(vc, "config", None)
                if cfg:
                    target_vw = int(getattr(cfg, "width", 0) or 0)
                    target_vh = int(getattr(cfg, "height", 0) or 0)
            if target_vw <= 0 or target_vh <= 0:
                target_vw, target_vh = int(frame.shape[1]), int(frame.shape[0])

            if self.video_width != target_vw or self.video_height != target_vh:
                self.video_width = target_vw
                self.video_height = target_vh
                if self.device_controller:
                    self.display_width, self.display_height, self.video_ratio = (
                        self.device_controller.set_display_resolution(
                            target_vw, target_vh, canvas_width, canvas_height
                        )
                    )
                print_log(LogLevel.INFO, self.log_title,
                          f"原始视频尺寸: {target_vw}x{target_vh} -> display {self.display_width}x{self.display_height}")
                # 首次拿到帧时也 push 一次 target_size
                if vc and hasattr(vc, "set_target_size"):
                    try:
                        vc.set_target_size(self.display_width, self.display_height)
                    except Exception:
                        pass
                self._last_canvas_size = (canvas_width, canvas_height)
                # 首次拿到帧时也 push 一次 target_size
                vc = self.connection_manager.get_video_client() if self.connection_manager else None
                if vc and hasattr(vc, "set_target_size"):
                    try:
                        vc.set_target_size(self.display_width, self.display_height)
                    except Exception:
                        pass

            # 3) canvas resize 触发重算 (避免 Configure 事件丢失)
            if (canvas_width, canvas_height) != getattr(self, "_last_canvas_size", (0, 0)):
                if canvas_width > 10 and canvas_height > 10 and self.device_controller:
                    self.display_width, self.display_height, self.video_ratio = (
                        self.device_controller.set_display_resolution(
                            self.video_width, self.video_height, canvas_width, canvas_height
                        )
                    )
                    # 把目标尺寸推送给 video_client (MJPEG 链路用此在 poll 线程
                    # 直接 resize 到目标尺寸, 主线程 PhotoImage 只需 ~2ms -> FPS 大幅提升)
                    vc = self.connection_manager.get_video_client() if self.connection_manager else None
                    if vc and hasattr(vc, "set_target_size"):
                        try:
                            vc.set_target_size(self.display_width, self.display_height)
                        except Exception:
                            pass
                self._last_canvas_size = (canvas_width, canvas_height)

            if self.display_width <= 0 or self.display_height <= 0:
                self.root.after(33, self._schedule_render_if_needed)
                return

            # 4) 创建 PhotoImage
            # 优化: MJPEG 链路下, frame 已经是目标显示尺寸 (mjpeg_client.poll 线程里
            # resize 完毕). 主线程这里只做 fromarray + PhotoImage, 实测 ~2ms.
            # HEVC 链路下 frame 仍是原始 1080x1920, 需要在这里 resize (不可避免).
            pil_img = Image.fromarray(frame)
            if (pil_img.width, pil_img.height) != (self.display_width, self.display_height):
                # 缩放算法: BOX (质量+速度平衡). BILINEAR/HAMMING/BOX/NEAREST 区别:
                #   LANCZOS  ~50ms (最慢, 画质最好)
                #   BILINEAR ~20ms
                #   BOX      ~14ms (推荐)
                #   NEAREST  ~5ms  (最快, 锯齿)
                pil_img = pil_img.resize(
                    (self.display_width, self.display_height),
                    Image.Resampling.BOX,
                )
            # 关键修复 (用户 RDP/X server 环境): PIL 的 ImageTk.PhotoImage 内部走
            # C 扩展 PyImagingPhoto 直接 blit 32-bit 像素, 在 xorgxrdp 驱动上会把
            # RGB 错误渲染 (白色 → 深灰/深蓝), 导致"闪现画面+黑屏".
            # 改用 Tk 原生 PhotoImage(data=<PNG bytes>) — Tk 自己的 PNG 解码器,
            # 实测在用户环境正确显示 (设备帧白色 → canvas 白色, diff<10).
            import io as _io
            _png_buf = _io.BytesIO()
            pil_img.save(_png_buf, format='PNG', optimize=False)
            new_tk_image = tk.PhotoImage(data=_png_buf.getvalue())


            # 5) 复用 image_item_id, 避免每帧 delete+create
            x_offset = (canvas_width - self.display_width) // 2
            y_offset = (canvas_height - self.display_height) // 2
            if self.image_item_id is None:
                self.image_item_id = self.canvas.create_image(
                    x_offset, y_offset, anchor=tk.NW, image=new_tk_image
                )
                self.canvas.tag_lower(self.image_item_id)
            else:
                try:
                    self.canvas.itemconfig(self.image_item_id, image=new_tk_image)
                    self.canvas.coords(self.image_item_id, x_offset, y_offset)
                except Exception as e:
                    print_log(LogLevel.WARN, self.log_title,
                        f"itemconfig 失败, 重建 image: {e}")
                    self.image_item_id = None
                    self.image_item_id = self.canvas.create_image(
                        x_offset, y_offset, anchor=tk.NW, image=new_tk_image)
            # 引用保留, 否则 PhotoImage 会被 GC
            self.tk_image = new_tk_image
            self.image_refs.append(new_tk_image)
            # 控制引用数组长度, 避免无限增长
            if len(self.image_refs) > 4:
                del self.image_refs[:-2]

            # 6) 状态文字: 用 PillBadge (半透明胶囊) 直接绘在 canvas 之上
            if current_time - getattr(self, "_last_status_time", 0.0) >= 0.5:
                self._last_status_time = current_time
                frame_count = video_client.frame_count if video_client else 0
                status_text = f"FPS {self.fps}  •  帧 {frame_count}  •  {self.display_width}×{self.display_height}"
                self.pill_badge.set_text(status_text)
                if self.performance_label:
                    self.performance_label.config(text=f"FPS: {self.fps} | 帧数: {frame_count}")

            # 7) HEVC 异常覆盖层
            if self.bad_frame_streak >= 5 and (time.time() - self.error_overlay_shown_at) > 1.0:
                msg = (
                    f"⚠️ 视频流异常(连续 {self.bad_frame_streak} 帧 HEVC 损坏)\n"
                    f"edge_y/edge_x={self.last_bad_ratio:.2f} (>2.5 表示马赛克)\n"
                    f"原因:设备 RK3568 的 HEVC 编码器在 1080x1920 输出有缺陷\n"
                    f"将自动重连 server;若仍异常请检查设备端屏幕采集权限"
                )
                self._show_error_overlay(msg)
            elif self.bad_frame_streak == 0 and self.error_overlay_id is not None:
                self._hide_error_overlay()

            self.displayed_frames += 1
            # 诊断: 每 30 帧打一次, 确认 _do_render 真的持续执行
            if self.displayed_frames <= 3 or self.displayed_frames % 30 == 0:
                print_log(LogLevel.INFO, self.log_title,
                    f"render #{self.displayed_frames}: img_id={self.image_item_id} "
                    f"fc={video_client.frame_count if video_client else 'N/A'}")
        except Exception as e:
            if self.connection_manager and self.connection_manager.is_connected:
                import traceback
                tb = traceback.format_exc()
                print_log(LogLevel.ERROR, self.log_title, f"显示错误: {e}\n{tb}")
            # 出错也要继续调度, 不要把渲染循环打死
            self.root.after(33, self._schedule_render_if_needed)
            return

        # 不再用 10ms 自我递归; 改成"pending_frame 来一次调度一次"。
        # pending_frame 为 None 时退回到 33ms 轮询。
        if self.pending_frame is not None and self.pending_frame is not frame:
            # 还有更新帧没渲
            self.root.after(0, self._schedule_render_if_needed)
        else:
            self.root.after(33, self._schedule_render_if_needed)

    def show_waiting_screen(self, message: str = "等待连接...") -> None:
        """显示等待画面"""
        self.canvas.delete("all")
        self.canvas.config(bg="#1a1a2e")
        
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width <= 10 or canvas_height <= 10:
            canvas_width = 800
            canvas_height = 600
        
        self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 - 30,
            text="OHScrcpy - OpenHarmony投屏工具",
            fill="white",
            font=("Microsoft YaHei", 14, "bold")
        )
        
        self.running_status_text_id = self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 10,
            text=message,
            fill="#3498db",
            font=("Microsoft YaHei", 12)
        )
        
        self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 50,
            text="请先选择设备，然后点击[连接]按钮进行投屏",
            fill="#95a5a6",
            font=("Microsoft YaHei", 10)
        )
        
        self.status_text_id = None
    
    def update_running_status(self, content: str) -> None:
        """更新运行状态"""
        if self.running_status_text_id:
            self.canvas.itemconfig(self.running_status_text_id, text=content)
    
    def toggle_osd(self) -> None:
        """F8 切换 OSD 显示."""
        self._osd_visible = not self._osd_visible
        self.pill_badge.set_visible(self._osd_visible)
        # 旧的 status_text_id 也跟着隐藏
        if self.status_text_id:
            if self._osd_visible:
                self.canvas.itemconfig(self.status_text_id, fill="")
            else:
                self.canvas.itemconfig(self.status_text_id, fill="")

    def update_phone_frame(self) -> None:
        """重画手机外壳 (窗口 resize 后调用)."""
        try:
            self.phone_frame.draw()
        except Exception:
            pass

    def set_device_aspect(self, w: int, h: int) -> None:
        """记录设备比例, 用于初始窗口尺寸."""
        if w > 0 and h > 0:
            self._device_aspect = (w, h)

    def reset(self) -> None:
        """重置显示状态"""
        self.video_width = 0
        self.video_height = 0
        self.video_ratio = 0.0
        self.display_width = 0
        self.display_height = 0
        self.displayed_frames = 0
        self.frame_counter = 0
        self.last_fps_time = time.time()
        self.last_print_frames = 0
        self.current_frame = None
        self.pending_frame = None
        self._last_rendered_frame_id = None
        self.tk_image = None
        self.image_refs.clear()
        self._render_scheduled = False
    
    def force_garbage_collection(self) -> None:
        """强制垃圾回收"""
        def _async_garbage_collection_func() -> None:
            print_log(LogLevel.INFO, self.log_title, f"强制垃圾回收...")
            collected = gc.collect()
            print_log(LogLevel.INFO, self.log_title, f"回收了 {collected} 个对象")
        
        threading.Thread(target=_async_garbage_collection_func, daemon=True).start()


__all__ = ["VideoDisplay"]
