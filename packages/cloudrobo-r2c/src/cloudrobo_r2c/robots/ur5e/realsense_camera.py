"""RealSense camera wrapper — runs capture in a background thread.

Each camera instance provides the latest RGB frame via get_latest_frame().
"""
import logging
import time
import threading
import numpy as np

logger = logging.getLogger(__name__)


class RealSenseCamera:
    """Intel RealSense D400 series camera.

    Usage:
        cam = RealSenseCamera(serial="123456", width=640, height=480, fps=30)
        cam.start()
        frame = cam.get_latest_frame()  # np.ndarray (H, W, 3) uint8
        cam.stop()
    """

    def __init__(
        self,
        serial: str = "",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        name: str = "camera",
    ):
        self.serial = serial
        self.width = width
        self.height = height
        self.fps = fps
        self.name = name

        self._pipeline = None
        self._align = None
        self._thread = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray = None
        self._latest_timestamp: float = 0.0
        self._running = False

    def start(self):
        import pyrealsense2 as rs

        self._pipeline = rs.pipeline()
        config = rs.config()
        if self.serial:
            config.enable_device(self.serial)
        config.enable_stream(
            rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
        )

        profile = self._pipeline.start(config)

        try:
            dev = profile.get_device()
            depth_sensor = dev.first_depth_sensor()
            if depth_sensor.supports(rs.option.emitter_enabled):
                depth_sensor.set_option(rs.option.emitter_enabled, 0)
        except Exception:
            logger.debug("realsense camera start error.", exc_info=True)

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, name=f"Camera-{self.name}", daemon=True
        )
        self._thread.start()
        print(f"[Camera:{self.name}] Started {self.width}x{self.height} @ {self.fps}fps")

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._pipeline:
            self._pipeline.stop()
        print(f"[Camera:{self.name}] Stopped")

    def get_latest_frame(self) -> np.ndarray:
        """Return the most recent RGB frame (H, W, 3) uint8 or None if no frame yet."""
        with self._frame_lock:
            return (
                self._latest_frame.copy()
                if self._latest_frame is not None
                else None
            )

    def get_latest_timestamp(self) -> float:
        return self._latest_timestamp

    def _capture_loop(self):
        import pyrealsense2 as rs

        while self._running and not self._stop_event.is_set():
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=1000)
                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue

                img = np.asanyarray(color_frame.get_data())
                ts = time.time()

                with self._frame_lock:
                    self._latest_frame = img
                    self._latest_timestamp = ts
            except Exception:
                time.sleep(0.001)
