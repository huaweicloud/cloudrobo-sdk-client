"""Teleop take-over actions for SONGYI teleoperation.

State decisions (mode, priority, dispatch) live in
``cloudrobo_r2c.client.mode_control.ModeControl``; this module only executes
the four mode actions:

- ``enter_teleop``: drop actions immediately, then (optionally) reset.
- ``exit_teleop``: (optionally) reset first, then resume action
  execution.
- ``enter_assist`` / ``exit_assist``: assist branch, same shape.

Reset/hold run in a worker thread so zenoh subscription callbacks are
never blocked.  The execution state (``teleop_active`` /
``assist_active``) gates action/waist dropping and stays True until the
exit workflow's hold/reset has finished.

The controller never runs external shell scripts: running device
scripts is the responsibility of the robot hardware adapter (权责外移),
so no command string / subprocess lives here.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TeleopController:
    def __init__(
        self,
        *,
        reset_on: Optional[Dict[str, bool]] = None,
        reset_fn: Optional[Callable[[], None]] = None,
        hold_fn: Optional[Callable[[], None]] = None,
        reset_done_cb: Optional[Callable[[], None]] = None,
        action_clear_cb: Optional[Callable[[], None]] = None,
    ) -> None:
        self._reset_on = {
            "enter": bool((reset_on or {}).get("enter", False)),
            "exit": bool((reset_on or {}).get("exit", False)),
        }
        self._reset_fn = reset_fn
        self._hold_fn = hold_fn
        self._reset_done_cb = reset_done_cb
        self._action_clear_cb = action_clear_cb
        self._active = False
        # 辅助接管分支（ASSIST）：行为与遥操（CONTROLLED）一致，
        # 但独立成分支便于后期分叉修改
        self._assist_active = False
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active or self._assist_active

    @property
    def teleop_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def assist_active(self) -> bool:
        with self._lock:
            return self._assist_active

    # ── mode actions（由 ModeControl 经配置回调调用）─────────────────

    def enter_teleop(self) -> None:
        with self._lock:
            if self._active:
                logger.info("teleop_enter ignored: already in teleop")
                return
            # Drop incoming actions from this instant on.
            self._active = True
            if self._action_clear_cb is not None:
                try:
                    self._action_clear_cb()
                except Exception:
                    logger.exception("action_clear_cb failed on enter")
        logger.info("teleop ENTER: actions are now ignored")
        self._spawn(self._enter_workflow)

    def exit_teleop(self) -> None:
        with self._lock:
            if not self._active:
                logger.info("teleop_exit ignored: not in teleop")
                return
            if self._action_clear_cb is not None:
                try:
                    self._action_clear_cb()
                except Exception:
                    logger.exception("action_clear_cb failed on exit")
        logger.info("teleop EXIT requested")
        self._spawn(self._exit_workflow)

    def enter_assist(self) -> None:
        with self._lock:
            if self._assist_active:
                logger.info("assist_enter ignored: already in assist")
                return
            # 优先级高于遥操：已处遥操则强制退出遥操分支（assist 分支
            # 直接接管，无遥操 exit 动作）
            self._active = False
            self._assist_active = True
            if self._action_clear_cb is not None:
                try:
                    self._action_clear_cb()
                except Exception:
                    logger.exception("action_clear_cb failed on assist enter")
        logger.info("assist ENTER: actions are now ignored (assist branch)")
        self._spawn(self._assist_enter_workflow)

    def exit_assist(self) -> None:
        with self._lock:
            if not self._assist_active:
                logger.info("assist_exit ignored: not in assist")
                return
            if self._action_clear_cb is not None:
                try:
                    self._action_clear_cb()
                except Exception:
                    logger.exception("action_clear_cb failed on assist exit")
        logger.info("assist EXIT requested")
        self._spawn(self._assist_exit_workflow)

    def _assist_enter_workflow(self) -> None:
        if self._reset_on["enter"]:
            self._do_reset()
        else:
            self._hold_current()
        logger.info("assist ready (reset/hold done)")

    def _assist_exit_workflow(self) -> None:
        if self._reset_on["exit"]:
            self._do_reset()
        else:
            self._hold_current()
        with self._lock:
            self._assist_active = False
        logger.info("assist exited: action execution resumed")
        if self._reset_done_cb is not None:
            try:
                self._reset_done_cb()
            except Exception:
                logger.exception("reset_done_cb failed")

    def _spawn(self, workflow: Callable[[], None]) -> None:
        def _run() -> None:
            try:
                workflow()
            except Exception:
                logger.exception("teleop workflow failed")

        thread = threading.Thread(target=_run, name="songyi-teleop", daemon=True)
        thread.start()
        self._worker = thread

    def _enter_workflow(self) -> None:
        if self._reset_on["enter"]:
            self._do_reset()
        else:
            # reset_on=false：按当前实际位伺服一次就地停止——不发送时
            # 控制器会继续朝之前流式目标的终点运动（危险）
            self._hold_current()
        logger.info("teleop ready (reset/hold done)")

    def _exit_workflow(self) -> None:
        if self._reset_on["exit"]:
            self._do_reset()
        else:
            self._hold_current()
        with self._lock:
            self._active = False
        logger.info("teleop exited: action execution resumed")
        if self._reset_done_cb is not None:
            try:
                self._reset_done_cb()
            except Exception:
                logger.exception("reset_done_cb failed")

    def _do_reset(self) -> None:
        if self._reset_fn is None:
            logger.warning("reset requested but no reset_fn configured")
            return
        logger.info("resetting to configured pose ...")
        self._reset_fn()
        logger.info("reset done")

    def _hold_current(self) -> None:
        """按当前实际关节位伺服一次（就地停止）。"""
        if self._hold_fn is None:
            logger.warning(
                "hold requested but no hold_fn configured; robot may keep "
                "moving to the last streaming target"
            )
            return
        logger.info("holding current pose ...")
        self._hold_fn()
        logger.info("hold done")

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        worker = self._worker
        if worker is None:
            return True
        worker.join(timeout=timeout)
        return not worker.is_alive()
