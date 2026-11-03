"""话题记录与回放核心（zenoh session 注入，与连接方式解耦）。

- ``TopicRecorder``：订阅一个或多个 key 表达式（支持 ``*`` 通配），
  按本地单调时基记录 (topic, 相对时间戳, payload)，保存为 pickle 文件。
- ``TopicReplayer``：加载记录文件，按白名单/黑名单过滤，并按原始
  相对时间戳时序发布（支持倍速与循环）。

pkl 结构::

    {
        "version": 1,
        "meta": {"start_wall_ms", "end_wall_ms", "count", "patterns"},
        "frames": [{"topic": str, "t": int(相对ns), "payload": bytes}, ...],
    }
"""

from __future__ import annotations

import fnmatch
import logging
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

RECORD_FORMAT_VERSION = 1


@dataclass
class RecordedFrame:
    """单条记录：topic 与 payload 原样保存，t 为相对起始的纳秒数。"""

    topic: str
    t: int
    payload: bytes


def _match_any(topic: str, patterns: Optional[Sequence[str]]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(topic, p) for p in patterns)


def filter_topics(
    topics: Sequence[str],
    whitelist: Optional[Sequence[str]] = None,
    blacklist: Optional[Sequence[str]] = None,
) -> List[str]:
    """按白/黑名单（fnmatch 通配）过滤 topic。

    规则：先黑名单排除，再白名单保留（白名单为空 = 全部保留）。
    """
    out: List[str] = []
    for topic in topics:
        if blacklist and _match_any(topic, blacklist):
            continue
        if whitelist and not _match_any(topic, whitelist):
            continue
        out.append(topic)
    return out


class TopicRecorder:
    """订阅 key 表达式并记录到 pkl。

    session 需提供 ``declare_subscriber(key_expr, handler(sample))``
    （zenoh 原生 session；bundle 模式由 helper 从 client 提取）。
    """

    def __init__(
        self,
        session: Any,
        key_exprs: Sequence[str],
        output_path: str,
    ) -> None:
        self._session = session
        self._key_exprs = [k for k in key_exprs if k]
        if not self._key_exprs:
            raise ValueError("no key expressions to record")
        self._output_path = output_path
        self._frames: List[RecordedFrame] = []
        self._subs: List[Any] = []
        self._t0: Optional[int] = None
        self._start_wall_ms: int = 0
        self._closed = False

    @property
    def frames(self) -> List[RecordedFrame]:
        return list(self._frames)

    @property
    def count(self) -> int:
        return len(self._frames)

    def start(self) -> None:
        for key_expr in self._key_exprs:
            sub = self._session.declare_subscriber(
                key_expr, self._make_handler(key_expr)
            )
            self._subs.append(sub)
            logger.info("recording %s", key_expr)
        self._start_wall_ms = int(time.time() * 1000)

    def _make_handler(self, pattern: str) -> Callable[[Any], None]:
        def _handler(sample: Any) -> None:
            if self._closed:
                return
            now = time.monotonic_ns()
            if self._t0 is None:
                self._t0 = now
            topic = str(getattr(sample, "key_expr", pattern))
            payload = bytes(sample.payload)
            self._frames.append(RecordedFrame(topic, now - self._t0, payload))

        return _handler

    def save(self, path: Optional[str] = None) -> str:
        path = path or self._output_path
        payload = {
            "version": RECORD_FORMAT_VERSION,
            "meta": {
                "start_wall_ms": self._start_wall_ms,
                "end_wall_ms": int(time.time() * 1000),
                "count": len(self._frames),
                "patterns": list(self._key_exprs),
            },
            "frames": [
                {"topic": f.topic, "t": f.t, "payload": f.payload}
                for f in self._frames
            ],
        }
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(out)
        logger.info(
            "recording saved: %s (%d frames, %d topics)",
            out,
            len(self._frames),
            len({f.topic for f in self._frames}),
        )
        return str(out)

    def stop(self, save: bool = True) -> None:
        self._closed = True
        for sub in self._subs:
            try:
                sub.undeclare()
            except Exception:
                logger.debug("subscription undeclare failed", exc_info=True)
        self._subs.clear()
        if save:
            self.save()


_PICKLE_SAFE_TYPES = {
    # 记录格式允许的类型白名单：(module, name)
    ("builtins", "dict"),
    ("builtins", "list"),
    ("builtins", "tuple"),
    ("builtins", "set"),
    ("builtins", "str"),
    ("builtins", "bytes"),
    ("builtins", "int"),
    ("builtins", "float"),
    ("builtins", "bool"),
    ("builtins", "NoneType"),
}


class _SafeUnpickler(pickle.Unpickler):
    """Restricted unpickler: only allow the recording format's own types.

    记录文件仅含 dict/list/tuple/set/str/bytes/int/float/bool/None（见
    ``TopicRecorder.save`` 的 payload 结构）。``find_class`` 白名单只放行
    这些基础类型，阻断 ``os.system``/``subprocess`` 等任意类实例化
    （反序列化 RCE，Python pickle 官方文档推荐的 RestrictedUnpickler 模式）。
    """

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in _PICKLE_SAFE_TYPES:
            raise pickle.UnpicklingError(
                f"forbidden class in recording file: {module}.{name}"
            )
        return super().find_class(module, name)

    def load_reduce(self) -> None:
        # REDUCE opcode 禁止：基础类型不产生该指令；防御任意 callable 构造
        raise pickle.UnpicklingError(
            "forbidden REDUCE opcode in recording file"
        )

    def load_build(self) -> None:
        # BUILD/__setstate__ 禁止：基础类型不使用该指令（防御纵深）
        raise pickle.UnpicklingError(
            "forbidden BUILD opcode in recording file"
        )


def load_recording(path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        data = _SafeUnpickler(fh).load()
    if not isinstance(data, dict) or "frames" not in data:
        raise ValueError(f"invalid recording file: {path}")
    return data


class TopicReplayer:
    """按原始时间戳回放记录，payload 原样发布。"""

    def __init__(
        self,
        session: Any,
        path: str,
        whitelist: Optional[Sequence[str]] = None,
        blacklist: Optional[Sequence[str]] = None,
        speed: float = 1.0,
        loop: bool = False,
        skip_lead_in: bool = False,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._session = session
        self._path = path
        self._whitelist = list(whitelist or [])
        self._blacklist = list(blacklist or [])
        self._speed = speed
        self._loop = loop
        data = load_recording(path)
        raw = data["frames"]
        topics = [f["topic"] for f in raw]
        kept = set(filter_topics(topics, self._whitelist, self._blacklist))
        self.meta = data.get("meta", {})
        # 发布统计（跨 loop 轮次累计）：日志首帧 INFO，之后每 5s 汇总
        self._pub_stats = {"count": 0, "bytes": 0, "last_log": time.monotonic()}
        self.frames: List[RecordedFrame] = [
            RecordedFrame(f["topic"], f["t"], f["payload"])
            for f in raw
            if f["topic"] in kept
        ]
        self.frames.sort(key=lambda f: f.t)
        # 使能时去掉首个（过滤后）话题前的等待：首帧 t 归零立即发布
        if skip_lead_in and self.frames:
            base = self.frames[0].t
            for f in self.frames:
                f.t -= base
        skipped = len(raw) - len(self.frames)
        logger.info(
            "loaded %s: %d/%d frames (skipped %d by filter)",
            path,
            len(self.frames),
            len(raw),
            skipped,
        )

    def run(
        self,
        max_duration_s: float = 0.0,
        on_frame: Optional[Callable[[RecordedFrame], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> int:
        """阻塞回放；返回发布的帧数。speed>1 加速，loop 循环。

        ``should_stop`` 在帧间与睡眠分片中轮询——信号 handler 只置位
        的场景（Ctrl+C）也能在 gap 内及时退出，而不必睡满帧间隔。
        """
        if not self.frames:
            return 0
        deadline = time.monotonic() + max_duration_s if max_duration_s > 0 else None

        def _stop() -> bool:
            if should_stop is not None and should_stop():
                return True
            return deadline is not None and time.monotonic() >= deadline

        published = 0
        while not _stop():
            published += self._play_once(on_frame, _stop)
            if not self._loop:
                break
        return published

    def _play_once(
        self,
        on_frame: Optional[Callable[[RecordedFrame], None]],
        should_stop: Callable[[], bool],
    ) -> int:
        start = time.monotonic()
        count = 0
        for frame in self.frames:
            if should_stop():
                break
            target = start + (frame.t / 1e9) / self._speed
            delay = target - time.monotonic()
            if delay > 0:
                self._sleep_interruptible(delay, should_stop)
            if should_stop() and count > 0:
                break
            t0 = time.perf_counter()
            self._session.put(frame.topic, frame.payload)
            dt_ms = (time.perf_counter() - t0) * 1000
            count += 1
            stats = self._pub_stats
            stats["count"] += 1
            stats["bytes"] += len(frame.payload)
            now = time.monotonic()
            if stats["count"] == 1 or now - stats["last_log"] >= 5.0:
                logger.info(
                    "replay[%s] total=%d frames / %.1f KB | last: %d bytes in %.1f ms",
                    frame.topic.rsplit("/", 1)[-1],
                    stats["count"],
                    stats["bytes"] / 1024,
                    len(frame.payload),
                    dt_ms,
                )
                stats["last_log"] = now
            else:
                logger.debug(
                    "replay publish %s (%d bytes, %.1f ms)",
                    frame.topic,
                    len(frame.payload),
                    dt_ms,
                )
            if on_frame is not None:
                on_frame(frame)
        return count

    @staticmethod
    def _sleep_interruptible(
        delay: float,
        should_stop: Callable[[], bool],
        slice_s: float = 0.1,
    ) -> None:
        """分片睡眠：每片 ≤slice_s，片中响应 should_stop（Ctrl+C 及时退出）。"""
        deadline = time.monotonic() + delay
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if should_stop():
                return
            time.sleep(min(slice_s, remaining))


def open_zenoh_session(
    *,
    endpoints: Sequence[str],
    mode: str = "peer",
    endpoint_role: str = "connect",
) -> Any:
    """按 ClientConfig 风格参数构建裸 zenoh session。"""
    import zenoh

    cfg = zenoh.Config()
    cfg.insert_json5("mode", f"'{mode}'")
    eps = ", ".join(f'"{ep}"' for ep in endpoints)
    key = "listen/endpoints" if endpoint_role == "listen" else "connect/endpoints"
    cfg.insert_json5(key, f"[{eps}]")
    return zenoh.open(cfg)


def zenoh_session_from_client(client: Any) -> Any:
    """从已连接的 R2CClient 提取内部 zenoh session（bundle 模式）。"""
    transport = getattr(client, "transport", None)
    session = getattr(transport, "_session", None)
    if session is None:
        raise RuntimeError(
            "cannot extract zenoh session from client; "
            "use explicit ClientConfig endpoints instead"
        )
    return session
