"""真 pypilot 隙锋 Robot（已适配 pypilot 2.0.0.260612.0, cp310）。

对外接口与 ``MockXifengRobot`` 严格一致，便于 ``XifengHardwareAdapter``
按配置在 mock / real 之间切换。

适配依据：wheel 自带类型桩 ``pypilot/_pypilot.pyi``（2.0.0.260612.0）。
与旧版（0422 文档 / 1.0.0 cp36）相比的关键 API 差异：

1. ``start()`` 返回 **bool**（True=成功），不再是 int 0=成功
2. ``armMoveJoints / armPluseToServo`` 形参为 ``FloatVector``，
   不保证接受 Python list，统一显式构造 ``pypilot.FloatVector``
3. **不上传图像**：隙锋确认目前**不支持 RGB 数据推流**，pypilot 2.0 相机回调也只有点云
   （``CameraData.points``）、无 RGB。故本桥 observation 只含关节 + 夹爪，不含图像。
   （未来若隙锋支持 RGB，可在此接独立 USB/RTSP 抓帧；当前不实现。）

版本历史：早期 2.0.0.0525 wheel 有 3 个缺陷（import 缺符号 / PilotSDK 析构段错误 /
boost 模块名遮蔽包），曾加 RTLD_LAZY 导入兜底 + sdk 对象故意泄漏兜底；
2.0.0.260612.0 已全部修复（模块改名 _pypilot），故本文件已移除所有 workaround。

内部约定：
- 对外关节单位：弧度（与 R2C 协议一致）
- 对内 pypilot 调用：度（pypilot 的 ``armMoveJoints/armGetJoints`` 用度）
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Dict, List, Mapping

from .robot_interface import (
    ALL_ARM_JOINT_NAMES,
    GRIPPER_NAMES,
    LEFT_ARM_JOINT_NAMES,
    RIGHT_ARM_JOINT_NAMES,
)

logger = logging.getLogger(__name__)


# 隙锋夹爪 hex 命令（来自原 SDK 文档示例 §1.4.3）
# 每个夹爪有 open / close 两个 hex 串，通过串口写出
DEFAULT_GRIPPER_COMMANDS: Dict[str, Dict[str, str]] = {
    "left_gripper": {
        "com": "COM1",
        "open_hex": "eb 90 01 03 11 f4 01 0a",
        "close_hex": "eb 90 01 05 10 f4 01 64 00 6f",
    },
    "right_gripper": {
        "com": "COM1",
        "open_hex": "eb 90 02 03 11 f4 01 0b",
        "close_hex": "eb 90 02 05 10 f4 01 64 00 70",
    },
}


def _deg_to_rad(values: List[float]) -> List[float]:
    return [v * math.pi / 180.0 for v in values]


def _rad_to_deg(values: List[float]) -> List[float]:
    return [v * 180.0 / math.pi for v in values]


class RealXifengRobot:
    """通过 pypilot.PilotSDK 直连隙锋机器人。

    使用前请先 `pip install pypilot-X.Y.Z-cp310-...-linux_x86_64.whl`。
    observation 只含关节 + 夹爪（目前不支持 RGB 推流，无图像）。
    """

    def __init__(
        self,
        *,
        robot_host: str,
        request_connection_ip: str | None = None,
        arm_local_ip: str = "",
        arm_ip: str = "",
        arm_port: int = 8080,
        left_arm_id: int = 1,
        right_arm_id: int = 2,
        gripper_commands: Mapping[str, Mapping[str, str]] | None = None,
        joint_poll_hz: float = 30.0,
    ) -> None:
        self._robot_host = robot_host
        self._request_ip = request_connection_ip or robot_host
        self._arm_local_ip = arm_local_ip
        self._arm_ip = arm_ip
        self._arm_port = int(arm_port)
        self._left_arm_id = int(left_arm_id)
        self._right_arm_id = int(right_arm_id)
        self._gripper_commands = dict(gripper_commands or DEFAULT_GRIPPER_COMMANDS)
        self._poll_period = 1.0 / max(joint_poll_hz, 1.0)

        self._sdk = None
        self._pypilot = None  # 模块引用，connect() 时填充（FloatVector 构造要用）
        self._connected = False
        self._lock = threading.Lock()

        self._latest_joint_obs: Dict[str, float] = {}
        self._latest_gripper_obs: Dict[str, float] = {
            name: 100.0 for name in GRIPPER_NAMES
        }

        # 后台轮询线程（pypilot 的关节/世界坐标接口是同步调用而非回调）
        self._poll_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        if self._connected:
            return
        pypilot = self._import_pypilot()

        self._pypilot = pypilot
        sdk = pypilot.PilotSDK(self._robot_host)
        # pypilot 2.0: start() 返回 bool（True=成功）。1.0 时代是 int 0=成功，勿混用。
        if not sdk.start():
            raise RuntimeError("pypilot.PilotSDK.start() failed (returned False)")
        rc = sdk.requestConnection(self._request_ip)
        if rc != 0:
            raise RuntimeError(f"pypilot.requestConnection({self._request_ip}) -> {rc}")

        # 机械臂初始化
        if self._arm_local_ip and self._arm_ip:
            sdk.armInitData(self._arm_local_ip, self._arm_ip, self._arm_port)
        sdk.armDetachHMI()
        for _ in range(5):
            ok, _alarms = sdk.armIsAlarming()
            if ok == 0:
                break
            sdk.armClearAlarm()
            time.sleep(0.3)
        sdk.armRobotEnableOrNot(True)
        sdk.armSetGlobalSpeed(10.0)

        self._sdk = sdk
        self._connected = True
        self._stop_evt.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="RealXifengPoll", daemon=True
        )
        self._poll_thread.start()
        logger.info("RealXifengRobot connected to %s", self._robot_host)

    def disconnect(self) -> None:
        if not self._connected or self._sdk is None:
            return
        self._stop_evt.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        sdk = self._sdk
        try:
            sdk.armRobotEnableOrNot(False)
            sdk.releaseControl()
            sdk.stop()
        except Exception as e:
            logger.warning("RealXifengRobot disconnect encountered: %s", e)
        finally:
            self._sdk = None
            self._pypilot = None
            self._connected = False
            logger.info("RealXifengRobot disconnected")

    # ------------------------------------------------------------------ read
    def get_observation(self) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("RealXifengRobot is not connected; call connect() first")
        with self._lock:
            obs: Dict[str, Any] = {}
            obs.update(self._latest_joint_obs)
            obs.update(self._latest_gripper_obs)
        # 目前不支持 RGB 推流：observation 不含图像。
        return obs

    # ------------------------------------------------------------------ write
    def send_action(self, command: Mapping[str, Any]) -> None:
        """接收 robot_xifeng_config.yaml r2c_to_device 透传过来的命令。

        预期 schema 与 MockXifengRobot 一致::

            {"arm_positions": [...], "gripper_positions": [...]}

        - arm_positions 前 7 个 → 左臂，后 7 个 → 右臂；
          长度不够 7+7 时只送有的关节，不动其他。
        - gripper_positions 前 1 个 → left_gripper，后 1 个 → right_gripper。
        """
        if not self._connected or self._sdk is None:
            raise RuntimeError("RealXifengRobot is not connected; call connect() first")

        arm_positions = command.get("arm_positions") or []
        if not isinstance(arm_positions, (list, tuple)):
            arm_positions = []
        arm_floats = [float(v) for v in arm_positions]

        n_left = len(LEFT_ARM_JOINT_NAMES)
        n_right = len(RIGHT_ARM_JOINT_NAMES)
        left_rad = arm_floats[:n_left]
        right_rad = arm_floats[n_left : n_left + n_right]

        # pypilot 2.0 的 armMoveJoints 形参是 FloatVector（boost.python 绑定，
        # 不保证 Python list 自动转换），统一显式构造
        if len(left_rad) == n_left:
            self._sdk.armMoveJoints(
                self._left_arm_id, self._to_float_vector(_rad_to_deg(left_rad)), False, 0
            )
        if len(right_rad) == n_right:
            self._sdk.armMoveJoints(
                self._right_arm_id, self._to_float_vector(_rad_to_deg(right_rad)), False, 0
            )

        # 末端：离散开/合（隙锋夹爪是串口 hex 控制）
        gripper_positions = command.get("gripper_positions") or []
        if not isinstance(gripper_positions, (list, tuple)):
            gripper_positions = []
        for i, value in enumerate(gripper_positions):
            if i >= len(GRIPPER_NAMES):
                break
            name = GRIPPER_NAMES[i]
            try:
                target = float(value)
            except (TypeError, ValueError):
                continue
            ee_cfg = self._gripper_commands.get(name)
            if not ee_cfg:
                continue
            hex_str = ee_cfg["open_hex"] if target > 50.0 else ee_cfg["close_hex"]
            data = bytes.fromhex(hex_str.replace(" ", ""))
            com = ee_cfg.get("com", "COM1")
            try:
                self._sdk.armOpenCom(com)
                self._sdk.armWriteCom(com, data)
            except Exception as e:
                logger.warning("Gripper %s write failed: %s", name, e)
            finally:
                try:
                    self._sdk.armCloseCom(com)
                except Exception as e:
                    logger.warning("Gripper %s close com failed: %s", name, e)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _import_pypilot():
        """导入 pypilot（cp310, 需 Python 3.10）。

        2.0.0.260612.0 已修复早期 0525 的 import 缺符号问题，可直接 import，
        无需 RTLD_LAZY 兜底。
        """
        try:
            import pypilot  # type: ignore

            return pypilot
        except ImportError as e:
            raise RuntimeError(
                "pypilot not installed; install pypilot 2.0.0.260612.0 cp310 wheel "
                "(requires Python 3.10) or run with source=mock for offline development"
            ) from e

    def _to_float_vector(self, values: List[float]):
        """把 Python list 转成 pypilot.FloatVector（2.0 运动接口的形参类型）。"""
        if self._pypilot is None:
            raise RuntimeError("connect() must run before _to_float_vector")
        fv = self._pypilot.FloatVector()
        fv.extend([float(v) for v in values])
        return fv

    # ------------------------------------------------------------------ polling
    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning("poll once failed: %s", e)
            self._stop_evt.wait(timeout=self._poll_period)

    def _poll_once(self) -> None:
        if self._sdk is None:
            return
        left_ok, left_deg = self._sdk.armGetJoints(self._left_arm_id)
        right_ok, right_deg = self._sdk.armGetJoints(self._right_arm_id)

        joint_obs: Dict[str, float] = {}
        if left_ok == 0 and left_deg:
            left_rad = _deg_to_rad(list(left_deg))
            for i, value in enumerate(left_rad[: len(LEFT_ARM_JOINT_NAMES)]):
                joint_obs[LEFT_ARM_JOINT_NAMES[i]] = float(value)
        if right_ok == 0 and right_deg:
            right_rad = _deg_to_rad(list(right_deg))
            for i, value in enumerate(right_rad[: len(RIGHT_ARM_JOINT_NAMES)]):
                joint_obs[RIGHT_ARM_JOINT_NAMES[i]] = float(value)

        # 注意：缺失关节填 0（避免 mapper 报 KeyError）
        for name in ALL_ARM_JOINT_NAMES:
            joint_obs.setdefault(name, 0.0)

        with self._lock:
            self._latest_joint_obs = joint_obs
