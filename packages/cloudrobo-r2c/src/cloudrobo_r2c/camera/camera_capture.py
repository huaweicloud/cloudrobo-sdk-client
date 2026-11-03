"""Camera frame capture loop for LiveKit streaming.

推流侧取帧器：从硬件适配器实现的 ``ICameraFrameSource`` 共享帧槽读取
最新帧，按推流目标帧率 keep-latest 采样，转换为 I420 后推入 LiveKit
``VideoSource``。

**不直接打开相机设备**——设备由硬件适配器独占持有（单一采集进程），
本模块只读取帧槽，从根源上避免 V4L2/RealSense 设备独占冲突
（详见技术设计文档 §2.4）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

import cv2
import numpy as np

from cloudrobo_r2c.common.models import CameraCapability
from cloudrobo_r2c.core.interfaces import ICameraFrameSource

logger = logging.getLogger(__name__)

try:
    from livekit import rtc
except Exception:  # pragma: no cover - dependency optional until streaming enabled
    rtc = None


class CameraCapture:
    """LiveKit 推流取帧器：从硬件适配器共享帧槽读帧，不做任何设备操作。

    Args:
        camera_id: 相机方位标识（front / left_front / ...）。
        source: LiveKit VideoSource（由 StreamPublisher 创建并已发布 Track）。
        frame_source: 硬件适配器实现的共享帧槽（相机设备唯一持有者）。
        width / height: 推流目标分辨率（帧尺寸不一致时 resize）。
        fps: 推流目标帧率（从帧槽 keep-latest 采样）。
        on_frame_stall: 帧源持续无帧回调（参数: camera_id）。
        on_frame_recovered: 帧源恢复回调（参数: camera_id）。
    """

    # 帧源无帧告警阈值（秒）：持续超过该时长无帧视为帧源异常（stall）
    _STALL_THRESHOLD_S = 3.0

    def __init__(
        self,
        camera_id: str,
        source: rtc.VideoSource,
        frame_source: ICameraFrameSource,
        width: int = 640,
        height: int = 480,
        fps: int = 20,
        on_frame_stall=None,
        on_frame_recovered=None,
        frame_slot_key: Optional[str] = None,
    ):
        if rtc is None:
            raise RuntimeError(
                "livekit-rtc is not installed; camera streaming unavailable"
            )
        self._camera_id = camera_id
        self._source = source
        self._frame_source = frame_source
        # 适配器帧槽 key：camera_id → 帧槽 key 翻译（camera_source_map），
        # 缺省时与 camera_id 相同（向后兼容）。
        self._frame_slot_key = frame_slot_key or camera_id
        self._width = width
        self._height = height
        self._fps = fps
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 帧源 stall 检测回调（参数: camera_id）——持续无帧时激活告警，
        # 帧恢复后消除告警
        self._on_frame_stall = on_frame_stall
        self._on_frame_recovered = on_frame_recovered
        self._stall_notified = False

    def start(self) -> None:
        """启动取帧循环。不做任何设备打开操作。"""
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-capture-{self._camera_id}",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        """按推流帧率从帧槽采样读取（keep-latest）。

        持续无帧超过 ``_STALL_THRESHOLD_S`` 时通知上层激活帧源告警；
        帧恢复后通知上层消除告警（去抖避免抖动误报）。
        """
        interval = 1.0 / self._fps
        started_at_ns = time.perf_counter_ns()
        no_frame_warning_logged = False
        last_frame_seen = time.monotonic()

        while not self._stop_event.is_set():
            bgr = self._frame_source.get_latest_frame(self._frame_slot_key)
            now = time.monotonic()
            if bgr is None:
                # 尚无帧（相机初始化中），短暂等待后重试
                if not no_frame_warning_logged:
                    logger.info(
                        "Camera %s: no frame from frame source yet, waiting...",
                        self._camera_id,
                    )
                    no_frame_warning_logged = True
                # 帧源 stall 检测：超过阈值仍无帧 → 激活告警
                if (now - last_frame_seen) >= self._STALL_THRESHOLD_S and not self._stall_notified:
                    self._stall_notified = True
                    logger.warning(
                        "Camera %s: frame source stalled for %.1fs",
                        self._camera_id,
                        now - last_frame_seen,
                    )
                    if self._on_frame_stall is not None:
                        self._on_frame_stall(self._camera_id)
                time.sleep(min(interval, 0.1))
                continue

            no_frame_warning_logged = False
            last_frame_seen = now
            # 帧恢复 → 消除帧源告警（此前已激活）
            if self._stall_notified:
                self._stall_notified = False
                logger.info("Camera %s: frame source recovered", self._camera_id)
                if self._on_frame_recovered is not None:
                    self._on_frame_recovered(self._camera_id)

            try:
                if bgr.shape[1] != self._width or bgr.shape[0] != self._height:
                    bgr = cv2.resize(
                        bgr,
                        (self._width, self._height),
                        interpolation=cv2.INTER_AREA,
                    )

                # BGR → I420 格式转换（YUV420p 平面格式，LiveKit FFI 直接可用）
                i420_data = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
                frame = rtc.VideoFrame(
                    self._width,
                    self._height,
                    rtc.VideoBufferType.I420,
                    i420_data.tobytes(),
                )
                timestamp_us = (time.perf_counter_ns() - started_at_ns) // 1000
                self._source.capture_frame(frame, timestamp_us=timestamp_us)
            except Exception:
                logger.warning(
                    "Camera %s: frame conversion/capture failed",
                    self._camera_id,
                    exc_info=True,
                )

            time.sleep(interval)

    def stop(self) -> None:
        """停止取帧循环。不释放设备（设备归硬件适配器所有）。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ── 能力枚举 ────────────────────────────────────────────────────────────

    @staticmethod
    def enumerate_cameras(
        camera_device_map: dict,
        frame_source: Optional[ICameraFrameSource] = None,
        default_width: int = 640,
        default_height: int = 480,
        default_fps: int = 20,
        camera_overrides: Optional[Dict[str, Dict[str, int]]] = None,
        camera_source_map: Optional[Dict[str, str]] = None,
    ) -> List[CameraCapability]:
        """基于配置映射 + 帧槽实际帧尺寸枚举能力信息。

        不打开设备——相机设备归硬件适配器所有。分辨率/帧率从实际收到的
        首帧推断，或回退到默认值。

        Args:
            camera_device_map: {camera_id: device_identifier} 映射（来自
                robot_config.yaml 的 camera_streaming.camera_device_map）。
            frame_source: 共享帧槽；为 None 时仅用默认尺寸。
            camera_overrides: {camera_id: {width, height, fps}} 每路相机
                独立推流配置，覆盖 default_width/height/fps。未列出的
                相机或缺失的字段回退到默认值。
            camera_source_map: {camera_id: 帧槽 key} 翻译映射（可选）；
                取帧时用翻译后的帧槽 key，能力上报的 camera_id 不变。
        """
        camera_overrides = camera_overrides or {}
        camera_source_map = camera_source_map or {}
        capabilities: List[CameraCapability] = []
        for camera_id, device in (camera_device_map or {}).items():
            ov = camera_overrides.get(camera_id, {})
            width = int(ov.get("width", default_width))
            height = int(ov.get("height", default_height))
            fps = int(ov.get("fps", default_fps))

            if frame_source is not None:
                slot_key = camera_source_map.get(camera_id, camera_id)
                frame = frame_source.get_latest_frame(slot_key)
                if frame is None or frame.ndim < 2:
                    # 尚无帧（相机初始化中或设备未就绪）→ 使用默认尺寸上报
                    logger.debug(
                        "Camera %s (slot=%s): no frame yet during enumeration, "
                        "using default resolution %dx%d",
                        camera_id,
                        slot_key,
                        width,
                        height,
                    )

            capabilities.append(
                CameraCapability(
                    camera_id=camera_id,
                    name=str(device),
                    resolution=f"{width}x{height}",
                    fps=fps,
                    codec="h264",
                )
            )
        return capabilities
