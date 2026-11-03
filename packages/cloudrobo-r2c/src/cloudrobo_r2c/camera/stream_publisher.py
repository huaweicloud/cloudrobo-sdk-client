"""LiveKit WebRTC stream publisher (camera push side).

基于 livekit-rtc Python SDK：连接 LiveKit Room（1 个 wss 连接），逐路
发布相机 VideoSource/LocalVideoTrack，管理 Track 生命周期与 Room 事件
回调（断连/重连/被服务端踢除）。

被服务端踢除（码率超限等）时通过 ``on_kicked`` 回调通知上层（CameraManager）
做交叉验证上报（event + alarm）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from cloudrobo_r2c.camera.enums import ErrorCode

logger = logging.getLogger(__name__)

try:
    from livekit import rtc
    from livekit.rtc._proto.participant_pb2 import DisconnectReason
except Exception:  # pragma: no cover - dependency optional until streaming enabled
    rtc = None
    DisconnectReason = None

# ConnectionState 枚举 → 可读名（livekit.rtc._proto.room_pb2.ConnectionState）
_CONN_STATE_NAMES = {
    0: "DISCONNECTED",
    1: "CONNECTED",
    2: "RECONNECTING",
}

# CandidatePairStats.state 枚举 → 可读名（stats_pb2）
_PAIR_STATE_NAMES = {
    0: "FROZEN",
    1: "WAITING",
    2: "IN_PROGRESS",
    3: "FAILED",
    4: "SUCCEEDED",
}

# IceCandidateStats.candidate_type 枚举 → 可读名（stats_pb2）
_CAND_TYPE_NAMES = {
    0: "host",
    1: "srflx",
    2: "prflx",
    3: "relay",
}

# 媒体（PeerConnection）连接建立超时诊断窗口：livekit-rtc 默认
# wait_pc_connection 超时 15s，这里覆盖该窗口（含重试裕量）。
_ICE_POLL_INTERVAL_S = 2.0
_ICE_POLL_MAX_ATTEMPTS = 8


class StreamPublisher:
    """LiveKit WebRTC 推流管理器。

    Args:
        max_framerate: TrackPublishOptions 最大帧率。
        max_bitrate: TrackPublishOptions 最大码率（配合 SFU max_bitrate REMB）。
        on_kicked: 被服务端踢除回调（参数: camera_ids 列表）。
    """

    def __init__(
        self,
        max_framerate: int = 20,
        max_bitrate: int = 300_000,
        on_kicked=None,
        on_disconnected=None,
        on_reconnected=None,
    ):
        if rtc is None:
            raise RuntimeError(
                "livekit-rtc is not installed; camera streaming unavailable"
            )
        self._room: Optional[rtc.Room] = None
        self._sources: Dict[str, rtc.VideoSource] = {}
        self._tracks: Dict[str, rtc.LocalVideoTrack] = {}
        self._publications: Dict[str, rtc.LocalTrackPublication] = {}
        self._max_framerate = max_framerate
        self._max_bitrate = max_bitrate
        self._on_kicked = on_kicked
        self._on_disconnected_cb = on_disconnected
        self._on_reconnected_cb = on_reconnected
        self._connected = False
        # 本地主动断开标记：disconnect() 时置位，抑制 SDK 同步触发的
        # "disconnected" 回调上报（避免 CAMERA_STOP 误报 wss 断连告警）
        self._local_disconnect = False
        # ICE 诊断状态
        self._conn_state: Optional[int] = None
        self._ice_poll_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, url: str, token: str, room: str) -> None:
        """连接 LiveKit Room 并注册事件回调。"""
        self._room = rtc.Room()

        self._register_room_events()

        # ICE 诊断：连接前记录目标地址，供后续候选比对
        logger.info(
            "LiveKit connect begin: url=%s room=%s", url, room,
        )
        # 在媒体建立窗口内启动周期 ICE 诊断轮询（连接成功后自动 dump 并停止）
        self._start_ice_poll()

        try:
            await self._room.connect(
                url,
                token,
                rtc.RoomOptions(
                    auto_subscribe=False,  # 推流端不需要订阅
                    dynacast=True,  # 无订阅者时自动降码率
                    connect_timeout=5.0,
                ),
            )
        except Exception:
            # 连接失败（信令或媒体超时）→ 停止 ICE 诊断轮询并上报
            self._cancel_ice_poll()
            raise
        self._connected = True
        logger.info("LiveKit Room connected: room=%s", room)
        # 连接成功 → 立即 dump 一次 ICE 候选对（含本地/远端地址）
        await self._dump_ice_diagnostics("connected")

    def _register_room_events(self) -> None:
        if self._room is None:
            return
        self._room.on("disconnected", self._on_disconnected)
        self._room.on("reconnecting", self._on_reconnecting)
        self._room.on("reconnected", self._on_reconnected)
        self._room.on("connection_state_changed", self._on_connection_state_changed)
        self._room.on("local_track_published", self._on_local_track_published)
        self._room.on("local_track_unpublished", self._on_local_track_unpublished)

    async def create_and_publish_track(
        self,
        camera_id: str,
        width: int = 640,
        height: int = 480,
    ) -> None:
        """为指定相机创建 VideoSource + LocalVideoTrack 并发布。

        幂等/可重入：该相机 track 已存在（发布中或残留未清理）时直接返回，
        不重复创建 VideoSource/Track，避免旧资源被覆盖泄漏。

        Args:
            camera_id: 相机方位标识。
            width: 推流目标宽度（与 VideoSource 分辨率一致）。
            height: 推流目标高度（与 VideoSource 分辨率一致）。
        """
        if self._room is None:
            raise RuntimeError("Room not connected")
        if camera_id in self._tracks or camera_id in self._publications:
            logger.debug(
                "Track already exists for camera %s, skip re-publish",
                camera_id,
            )
            return

        source = rtc.VideoSource(width, height)
        track = rtc.LocalVideoTrack.create_video_track(
            name=f"camera-{camera_id}",
            source=source,
        )
        options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            video_encoding=rtc.VideoEncoding(
                max_framerate=self._max_framerate,
                max_bitrate=self._max_bitrate,
            ),
        )
        publication = await self._room.local_participant.publish_track(track, options)

        self._sources[camera_id] = source
        self._tracks[camera_id] = track
        self._publications[camera_id] = publication
        logger.info(
            "Published camera track: camera=%s sid=%s %dx%d",
            camera_id,
            publication.sid,
            width,
            height,
        )

    def reset_local_tracks(self, camera_ids: List[str]) -> None:
        """清理指定相机的本地 track/source/publication 记录（不操作 Room）。

        用于被服务端踢除后清理残留发布记录（Room 已被服务端移除，无需
        再向 SDK 发起 unpublish）；仅从本地字典移除，避免后续复用失效资源。
        """
        for cid in camera_ids:
            self._sources.pop(cid, None)
            self._tracks.pop(cid, None)
            self._publications.pop(cid, None)

    @property
    def has_tracks(self) -> bool:
        """是否仍有本地 track/source 记录。"""
        return bool(self._tracks or self._sources or self._publications)

    def video_source(self, camera_id: str) -> rtc.VideoSource:
        """返回指定相机已发布的 VideoSource（供 CameraCapture 推帧）。"""
        if camera_id not in self._sources:
            raise KeyError(f"No published VideoSource for camera {camera_id}")
        return self._sources[camera_id]

    async def unpublish_track(self, camera_id: str) -> None:
        """取消发布指定相机的 Track 并释放本地资源。"""
        if self._room is None:
            return
        track = self._tracks.pop(camera_id, None)
        source = self._sources.pop(camera_id, None)
        publication = self._publications.pop(camera_id, None)

        if track is not None:
            try:
                await self._room.local_participant.unpublish_track(track.sid)
            except Exception:
                logger.warning(
                    "Failed to unpublish track for camera %s",
                    camera_id,
                    exc_info=True,
                )
        if source is not None:
            try:
                await source.aclose()
            except Exception:
                logger.debug("video source close failed", exc_info=True)

        logger.info("Unpublished camera track: camera=%s", camera_id)

    async def disconnect(self) -> None:
        """断开 Room 并清理本地资源。

        先置位 ``_local_disconnect``：livekit ``Room.disconnect()`` 会同步
        emit ``disconnected`` 事件，此时回调应被抑制（主动停止推流不是断连
        故障），不应上报告警。
        """
        self._cancel_ice_poll()
        self._local_disconnect = True
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:
                logger.debug("room disconnect failed", exc_info=True)
        for source in list(self._sources.values()):
            try:
                await source.aclose()
            except Exception:
                logger.debug("video source close failed during disconnect",
                             exc_info=True)
        self._sources.clear()
        self._tracks.clear()
        self._publications.clear()
        self._connected = False
        self._room = None
        self._local_disconnect = False

    # ── Room 事件回调 ────────────────────────────────────────────────────────

    def _on_disconnected(self, reason) -> None:
        """Room 断连回调。

        本地主动断开（CAMERA_STOP 路径的 disconnect()）会同步触发本回调，
        此时 ``_local_disconnect`` 为 True，直接返回不上报——停止推流不是
        断连故障。
        """
        if self._local_disconnect:
            logger.debug(
                "LiveKit Room disconnected (local, reason=%s)", reason,
            )
            self._connected = False
            self._cancel_ice_poll()
            return
        self._connected = False
        self._cancel_ice_poll()
        reason_name = "unknown"
        if DisconnectReason is not None:
            try:
                reason_name = DisconnectReason.Name(reason)
            except Exception:
                reason_name = str(reason)
        logger.warning("LiveKit Room disconnected, reason=%s", reason_name)

        # 被服务端踢除（码率超限等）→ 通知上层做交叉验证上报
        if reason == DisconnectReason.PARTICIPANT_REMOVED:
            camera_ids = list(self._sources.keys())
            logger.warning(
                "Removed by LiveKit server (PARTICIPANT_REMOVED), "
                "cameras=%s",
                camera_ids,
            )
            if self._on_kicked is not None:
                self._on_kicked(camera_ids)
        elif self._on_disconnected_cb is not None:
            # 非踢除断连（网络故障等）→ 通知上层激活 wss 断连告警
            self._on_disconnected_cb(list(self._sources.keys()))

    def _on_reconnecting(self) -> None:
        logger.info("LiveKit Room reconnecting...")

    def _on_reconnected(self) -> None:
        """Room 重连成功，SDK 自动重新 publish Track。"""
        self._connected = True
        logger.info("LiveKit Room reconnected, tracks auto-republished")
        if self._on_reconnected_cb is not None:
            # 重连成功 → 通知上层消除 wss 断连告警
            self._on_reconnected_cb(list(self._sources.keys()))

    def _on_connection_state_changed(self, state) -> None:
        """连接状态机变化 → 打点 + 在"媒体建立窗口"内轮询 ICE 诊断。"""
        state_name = _CONN_STATE_NAMES.get(int(state), f"UNKNOWN({state})")
        prev = _CONN_STATE_NAMES.get(
            int(self._conn_state) if self._conn_state is not None else -1,
            f"UNKNOWN({self._conn_state})",
        )
        self._conn_state = int(state)
        logger.info(
            "LiveKit connection state: %s -> %s", prev, state_name,
        )

        # 媒体（PeerConnection）建立前/建立中：由 connect() 启动的周期 ICE
        # 诊断轮询持续运行，覆盖 wait_pc_connection 超时窗口。媒体建立
        # （CONN_CONNECTED）后轮询自停；彻底断开才取消轮询。
        if state_name == "DISCONNECTED":
            self._cancel_ice_poll()

    def _start_ice_poll(self) -> None:
        """启动周期 ICE 诊断轮询（每 2s，最多 8 次，覆盖 15s 超时窗口）。"""
        if self._room is None or self._ice_poll_task is not None:
            return
        loop = asyncio.get_event_loop()
        self._ice_poll_task = loop.create_task(self._ice_poll_loop())

    async def _ice_poll_loop(self) -> None:
        """周期拉取 RTC stats 并打印候选对状态；媒体连接建立后停止。"""
        try:
            for attempt in range(1, _ICE_POLL_MAX_ATTEMPTS + 1):
                if self._room is None:
                    break
                await self._dump_ice_diagnostics(f"poll#{attempt}")
                # 媒体（PeerConnection）建立 → 成功路径已在 connect() 中
                # dump 过候选对，这里停止轮询即可
                if self._conn_state == 1:  # CONN_CONNECTED
                    break
                await asyncio.sleep(_ICE_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            logger.debug("ICE diagnostics poll cancelled")
        finally:
            self._ice_poll_task = None

    def _cancel_ice_poll(self) -> None:
        if self._ice_poll_task is not None:
            self._ice_poll_task.cancel()
            self._ice_poll_task = None

    async def _dump_ice_diagnostics(self, reason: str) -> None:
        """拉取 Room RTC stats，打印候选对状态与本地/远端候选地址。

        用于排查 ``wait_pc_connection timed out``（信令通、媒体不通）类问题：
        - 远端候选地址 = SFU 通告的 ICE 候选（node_ip:port），可核对
          是否与预期 EIP/端口一致、是否可达；
        - 候选对状态（SUCCEEDED/FAILED/IN_PROGRESS）直接指示媒体路径成败。
        """
        if self._room is None or not self._room.isconnected():
            logger.debug(
                "ICE diagnostics skipped (%s): room not connected (wss only)",
                reason,
            )
            return
        try:
            stats = await self._room.get_rtc_stats()
        except Exception as e:
            logger.warning(
                "ICE diagnostics get_rtc_stats failed (%s): %s",
                reason, type(e).__name__,
            )
            return

        pair_summaries = self._summarize_candidate_pairs(stats)
        if not pair_summaries:
            logger.debug(
                "ICE diagnostics (%s): no candidate pair stats yet "
                "(media connection still gathering/connecting)",
                reason,
            )
            return

        for line in pair_summaries:
            logger.info("ICE diagnostics (%s): %s", reason, line)

    @staticmethod
    def _summarize_candidate_pairs(stats) -> List[str]:
        """从 RtcStats 提取候选对摘要（本地/远端地址、状态、提名）。

        注意：``livekit.proto.RtcStats`` 的 candidate_pair / local_candidate /
        remote_candidate 同属一个 oneof（``stats``），一个 RtcStats 条目
        只承载其中一种。因此必须跨 ``publisher_stats`` + ``subscriber_stats``
        全部条目聚合：先收集候选对与候选地址，再按 id 匹配。
        """
        if stats is None:
            return []

        try:
            pairs = list(stats.publisher_stats) + list(stats.subscriber_stats)
        except Exception:
            return []

        pair_msgs = []
        cand_by_id: Dict[str, tuple] = {}

        for rtc_stats in pairs:
            try:
                which = rtc_stats.WhichOneof("stats")
            except Exception:
                which = None
            try:
                if which == "candidate_pair":
                    cps = rtc_stats.candidate_pair.candidate_pair
                    pair_msgs.append(
                        (cps.local_candidate_id,
                         cps.remote_candidate_id,
                         int(cps.state),
                         bool(cps.nominated),
                         int(cps.packets_sent or 0),
                         int(cps.packets_received or 0),
                         )
                    )
                elif which in ("local_candidate", "remote_candidate"):
                    c = getattr(rtc_stats, which).candidate
                    cand_by_id[getattr(rtc_stats, which).rtc.id] = (
                        c.address,
                        int(c.port),
                        c.protocol,
                        int(c.candidate_type),
                    )
            except Exception:
                logger.debug("rtc stats entry parse skipped", exc_info=True)
                continue

        lines: List[str] = []
        for local_id, remote_id, state, nominated, p_sent, p_recv in pair_msgs:
            local = StreamPublisher._candidate_summary(cand_by_id, local_id)
            remote = StreamPublisher._candidate_summary(cand_by_id, remote_id)
            lines.append(
                "candidate pair state=%s nominated=%s sent=%d recv=%d "
                "local=%s remote=%s" % (
                    _PAIR_STATE_NAMES.get(state, f"UNKNOWN({state})"),
                    nominated, p_sent, p_recv,
                    local, remote,
                )
            )
        return lines

    @staticmethod
    def _candidate_summary(cand_by_id: Dict[str, tuple], cid: str) -> str:
        """候选 ID → "address:port/protocol(type)" 摘要。"""
        if not cid:
            return "(none)"
        info = cand_by_id.get(cid)
        if info is None:
            # 该轮 stats 快照中未带此候选数据（可能在其他 transport 快照）
            return f"id={cid[:24]}"
        address, port, protocol, ctype = info
        return "%s:%d/%s(%s)" % (
            address, port, protocol,
            _CAND_TYPE_NAMES.get(ctype, f"t{ctype}"),
        )

    def _on_local_track_published(self, publication, track) -> None:
        logger.info("Track published: %s (sid=%s)", publication.name, publication.sid)

    def _on_local_track_unpublished(self, publication) -> None:
        """Track 取消发布（可能是服务端 MutePublishedTrack 触发）。"""
        logger.info("Track unpublished: %s", publication.name)
