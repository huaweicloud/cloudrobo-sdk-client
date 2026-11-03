"""Raw passthrough forwarder: configurable receive topics -> fixed send topics.

Self-contained: owns its session/route config model (``forwarder_config.yaml``:
``sessions`` + ``routes``) and session lifecycle management.
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from cloudrobo_r2c.client.client import R2CClient
from cloudrobo_r2c.client.session import R2CSession
from cloudrobo_r2c.client.topic_resolver import TopicResolver
from cloudrobo_r2c.common.config import ClientConfig

logger = logging.getLogger(__name__)


# ── config model (forwarder_config.yaml) ───────────────────────────────


def _require_mapping(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, where: str, allow_empty: bool = False) -> str:
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(f"{where} is required")
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, got {type(value).__name__}")
    if not value.strip() and not allow_empty:
        raise ValueError(f"{where} must not be empty")
    return value


@dataclass
class SessionSpec:
    """Self-contained session declaration (endpoint/mode/role/identity)."""

    name: str
    endpoint: str = ""
    mode: str = "peer"
    role: str = "client"
    bundle: str = ""
    client_id: str = ""
    project_id: str = ""
    device_id: str = ""


@dataclass
class RouteSpec:
    from_topic: str
    to_topic: str
    from_session: str
    to_session: str
    # 可选：消息 pb2 类（"module:ClassName"），声明后转发时按
    # send session 身份覆盖 source 字段（等价 _ensure_state_source）
    proto: Optional[str] = None
    # 转发降采样（route 级独立配置）：
    #   sample_factor：每 N 帧转发 1 帧（1=全量）
    #   sample_max_interval_s：距上次转发超过该秒数则强制转发一帧（0=禁用，
    #     防止长时间无输出；优先级高于 factor/min_interval）
    #   sample_min_interval_s：两次转发的最小间隔秒（0=禁用，限频）
    sample_factor: int = 1
    sample_max_interval_s: float = 0.0
    sample_min_interval_s: float = 0.0
    # 声明 proto 后默认仅覆盖 source；timestamp 复写为转发时刻需显式
    # 开启（rewrite_timestamp: true）。false/缺省保留设备本地时间戳
    rewrite_timestamp: bool = False
    # 限频帧缓存（默认 0=禁用）：发送间隔低于 sample_min_interval_s
    # 时帧不丢弃而是入缓存（按收到时刻完成 source/timestamp 改写），
    # 门开后按序冲刷（旧帧在前）；缓存满则丢弃新帧
    cache_size: int = 0


@dataclass
class ForwarderConfig:
    sessions: Dict[str, SessionSpec] = field(default_factory=dict)
    routes: List[RouteSpec] = field(default_factory=list)
    forwarder_session: Optional[SessionSpec] = None
    topic_defaults: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for route in self.routes:
            for session_name in (route.from_session, route.to_session):
                if session_name in self.sessions:
                    continue
                if session_name == "main":
                    continue
                raise ValueError(
                    f"route {route.from_topic!r} -> {route.to_topic!r}: "
                    f"unknown session {session_name!r}"
                )


def _parse_session(name: str, data: Any) -> SessionSpec:
    data = _require_mapping(data, f"session {name!r}")
    return SessionSpec(
        name=name,
        endpoint=_require_str(data.get("endpoint"), f"session {name!r}.endpoint", True),
        mode=_require_str(data.get("mode"), f"session {name!r}.mode", True) or "peer",
        role=_require_str(data.get("role"), f"session {name!r}.role", True) or "client",
        bundle=_require_str(data.get("bundle"), f"session {name!r}.bundle", True),
        client_id=_require_str(data.get("client_id"), f"session {name!r}.client_id", True),
        project_id=_require_str(
            data.get("project_id"), f"session {name!r}.project_id", True
        ),
        device_id=_require_str(
            data.get("device_id"), f"session {name!r}.device_id", True
        ),
    )


# 允许被 topic_defaults 全局默认覆盖的 route 级键（降采样 + 缓存参数）
_DEFAULTABLE_KEYS = (
    "sample_factor",
    "sample_max_interval_s",
    "sample_min_interval_s",
    "rewrite_timestamp",
    "cache_size",
)


def _parse_route(r: Any, where: str, defaults: Optional[Dict[str, Any]] = None) -> RouteSpec:
    r = _require_mapping(r, where)
    defaults = defaults or {}

    def _v(key: str, fallback):
        # route 级显式值优先；未声明时回退 topic_defaults 全局默认；再回退内置缺省
        if key in r:
            return r[key]
        if key in defaults:
            return defaults[key]
        return fallback

    return RouteSpec(
        from_topic=_require_str(r.get("from"), f"{where}.from"),
        to_topic=_require_str(r.get("to"), f"{where}.to"),
        from_session=_require_str(r.get("from_session"), f"{where}.from_session", True)
        or "main",
        to_session=_require_str(r.get("to_session"), f"{where}.to_session", True)
        or "forwarder_session",
        proto=r.get("proto") or None,
        sample_factor=int(_v("sample_factor", 1) or 1),
        sample_max_interval_s=float(_v("sample_max_interval_s", 0) or 0),
        sample_min_interval_s=float(_v("sample_min_interval_s", 0) or 0),
        rewrite_timestamp=bool(_v("rewrite_timestamp", False)),
        cache_size=max(int(_v("cache_size", 0) or 0), 0),
    )


def parse_forwarder_config(path: str) -> ForwarderConfig:
    """Load and validate a ``forwarder_config.yaml`` file.

      * 顶层只声明 ``forwarder_session``
        （云，mode=client，身份/证书由 CLI --bundle 注入）+ ``topic_defaults``
        全局默认 + ``routes``（from_session/to_session 引用 "main"/"forwarder_session"）
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw = _require_mapping(raw, f"config file {path!r}")

    fs_spec = _parse_session("forwarder_session", raw.get("forwarder_session"))
    topic_defaults = dict(raw.get("topic_defaults") or {})
    routes_raw = raw.get("routes", []) or []
    if not isinstance(routes_raw, list):
        raise ValueError("routes must be a list")
    routes = [
        _parse_route(r, f"routes[{i}]", topic_defaults)
        for i, r in enumerate(routes_raw)
    ]
    config = ForwarderConfig(
        sessions={"forwarder_session": fs_spec},
        routes=routes,
        forwarder_session=fs_spec,
        topic_defaults=topic_defaults,
    )
    config.validate()
    return config


class SessionManager:
    """Build and own the lifecycle of the forwarder's sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, R2CSession] = {}

    @staticmethod
    def _build_client_config(spec: SessionSpec) -> ClientConfig:
        """非 bundle 模式构建 ClientConfig：叠加 SDK 内建 default_zenoh_perf.yaml
        的性能默认（publisher/subscriber/predeclare），与 bundle 模式（perf.yaml）
        行为对齐——forwarder_session 连云时同样获得 QoS 等性能配置。
        """
        perf_kwargs = ClientConfig._perf_kwargs_from_mapping(
            ClientConfig._load_default_perf_mapping()
        )
        return ClientConfig(
            project_id=spec.project_id,
            device_id=spec.device_id,
            client_id=spec.client_id,
            endpoints=[spec.endpoint] if spec.endpoint else [],
            mode=spec.mode or "client",
            endpoint_role=(
                spec.role if spec.role in ("listen", "connect") else "connect"
            ),
            **perf_kwargs,
        )

    def open(self, spec: SessionSpec) -> R2CSession:
        if spec.name in self._sessions:
            raise ValueError(f"session {spec.name!r} is already open")

        if spec.bundle:
            session = R2CClient.connect(
                os.fspath(spec.bundle),
                client_id=spec.client_id or None,
            )
        else:
            session = R2CClient._connect_from_config(
                self._build_client_config(spec)
            )

        self._sessions[spec.name] = session
        logger.info(
            "session %r opened (project_id=%s, device_id=%s, bundle=%s)",
            spec.name,
            session.project_id,
            session.device_id,
            bool(spec.bundle),
        )
        return session

    def open_all(self, specs: List[SessionSpec]) -> Dict[str, R2CSession]:
        opened: List[str] = []
        try:
            for spec in specs:
                self.open(spec)
                opened.append(spec.name)
        except Exception:
            self.close_all()
            raise
        return dict(self._sessions)

    def get(self, name: str) -> R2CSession:
        session = self._sessions.get(name)
        if session is None:
            raise KeyError(f"session {name!r} is not open")
        return session

    def provide(self, name: str, session: R2CSession) -> None:
        if name in self._sessions:
            raise ValueError(f"session {name!r} is already provided")
        self._sessions[name] = session

    def open_forwarder_session(
        self,
        config: ForwarderConfig,
        *,
        main_session: Optional[R2CSession] = None,
        bundle: Optional[str] = None,
        connect: Any = None,
        private_key_password: Optional[Union[str, bytes]] = None,
    ) -> None:
        if config.forwarder_session is None:
            raise ValueError("config must declare forwarder_session")
        if main_session is not None:
            self.provide("main", main_session)
        spec = config.forwarder_session
        if bundle:
            # bundle 路径在运行时注入（配置不写 bundle），提供连云身份/证书
            spec = SessionSpec(
                name=spec.name,
                endpoint=spec.endpoint,
                mode=spec.mode,
                role=spec.role,
                bundle=bundle,
                client_id=spec.client_id,
                project_id=spec.project_id,
                device_id=spec.device_id,
            )
        connect_fn = connect or R2CClient.connect
        if bundle:
            session = connect_fn(
                os.fspath(bundle),
                client_id=spec.client_id or None,
                private_key_password=private_key_password,
            )
        else:
            session = R2CClient._connect_from_config(
                self._build_client_config(spec)
            )
        self._sessions[spec.name] = session
        logger.info(
            "Forwarder session %r opened (project_id=%s, device_id=%s, bundle=%s)",
            spec.name,
            session.project_id,
            session.device_id,
            bool(bundle),
        )

    def close_all(self) -> None:
        """Close every session; one failure never blocks the others."""
        for name in list(self._sessions):
            session = self._sessions.pop(name)
            try:
                session.close()
            except Exception as exc:
                logger.debug(
                    "session %s close failed: %s", name, type(exc).__name__
                )


class _RouteRuntime:
    """Per-route forwarding runtime extracted from ``Forwarder._start_route``.

    持有单条 route 的转发共享状态（统计 / 缓存 / 限频门），把原先内嵌在
    ``_start_route`` 中的 5 个闭包（should_forward / rewrite / send /
    handler / flush_if_due）各自实现为有界方法，使 ``_start_route`` 只保留
    骨架，从而把 "含闭包圈复杂度" 拆散到本类各方法，规避 CodeCheck 的
    Maximum Complexity 门禁（≤20）。
    """

    def __init__(
        self,
        forwarder: "Forwarder",
        from_topic: str,
        to_topic: str,
        to_session: R2CSession,
        rewrite: Optional[Tuple[Any, Any, bool]],
        factor: int,
        max_iv: float,
        min_iv: float,
        cache_size: int,
    ) -> None:
        self._forwarder = forwarder
        self._from_topic = from_topic
        self._to_topic = to_topic
        self._to_session = to_session
        self._rewrite = rewrite
        self._factor = max(factor, 1)
        self._max_iv = max_iv
        self._min_iv = min_iv
        self._cache_size = cache_size
        self.stats = {
            "count": 0, "bytes": 0, "dropped": 0, "cached": 0,
            "period": 0, "recent": deque(maxlen=10),
            "rx_period": 0, "rx_recent": deque(maxlen=10),
            # 接收/转发字节累计（统计表 from_size/to_size 列）
            "from_size": 0, "to_size": 0,
        }
        # 限频帧缓存（cache_size>0 启用）：min_iv 窗口内的帧入缓存
        # 而非丢弃，门开后按序冲刷（旧帧在前 → 当前帧最后）
        self._cache: Optional[deque] = deque() if cache_size > 0 else None
        self._lock = threading.Lock()
        self._frame_idx = 0
        self._last_fwd = 0.0

    @property
    def lock(self) -> threading.Lock:
        """供统计/看门狗线程在「同一把写锁」下读取或重置统计字段。

        handle/send 在 `self._lock` 内并发递增 stats 各字段，统计线程若
        无锁读写在 period/rx_period 读+重置时可能与写侧交错（竞态）。此处
        暴露同一把锁，使读侧与写侧共享临界区，避免死锁（单一锁、无嵌套）。
        """
        return self._lock

    def meta(self) -> Tuple[str, str, Any, int]:
        """提供给 ``Forwarder._route_meta[from_topic]`` 的展示元组。"""
        if self._rewrite is not None:
            proto_name = self._rewrite[0].__name__
        else:
            proto_name = "-"
        return (self._to_topic, proto_name, self._cache, self._cache_size)

    def _factor_skip(self) -> bool:
        """factor 降采样跳帧：frame_idx 不能被 factor 整除则跳过。"""
        return self._factor > 1 and self._frame_idx % self._factor != 0

    def should_forward(self, now: float) -> bool:
        """降采样决策：max 强制 > min 限频 > factor 跳帧；首帧恒转发。"""
        self._frame_idx += 1
        if math.isclose(self._last_fwd, 0.0, rel_tol=0.0, abs_tol=1e-9):
            return True
        if self._max_iv > 0 and now - self._last_fwd >= self._max_iv:
            return True
        if self._min_iv > 0 and now - self._last_fwd < self._min_iv:
            return False
        if self._factor_skip():
            return False
        return True

    def rewrite(self, payload: bytes) -> bytes:
        if self._rewrite is None:
            return payload
        return self._forwarder._rewrite_source(
            payload, self._rewrite[0], self._rewrite[1], self._rewrite[2]
        )

    def _record_sent(self, out: bytes, now: float) -> None:
        """锁内记录一次成功发送的统计（调用方需已持有 self._lock）。"""
        self.stats["count"] += 1
        self.stats["period"] += 1
        self.stats["bytes"] += len(out)
        self.stats["to_size"] += len(out)
        self.stats["recent"].append(now)

    def _publish(self, out: bytes) -> None:
        """锁外执行网络 publish；失败抛错由调用方捕获并记日志。

        把阻塞 I/O 移出锁，仅共享状态（缓存/stats/last_fwd）读写留锁内，
        避免回调并发场景下锁内做网络发送降低吞吐。
        """
        self._to_session.transport.publish(self._to_topic, out)

    def _enqueue_or_drop(self, payload: bytes) -> None:
        """缓存可容纳则把改写后的帧入缓存（FIFO），否则丢弃新帧。"""
        if self._cache is not None and len(self._cache) < self._cache_size:
            self._cache.append(self.rewrite(payload))
            self.stats["cached"] += 1
        else:
            self.stats["dropped"] += 1

    def _send_due(self, payload: bytes) -> bytes:
        """锁内决策本发送槽要发的帧：缓存非空发最老缓存帧（当前帧入
        缓存保序），否则发当前帧；返回待 publish 的 out（I/O 在锁外）。
        缓存 FIFO 保序与丢弃语义与原实现完全一致。"""
        if self._cache:
            out = self._cache.popleft()
            self._enqueue_or_drop(payload)
            return out
        return self.rewrite(payload)

    def handle(self, payload: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self.stats["rx_period"] += 1
            self.stats["rx_recent"].append(now)
            self.stats["from_size"] += len(payload)
            limited = (
                self._min_iv > 0 and self._last_fwd > 0
                and (now - self._last_fwd) < self._min_iv
            )
            if not self.should_forward(now):
                # min_iv 限频窗口内入缓存（FIFO）不丢弃，满则丢弃
                # 新帧；其余拒绝（factor 跳帧等）维持丢弃
                if limited:
                    self._enqueue_or_drop(payload)
                else:
                    self.stats["dropped"] += 1
                return
            out = self._send_due(payload)
        # 网络 publish 移出锁：避免阻塞 I/O 占用锁、降低并发吞吐
        try:
            self._publish(out)
        except Exception:
            logger.exception(
                "forward failed (%s -> %s, %d bytes)",
                self._from_topic,
                self._to_topic,
                len(payload),
            )
            return
        with self._lock:
            # 仅成功发送后推进统计与 last_fwd（与原语义一致）
            self._record_sent(out, now)
            self._last_fwd = now

    def flush_if_due(self) -> None:
        """min_iv 门开即冲刷缓存——不依赖下一帧触发，
        且每次只占一个发送槽（逐帧节流，不突发）。"""
        if self._cache is None or self._min_iv <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if not self._cache:
                return
            if self._last_fwd > 0 and (now - self._last_fwd) < self._min_iv:
                return
            out = self._cache.popleft()
        try:
            self._publish(out)
        except Exception:
            logger.exception(
                "cache flush failed (%s -> %s)",
                self._from_topic,
                self._to_topic,
            )
            return
        send_t = time.monotonic()
        with self._lock:
            self._record_sent(out, send_t)
            self._last_fwd = send_t


class Forwarder:
    """Subscribe configurable topics on one session, republish on another.

    Payloads are forwarded as raw bytes ("收到就发"); the send side topic
    templates are resolved against the send session's bundle identity.
    """

    def __init__(
        self,
        routes: List[RouteSpec],
        sessions: Dict[str, R2CSession],
        full_topic: bool = False,
        name: str = "forwarder",
    ) -> None:
        self._routes = routes
        self._name = name
        self._sessions = sessions
        # 统计表 topic 展示：False（默认）隐藏 project_id/device_id
        # （以 {project_id}/{device_id} 占位），True 完整展示（--full-topic）
        self._full_topic = bool(full_topic)
        self._route_stats: Dict[str, Dict[str, Any]] = {}
        # from_topic -> 与写侧同一把的统计锁（见 _RouteRuntime.lock）
        self._route_locks: Dict[str, threading.Lock] = {}
        self._route_meta: Dict[str, Tuple[str, str, Any, int]] = {}
        # 每条启用缓存的 route 注册一个"门开即冲刷"闭包
        self._cache_flushers: List[Any] = []
        self._resolvers: Dict[str, TopicResolver] = {}
        for name_, session in sessions.items():
            self._resolvers[name_] = TopicResolver(session.project_id, session.device_id)

    def _mask_topic(self, topic: str) -> str:
        """统计表 topic 展示：默认隐藏 project_id/device_id（占位符替代）。"""
        if self._full_topic:
            return topic
        for session in self._sessions.values():
            topic = topic.replace(
                f"{session.project_id}/{session.device_id}",
                "{pid}/{did}",
            )
        return topic

    def start(self) -> None:
        # 预验证所有 route 的 proto 引用，确保任一非法时 fail-fast，
        # 不泄漏已订阅的 route 线程
        for route in self._routes:
            self._load_pb2_class(getattr(route, "proto", None))
        for route in self._routes:
            self._start_route(route)
        self._start_cache_flusher()
        self._start_link_watchdog()
        self._start_stats_reporter()
        # PID 打进日志：站点误起两个 forwarder 时（帧翻倍）可据此定位
        logger.info(
            "Forwarder started (process=%s, routes=%d, pid=%d)",
            self._name,
            len(self._routes),
            os.getpid(),
        )

    def _start_stats_reporter(self, interval_s: float = 1.0) -> None:
        """每秒输出一次转发统计表（替代逐帧/周期性单 topic 日志）。

        每行：from_topic（接收频率 rxHz + 累计接收字节 from_size）、
        to_topic（转发频率 txHz + 累计转发字节 to_size）、proto、缓存占用
        used/total（未启用显示 "-"）、累计丢弃数、累计发送数。本周期有
        任何接收或转发才输出该行（全新无流量时由 link watchdog 负责
        no-frames 提示）。
        """

        def _hz(dq: deque) -> float:
            if len(dq) >= 2:
                dt = dq[-1] - dq[0]
                return (len(dq) - 1) / dt if dt > 0 else 0.0
            return 0.0

        def _report() -> None:
            n = 0
            start_t = time.monotonic()
            while True:
                time.sleep(interval_s)
                n += 1
                elapsed = time.monotonic() - start_t
                rows = []
                for from_topic, stats in self._route_stats.items():
                    # 与 handle/send 写侧同一把锁：读取+重置统计字段原子化，
                    # 消除统计线程无锁读写的竞态（period/rx_period 读一半）
                    with self._route_locks[from_topic]:
                        if stats["count"] == 0 and stats["rx_period"] == 0:
                            continue
                        to_topic, proto, cache, cache_size = self._route_meta.get(
                            from_topic, ("?", "-", None, 0)
                        )
                        used = len(cache) if cache is not None else 0
                        cache_str = (
                            f"{used}/{cache_size}" if cache is not None else "-"
                        )
                        rows.append((
                            from_topic,
                            _hz(stats["rx_recent"]),
                            stats["from_size"],
                            to_topic,
                            _hz(stats["recent"]),
                            stats["to_size"],
                            proto,
                            cache_str,
                            stats["dropped"],
                            stats["count"],
                        ))
                        stats["period"] = 0
                        stats["rx_period"] = 0
                if not rows:
                    continue
                lines = [
                    f"========== forwarder stats pid={os.getpid()}"
                    f" #{n} (t+{elapsed:.0f}s) ==========",
                    f"{'from_topic':<44} {'rxHz':>6} {'from_size':>10}"
                    f" {'to_topic':<44} {'txHz':>6} {'to_size':>10}"
                    f" {'proto':<20} {'cache':>8} {'drop':>7} {'total':>8}",
                ]
                for (from_topic, rx_hz, from_size, to_topic, tx_hz, to_size,
                     proto, cache_str, dropped, total) in rows:
                    # topic 完整展示（不截断）；默认隐藏 pid/did（--full-topic 显示）
                    lines.append(
                        f"{self._mask_topic(str(from_topic)):<44}"
                        f" {rx_hz:>6.1f}"
                        f" {from_size:>10}"
                        f" {self._mask_topic(str(to_topic)):<44}"
                        f" {tx_hz:>6.1f}"
                        f" {to_size:>10}"
                        f" {str(proto):<20.20}"
                        f" {cache_str:>8}"
                        f" {dropped:>7}"
                        f" {total:>8}"
                    )
                logger.info("\n".join(lines))

        threading.Thread(
            target=_report, name="forwarder-stats", daemon=True
        ).start()

    def _start_cache_flusher(self, interval_s: float = 0.1) -> None:
        """周期检查各 route 缓存：min_iv 门开即按序冲刷。

        此前缓存帧需等下一帧到来才冲刷，输入间歇时帧滞留缓存（统计表
        cache 长期非 0）。100ms 轮询足够及时且开销可忽略。
        """

        def _flush_loop() -> None:
            while True:
                time.sleep(interval_s)
                for flush in list(self._cache_flushers):
                    try:
                        flush()
                    except Exception:
                        logger.exception("cache flusher error")

        threading.Thread(
            target=_flush_loop, name="forwarder-cache-flush", daemon=True
        ).start()

    def _start_link_watchdog(self, interval_s: float = 10.0) -> None:
        """周期诊断：session 断链 WARNING + route 长时间无帧 WARNING。

        connected 是 connect() 后的静态标志（peer connect 到无人监听的
        endpoint 也为 True），因此无帧告警才是"云端收不到"的直接信号。
        """
        def _watch() -> None:
            while True:
                time.sleep(interval_s)
                for name, session in self._sessions.items():
                    info_fn = getattr(session.transport, "connection_info", None)
                    info = info_fn() if info_fn else {}
                    if not info.get("connected", False):
                        logger.warning(
                            "link watch: session %r NOT connected "
                            "(stage=%s, last_error=%s) — publishes are dropped",
                            name,
                            info.get("stage"),
                            info.get("last_error"),
                        )
                for from_topic, stats in self._route_stats.items():
                    # 与 handle/send 写侧同一把锁下读取 count，避免读到
                    # 进行中的半更新状态
                    with self._route_locks[from_topic]:
                        count = stats["count"]
                    if count == 0:
                        logger.warning(
                            "no frames received on %s since start — "
                            "check publisher topic/identity, and that the "
                            "endpoint has a listener (or set session role: listen)",
                            from_topic,
                        )

        threading.Thread(target=_watch, name="forwarder-link-watch", daemon=True).start()

    @staticmethod
    def _load_pb2_class(spec: Optional[str]) -> Optional[Any]:
        if not spec:
            return None
        from cloudrobo_r2c.common.proto_registry import (
            ensure_sdk_builtin,
            resolve_registered,
        )

        registered = resolve_registered(spec, "route.proto")
        if registered is not None:
            return registered
        module_name, _, class_name = spec.partition(":")
        if not module_name or not class_name:
            raise ValueError(f"route.proto: invalid proto path {spec!r}")
        # 裸路径仅限 SDK 内建 proto；第三方/厂商私有 proto 必须经
        # entry_point 注册。将 ensure_sdk_builtin 纳入 try/except 统一
        # 错误消息，使第三方裸路径与内建模块属性缺失都产生
        # "cannot import" 格式的错误。
        try:
            ensure_sdk_builtin(module_name, "route.proto")
            module = importlib.import_module(module_name)
            return getattr(module, class_name)
        except Exception as exc:
            raise ValueError(
                f"route.proto: cannot import {module_name!r}: {exc}"
            ) from exc

    def _start_route(self, route: RouteSpec) -> None:
        from_session = self._sessions[route.from_session]
        to_session = self._sessions[route.to_session]
        from_topic = self._resolvers[route.from_session].resolve(route.from_topic)
        to_topic = self._resolvers[route.to_session].resolve(route.to_topic)

        pb_cls = self._load_pb2_class(getattr(route, "proto", None))
        rewrite: Optional[Tuple[Any, Any, bool]] = (
            (pb_cls, to_session, bool(getattr(route, "rewrite_timestamp", False)))
            if pb_cls is not None
            else None
        )

        factor = max(int(getattr(route, "sample_factor", 1) or 1), 1)
        max_iv = float(getattr(route, "sample_max_interval_s", 0) or 0)
        min_iv = float(getattr(route, "sample_min_interval_s", 0) or 0)
        cache_size = max(int(getattr(route, "cache_size", 0) or 0), 0)

        runtime = _RouteRuntime(
            self,
            from_topic,
            to_topic,
            to_session,
            rewrite,
            factor,
            max_iv,
            min_iv,
            cache_size,
        )
        self._route_stats[from_topic] = runtime.stats
        # 每条 route 一把与写侧共用/独立于外部的统计锁，供统计/看门狗线程
        # 读取+重置统计字段时使用（见 _start_stats_reporter / _start_link_watchdog）
        self._route_locks[from_topic] = runtime.lock
        self._route_meta[from_topic] = runtime.meta()
        # 注册"门开即冲刷"回调：bound method 持有 runtime，等价原闭包
        self._cache_flushers.append(runtime.flush_if_due)

        from_session.transport.subscribe(from_topic, runtime.handle)
        logger.info(
            "forwarding %s -> %s%s",
            from_topic,
            to_topic,
            " (source rewritten)" if rewrite is not None else "",
        )

    @staticmethod
    def _rewrite_source(
        payload: bytes,
        pb_cls: Any,
        to_session: R2CSession,
        rewrite_timestamp: bool = False,
    ) -> bytes:
        """按 send session 身份覆盖 source；*rewrite_timestamp* 为 true 时
        把 timestamp 复写为当前时间（route 级 `rewrite_timestamp: true`
        显式开启，缺省保留设备本地时间戳）。

        兼容两类消息类：**wrapper 类**（如 ``cloudrobo_r2c.common.models:EventMessage``，
        有 ``from_protobuf``/``serialize`` 协议——配置统一使用 wrapper 路径）
        与 **pb2 消息类**（``module:ClassName`` 直引用，``ParseFromString`` +
         ``DESCRIPTOR``）。timestamp 为 int64 毫秒的消息赋当前毫秒；
        google.protobuf.Timestamp 类型（如 Heartbeat）取当前时间。解析失败
        （非本 proto 的帧）时原样透传，不阻塞转发。
        """
        try:
            wrapper_protocol = callable(getattr(pb_cls, "from_protobuf", None))
            if wrapper_protocol:
                msg = pb_cls.from_protobuf(payload)
                ensure = getattr(to_session, "_ensure_state_source", None)
                if ensure is not None:
                    ensure(msg)
                elif hasattr(msg, "source"):
                    msg.source = f"{to_session.project_id}/{to_session.device_id}"
                if rewrite_timestamp and hasattr(msg, "timestamp"):
                    from cloudrobo_r2c.common.models.wrappers.validators import (
                        normalize_proto_timestamp,
                    )

                    raw_ts = getattr(msg, "timestamp", None)
                    if isinstance(raw_ts, int):
                        msg.timestamp = int(time.time() * 1000)
                    elif raw_ts is not None:
                        # protobuf Timestamp（如 Heartbeat）——归一化为当前时刻
                        msg.timestamp = normalize_proto_timestamp(
                            int(time.time() * 1000),
                            "timestamp",
                            type(msg).__name__,
                        )
                return msg.serialize()
            # pb2 消息类路径
            msg = pb_cls()
            msg.ParseFromString(payload)
            ensure = getattr(to_session, "_ensure_state_source", None)
            if ensure is not None:
                ensure(msg)
            elif hasattr(msg, "source"):
                msg.source = f"{to_session.project_id}/{to_session.device_id}"
            if rewrite_timestamp:
                ts_field = msg.DESCRIPTOR.fields_by_name.get("timestamp")
                if ts_field is not None:
                    if ts_field.message_type is None:
                        msg.timestamp = int(time.time() * 1000)
                    elif (
                        ts_field.message_type.full_name
                        == "google.protobuf.Timestamp"
                    ):
                        msg.timestamp.GetCurrentTime()
            return msg.SerializeToString()
        except Exception:
            logger.debug("source/timestamp rewrite skipped (%d bytes)", len(payload))
            return payload


def start_forwarder(
    config: ForwarderConfig,
    sessions: Dict[str, R2CSession],
    full_topic: bool = False,
    name: str = "forwarder",
) -> Forwarder:
    """Build and start a Forwarder from a parsed ``ForwarderConfig``."""
    forwarder = Forwarder(
        config.routes, sessions, full_topic=full_topic, name=name
    )
    forwarder.start()
    return forwarder


def open_forwarder_sessions(
    config: ForwarderConfig,
    *,
    main_session: Optional[R2CSession] = None,
    bundle: Optional[str] = None,
    connect: Any = None,
    private_key_password: Optional[Union[str, bytes]] = None,
) -> Dict[str, R2CSession]:
    """Build the session map for a parsed config.

    * 注册 ``main``（进程内主 session）+ 经 ``bundle`` 连云打开
      ``forwarder_session``。无 bundle 时只注册 main（不连云、不转发）。
    """
    manager = SessionManager()
    if bundle:
        manager.open_forwarder_session(
            config,
            main_session=main_session,
            bundle=bundle,
            connect=connect,
            private_key_password=private_key_password,
        )
    elif main_session is not None:
        manager.provide("main", main_session)
    else:
        raise ValueError(
            "config: 'main' session must be provided in-process "
            "(cloudroboclient local session)"
        )
    return dict(manager._sessions)


def _cli_wait_for_interrupt() -> None:
    """阻塞主线程直到收到 Ctrl+C（KeyboardInterrupt）。

    独立为模块级函数便于测试注入（main 内可被 patch 为立即抛
    KeyboardInterrupt，从而在不真起后台线程的情况下验证优雅 close 路径）。
    """
    while True:
        time.sleep(1.0)


def main(argv: Optional[List[str]] = None) -> int:
    """独立 forwarder CLI 旁路入口（``python -m cloudrobo_r2c.forwarder``）。

    演进后推荐用 ``cloudroboclient --forwarder-config`` 进程内 forwarder；
    本 CLI 保留为旁路。

    参数：
      * ``--config <yaml>``：forwarder 配置文件路径（默认
        ``config/forwarder_config.yaml``）
      * ``--client-config <yaml>``：本地主 session（main）客户端配置，
        用于建立本地主会话（默认 ``config/client_config.yaml``）
      * ``--bundle <zip|dir>``：转发会话（forwarder_session）的云凭证包，
        提供连云身份/证书
      * ``--full-topic``（flag）：统计表完整展示 topic（默认以
        ``{project_id}/{device_id}`` 占位隐藏真实身份）

    流程：argparse → parse_forwarder_config → 由 --client-config 建立 main
    主会话、经 --bundle 建立 forwarder_session → start_forwarder → 阻塞等
    Ctrl+C → forwarder.close() → 返回 0。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cloudrobo_r2c.forwarder",
        description=(
            "Standalone raw-passthrough forwarder CLI. "
            "Parses forwarder_config.yaml (forwarder_session + topic_defaults "
            "+ routes), opens sessions, and runs the forwarder until Ctrl+C."
        ),
    )
    parser.add_argument(
        "--config",
        default="config/forwarder_config.yaml",
        help="Path to forwarder_config.yaml (default: config/forwarder_config.yaml)",
    )
    parser.add_argument(
        "--client-config",
        default="config/client_config.yaml",
        help=(
            "Path to the local main session client config YAML "
            "(default: config/client_config.yaml); establishes the 'main' "
            "local session"
        ),
    )
    parser.add_argument(
        "--bundle",
        default=None,
        help=(
            "Path to the platform credential bundle for the forwarder_session "
            "(cloud); provides cloud identity/certificates"
        ),
    )
    parser.add_argument(
        "--private-key-password",
        default=None,
        help=(
            "Password for encrypted private key in the bundle. "
            "Required when the bundle contains an encrypted private key."
        ),
    )
    parser.add_argument(
        "--full-topic",
        action="store_true",
        default=False,
        help=(
            "Show full topic names in stats table (default: mask "
            "project_id/device_id as {project_id}/{device_id})"
        ),
    )
    args = parser.parse_args(argv)

    config = parse_forwarder_config(args.config)

    main_session = None
    if args.client_config and os.path.exists(args.client_config):
        main_cfg = ClientConfig.from_yaml(args.client_config)
        main_session = R2CClient.connect(main_cfg)
        logger.info(
            "Main session opened from client config: %s", args.client_config
        )
    elif args.client_config:
        logger.warning(
            "Client config not found: %s (skip main session); "
            "pass --bundle to enable forwarding",
            args.client_config,
        )

    sessions = open_forwarder_sessions(
        config,
        main_session=main_session,
        bundle=args.bundle,
        private_key_password=args.private_key_password,
    )
    try:
        forwarder = start_forwarder(config, sessions, full_topic=args.full_topic)
    except Exception:
        # 启动失败时清理已打开的 session，避免泄漏
        logger.error("forwarder start failed, cleaning up sessions")
        for session in sessions.values():
            try:
                session.close()
            except Exception:
                logger.debug("session close failed (ignored)")
        raise
    try:
        _cli_wait_for_interrupt()
    except KeyboardInterrupt:
        logger.info("forwarder CLI stopped by keyboard interrupt")
    finally:
        try:
            forwarder.close()
        except Exception:
            logger.exception("forwarder close failed")
        # 关闭由 forwarder 自身打开的 session（main 进程内 session 除外——
        # 旁路 CLI 下不存在外部主 session，全部由 open_forwarder_sessions 打开）
        for session in sessions.values():
            try:
                session.close()
            except Exception:
                logger.debug("session close failed (ignored)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
