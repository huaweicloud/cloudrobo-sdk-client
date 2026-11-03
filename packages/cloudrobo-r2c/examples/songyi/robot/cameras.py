"""USB 相机采集（OpenCV，/dev/video* 设备）。

``CameraManager`` 在 connect 时打开全部相机（fail-fast，配置错误即报错），
观测周期调用 ``read_all()`` 逐台 ``read()``；单台读取失败仅告警（去重）
并省略该路图像，不影响其余相机与其余观测字段。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import cv2
except Exception:  # pragma: no cover - opencv 为 pyproject 既有依赖
    cv2 = None


class CameraError(RuntimeError):
    pass


class CameraManager:
    """OpenCV 相机集合：head / wrist_left / wrist_right。"""

    def __init__(self, cameras_cfg: Dict[str, Any]) -> None:
        if cv2 is None:
            raise CameraError("opencv (cv2) is required for cameras")
        self._cfg = dict(cameras_cfg or {})
        self._caps: Dict[str, Any] = {}
        self._warned: set = set()
        self._failed: set = set()  # 当前读取失败未恢复（告警用）

    @property
    def names(self) -> list:
        return list(self._caps)

    def open_all(self) -> None:
        """打开全部相机；任一打开失败即抛错（fail-fast）。"""
        if not self._cfg:
            return
        for name, cfg in self._cfg.items():
            cfg = cfg if isinstance(cfg, dict) else {"path": cfg}
            path = cfg.get("path", name)
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                cap.release()
                raise CameraError(f"camera {name!r} open failed: {path!r}")
            width, height, fps = cfg.get("width"), cfg.get("height"), cfg.get("fps")
            if width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
            if height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
            if fps:
                cap.set(cv2.CAP_PROP_FPS, int(fps))
            self._caps[name] = cap
            logger.info(
                "camera %r opened (%s, %sx%s@%s)",
                name,
                path,
                width or "?",
                height or "?",
                fps or "?",
            )

    def read_all(self) -> Dict[str, Any]:
        """读取全部相机一帧，返回 {name: BGR ndarray}；失败路省略。"""
        out: Dict[str, Any] = {}
        for name, cap in self._caps.items():
            ok, frame = cap.read()
            if ok and frame is not None:
                out[name] = frame
                self._warned.discard(name)
                self._failed.discard(name)
            else:
                self._failed.add(name)
                if name not in self._warned:
                    self._warned.add(name)
                    logger.warning("camera %r read failed; image omitted", name)
        return out

    def failed_cameras(self) -> set:
        """当前读取失败未恢复的相机名集合（告警用）。"""
        return set(self._failed)

    def close_all(self) -> None:
        for name, cap in self._caps.items():
            try:
                cap.release()
            except Exception:
                logger.debug("camera %r release failed", name, exc_info=True)
        self._caps.clear()
        logger.info("all cameras released")


class TeleimagerCameraManager:
    """teleimager 后端（zmq camera_client）：head / wrist_left / wrist_right。

    read_all() 返回 {name: 原始 jpeg bytes}（**原始字节直传**，观测上
    传不再解码再压缩）；单路取帧为 None/失败时省略（告警去重），不影
    响其余相机。
    """

    _FETCHERS = (
        ("head", "get_head_frame_raw"),
        ("wrist_left", "get_left_wrist_frame_raw"),
        ("wrist_right", "get_right_wrist_frame_raw"),
    )

    def __init__(self, cfg: Dict[str, Any], client: Any = None) -> None:
        if client is None:
            from .teleimager_client import ImageClientInterface

            client = ImageClientInterface(
                camera_interface_bin=cfg.get("camera_interface_bin",
                                             "./camera_client.bin"),
                host=cfg.get("host", "127.0.0.1"),
                jpeg_quality = int(cfg.get("jpeg_quality", 85)),
                request_port=int(cfg.get("request_port", 60000)),
                camera_interface_port=int(cfg.get("interface_port", 60002)),
                start_process=bool(cfg.get("start_process", True)),
                ready_timeout=float(cfg.get("ready_timeout", 15.0)),
                timeout_ms=int(cfg.get("timeout_ms", 3000)),
            )
        self._client = client
        self._warned: set = set()
        self._failed: set = set()  # 当前读取失败未恢复（告警用）
        self._retry_after = 5.0  # 无服务冷却期：期间跳过调用，观测循环不阻塞
        self._last_fail = 0.0

    @property
    def names(self) -> list:
        return [name for name, _ in self._FETCHERS]

    def open_all(self) -> None:
        """软失败：服务未就绪仅告警降级（read_all 返回空），后续自愈重试。"""
        try:
            cfg = self._client.get_cam_config()
        except Exception as e:
            self._last_fail = time.monotonic()
            logger.warning(
                "teleimager unavailable at open (%s); images omitted "
                "until it responds", e,
            )
            return
        logger.info("teleimager cameras opened: config keys=%s", list(cfg or {}))

    def read_all(self) -> Dict[str, Any]:
        now = time.monotonic()
        if now - self._last_fail < self._retry_after:
            return {}  # 冷却期：跳过调用，避免每帧等超时阻塞观测循环
        out: Dict[str, Any] = {}
        failed = False
        for name, method in self._FETCHERS:
            try:
                img, _fps = getattr(self._client, method)()
            except Exception as e:
                failed = True
                self._failed.add(name)
                if name not in self._warned:
                    self._warned.add(name)
                    logger.warning("teleimager %r read failed: %s", name, e)
                continue
            if img is not None:
                out[name] = img
                self._warned.discard(name)
                self._failed.discard(name)
            else:
                self._failed.add(name)
                if name not in self._warned:
                    self._warned.add(name)
                    logger.warning("teleimager %r frame None; image omitted", name)
        if failed:
            self._last_fail = now
        return out

    def failed_cameras(self) -> set:
        """当前读取失败未恢复的相机名集合（告警用；冷却期内保持）。"""
        return set(self._failed)

    def close_all(self) -> None:
        try:
            self._client.close()
        except Exception:
            logger.debug("teleimager close failed", exc_info=True)


def to_jpeg_bytes(value: Any, quality: int = 95) -> bytes:
    """观测图片统一为 jpeg bytes：bytes 原样直传（原始字节，不解码再
    压缩）；ndarray（cv2 后端原始帧）就地编码为 jpeg。"""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if cv2 is None:
        raise CameraError("opencv (cv2) is required to encode images")
    ok, buf = cv2.imencode(
        ".jpg", value, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    if not ok:
        raise CameraError("jpeg encode failed")
    return buf.tobytes()


def from_jpeg_bytes(value: Any) -> Any:
    """推流帧槽归一化：bytes/bytearray → BGR ndarray；ndarray 原样返回。

    teleimager 后端 ``read_all()`` 返回原始 jpeg bytes（观测上传直传），
    但推流侧 ``CameraCapture`` 需要 BGR ndarray——采集线程写入帧槽前
    统一解码为 ndarray（推理侧 bytes 直传仍走 ``_latest_raw`` 缓存）。
    """
    if isinstance(value, (bytes, bytearray)):
        if cv2 is None:
            raise CameraError("opencv (cv2) is required to decode images")
        import numpy as np

        return cv2.imdecode(np.frombuffer(value, dtype=np.uint8), cv2.IMREAD_COLOR)
    return value


def build_camera_manager(cameras_cfg: Dict[str, Any]):
    """按 cameras.backend 构造采集后端：'cv2'（默认）/ 'teleimager'。

    cv2 后端沿用旧结构 {name: {path, width, height, fps, ...}}；
    teleimager 后端为 {backend: teleimager, host, request_port,
    interface_port, camera_interface_bin, start_process}。
    """
    backend = str((cameras_cfg or {}).get("backend", "cv2")).lower()
    if backend == "teleimager":
        return TeleimagerCameraManager(dict(cameras_cfg))
    devices = {k: v for k, v in (cameras_cfg or {}).items() if k != "backend"}
    return CameraManager(devices)
