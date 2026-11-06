"""CameraManager: camera streaming lifecycle orchestration.

编排相机推流全生命周期：能力枚举上报、CAMERA_START/CAMERA_STOP 命令执行、
JWT 续签、自动关流响应、命令结果与告警上报（复用 EventManager → event topic）。

相机设备由硬件适配器独占持有（单一采集进程），本管理器通过
``ICameraFrameSource`` 读取共享帧槽，不直接打开设备。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from cloudrobo_r2c.camera.camera_capture import CameraCapture
from cloudrobo_r2c.camera.command_handler import CommandHandler
from cloudrobo_r2c.camera.enums import (
    CommandType,
    ErrorCode,
    EventSeverity,
)
from cloudrobo_r2c.camera.jwt_renewal import JwtRenewalManager
from cloudrobo_r2c.camera.models import (
    build_alarm_clear_event,
    build_alarm_event,
    build_command_result_event,
)
from cloudrobo_r2c.camera.stream_publisher import StreamPublisher
from cloudrobo_r2c.core.interfaces import ICameraFrameSource
from cloudrobo_r2c.common.models import CameraCommand, CameraCapability

logger = logging.getLogger(__name__)

try:
    from cloudrobo_r2c.client.event_manager import event_manager
except Exception:  # pragma: no cover
    event_manager = None


class CameraManager:
    """相机推流生命周期管理。

    Args:
        session: R2CSession（transport / project_id / device_id / event 上报）。
        frame_source: 硬件适配器实现的共享帧槽；None 时推流不可用。
        camera_streaming_cfg: 相机推流配置（CameraStreamingConfig 或 dict）。
    """

    def __init__(
        self,
        session: Any,
        frame_source: Optional[ICameraFrameSource] = None,
        camera_streaming_cfg: Optional[Any] = None,
    ):
        self._session = session
        self._frame_source = frame_source
        self._cfg = self._normalize_cfg(camera_streaming_cfg)

        self._publisher: Optional[StreamPublisher] = None
        self._captures: Dict[str, CameraCapture] = {}
        self._command_handler: Optional[CommandHandler] = None
        self._jwt_renewal: Optional[JwtRenewalManager] = None
        self._current_jwt: Optional[str] = None
        self._current_url: Optional[str] = None
        self._current_room: Optional[str] = None
        self._active_cameras: set = set()
        # 启动/停止进行中的相机（TOCTOU 竞态防护：STOP 到达时相机可能
        # 尚未进入 _active_cameras 但已在启动中，不能直接判定 NOT_ACTIVE）
        self._pending_cameras: set = set()
        # 已收到 STOP 但相机仍处于启动中的集合：异步启动协程启动每个
        # 相机前检查该集合，命中则放弃启动——保证"启动期间到达的 STOP"
        # 不会被 START 协程后启动的相机漏掉（无论两协程谁先获锁）。
        self._stop_requested: set = set()
        self._lock = threading.Lock()
        # 序列化异步推流操作的互斥锁：并发 CAMERA_START/STOP 命令不会
        # 在事件循环上交错执行，避免重复创建 publisher/track 或重复断开
        self._async_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready: Optional[threading.Event] = None
        self._initialized = False

    # ── 配置 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_cfg(cfg: Optional[Any]) -> Dict[str, Any]:
        """将 CameraStreamingConfig / dict / dataclass 归一化为普通 dict。

        仅处理已知输入类型（dict / pydantic BaseModel / dataclass），其余
        返回空 dict。不静默抑制异常——``model_dump`` / ``asdict`` 的真实
        异常会向上传播（G.ERR.07：避免抑制或忽略异常）。
        """
        if cfg is None:
            return {}
        if isinstance(cfg, dict):
            return dict(cfg)

        # pydantic BaseModel（v1/v2）
        from pydantic import BaseModel

        if isinstance(cfg, BaseModel):
            try:
                dump = cfg.model_dump()
            except AttributeError:
                # pydantic v1 无 model_dump() → 用 dict(cfg)（v1 官方转换 API）
                return dict(cfg)
            return dump if isinstance(dump, dict) else {}

        # dataclass
        from dataclasses import asdict, is_dataclass

        if is_dataclass(cfg):
            return asdict(cfg)

        return {}

    def _cfg_get(self, key: str, default: Any) -> Any:
        return self._cfg.get(key, default)

    @property
    def camera_device_map(self) -> Dict[str, str]:
        return dict(self._cfg_get("camera_device_map", {}) or {})

    @property
    def camera_source_map(self) -> Dict[str, str]:
        """云端 camera_id → 适配器帧槽 key 的翻译映射。

        可选。用于解耦云端 camera_id 命名约定（1 路 front / 2 路 front,
        wrist / 3 路 front, left_wrist, right_wrist / 4 路 left_front,
        right_front, left_wrist, right_wrist）与适配器自身帧槽 key（如
        LeRobot ``robot.cameras`` 的 cam_high 等不可改的 key）。

        缺省（未配置）时帧槽 key = camera_id（向后兼容）。
        """
        return dict(self._cfg_get("camera_source_map", {}) or {})

    def frame_slot_key(self, camera_id: str) -> str:
        """把云端 camera_id 翻译为适配器帧槽 key。"""
        return self.camera_source_map.get(camera_id, camera_id)

    def camera_id_from_slot(self, slot_key: str) -> str:
        """反向：适配器帧槽 key → 云端 camera_id（用于能力枚举去重）。"""
        source_map = self.camera_source_map
        if not source_map:
            return slot_key
        for camera_id, slot in source_map.items():
            if slot == slot_key:
                return camera_id
        return slot_key

    @property
    def default_width(self) -> int:
        return int(self._cfg_get("default_width", 640))

    @property
    def default_height(self) -> int:
        return int(self._cfg_get("default_height", 480))

    @property
    def default_fps(self) -> int:
        return int(self._cfg_get("default_fps", 20))

    @property
    def max_bitrate(self) -> int:
        return int(self._cfg_get("max_bitrate", 300_000))

    @property
    def max_framerate(self) -> int:
        return int(self._cfg_get("max_framerate", 20))

    @property
    def camera_overrides(self) -> Dict[str, Dict[str, int]]:
        """每路相机独立推流配置：{camera_id: {width, height, fps}}。"""
        raw = self._cfg_get("camera_overrides", {}) or {}
        result: Dict[str, Dict[str, int]] = {}
        for cid, ov in raw.items():
            if isinstance(ov, dict):
                entry: Dict[str, int] = {}
                for k in ("width", "height", "fps"):
                    if k in ov and ov[k] is not None:
                        entry[k] = int(ov[k])
                result[str(cid)] = entry
        return result

    def _camera_width(self, camera_id: str) -> int:
        """返回指定相机的推流宽度（camera_overrides → default_width）。"""
        ov = self.camera_overrides.get(camera_id, {})
        return int(ov.get("width", self.default_width))

    def _camera_height(self, camera_id: str) -> int:
        """返回指定相机的推流高度（camera_overrides → default_height）。"""
        ov = self.camera_overrides.get(camera_id, {})
        return int(ov.get("height", self.default_height))

    def _camera_fps(self, camera_id: str) -> int:
        """返回指定相机的推流帧率（camera_overrides → default_fps）。"""
        ov = self.camera_overrides.get(camera_id, {})
        return int(ov.get("fps", self.default_fps))

    # ── 初始化 ──────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """初始化相机推流服务（能力枚举、上报、订阅命令）。"""
        # 0. 确保 EventManager 已绑定 session（复用现有 event_manager 单例）
        if event_manager is not None and not event_manager.configured:
            try:
                event_manager.configure(self._session)
            except Exception:
                logger.debug("event_manager.configure failed", exc_info=True)

        # 0.5 检查共享帧槽可用性
        if self._frame_source is None:
            logger.warning(
                "Hardware adapter does not implement ICameraFrameSource; "
                "camera streaming will be unavailable"
            )
            # 帧槽不可用 → 不上报能力、不订阅命令。
            # 否则云端会收到"有相机能力"的上报，但实际 CAMERA_START 时
            # _start_camera_capture 会因 frame_source is None 而失败。
            return

        # 1. 枚举本地相机（基于配置映射 + 帧槽实际帧尺寸，不打开设备）
        capabilities = CameraCapture.enumerate_cameras(
            camera_device_map=self.camera_device_map,
            frame_source=self._frame_source,
            default_width=self.default_width,
            default_height=self.default_height,
            default_fps=self.default_fps,
            camera_overrides=self.camera_overrides,
            camera_source_map=self.camera_source_map,
        )
        logger.info(
            "Enumerated %d cameras: %s",
            len(capabilities),
            [c.camera_id for c in capabilities],
        )

        # 2. 上报相机能力信息
        if capabilities:
            self.publish_camera_capabilities(capabilities)

        # 3. 订阅 job/command topic
        self._command_handler = CommandHandler(self._session)
        self._command_handler.set_dispatcher(self)
        self._command_handler.subscribe()

        self._initialized = True
        logger.info("Camera streaming service initialized")

    def publish_camera_capabilities(
        self, cameras: List[CameraCapability]
    ) -> None:
        """上报相机能力信息（protobuf 序列化）。"""
        from cloudrobo_r2c.common.models import CameraCapabilityReport

        topic = (
            f"{self._session.project_id}/{self._session.device_id}/"
            f"device/camera_capability"
        )
        report = CameraCapabilityReport(
            device_id=self._session.device_id,
            cameras=cameras,
            timestamp=self._now_ms(),
        )
        try:
            self._session.transport.publish(topic, report.serialize())
            logger.info(
                "Published camera capabilities: %d cameras",
                len(cameras),
            )
            logger.debug(
                "camera_capability TX: topic=%s payload=%s",
                topic,
                report.to_dict(),
            )
        except Exception:
            logger.warning(
                "Failed to publish camera capabilities",
                exc_info=True,
            )

    # ── CAMERA_START ────────────────────────────────────────────────────────

    def start_streaming(self, payload: CameraCommand) -> None:
        """处理 CAMERA_START 命令。

        幂等/可重入保证（同一相机重复收到 CAMERA_START 不重复推流）：
        - 已激活（_active_cameras）或启动中（_pending_cameras）的相机
          直接返回 ALREADY_ACTIVE，不重复创建 publisher/track；
        - ``all`` 语义：仅当没有任何相机激活/启动中时整组启动；否则视为
          幂等命中（避免重复建立第二个 Room 连接）。
        """
        with self._lock:
            if payload.camera_id == "all":
                if self._active_cameras or self._pending_cameras:
                    # 已有相机在推流/启动中 → 幂等返回 ALREADY_ACTIVE
                    self._report_event(
                        "CAMERA_START", "all", "success",
                        error_code=ErrorCode.ALREADY_ACTIVE,
                    )
                    return
                target_cameras = self._enumerate_available_cameras()
                # 标记启动中（异步实际执行；STOP 竞态防护）
                self._pending_cameras.update(target_cameras)
            else:
                if payload.camera_id in self._active_cameras:
                    self._report_event(
                        "CAMERA_START", payload.camera_id, "success",
                        error_code=ErrorCode.ALREADY_ACTIVE,
                    )
                    return
                if payload.camera_id in self._pending_cameras:
                    # 启动中 → 幂等命中（异步启动尚未完成）
                    self._report_event(
                        "CAMERA_START", payload.camera_id, "success",
                        error_code=ErrorCode.ALREADY_ACTIVE,
                    )
                    return
                if payload.camera_id not in set(self._enumerate_available_cameras()):
                    self._report_event(
                        "CAMERA_START", payload.camera_id, "failed",
                        error_code=ErrorCode.CAMERA_NOT_FOUND,
                        error_msg="no available cameras from device map",
                    )
                    return
                target_cameras = [payload.camera_id]
                self._pending_cameras.add(payload.camera_id)

            # 保存 JWT 信息
            self._current_jwt = payload.token
            self._current_url = payload.url
            self._current_room = payload.room

        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._do_start_streaming(payload, target_cameras),
            self._loop,
        )

    async def _do_start_streaming(
        self,
        payload: CameraCommand,
        target_cameras: List[str],
    ) -> None:
        """异步执行推流启动（经 _async_lock 序列化，不可重入交错）。"""
        async with self._get_async_lock():
            try:
                # 1. 连接 LiveKit Room（1 个 wss 连接，N 个 Track）
                if not self._publisher or not self._publisher.connected:
                    # 旧的 publisher 若已失效（被踢除/断连），先释放残留资源
                    if self._publisher is not None:
                        try:
                            await self._publisher.disconnect()
                        except Exception:
                            logger.debug(
                                "stale publisher disconnect failed", exc_info=True
                            )
                        self._publisher = None
                    self._publisher = StreamPublisher(
                        max_framerate=self.max_framerate,
                        max_bitrate=self.max_bitrate,
                        on_kicked=self._on_kicked,
                        on_disconnected=self._on_wss_disconnected,
                        on_reconnected=self._on_wss_reconnected,
                    )
                    await self._publisher.connect(
                        url=payload.url,
                        token=payload.token,
                        room=payload.room,
                    )

                # 2. 逐路启动相机采集 + publish Track
                for camera_id in target_cameras:
                    with self._lock:
                        if camera_id in self._stop_requested:
                            # 启动期间收到过 STOP → 放弃启动（保持未激活），
                            # 停止意图由随后执行的 _do_stop_streaming 处理。
                            self._pending_cameras.discard(camera_id)
                            self._stop_requested.discard(camera_id)
                            self._report_event(
                                "CAMERA_START", camera_id, "failed",
                                error_code=ErrorCode.NOT_ACTIVE,
                                error_msg="cancelled by CAMERA_STOP during start",
                            )
                            continue
                    try:
                        await self._publisher.create_and_publish_track(
                            camera_id,
                            width=self._camera_width(camera_id),
                            height=self._camera_height(camera_id),
                        )
                        self._start_camera_capture(camera_id)

                        with self._lock:
                            self._active_cameras.add(camera_id)
                            self._pending_cameras.discard(camera_id)

                        self._report_event("CAMERA_START", camera_id, "success")
                        # 重新推流成功 → 消除此前被服务端踢除（BITRATE_EXCEEDED）告警
                        self._clear_alarm(ErrorCode.BITRATE_EXCEEDED, camera_id=camera_id)
                    except Exception as e:
                        # 单路启动失败 → 回滚已创建的资源 + 清除 pending 标记
                        await self._rollback_start(camera_id)
                        with self._lock:
                            self._pending_cameras.discard(camera_id)
                        self._report_event(
                            "CAMERA_START", camera_id, "failed",
                            error_code=self._classify_error(e),
                            error_msg=str(e),
                        )

                # 3. 启动 JWT 续签定时器
                if self._jwt_renewal is None and self._active_cameras:
                    self._jwt_renewal = JwtRenewalManager(
                        session=self._session,
                        on_token_renewed=self._on_token_renewed,
                        on_renew_failed=self._on_renew_failed,
                        on_renew_succeeded=self._on_renew_succeeded,
                    )
                    self._jwt_renewal.start(
                        jwt=payload.token,
                        expires_in=payload.expires_in,
                        renewal_before=payload.renewal_before,
                        camera_id=payload.camera_id,
                    )

            except Exception as e:
                logger.error("CAMERA_START failed: %s", e)
                # 连接级失败（Room connect 失败等）→ 全部目标相机回滚 + 清 pending
                for cid in target_cameras:
                    try:
                        await self._rollback_start(cid)
                    except Exception:
                        logger.debug("rollback failed for %s", cid, exc_info=True)
                with self._lock:
                    self._pending_cameras.difference_update(target_cameras)
                    # 连接级失败 → 全部目标相机均未启动，清除其停止意图
                    # 标记，避免残留导致后续重新 START 被误判放弃
                    self._stop_requested.difference_update(target_cameras)
                self._report_event(
                    "CAMERA_START", payload.camera_id, "failed",
                    error_code=self._classify_error(e),
                    error_msg=str(e),
                )

    async def _rollback_start(self, camera_id: str) -> None:
        """回滚单路相机启动：停止取帧器、unpublish track、清理状态。

        仅清理本次启动创建的资源（幂等：不存在则跳过）。
        """
        capture = self._captures.pop(camera_id, None)
        if capture is not None:
            try:
                capture.stop()
            except Exception:
                logger.debug("capture stop failed during rollback", exc_info=True)
        if self._publisher is not None:
            try:
                await self._publisher.unpublish_track(camera_id)
            except Exception:
                logger.debug("track unpublish failed during rollback", exc_info=True)
        with self._lock:
            self._active_cameras.discard(camera_id)

    def _start_camera_capture(self, camera_id: str) -> None:
        """创建取帧器并启动取帧循环（不打开设备，从帧槽读帧）。"""
        if self._frame_source is None:
            raise RuntimeError(
                "Hardware adapter does not implement ICameraFrameSource; "
                f"cannot stream camera {camera_id}"
            )
        if camera_id in self._captures:
            return
        source = self._publisher.video_source(camera_id)
        capture = CameraCapture(
            camera_id=camera_id,
            source=source,
            frame_source=self._frame_source,
            width=self._camera_width(camera_id),
            height=self._camera_height(camera_id),
            fps=self._camera_fps(camera_id),
            on_frame_stall=self._on_frame_stall,
            on_frame_recovered=self._on_frame_recovered,
            frame_slot_key=self.frame_slot_key(camera_id),
        )
        capture.start()
        self._captures[camera_id] = capture

    def _enumerate_available_cameras(self) -> List[str]:
        """枚举可用的相机 ID 列表（基于配置映射，不打开设备）。"""
        caps = CameraCapture.enumerate_cameras(
            camera_device_map=self.camera_device_map,
            frame_source=self._frame_source,
            default_width=self.default_width,
            default_height=self.default_height,
            default_fps=self.default_fps,
            camera_overrides=self.camera_overrides,
            camera_source_map=self.camera_source_map,
        )
        return [c.camera_id for c in caps]

    def _get_async_lock(self) -> asyncio.Lock:
        """返回序列化异步推流操作的锁（绑定事件循环，惰性创建）。"""
        if self._async_lock is None or self._async_lock._loop is not self._loop:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    # ── CAMERA_STOP ─────────────────────────────────────────────────────────

    def stop_streaming(self, payload: CameraCommand) -> None:
        """处理 CAMERA_STOP 命令。

        幂等/可重入保证：目标相机不在推流（且不在启动中）时返回 NOT_ACTIVE；
        若相机正在启动中（_pending_cameras），则等待异步启动完成后由
        ``_do_stop_streaming`` 停止——避免 CAMERA_STOP 被 START 竞态遗漏。
        """
        with self._lock:
            if payload.camera_id == "all":
                if not self._active_cameras and not self._pending_cameras:
                    self._report_event(
                        "CAMERA_STOP", "all", "success",
                        error_code=ErrorCode.NOT_ACTIVE,
                    )
                    return
                target_cameras = list(self._active_cameras | self._pending_cameras)
                # 启动中的相机标记停止意图（START 协程启动前会放弃启动）
                self._stop_requested.update(self._pending_cameras)
            else:
                if payload.camera_id not in self._active_cameras:
                    if payload.camera_id not in self._pending_cameras:
                        # 既未激活也未启动中 → 幂等命中
                        self._report_event(
                            "CAMERA_STOP", payload.camera_id, "success",
                            error_code=ErrorCode.NOT_ACTIVE,
                        )
                        return
                    # 启动中：延迟停止。先标记停止意图（_stop_requested），
                    # 异步启动协程在启动每个相机前检查该集合；若 START 协程
                    # 先获得异步锁，则启动完成后本协程再停止。
                    self._stop_requested.add(payload.camera_id)
                    target_cameras = [payload.camera_id]
                else:
                    target_cameras = [payload.camera_id]

        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._do_stop_streaming(payload.camera_id, target_cameras),
            self._loop,
        )

    async def _do_stop_streaming(
        self, camera_id: str, targets: List[str]
    ) -> None:
        """异步停止推流（经 _async_lock 序列化）。

        注意：只停止推流侧取帧，**不释放相机设备**——设备归硬件
        适配器所有，推理侧继续使用。
        """
        async with self._get_async_lock():
            for cid in targets:
                # 停止取帧循环（不释放设备）
                capture = self._captures.pop(cid, None)
                if capture is not None:
                    capture.stop()

                with self._lock:
                    self._active_cameras.discard(cid)
                    # 清理启动标记（含"启动中即收到 STOP"的延迟停止场景）。
                    # 注意：_stop_requested 仅在相机**确实已启动**（capture
                    # 存在）时清理；若相机尚未启动（capture 为 None，START
                    # 协程尚未执行），保留该标记供 START 协程启动前检查放弃
                    # ——防止 STOP 协程先获锁清掉标记后 START 又错误启动。
                    self._pending_cameras.discard(cid)
                    if capture is not None:
                        self._stop_requested.discard(cid)

                self._report_event("CAMERA_STOP", cid, "success")

            # 所有相机都停止（且无新启动中）→ 断开 Room + 停止 JWT 续签
            with self._lock:
                all_stopped = not self._active_cameras and not self._pending_cameras

            if self._publisher is not None:
                if all_stopped:
                    # 整房间停推：直接断开 Room（隐式 unpublish 全部 track），
                    # 避免逐 track unpublish 的异步 SDP renegotiation 与随后
                    # disconnect 关闭 PeerConnection 竞争（livekit 报
                    # "Failed to set local offer sdp: Called in wrong state:
                    # closed"）。
                    await self._publisher.disconnect()
                    self._publisher = None
                else:
                    # 部分停推：Room 继续保留，只 unpublish 停掉的 track
                    for cid in targets:
                        await self._publisher.unpublish_track(cid)

            if all_stopped:
                if self._jwt_renewal is not None:
                    self._jwt_renewal.stop()
                    self._jwt_renewal = None
                self._current_jwt = None
                self._current_url = None
                self._current_room = None
                # 推流结束 → 清除全部激活告警（wss 断连/帧源/续签/被踢除）
                self._clear_alarm(ErrorCode.WSS_CONNECT_FAILED)
                self._clear_alarm("FRAME_SOURCE_STALL")
                self._clear_alarm("JWT_RENEW_FAILED")
                self._clear_alarm(ErrorCode.BITRATE_EXCEEDED)

    # ── 自动关流 ────────────────────────────────────────────────────────────

    def on_auto_stop_notification(self) -> None:
        """收到云端自动关流通知（无订阅者超时）。"""
        logger.info(
            "Received auto-stop notification from cloud (no viewer timeout)"
        )
        with self._lock:
            targets = list(self._active_cameras | self._pending_cameras)
            # 启动中的相机标记停止意图（START 协程启动前会检查并放弃）
            self._stop_requested.update(self._pending_cameras)
        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._do_stop_streaming("all", targets),
            self._loop,
        )

    # ── JWT 续签 ────────────────────────────────────────────────────────────

    def on_token_renewed(self, cmd: CameraCommand) -> None:
        """接收 RTC_TOKEN_RENEW 命令，更新 JWT 缓存（预加载不重连）。"""
        if self._jwt_renewal is not None:
            self._jwt_renewal.on_token_renewed(
                cmd.token, cmd.expires_in, cmd.renewal_before
            )
        else:
            # 兜底：直接更新缓存
            with self._lock:
                self._current_jwt = cmd.token
            logger.info(
                "RTC_TOKEN_RENEW received (no renewal manager active); "
                "JWT preloaded"
            )

    def _on_token_renewed(self, new_jwt: str, expires_in: int, renewal_before: int) -> None:
        """JWT 续签成功后的缓存更新回调。"""
        with self._lock:
            self._current_jwt = new_jwt
        logger.debug("JWT renewed and preloaded (no reconnect)")

    @property
    def current_jwt(self) -> Optional[str]:
        with self._lock:
            return self._current_jwt

    @property
    def active_cameras(self) -> List[str]:
        with self._lock:
            return sorted(self._active_cameras)

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ── 推流生命周期告警 ────────────────────────────────────────────────────
    # 告警类型（abstract 命名，激活/消除成对使用）：
    #   WSS_DISCONNECT    — wss 断连（非被踢除），重连成功后消除
    #   BITRATE_EXCEEDED  — 被服务端踢除，重新推流成功后消除
    #   FRAME_SOURCE_STALL — 帧源持续无帧，帧恢复后消除
    #   JWT_RENEW_FAILED  — JWT 续签失败，续签成功后消除

    def _on_kicked(self, camera_ids: List[str]) -> None:
        """被服务端踢除（码率超限等）后的交叉验证上报 + 状态清理。

        通过 EventManager 上报两类 EventMessage：
        1. 命令结果（type="event"）：每路相机一条 BITRATE_EXCEEDED failed 结果
        2. 告警（type="alarm"）：一条 BITRATE_EXCEEDED 告警（激活中）

        同时清理推流状态（captures/active/pending），使后续 CAMERA_START
        能够重新推流，避免幂等检查误判 ALREADY_ACTIVE。

        注意：本回调在 livekit FFI 回调线程执行。EventManager.publish 线程
        安全；涉及 asyncio 状态（_captures/_publisher）的清理调度到事件循环
        线程执行，避免字典并发读写。
        """
        if event_manager is not None:
            for cid in camera_ids:
                self._report_event(
                    "CAMERA_START", cid, "failed",
                    error_code=ErrorCode.BITRATE_EXCEEDED,
                    error_msg=(
                        "Push bitrate exceeded max_bitrate limit, "
                        "removed by server"
                    ),
                )
            self._report_alarm(
                ErrorCode.BITRATE_EXCEEDED,
                camera_id=",".join(camera_ids),
                message="Removed by server due to bitrate exceeded",
                severity=EventSeverity.MAJOR,
            )

        # 同步清除 active/pending 标记（受 _lock 保护，可安全在任意线程执行）
        with self._lock:
            for cid in camera_ids:
                self._active_cameras.discard(cid)
                self._pending_cameras.discard(cid)
                self._stop_requested.discard(cid)

        # 取帧器与 publisher 状态属于 asyncio 线程 → 调度到事件循环清理
        if self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._cleanup_kicked(camera_ids),
                    self._loop,
                )
            except Exception:
                logger.debug("kicked cleanup scheduling failed", exc_info=True)
        else:
            # 事件循环尚未启动（理论不发生，被踢除必然已连接）
            for cid in camera_ids:
                capture = self._captures.pop(cid, None)
                if capture is not None:
                    try:
                        capture.stop()
                    except Exception:
                        logger.debug("capture stop failed after kick", exc_info=True)

    async def _cleanup_kicked(self, camera_ids: List[str]) -> None:
        """在事件循环线程中清理被踢除相机的取帧器与 publisher 残留。"""
        async with self._get_async_lock():
            for cid in camera_ids:
                capture = self._captures.pop(cid, None)
                if capture is not None:
                    try:
                        capture.stop()
                    except Exception:
                        logger.debug("capture stop failed after kick", exc_info=True)

            # 该 Room 已被服务端移除：残留 track/source 全部失效，且
            # publisher 内部已置 connected=False。仅清空本地发布记录，
            # 不重复 disconnect（SDK 已断），允许后续 START 重建连接。
            if self._publisher is not None:
                self._publisher.reset_local_tracks(camera_ids)
                if not self._publisher.has_tracks:
                    self._publisher = None

    # -- wss 断连 / 重连 --

    def _on_wss_disconnected(self, camera_ids: List[str]) -> None:
        """wss 断连（非被踢除，网络故障等）→ 激活断连告警。

        注意：livekit ``disconnect()`` 会同步 emit ``disconnected`` 事件
        （CLIENT_INITIATED），本回调仅在**非本地主动断开**（如 CAMERA_STOP
        路径由 publisher 标记跳过）时上报告警，避免停止推流时误报断连。
        """
        logger.warning("wss disconnected, raising alarm: cameras=%s", camera_ids)
        self._report_alarm(
            ErrorCode.WSS_CONNECT_FAILED,
            camera_id=",".join(camera_ids),
            message="LiveKit wss connection lost, attempting auto-reconnect",
            severity=EventSeverity.MAJOR,
        )

    def _on_wss_reconnected(self, camera_ids: List[str]) -> None:
        """wss 重连成功 → 消除断连告警。"""
        logger.info("wss reconnected, clearing alarm: cameras=%s", camera_ids)
        self._clear_alarm(
            ErrorCode.WSS_CONNECT_FAILED,
            camera_id=",".join(camera_ids),
        )

    # -- 帧源 stall / 恢复 --

    def _on_frame_stall(self, camera_id: str) -> None:
        """帧源持续无帧 → 激活帧源告警。"""
        logger.warning("frame source stall, raising alarm: camera=%s", camera_id)
        self._report_alarm(
            "FRAME_SOURCE_STALL",
            camera_id=camera_id,
            message="Camera frame source stalled (no frames for a while)",
            severity=EventSeverity.MAJOR,
        )

    def _on_frame_recovered(self, camera_id: str) -> None:
        """帧源恢复 → 消除帧源告警。"""
        logger.info("frame source recovered, clearing alarm: camera=%s", camera_id)
        self._clear_alarm("FRAME_SOURCE_STALL", camera_id=camera_id)

    # -- JWT 续签失败 / 成功 --

    def _on_renew_failed(self) -> None:
        """JWT 续签请求失败 → 激活续签告警。"""
        logger.warning("JWT renewal failed, raising alarm")
        self._report_alarm(
            "JWT_RENEW_FAILED",
            camera_id=",".join(sorted(self._active_cameras)),
            message="JWT renewal request failed, will retry",
            severity=EventSeverity.MINOR,
        )

    def _on_renew_succeeded(self) -> None:
        """JWT 续签成功 → 消除续签告警。"""
        logger.info("JWT renewal succeeded, clearing alarm")
        self._clear_alarm(
            "JWT_RENEW_FAILED",
            camera_id=",".join(sorted(self._active_cameras)),
        )

    # ── 事件上报 ────────────────────────────────────────────────────────────

    def _report_event(
        self,
        command: str,
        camera_id: str,
        status: str,
        error_code: str = "",
        error_msg: str = "",
    ) -> None:
        """通过 EventManager 上报命令执行结果（复用现有 event topic）。"""
        if event_manager is None:
            logger.debug(
                "event_manager unavailable; skip event report "
                "(command=%s camera=%s)",
                command,
                camera_id,
            )
            return
        event = build_command_result_event(
            command=command,
            camera_id=camera_id,
            status=status,
            error_code=error_code,
            error_msg=error_msg,
        )
        event_manager.publish(event)

    def _report_alarm(
        self,
        alarm_type: str,
        camera_id: str = "",
        message: str = "",
        severity: str = EventSeverity.MAJOR,
    ) -> None:
        """通过 EventManager 上报告警（复用现有 event topic）。

        告警（type="alarm"，ends_at=0）自动进入 EventManager 激活告警
        登记簿；异常恢复后由 :meth:`_clear_alarm` 上报消除事件出册。
        """
        if event_manager is None:
            return
        event = build_alarm_event(
            alarm_type=alarm_type,
            camera_id=camera_id,
            message=message,
            severity=severity,
        )
        event_manager.publish(event)

    def _clear_alarm(self, alarm_type: str, camera_id: str = "") -> None:
        """通过 EventManager 上报告警消除事件（ends_at>0 → 登记簿出册）。

        与 :meth:`_report_alarm` 配对使用：异常恢复（重连成功、帧恢复、
        续签成功、重新推流成功）后清除对应激活告警。
        """
        if event_manager is None:
            return
        event = build_alarm_clear_event(
            alarm_type=alarm_type,
            camera_id=camera_id,
        )
        event_manager.publish(event)

    # ── 错误分类 ────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_error(e: Exception) -> str:
        """根据异常类型分类错误码。"""
        text = str(e).lower()
        if "frame source" in text or "icameraframesource" in text:
            return ErrorCode.RESOURCE_INSUFFICIENT
        if "camera" in text and ("not found" in text or "no available" in text):
            return ErrorCode.CAMERA_NOT_FOUND
        if "connect" in text or "wss" in text or "room" in text:
            return ErrorCode.WSS_CONNECT_FAILED
        if "timeout" in text:
            return ErrorCode.PUBLISH_TIMEOUT
        if "resolution" in text or "format" in text:
            return ErrorCode.RESOLUTION_NOT_SUPPORTED
        if "memory" in text or "resource" in text or "insufficient" in text:
            return ErrorCode.RESOURCE_INSUFFICIENT
        if "encoder" in text or "encode" in text:
            return ErrorCode.ENCODER_INIT_FAILED
        return ErrorCode.INTERNAL_ERROR

    # ── 异步循环 ────────────────────────────────────────────────────────────

    def _ensure_loop(self) -> None:
        """确保存在独立 asyncio 事件循环（供 LiveKit 异步 API 使用）。

        线程启动后等待循环创建完成（_loop_ready 事件），避免调用方
        立即 run_coroutine_threadsafe 时循环尚未就绪的竞态。
        """
        if self._loop is not None:
            return
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._run_async_loop,
            name="r2c-sdk-camera-asyncio",
            daemon=True,
        )
        self._loop_thread.start()
        self._loop_ready.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("Camera streaming asyncio loop failed to start")

    def _run_async_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop_ready.set()
            self._loop.run_forever()
        finally:
            # run_forever 返回（loop.stop 后）循环已退出运行态。
            # asyncio.all_tasks(loop) 在 Python 3.10 要求循环正在运行，
            # 这里在循环仍运行期间由 cleanup 先行取消任务（见 cleanup），
            # 退出后仅做关闭清理。
            try:
                self._loop.close()
            except Exception:
                logger.debug("event loop close failed", exc_info=True)
            self._loop = None

    def _cancel_pending_tasks(self) -> None:
        """在事件循环线程内取消全部未完成任务（要求循环处于运行态）。"""
        try:
            for task in asyncio.all_tasks(self._loop):
                task.cancel()
        except Exception:
            logger.debug("pending task cancel failed", exc_info=True)

    @staticmethod
    def _now_ms() -> int:
        import time

        return int(time.time() * 1000)

    # ── 清理 ────────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """停机清理：停止推流、停止续签、释放异步循环。"""
        logger.info("CameraManager cleanup started")
        with self._lock:
            targets = list(self._active_cameras | self._pending_cameras)

        if targets:
            if self._loop is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._do_stop_streaming("all", targets),
                        self._loop,
                    ).result(timeout=5.0)
                except Exception:
                    logger.warning(
                        "CameraManager stop-streaming failed during cleanup",
                        exc_info=True,
                    )
            else:
                # 异步循环从未启动（未实际推流过）：直接同步清理状态
                for cid in targets:
                    capture = self._captures.pop(cid, None)
                    if capture is not None:
                        try:
                            capture.stop()
                        except Exception:
                            logger.debug("capture stop failed", exc_info=True)
                self._active_cameras.clear()
                self._pending_cameras.clear()
                self._stop_requested.clear()
                self._publisher = None

        # 兜底清空全部状态标记（异步循环分支也可能残留 _stop_requested）
        with self._lock:
            self._stop_requested.clear()

        if self._jwt_renewal is not None:
            try:
                self._jwt_renewal.stop()
            except Exception:
                logger.debug("jwt renewal stop failed during cleanup", exc_info=True)
            self._jwt_renewal = None

        if self._loop is not None:
            try:
                # 在循环仍运行期间取消未完成任务，再停止循环：
                # asyncio.all_tasks 要求循环处于运行态，必须由 call_soon
                # 调度到循环线程执行（cleanup 调用时循环未必在运行）。
                self._loop.call_soon_threadsafe(self._cancel_pending_tasks)
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                logger.debug("event loop stop failed during cleanup", exc_info=True)
        self._loop_ready = None
        self._async_lock = None
        self._initialized = False
        logger.info("CameraManager cleanup completed")
