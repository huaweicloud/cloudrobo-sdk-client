"""Native WebSocket policy client (all ``ws://`` / ``wss://``).

Fully openpi-free: every WebSocket connection uses the native
``websockets.sync.client`` with msgpack (numpy-aware) serialization and an
optional TLS context for ``wss://``. Probe liveness is activated at connect
time by ``protocol.probe``, and ``close()`` is designed to interrupt a
long-running blocked ``recv`` wait.

Encryption upgrade: the same client serves both ``ws://`` and ``wss://`` —
the scheme of ``protocol.endpoint`` decides whether TLS is applied, so a
``type: ws`` or ``type: wss`` route can point at either a plain or encrypted
endpoint without any client-side change.

Reference: ``se_commit/.../skill_engine/scripts/inference_client.py`` — the
connect-time ``recv()`` (line ~179) that confirms model liveness, gated here by
``protocol.probe``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from cloudrobo_r2c.core.interfaces import IPolicyClient
from cloudrobo_r2c.core.transformers import msgpack_pack, msgpack_unpack
from cloudrobo_r2c.inference.route_config import ProtocolConfig
from cloudrobo_r2c.inference.tls import create_ssl_context
import websockets.sync.client
import websockets.exceptions

logger = logging.getLogger(__name__)

# Poll interval for the interruptible recv loop. We avoid one unbounded blocking
# ``recv()`` so ``close()`` can tear down a 60-minute-scale wait.
_RECV_POLL_INTERVAL = 0.25

# Liveness-probe wait bound (seconds). During construction there is no
# concurrent handle to call ``close()``, so the probe fails fast instead of
# blocking forever when the model is not ready.
_PROBE_TIMEOUT = 5.0


class WebSocketPolicyClient(IPolicyClient):
    """WebSocket-based policy client using native `websockets.sync.client`.

    - Serialization: numpy-aware msgpack via ``PAYLOAD_CODECS``.
    - TLS: ``wss://`` endpoints get a self-signed-tolerant ``SSLContext``.
    - Probe: when ``protocol.probe`` is True, a single ``recv()`` is performed
      right after connect to confirm model liveness (data discarded).
    - Long-wait safety: ``close()`` can be called from another thread to
      interrupt a blocked ``recv`` (via flag + bounded-timout polling).
    """

    def __init__(
        self,
        protocol: ProtocolConfig,
        api_key: Optional[str] = None,
    ) -> None:
        self._protocol = protocol
        self._api_key = api_key
        # 连接策略控制连接的建立与释放时机：
        #   eager         —— 启动即建连，长驻
        #   lazy_resident —— 首次 infer 时才建连，连接后长驻
        #   lazy_fresh    —— 每次 infer 才建连，推理完成后断开（默认）
        self._connect_strategy = getattr(protocol, "connect_strategy", "lazy_fresh")
        self._client: Optional[Any] = None
        self._closed = False
        # eager：构造时即建立连接（与原先行为一致）。
        # lazy_resident / lazy_fresh：延迟到首次 infer 时再建连。
        if self._connect_strategy == "eager":
            self._connect()
            self._run_probe_if_needed()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect_if_needed(self) -> None:
        """若当前未建连则建立连接并执行探活（惰性拉取连接）。

        每次真正新建连接后都会执行 ``_run_probe_if_needed()``，
        保证 lazy_fresh 每次重连后同样会 recv 探活（数据丢弃）。
        从断开状态重连时重置 ``_closed`` 标志，使新的连接可正常收发。
        """
        if self._client is None:
            self._closed = False
            logger.info("Ws client connecting.")
            self._connect()
            self._run_probe_if_needed()
            logger.info("Ws client connected.")

    def _connect(self) -> None:
        """Establish the WebSocket connection (aligned with inference_client.py).

        TLS is applied automatically when the endpoint uses the ``wss://``
        scheme (case-insensitive); a ``ws://`` endpoint stays plain. This lets
        one client handle both plain and encrypted connections, and the
        ``protocol.type`` (``ws`` or ``wss``) need not match the URL scheme.
        """

        url = self._protocol.endpoint or ""
        headers: Optional[Dict[str, str]] = None
        if self._api_key:
            headers = {"Authorization": f"Bearer {self._api_key}"}
        ssl_ctx = create_ssl_context() if url.lower().startswith("wss://") else None

        try:
            self._client = websockets.sync.client.connect(
                url,
                compression=None,
                max_size=None,
                additional_headers=headers,
                ssl=ssl_ctx,
            )
        except Exception as exc:
            # 建联失败：记录明确错误并抛出带原因的 ConnectionError（配置层可重试）。
            logger.error("Failed to connect to inference service: %s", exc)
            raise ConnectionError(
                f"Failed to connect to inference service: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Probe (仅建联时探活，不做周期性心跳)
    # ------------------------------------------------------------------

    def _run_probe_if_needed(self) -> None:
        """probe=True 时，建联后执行一次 recv 确认模型存活（数据直接丢弃）。

        探活由 ``protocol.probe`` 决定是否激活：False 时跳过，不做任何探测。
        这是一次有界等待（``_PROBE_TIMEOUT``）：建联阶段没有并发句柄可调用
        ``close()``，所以 recv 超时/异常时应快速失败并抛出带原因的
        ConnectionError，直接暴露「模型不可用」（配置层可重试）。
        """
        if not getattr(self._protocol, "probe", False):
            return
        if self._client is None:
            raise RuntimeError("WebSocket client not connected for probe recv")
        try:
            # 读取一帧确认存活，数据直接丢弃；超时即认为模型未就绪。
            self._client.recv(timeout=_PROBE_TIMEOUT)  # noqa: B018 - probe, discard result
        except Exception as exc:
            logger.error("Inference service not ready (probe failed): %s", exc)
            raise ConnectionError(
                f"Inference service not ready (probe failed): {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Interruptible recv (长期等待可被 close 中断)
    # ------------------------------------------------------------------

    def _interruptible_recv(self) -> Any:
        """Receive one frame while allowing ``close()`` to interrupt a long wait.

        Instead of one unbounded blocking ``recv()``, we poll with a bounded
        timeout and check ``self._closed`` each iteration. ``close()`` sets the
        flag (and closes the underlying connection), so a recv blocked for up
        to 60 minutes can be torn down promptly from another thread.
        """
        while True:
            if self._closed:
                raise RuntimeError("WebSocket client closed during recv")
            try:
                return self._client.recv(timeout=_RECV_POLL_INTERVAL)
            except TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosedOK:
                continue

    # ------------------------------------------------------------------
    # IPolicyClient
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Ensure the WebSocket connection is established. Idempotent.

        No-op when already connected; lazily (re)establishes the connection
        (and runs the liveness probe) after construction or after ``close()``.
        """
        self._connect_if_needed()

    def infer(self, payload: Any) -> Dict[str, Any]:
        """msgpack-pack payload, send, then receive & unpack the response.

        A string response indicates a service-side error and raises RuntimeError.

        连接生命周期由 ``connect_strategy`` 控制：
          - lazy_fresh：每次 infer 前若未建连则建连（含 probe），推理完成后断开，
            下次 infer 再重新建连。
          - eager / lazy_resident：建连后常驻，推理完成后保持连接。
        """
        self._connect_if_needed()
        try:
            self._client.send(payload)
        except Exception as e:
            # 发送失败：关闭连接并直接抛出异常，交由上层调用方决定是否重试。
            # 不自动重连重发，避免同一 payload 被服务端重复处理（幂等性问题）。
            self.close()
            raise e

        try:
            response = self._interruptible_recv()
            # 字符串响应表示服务端错误
            if isinstance(response, str):
                self.close()
                raise RuntimeError(f"Inference service error: {response}")
        except Exception as e:
            self.close()
            raise e
        else:
            # lazy_fresh：推理完成后断开连接（下次 infer 再建连）
            if self._connect_strategy == "lazy_fresh":
                self.close()
        return response

    def close(self) -> None:
        """Close the connection and allow an in-flight blocked recv to exit.

        Sets the closed flag first so the interruptible recv loop terminates,
        then closes the underlying websocket connection. Idempotent.
        """
        self._closed = True
        if self._client is not None:
            close_method = getattr(self._client, "close", None)
            if close_method:
                logger.info("Ws client closing.")
                try:
                    close_method()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Error closing websocket: %s", exc)
            self._client = None
            logger.info("Ws client closed.")
