"""OpenCV camera wrapper — runs capture in a background thread.

Each camera instance provides the latest RGB frame via get_latest_frame().
"""

import time
import threading
import numpy as np


class OpenCVCamera:
    """USB / built-in camera accessed via OpenCV VideoCapture.

    Usage:
        cam = OpenCVCamera(name="wrist", index_or_path=0, width=640, height=480, fps=30)
        cam.start()
        frame = cam.get_latest_frame()  # np.ndarray (H, W, 3) uint8 or None
        cam.stop()
    """

    def __init__(
        self,
        name: str,
        index_or_path: object = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ):
        self.name = name
        self.index_or_path = index_or_path
        self.width = width
        self.height = height
        self.fps = fps

        self._cap = None
        self._thread = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray = None
        self._latest_timestamp: float = 0.0
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        import cv2

        self._cap = cv2.VideoCapture(self.index_or_path)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Failed to open camera {self.name!r} at {self.index_or_path!r}"
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
        self._cap.set(cv2.CAP_PROP_FPS, float(self.fps))

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"Camera-{self.name}",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[Camera:{self.name}] Started {self.width}x{self.height} "
            f"@ {self.fps}fps"
        )

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        print(f"[Camera:{self.name}] Stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_latest_frame(self) -> np.ndarray:
        """Return the most recent frame (H, W, 3) uint8 or None."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def get_latest_timestamp(self) -> float:
        return self._latest_timestamp

    # ------------------------------------------------------------------
    # Background capture loop
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(0.001)
                continue

            ts = time.time()
            with self._frame_lock:
                self._latest_frame = frame
                self._latest_timestamp = ts
