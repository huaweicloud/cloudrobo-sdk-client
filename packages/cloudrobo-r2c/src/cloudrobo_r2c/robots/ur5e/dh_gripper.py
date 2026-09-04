"""DH gripper driver via Modbus RTU serial protocol."""
import time
import threading


class DHGripper:
    """DH robotic gripper controlled via Modbus RTU over serial.

    Usage:
        gripper = DHGripper(port="/dev/ttyUSBDH_", baudrate=115200)
        gripper.connect()
        gripper.initialize()
        # In control loop:
        state = gripper.get_state()
        gripper.set_position(0.04)  # open to 40mm
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSBDH_",
        baudrate: int = 115200,
        max_width: float = 0.08,
        max_speed: float = 0.07273,
        max_force: float = 140.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.max_width = max_width
        self.max_speed = max_speed
        self.max_force = max_force
        self._serial = None
        self._lock = threading.Lock()
        self._gripper_id = 0x01

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        import serial
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.1,
        )
        if self._serial.isOpen():
            print(f"[DHGripper] Connected to {self.port}")
        else:
            raise RuntimeError(f"Failed to open {self.port}")

    def disconnect(self):
        if self._serial and self._serial.isOpen():
            self._serial.close()
            print("[DHGripper] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ------------------------------------------------------------------
    # Modbus low-level
    # ------------------------------------------------------------------

    @staticmethod
    def _crc16(data: list) -> int:
        crc = 0xFFFF
        for b in data:
            crc ^= b & 0xFF
            for _ in range(8):
                if crc & 0x01:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def _write_register(self, index: int, value: int) -> bool:
        buf = [
            self._gripper_id, 0x06,
            (index >> 8) & 0xFF, index & 0xFF,
            (value >> 8) & 0xFF, value & 0xFF,
        ]
        crc = self._crc16(buf)
        buf += [crc & 0xFF, (crc >> 8) & 0xFF]

        for _ in range(3):
            with self._lock:
                self._serial.write(bytes(buf))
                resp = self._serial.read(8)
            if len(resp) == 8:
                return True
        return False

    def _read_register(self, index: int) -> int:
        buf = [
            self._gripper_id, 0x03,
            (index >> 8) & 0xFF, index & 0xFF,
            0x00, 0x01,
        ]
        crc = self._crc16(buf)
        buf += [crc & 0xFF, (crc >> 8) & 0xFF]

        for _ in range(3):
            with self._lock:
                self._serial.write(bytes(buf))
                resp = self._serial.read(7)
            if len(resp) == 7:
                return (resp[3] << 8) | (resp[4] & 0xFF)
        return 0

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------

    def initialize(self, timeout: float = 10.0):
        """Initialize gripper (homing). Blocks until ready or timeout."""
        self._write_register(0x0100, 0xA5)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._read_register(0x0200) == 1:
                print("[DHGripper] Initialized")
                return
            time.sleep(0.2)
        raise TimeoutError("Gripper initialization timed out")

    def set_position(self, pos_m: float):
        """Set target position in meters (0.0 = closed, max_width = open)."""
        reg_val = int(pos_m / self.max_width * 1000)
        reg_val = max(0, min(1000, reg_val))
        self._write_register(0x0103, reg_val)

    def set_force(self, force_pct: float):
        """Set target force percentage (0-100)."""
        reg_val = int(max(0, min(100, force_pct)))
        self._write_register(0x0101, reg_val)

    def set_speed(self, speed_pct: float):
        """Set target speed percentage (0-100)."""
        reg_val = int(max(0, min(100, speed_pct)))
        self._write_register(0x0104, reg_val)

    def get_position(self) -> float:
        """Get current position in meters."""
        reg_val = self._read_register(0x0202)
        return reg_val / 1000.0 * self.max_width

    def get_grip_state(self) -> int:
        """0=moving, 1=reached position, 2=grasped object, 3=object dropped."""
        return self._read_register(0x0201)

    def get_state(self) -> dict:
        """Return current gripper state as a dict."""
        return {
            "position": self.get_position(),
            "grip_state": self.get_grip_state(),
            "timestamp": time.time(),
        }
