"""Keyboard controller using stdin polling.

Background thread polls ``sys.stdin`` via ``select`` (Unix) or
``msvcrt.kbhit`` (Windows).  Only responds to keystrokes when the
terminal window has focus — no global hotkey capture.
"""

from __future__ import annotations

import atexit
import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from cloudrobo_r2c.common.utils.keyboard_mapper import KeyboardCommandMapper

logger = logging.getLogger(__name__)

LifecycleCallback = Callable[[], None]


class KeyboardController:
    """Keyboard command dispatcher via stdin polling.

    Lifecycle commands (*pause_resume*, *graceful_stop*) are dispatched
    via callbacks.  All other mapped keys are forwarded to
    ``adapter.execute_command(instance_name)``.

    Keystrokes are only captured when the terminal window has keyboard
    focus — the user must click into the terminal before pressing keys.

    On Unix the terminal is switched to cbreak for per-character reads
    and restored on ``stop()`` via the main thread.  An ``atexit``
    handler provides a final safety net.
    """

    def __init__(
        self,
        adapter: Any = None,
        keymap: Optional[Dict[str, str]] = None,
        on_pause_resume: Optional[LifecycleCallback] = None,
        on_graceful_stop: Optional[LifecycleCallback] = None,
        is_paused: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._adapter = adapter
        self._mapper = KeyboardCommandMapper(keymap=keymap)
        self._on_pause_resume = on_pause_resume
        self._on_graceful_stop = on_graceful_stop
        self._is_paused = is_paused

        self._stdin_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Terminal recovery state (Unix only)
        self._tty_fd: Optional[int] = None
        self._tty_old_settings: Any = None

    # ── public API ──────────────────────────────────────────────────

    def start(self) -> None:
        """Start the stdin polling background thread."""
        if sys.stdin is None or not sys.stdin.isatty():
            logger.info(
                "[KB] Keyboard control unavailable: stdin is not a terminal."
            )
            return

        self._stop_event.clear()
        self._stdin_thread = threading.Thread(
            target=self._poll_stdin,
            name="r2c-kb-stdin",
            daemon=True,
        )
        self._stdin_thread.start()
        atexit.register(self._restore_terminal)
        self._log_keymap()

    def stop(self) -> None:
        """Stop the stdin polling thread and restore terminal settings."""
        self._stop_event.set()
        if self._stdin_thread is not None:
            self._stdin_thread.join(timeout=2.0)
            self._stdin_thread = None
        self._restore_terminal()
        logger.debug("[KB] Keyboard control stopped.")

    def update_keymap(self, keymap: Dict[str, str]) -> None:
        """Merge *keymap* into the current key mapping at runtime."""
        self._mapper.update_keymap(keymap)

    # ── terminal recovery ────────────────────────────────────────────

    def _restore_terminal(self) -> None:
        """Restore terminal settings from the calling thread.

        Safe to call multiple times — only acts if settings were captured.
        Does nothing on Windows.
        """
        if self._tty_fd is None or self._tty_old_settings is None:
            return
        try:
            import termios
            termios.tcsetattr(self._tty_fd, termios.TCSADRAIN, self._tty_old_settings)
        except Exception:
            logger.debug("[KB] failed to restore terminal settings", exc_info=True)
        finally:
            self._tty_fd = None
            self._tty_old_settings = None

    # ── stdin polling ────────────────────────────────────────────────

    def _poll_stdin(self) -> None:
        if sys.platform == "win32":
            self._poll_stdin_windows()
        else:
            self._poll_stdin_unix()

    def _poll_stdin_unix(self) -> None:
        import os
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        self._tty_old_settings = termios.tcgetattr(fd)
        self._tty_fd = fd
        try:
            tty.setcbreak(fd)
            buf = ""
            while not self._stop_event.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if not ready:
                    continue
                try:
                    raw = os.read(fd, 16)
                except (OSError, ValueError):
                    break
                if not raw:
                    continue
                # Decode bytes; accumulate partial multi-byte sequences
                try:
                    chars = (buf + raw.decode("utf-8", errors="strict"))
                    buf = ""
                except UnicodeDecodeError:
                    buf += raw.decode("utf-8", errors="ignore")
                    continue
                for ch in chars:
                    if ch == "\x03":
                        continue
                    self._handle_char(ch)
        finally:
            self._restore_terminal()

    def _poll_stdin_windows(self) -> None:
        import msvcrt
        import time

        while not self._stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\x03":
                    continue
                if ch in ("\x00", "\xe0"):
                    try:
                        msvcrt.getwch()
                    except Exception:
                        logger.debug("[KB] failed to read extended key on Windows", exc_info=True)
                    continue
                self._handle_char(ch)
            else:
                time.sleep(0.01)

    # ── logging ──────────────────────────────────────────────────────

    def _log_keymap(self) -> None:
        """Print the current keymap so the user knows what keys are active."""
        items = self._mapper.items()
        if not items:
            return
        parts = [f"[{k}] {v}" for k, v in items]
        logger.info("[KB] Keyboard control started. keymap: %s", ", ".join(parts))

    # ── dispatch ─────────────────────────────────────────────────────

    def _handle_char(self, ch: str) -> None:
        cmd_name = self._mapper.map(ch)
        if cmd_name is None:
            return
        self._dispatch(cmd_name, ch)

    def _dispatch(self, cmd_name: str, key_char: str) -> None:
        """Route a mapped command name to its handler."""
        if cmd_name == "pause_resume":
            if self._on_pause_resume:
                logger.info("[KB] %s → pause_resume", key_char)
                self._on_pause_resume()
            return

        if cmd_name == "graceful_stop":
            if self._on_graceful_stop:
                logger.info("[KB] %s → graceful_stop", key_char)
                self._on_graceful_stop()
            return

        # Adapter commands: check pause gating
        adapter = self._adapter
        if adapter is None:
            logger.warning("[KB] %s → %s: no adapter, skipping.", key_char, cmd_name)
            return

        # Check if this command requires the flow to be paused
        cmd_instance = getattr(adapter, "_adapter_commands", {}).get(cmd_name)
        requires_pause = getattr(cmd_instance, "requires_pause", True) if cmd_instance is not None else False
        if requires_pause and self._is_paused is not None and not self._is_paused():
            logger.info(
                "[KB] %s → %s: requires pause — press space to pause first.",
                key_char,
                cmd_name,
            )
            return

        try:
            ok = adapter.execute_command(cmd_name)
            if ok:
                logger.info("[KB] %s → %s: executed.", key_char, cmd_name)
            else:
                logger.warning("[KB] %s → %s: not registered.", key_char, cmd_name)
        except Exception as exc:
            logger.warning("[KB] %s → %s: error — %s", key_char, cmd_name, exc)
