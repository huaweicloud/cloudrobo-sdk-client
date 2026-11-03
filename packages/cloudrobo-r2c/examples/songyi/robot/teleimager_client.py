"""Teleimager 图像客户端（zmq REQ ↔ camera_client.bin / 已运行实例）。

接口与 teleimager/demo/camera_client_interface.py 一致：
  - get_cam_config() / get_head_frame() / get_left_wrist_frame() /
    get_right_wrist_frame() / get_head_depth_frame() / close()
  - 彩色帧为 BGR ndarray（jpeg 解码），深度帧为 uint16 ndarray

SDK 化差异：
  - start_process=False 时只连已运行的 camera_client（不启动二进制）；
  - zmq 延迟导入，未安装 pyzmq 时抛 CameraError 提示。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any, Optional, Tuple

from .cameras import CameraError

logger = logging.getLogger(__name__)


class ImageClientInterface:
    """camera_client 的 Python 封装（REQ json + 二进制帧）。"""

    def __init__(
        self,
        camera_interface_bin: str = "./camera_client.bin",
        host: str = "127.0.0.1",
        request_port: int = 60000,
        camera_interface_port: int = 60002,
        jpeg_quality: int = 85,
        start_process: bool = True,
        ready_timeout: float = 15.0,
        timeout_ms: int = 3000,
    ) -> None:
        try:
            import zmq
        except Exception as e:
            raise CameraError(
                "teleimager backend requires pyzmq (pip install pyzmq)"
            ) from e

        self._camera_interface_bin = os.path.abspath(camera_interface_bin)
        self._camera_interface_port = camera_interface_port
        self._process: Optional[subprocess.Popen] = None
        self._cam_config: Optional[dict] = None

        self._zmq = zmq
        self._timeout_ms = int(timeout_ms)
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REQ)
        self._socket.setsockopt(zmq.LINGER, 0)
        # 收发超时：服务端不存在时 send/recv 不再永久阻塞（C 层阻塞会
        # 吞掉 Ctrl+C，必须靠 socket 超时退出）
        self._socket.setsockopt(zmq.SNDTIMEO, self._timeout_ms)
        self._socket.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        self._socket.connect(f"tcp://127.0.0.1:{camera_interface_port}")

        if start_process:
            if not os.path.isfile(self._camera_interface_bin):
                raise CameraError(
                    f"camera_interface bin 不存在: {self._camera_interface_bin}"
                    "（或改用 start_process=False 连接已运行实例）"
                )
            self._process = subprocess.Popen(
                [
                    self._camera_interface_bin,
                    "--host", str(host),
                    "--request-port", str(request_port),
                    "--interface-port", str(camera_interface_port),
                    "--jpeg-quality", str(jpeg_quality),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._wait_ready(timeout=ready_timeout)

    def _wait_ready(self, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        last_line = ""
        while time.monotonic() < deadline:
            if self._process is None or self._process.stdout is None:
                break
            line = self._process.stdout.readline().decode(errors="replace").strip()
            if line.startswith("READY:"):
                return
            if line:
                last_line = line
                continue
            if self._process.poll() is not None:
                break
            time.sleep(0.05)
        raise CameraError(
            f"CameraInterface 启动失败（{timeout:.0f}s 内未就绪）: {last_line!r}"
        )

    def _reset_socket(self) -> None:
        """重建 REQ socket：超时/断连后 REQ 状态机可能卡在
        waiting-for-reply（下次 send 抛 EFSM），必须重建才能自愈。"""
        try:
            if self._socket is not None:
                self._socket.close(linger=0)
        except Exception:
            logger.debug("zmq socket close failed", exc_info=True)
        zmq = self._zmq
        self._socket = self._context.socket(zmq.REQ)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDTIMEO, self._timeout_ms)
        self._socket.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        self._socket.connect(
            f"tcp://127.0.0.1:{self._camera_interface_port}"
        )

    def _request(self, cmd: str):
        try:
            self._socket.send_json({"cmd": cmd})
            meta = self._socket.recv_json()
        except self._zmq.Again as e:
            self._reset_socket()
            raise CameraError(
                f"camera_client 无响应（超时 {self._timeout_ms}ms，"
                f"端口 {self._camera_interface_port}）——确认服务已启动"
            ) from e
        except self._zmq.ZMQError as e:
            # EFSM 等状态机错误（对端断连/半途超时残留）：重建后自愈
            self._reset_socket()
            raise CameraError(
                f"camera_client 连接异常已重置（{e}）——等待服务恢复"
            ) from e
        payload = None
        if self._socket.getsockopt(self._zmq.RCVMORE):
            payload = self._socket.recv()
        if not meta.get("ok"):
            raise RuntimeError(
                f"camera_interface 命令 {cmd} 失败: {meta.get('error', 'unknown error')}"
            )
        return meta, payload

    def get_cam_config(self) -> dict:
        if self._cam_config is None:
            meta, _ = self._request("get_cam_config")
            self._cam_config = meta.get("config")
        return self._cam_config

    def _decode_color(self, meta: dict, payload: Optional[bytes]) -> Tuple[Any, float]:
        import cv2
        import numpy as np

        if payload is None:
            return None, float(meta.get("fps", 0.0))
        img = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img, float(meta.get("fps", 0.0))

    def get_head_frame(self) -> Tuple[Any, float]:
        """头部彩色图像 (BGR ndarray, fps)。"""
        meta, payload = self._request("get_head_frame")
        return self._decode_color(meta, payload)

    def get_left_wrist_frame(self) -> Tuple[Any, float]:
        """左腕彩色图像 (BGR ndarray, fps)。"""
        meta, payload = self._request("get_left_wrist_frame")
        return self._decode_color(meta, payload)

    def get_right_wrist_frame(self) -> Tuple[Any, float]:
        """右腕彩色图像 (BGR ndarray, fps)。"""
        meta, payload = self._request("get_right_wrist_frame")
        return self._decode_color(meta, payload)

    # ── 原始字节通道：观测上传直传 jpeg payload（不解码再压缩）──────

    def get_head_frame_raw(self) -> Tuple[Optional[bytes], float]:
        """头部彩色图像原始 jpeg 字节 (payload | None, fps)。"""
        meta, payload = self._request("get_head_frame")
        return payload, float(meta.get("fps", 0.0))

    def get_left_wrist_frame_raw(self) -> Tuple[Optional[bytes], float]:
        """左腕彩色图像原始 jpeg 字节 (payload | None, fps)。"""
        meta, payload = self._request("get_left_wrist_frame")
        return payload, float(meta.get("fps", 0.0))

    def get_right_wrist_frame_raw(self) -> Tuple[Optional[bytes], float]:
        """右腕彩色图像原始 jpeg 字节 (payload | None, fps)。"""
        meta, payload = self._request("get_right_wrist_frame")
        return payload, float(meta.get("fps", 0.0))

    def get_head_depth_frame(self) -> Tuple[Any, float]:
        """头部深度图 (uint16 ndarray, fps)。"""
        import cv2
        import numpy as np

        meta, payload = self._request("get_head_depth_frame")
        if payload is None:
            return None, float(meta.get("fps", 0.0))
        depth = cv2.imdecode(
            np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_UNCHANGED
        )
        return depth, float(meta.get("fps", 0.0))

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.send_json({"cmd": "close"})
                try:
                    self._socket.recv()
                except Exception:
                    logger.debug("camera close recv failed", exc_info=True)
            except Exception:
                logger.debug("camera close send failed", exc_info=True)
        if self._socket is not None:
            try:
                self._socket.close(linger=0)
            except Exception:
                logger.debug("camera socket close failed", exc_info=True)
            self._socket = None
        try:
            self._context.term()
        except Exception:
            logger.debug("zmq context term failed", exc_info=True)
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    logger.debug("camera process kill failed", exc_info=True)
            self._process = None
