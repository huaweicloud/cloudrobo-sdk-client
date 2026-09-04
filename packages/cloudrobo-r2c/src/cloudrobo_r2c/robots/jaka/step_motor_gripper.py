"""Step-motor gripper driver via serial protocol.

Thread-safe access with lock protection, matching the MS42DC protocol
used by the Wheeltec stepper-motor gripper on JAKA Mini2.

Usage:
    gripper = StepMotorGripper(port="/dev/ttyACM0", baudrate=115200, motor_id=1)
    gripper.connect()
    gripper.set_percent(50.0)
    state = gripper.get_state()
    gripper.disconnect()
"""

import time
import threading
from typing import Optional, Dict, Any


class StepMotorGripper:
    """Wheeltec stepper-motor gripper via 11-byte custom serial protocol.

    Protocol frame: |0x7B|id|mode|dir|sub|ang_h|ang_l|spd_h|spd_l|checksum|0x7D|
    Checksum = XOR of bytes 0-8 (INCLUDES header 0x7B).
    """

    FRAME_HEADER = 0x7B
    FRAME_FOOTER = 0x7D

    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        baudrate: int = 115200,
        timeout_s: float = 2.0,
        motor_id: int = 1,
        grip_angle_open: int = 361,
        grip_angle_closed: int = 19070,
        mode: int = 0,
        direction: int = 0,
        sub_divide: int = 16,
        speed: int = 1200,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.motor_id = motor_id
        self.grip_angle_open = grip_angle_open
        self.grip_angle_closed = grip_angle_closed
        self.mode = mode
        self.direction = direction
        self.sub_divide = sub_divide
        self.speed = speed

        self._serial = None
        self._lock = threading.Lock()
        self._latest_raw_angle: int = grip_angle_closed

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        import serial

        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
        )
        print(f"[StepMotorGripper] Connected to {self.port}")

    def disconnect(self) -> None:
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self._serial = None
        print("[StepMotorGripper] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_percent(self, percent: float) -> None:
        """Set gripper target as percentage 0 (closed) – 100 (open)."""
        clamped = max(0.0, min(100.0, float(percent)))
        raw_angle = int(
            self.grip_angle_closed
            - (self.grip_angle_closed - self.grip_angle_open) * (clamped / 100.0)
        )
        with self._lock:
            self._write(self._build_set_packet(raw_angle=raw_angle))
            self._latest_raw_angle = raw_angle

    def get_percent(self) -> Optional[float]:
        """Query current gripper opening as percentage.  Returns None on failure."""
        with self._lock:
            self._write(self._build_query_packet())
            resp = self._read(9)
        if resp is None or len(resp) != 9:
            return None
        if self._checksum(resp[:8]) != resp[8]:
            return None

        angle = (
            (int(resp[4]) << 24)
            | (int(resp[5]) << 16)
            | (int(resp[6]) << 8)
            | int(resp[7])
        )
        self._latest_raw_angle = angle
        return self._angle_to_percent(angle)

    def get_state(self) -> Dict[str, Any]:
        """Return current gripper state as a dict."""
        pct = self.get_percent()
        return {
            "position": pct if pct is not None else 0.0,
            "raw_angle": self._latest_raw_angle,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------

    def _write(self, payload: bytes) -> None:
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("Gripper serial is not opened")
        self._serial.write(payload)

    def _read(self, size: int) -> Optional[bytes]:
        if self._serial is None or not self._serial.is_open:
            return None
        deadline = time.time() + self.timeout_s
        while self._serial.in_waiting < size:
            if time.time() >= deadline:
                return None
            time.sleep(0.001)
        return self._serial.read(size)

    # ------------------------------------------------------------------

    def _build_query_packet(self) -> bytes:
        data = bytearray(11)
        data[0] = self.FRAME_HEADER
        data[1] = int(self.motor_id) & 0xFF
        data[2:9] = b"\x00" * 7
        data[9] = self._checksum(data[:9])
        data[10] = self.FRAME_FOOTER
        return bytes(data)

    def _build_set_packet(self, raw_angle: int) -> bytes:
        angle_16 = max(0, min(0xFFFF, int(raw_angle)))
        speed_16 = max(0, min(0xFFFF, int(self.speed)))

        data = bytearray(11)
        data[0] = self.FRAME_HEADER
        data[1] = self.motor_id & 0xFF
        data[2] = self.mode & 0xFF
        data[3] = self.direction & 0xFF
        data[4] = self.sub_divide & 0xFF
        data[5] = (angle_16 >> 8) & 0xFF
        data[6] = angle_16 & 0xFF
        data[7] = (speed_16 >> 8) & 0xFF
        data[8] = speed_16 & 0xFF
        data[9] = self._checksum(data[:9])
        data[10] = self.FRAME_FOOTER
        return bytes(data)


    @staticmethod
    def _checksum(data: bytes) -> int:
        check = 0
        for value in data:
            check ^= int(value) & 0xFF
        return check

    def _angle_to_percent(self, raw_angle: int) -> float:
        if self.grip_angle_closed <= self.grip_angle_open:
            return 0.0
        percent = (
            float(self.grip_angle_closed - raw_angle)
            / float(self.grip_angle_closed - self.grip_angle_open)
            * 100.0
        )
        return max(0.0, min(100.0, percent))
