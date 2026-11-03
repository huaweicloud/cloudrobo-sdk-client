"""ModeControl: single decision point for teleop/assist operating mode.

Modes: ``AUTO`` (default) / ``CONTROLLED`` (teleop takeover) / ``ASSISTED``
(assist takeover, priority over CONTROLLED).

Every trigger source converges on :meth:`ModeControl.request`:

- cloud command frames (``teleop_enter`` / ``teleop_exit`` /
  ``assist_enter`` / ``assist_exit``), subscribed directly by the
  sync client (``SyncRobotClient._subscribe_commands``).

On an accepted request the mode changes immediately — the heartbeat
reports the nominal value through ``heartbeat_setter`` — and the matching
action callback runs.  Execution gating (actions stay ignored until the
hold/reset finish work) lives inside the callback implementation, not
here: the heartbeat may report the new mode while safety work is still in
flight, by design.

Action callbacks are bound by name from the robot config
(``runtime.mode_control``, resolved against the hardware adapter).  A
request whose callback is not configured still flips the mode and the
heartbeat — a pure state switch.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

AUTO = "AUTO"
CONTROLLED = "CONTROLLED"
ASSISTED = "ASSISTED"

_TELEOP_ENTER = "teleop_enter"
_TELEOP_EXIT = "teleop_exit"
_ASSIST_ENTER = "assist_enter"
_ASSIST_EXIT = "assist_exit"


class ModeControl:
    """Own the operating-mode state and dispatch mode requests.

    ``callbacks`` maps action names (same strings as the requests) to
    callables — typically hardware-adapter methods bound from the robot
    config's ``runtime.mode_control`` section.  ``heartbeat_setter`` is
    usually ``session.set_heartbeat_mode``; when the heartbeat is
    disabled it is a silent no-op.
    """

    def __init__(
        self,
        callbacks: Optional[Dict[str, Callable[[], Any]]] = None,
        heartbeat_setter: Optional[Callable[[str], Any]] = None,
        name: str = "mode_control",
    ) -> None:
        self._callbacks: Dict[str, Callable[[], Any]] = dict(callbacks or {})
        self._heartbeat_setter = heartbeat_setter
        self._name = name
        self._mode = AUTO

    @property
    def mode(self) -> str:
        return self._mode

    def is_controlled(self) -> bool:
        """True while a takeover (teleop or assist) is active."""
        return self._mode in (CONTROLLED, ASSISTED)

    def bind_callback(self, action: str, callback: Callable[[], Any]) -> None:
        """Bind/replace the action callback for *action*."""
        self._callbacks[action] = callback

    def request(self, command_type: str) -> None:
        """Apply a mode request (case/whitespace insensitive).

        Priority rules: ``assist_enter`` while CONTROLLED force-exits the
        teleop branch (no teleop exit action — the assist branch takes
        over); ``teleop_enter`` while ASSISTED is rejected.  Unknown
        command types are ignored with a warning.
        """
        normalized = str(command_type or "").strip().lower()
        if normalized == _ASSIST_ENTER:
            if self._mode == ASSISTED:
                logger.info("assist_enter ignored: already in assist")
                return
            if self._mode == CONTROLLED:
                # 顺序：assist 接管先执行遥操退出动作（hold 收尾），再进入协助
                logger.info("assist take-over: teleop branch force-exited")
                self._run_action(_TELEOP_EXIT)
            self._switch(ASSISTED, _ASSIST_ENTER)
        elif normalized == _ASSIST_EXIT:
            if self._mode == ASSISTED:
                self._switch(AUTO, _ASSIST_EXIT)
            else:
                logger.info("assist_exit ignored: not in assist")
        elif normalized == _TELEOP_ENTER:
            if self._mode == ASSISTED:
                logger.info(
                    "teleop_enter ignored: assist mode active (assist 优先)"
                )
                return
            if self._mode == CONTROLLED:
                logger.info("teleop_enter ignored: already in teleop")
                return
            self._switch(CONTROLLED, _TELEOP_ENTER)
        elif normalized == _TELEOP_EXIT:
            if self._mode == CONTROLLED:
                self._switch(AUTO, _TELEOP_EXIT)
            else:
                logger.info("teleop_exit ignored: not in teleop")
        else:
            logger.warning("Unknown command_type %r ignored", command_type)

    def request_toggle(self, kind: str) -> None:
        """Flip a takeover mode: ``kind`` is ``"teleop"`` or ``"assist"``.

        Keyboard toggles map to this: entering when out, exiting when
        in.  No-op when the other takeover currently owns the mode.
        """
        if kind == "teleop":
            self.request(_TELEOP_EXIT if self._mode == CONTROLLED else _TELEOP_ENTER)
        elif kind == "assist":
            self.request(_ASSIST_EXIT if self._mode == ASSISTED else _ASSIST_ENTER)
        else:
            logger.warning("request_toggle: unknown kind %r", kind)

    # ── internals ────────────────────────────────────────────────────

    def _switch(self, new_mode: str, action: str) -> None:
        self._mode = new_mode
        self._notify_heartbeat()
        self._run_action(action)

    def _notify_heartbeat(self) -> None:
        if self._heartbeat_setter is None:
            return
        try:
            self._heartbeat_setter(self._mode)
        except Exception:
            logger.exception("heartbeat mode update failed")

    def _run_action(self, action: str) -> None:
        callback = self._callbacks.get(action)
        if callback is None:
            # Q3b semantics: pure state switch when no callback is bound
            logger.debug(
                "mode action %r has no callback (pure state switch)", action
            )
            return
        try:
            callback()
        except Exception:
            logger.exception("mode action %r failed", action)
