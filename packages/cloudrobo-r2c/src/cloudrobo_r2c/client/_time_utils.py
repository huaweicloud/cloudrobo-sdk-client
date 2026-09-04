"""Shared time and error utilities for the client package."""

from __future__ import annotations

import time


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_error(e: BaseException, max_len: int = 512) -> str:
    s = f"{type(e).__name__}: {e}"
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s
