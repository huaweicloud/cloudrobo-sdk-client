"""JWT renewal manager for LiveKit streaming tokens.

SDK 利用 CAMERA_START payload 中的 ``expires_in`` 与 ``renewal_before``
在本地设定时器，在 JWT 过期前 ``renewal_before`` 秒主动通过 Zenoh publish
到 ``{project_id}/{device_id}/device/token_renew_request`` 请求续签。
pilot-datacollector 响应后通过 ``job/command`` topic 下发
``RTC_TOKEN_RENEW`` 命令 + 新 JWT，SDK 订阅接收后预加载（不重连）。

SDK 主动请求优势：SDK 本地推流状态 100% 准确（SDK 崩溃则定时器不触发
不请求），collector 无需维护推流状态列表。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from typing import Callable, Optional

from cloudrobo_r2c.common.models import TokenRenewRequest

logger = logging.getLogger(__name__)


class JwtRenewalManager:
    """JWT 定时续签管理器。

    Args:
        session: R2CSession（提供 transport.publish / project_id / device_id）。
        on_token_renewed: 收到新 JWT 后的回调（参数: new_jwt, expires_in,
            renewal_before）。
    """

    def __init__(
        self,
        session,
        on_token_renewed: Optional[Callable[[str, int, int], None]] = None,
        on_renew_failed: Optional[Callable[[], None]] = None,
        on_renew_succeeded: Optional[Callable[[], None]] = None,
    ):
        self._session = session
        self._on_token_renewed = on_token_renewed
        self._on_renew_failed = on_renew_failed
        self._on_renew_succeeded = on_renew_succeeded
        self._timer: Optional[threading.Timer] = None
        self._current_jwt: Optional[str] = None
        self._expires_in: int = 0
        self._renewal_before: int = 600
        self._camera_id: str = "all"
        self._jti: Optional[str] = None
        self._lock = threading.Lock()
        self._running = False

    def start(
        self,
        jwt: str,
        expires_in: int,
        renewal_before: int,
        camera_id: str,
    ) -> None:
        """启动 JWT 续签定时器。

        Args:
            jwt: 当前推流 JWT。
            expires_in: JWT 有效期（秒）。
            renewal_before: 续签窗口（秒）；0/None 时默认 600。
            camera_id: 当前推流的 camera_id。
        """
        with self._lock:
            self._current_jwt = jwt
            self._expires_in = int(expires_in) if expires_in else 0
            self._renewal_before = (
                int(renewal_before) if renewal_before else 600
            )
            self._camera_id = camera_id
            self._jti = self._extract_jti(jwt)
            self._running = True

            # 续签时机：签发后 expires_in - renewal_before 秒
            delay = max(self._expires_in - self._renewal_before, 60)
            self._schedule_timer(delay)
            logger.info(
                "JWT renewal timer started: jti=%s renewal_in=%.0fs",
                self._jti,
                delay,
            )

    def _schedule_timer(self, delay: float) -> None:
        """调度下一个续签定时器（先取消旧定时器，避免多个 timer 并行）。

        调用方须已持有 :attr:`_lock`（start / on_token_renewed / _do_renew
        均在锁内调用）——本方法不再自行获取锁，避免 Lock（非重入）死锁。
        """
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        timer = threading.Timer(delay, self._do_renew)
        timer.daemon = True
        timer.name = "r2c-sdk-jwt-renew"
        self._timer = timer
        timer.start()

    def _do_renew(self) -> None:
        """定时器到期，发送续签请求。"""
        with self._lock:
            if not self._running:
                return
            old_jti = self._jti
            camera_id = self._camera_id

        if not old_jti:
            logger.error("Cannot renew JWT: jti not available")
            return

        # Zenoh publish 续签请求（protobuf 序列化；source 由上层填充）
        topic = (
            f"{self._session.project_id}/{self._session.device_id}/"
            f"device/token_renew_request"
        )
        request = TokenRenewRequest(old_jti=old_jti, camera_id=camera_id)
        request.source = f"{self._session.project_id}/{self._session.device_id}"

        try:
            self._session.transport.publish(topic, request.serialize())
            logger.info("JWT renew request sent: old_jti=%s", old_jti)
            logger.debug(
                "token_renew_request TX: topic=%s payload=%s",
                topic,
                request.to_dict(),
            )
        except Exception:
            logger.error(
                "Failed to send JWT renew request: old_jti=%s; "
                "retrying in 60s",
                old_jti,
                exc_info=True,
            )
            # 续签失败 → 通知上层激活 JWT 续签告警（去抖：连续失败才报）
            if self._on_renew_failed is not None:
                self._on_renew_failed()
            # 重试：60s 后重试
            with self._lock:
                if self._running:
                    self._schedule_timer(60)

    def on_token_renewed(
        self,
        new_jwt: str,
        expires_in: int,
        renewal_before: int,
    ) -> None:
        """收到 RTC_TOKEN_RENEW 响应，更新 JWT 缓存并设下一次定时器。"""
        with self._lock:
            if not self._running:
                return
            self._current_jwt = new_jwt
            self._expires_in = int(expires_in) if expires_in else 0
            self._renewal_before = (
                int(renewal_before) if renewal_before else 600
            )
            self._jti = self._extract_jti(new_jwt)

        # 回调通知上层更新 JWT 缓存（新 JWT 预加载，不重连）
        if self._on_token_renewed is not None:
            self._on_token_renewed(new_jwt, self._expires_in, self._renewal_before)

        # 续签成功 → 通知上层消除 JWT 续签告警
        if self._on_renew_succeeded is not None:
            self._on_renew_succeeded()

        # 设定下一次续签定时器
        delay = max(self._expires_in - self._renewal_before, 60)
        with self._lock:
            if self._running:
                self._schedule_timer(delay)
                logger.info(
                    "JWT renewed: new_jti=%s next_renewal_in=%.0fs",
                    self._jti,
                    delay,
                )

    def stop(self) -> None:
        """停止续签定时器，清除 JWT 缓存。"""
        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._current_jwt = None
            self._jti = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def current_jwt(self) -> Optional[str]:
        """当前缓存的最新 JWT（wss 重连时使用）。"""
        with self._lock:
            return self._current_jwt

    @staticmethod
    def _extract_jti(jwt: str) -> Optional[str]:
        """从 JWT payload 中提取 jti（不验签，仅解析）。"""
        try:
            parts = jwt.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            # 补齐 base64 padding
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            return payload.get("jti")
        except Exception:
            return None
