"""RealSense camera wrapper — runs capture in a background thread.

Each camera instance provides the latest RGB frame via get_latest_frame().
Shared by hardware adapters (ur5e, dummy, ...) so that a single capture
thread owns the device and the LiveKit streaming side only reads the frame
slot. V4L2 and RealSense cannot open the same L515 concurrently
(``errno=16 Device or resource busy``), which is why the device must be
opened exactly once, here.

Import path history: this module used to live at
``cloudrobo_r2c.robots.ur5e.realsense_camera``; that path is kept as a re-export
shim for backwards compatibility.
"""
import logging
import time
import threading
from typing import Any, Mapping, Optional

import numpy as np

logger = logging.getLogger(__name__)


def normalize_serial(serial: Any) -> str:
    """Normalize a RealSense serial for matching across sources.

    The same unit is reported as ``00000000F1180540`` by sysfs and
    ``f1180540`` by pyrealsense2. Compare on the trailing 8 hex chars,
    lowercased, so either form (or a full ``f1180540``) matches.
    """
    text = str(serial or "").strip().lower()
    if not text:
        return ""
    return text[-8:]


class RealSenseCamera:
    """Intel RealSense camera (D400 / L500 series).

    Usage:
        cam = RealSenseCamera(serial="123456", width=640, height=480, fps=30)
        cam.start()
        frame = cam.get_latest_frame()  # np.ndarray (H, W, 3) uint8
        cam.stop()

    Note:
        The requested width/height/fps are best-effort. L515 units with older
        firmware (e.g. 1.5.4.1) do not expose a 640x480 color mode; an
        unsupported request makes ``pipeline.start()`` raise
        "Couldn't resolve requests". ``start()`` therefore probes the device's
        actual color profiles and falls back to the closest supported mode
        (see ``_resolve_video_mode``).
    """

    # Fallback preference when the requested mode is unsupported, best first.
    _FALLBACK_MODES = [
        (640, 480),
        (640, 360),
        (960, 540),
        (1280, 720),
        (1920, 1080),
    ]

    # rs.option name -> attribute, applied to the RGB sensor in start().
    # Only options the sensor actually reports via supports() are set.
    _OPTION_MAP = {
        "exposure": "exposure",
        "gain": "gain",
        "brightness": "brightness",
        "contrast": "contrast",
        "gamma": "gamma",
        "sharpness": "sharpness",
        "saturation": "saturation",
        "white_balance": "white_balance",
        "backlight_compensation": "backlight_compensation",
        "power_line_frequency": "power_line_frequency",
        "enable_auto_exposure": "enable_auto_exposure",
    }

    def __init__(
        self,
        serial: str = "",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        name: str = "camera",
        controls: Optional[Mapping[str, Any]] = None,
    ):
        self.serial = serial
        self.width = width
        self.height = height
        self.fps = fps
        self.name = name
        # rs.option settings applied to the RGB sensor (e.g. exposure=100).
        self.controls = dict(controls or {})

        self._pipeline = None
        self._align = None
        self._thread = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._running = False

    # ── device lookup ───────────────────────────────────────────────────────

    def _find_device(self, rs: Any):
        """Return the rs.device matching ``self.serial``, or None.

        An empty serial matches the first available device. Matching is done
        on the normalized trailing-8 form so ``F1180540`` and ``f1180540``
        both resolve to the same unit.
        """
        devices = rs.context().query_devices()
        if not self.serial:
            return devices[0] if len(devices) else None

        want = normalize_serial(self.serial)
        for dev in devices:
            if normalize_serial(dev.get_info(rs.camera_info.serial_number)) == want:
                return dev
        return None

    def _resolve_video_mode(self, rs: Any, device: Any):
        """Return a (width, height, fps) color mode the device actually supports.

        Probes the device's color bgr8 stream profiles. Returns the requested
        mode unchanged when it is supported (or when profiles cannot be
        queried, so the caller keeps the original error semantics). Otherwise
        picks the closest fallback by resolution, preferring the requested fps
        and falling back to the nearest available rate.

        L515 units on old firmware expose no 640x480 color mode, which would
        otherwise make pipeline.start() fail with "Couldn't resolve requests".
        """
        try:
            if device is None:
                return (self.width, self.height, self.fps)

            # Collect supported (w, h) -> set of fps for color bgr8.
            modes: dict = {}
            for sensor in device.sensors:
                for prof in sensor.get_stream_profiles():
                    video = prof.as_video_stream_profile()
                    if (
                        video.stream_type() == rs.stream.color
                        and video.format() == rs.format.bgr8
                    ):
                        modes.setdefault((video.width(), video.height()), set()).add(
                            video.fps()
                        )
            if not modes:
                return (self.width, self.height, self.fps)

            if (self.width, self.height) in modes:
                fps_set = modes[(self.width, self.height)]
                if self.fps in fps_set:
                    return (self.width, self.height, self.fps)
                # Resolution is fine, fps is not: clamp to the nearest rate.
                best_fps = min(fps_set, key=lambda f: (abs(f - self.fps), -f))
                return (self.width, self.height, best_fps)

            # Resolution unsupported: pick nearest by preference order, then area.
            order = {mode: i for i, mode in enumerate(self._FALLBACK_MODES)}

            def _fallback_key(wh):
                return (
                    order.get(wh, len(order)),
                    abs(wh[0] * wh[1] - self.width * self.height),
                )

            width, height = min(modes, key=_fallback_key)
            fps_set = modes[(width, height)]
            best_fps = min(fps_set, key=lambda f: (abs(f - self.fps), -f))
            return (width, height, best_fps)
        except Exception:
            logger.debug(
                "[Camera:%s] video mode probe failed, using requested mode",
                self.name, exc_info=True,
            )
            return (self.width, self.height, self.fps)

    def _apply_controls(self, rs: Any, device: Any) -> None:
        """Apply configured rs.option values to the device's RGB sensor.

        RealSense RGB sensors expose options (exposure/gain/brightness/...) that
        the UVC/V4L2 path cannot reach, so unlike the V4L2 path no CLAHE
        post-processing is needed for a correctly exposed color image.
        Unsupported options and rejected values are logged at debug level
        only — a driver that does not implement an option must not break
        startup.
        """
        if not self.controls:
            return

        # Prefer the RGB/color sensor; fall back to every sensor.
        candidates = []
        try:
            for sensor in device.sensors:
                try:
                    if sensor.get_info(rs.camera_info.name) == "RGB Camera":
                        candidates = [sensor]
                        break
                except Exception:
                    logger.debug("sensor name query failed", exc_info=True)
                    continue
        except Exception:
            logger.debug("sensor enumeration failed", exc_info=True)
        if not candidates:
            candidates = list(device.sensors)

        for key, value in self.controls.items():
            opt_name = self._OPTION_MAP.get(str(key))
            if opt_name is None or value is None:
                if opt_name is None:
                    logger.debug(
                        "[Camera:%s] unknown realsense control %r ignored",
                        self.name, key,
                    )
                continue
            option = getattr(rs.option, opt_name, None)
            if option is None:
                continue
            for sensor in candidates:
                try:
                    if not sensor.supports(option):
                        continue
                    sensor.set_option(option, float(value))
                    logger.info(
                        "[Camera:%s] set %s=%s", self.name, opt_name, value
                    )
                except Exception:
                    logger.debug(
                        "[Camera:%s] failed to set %s=%r",
                        self.name, opt_name, value, exc_info=True,
                    )

    # ── lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        import pyrealsense2 as rs

        device = self._find_device(rs)
        if self.serial and device is None:
            available = [
                normalize_serial(d.get_info(rs.camera_info.serial_number))
                for d in rs.context().query_devices()
            ]
            raise RuntimeError(
                f"[Camera:{self.name}] RealSense serial {self.serial!r} not found "
                f"(available: {available})"
            )

        requested = (self.width, self.height, self.fps)
        self.width, self.height, self.fps = self._resolve_video_mode(rs, device)
        if (self.width, self.height, self.fps) != requested:
            logger.warning(
                "[Camera:%s] requested %dx%d@%d unsupported; using %dx%d@%d",
                self.name, *requested, self.width, self.height, self.fps,
            )

        # enable_device() requires the exact serial string as reported by
        # pyrealsense2 ("f1180540") — the sysfs/config spellings
        # ("F1180540", "00000000F1180540") are rejected with
        # "No device connected". Use the matched device's own serial.
        found_serial = ""
        if device is not None:
            try:
                found_serial = device.get_info(rs.camera_info.serial_number)
            except Exception:
                found_serial = ""

        self._pipeline = rs.pipeline()
        config = rs.config()
        if found_serial:
            config.enable_device(found_serial)
        elif self.serial:
            config.enable_device(self.serial)
        config.enable_stream(
            rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
        )

        profile = self._pipeline.start(config)
        profile_device = profile.get_device()

        # L515: disable the depth emitter unless the adapter asked otherwise
        # (the RGB stream does not need it, and it saves power/thermal budget).
        try:
            depth_sensor = profile_device.first_depth_sensor()
            if depth_sensor.supports(rs.option.emitter_enabled):
                depth_sensor.set_option(rs.option.emitter_enabled, 0)
        except Exception:
            logger.debug(
                "[Camera:%s] emitter disable skipped", self.name, exc_info=True
            )

        self._apply_controls(rs, profile_device)

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, name=f"Camera-{self.name}", daemon=True
        )
        self._thread.start()
        logger.info(
            "[Camera:%s] started %dx%d @ %dfps (serial=%s)",
            self.name, self.width, self.height, self.fps, self.serial or "<first>",
        )

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        if self._pipeline:
            try:
                self._pipeline.stop()
            except Exception:
                logger.debug(
                    "[Camera:%s] pipeline stop failed", self.name, exc_info=True
                )
            self._pipeline = None
        # Release the frame so a stopped camera does not serve stale data.
        with self._frame_lock:
            self._latest_frame = None
        logger.info("[Camera:%s] stopped", self.name)

    @property
    def running(self) -> bool:
        return self._running

    # ── frame slot ──────────────────────────────────────────────────────────

    def get_latest_frame(self) -> Optional[np.ndarray]:
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
        while self._running and not self._stop_event.is_set():
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=1000)
                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue

                img = np.asanyarray(color_frame.get_data())
                ts = time.monotonic()

                with self._frame_lock:
                    self._latest_frame = img
                    self._latest_timestamp = ts
            except Exception:
                time.sleep(0.001)
