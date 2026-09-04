from __future__ import annotations

from typing import Dict, Optional

# Default key-to-command mapping.
# Keys listed in RESERVED_KEYS can never be remapped or disabled.
DEFAULT_KEYMAP: Dict[str, str] = {
    " ": "pause_resume",
    "h": "go_home",
    "q": "graceful_stop",
}

RESERVED_KEYS: set[str] = {" ", "q"}


class KeyboardCommandMapper:
    """Maps raw key-press strings to named commands.

    Custom keymaps are **merged** on top of the defaults — keys not
    mentioned in the custom map keep their default binding.  Reserved
    keys (``" "`` and ``"q"``) can **never** be remapped or disabled
    via a custom keymap.

    Usage::

        mapper = KeyboardCommandMapper()
        cmd = mapper.map(' ')   # returns 'pause_resume'
        cmd = mapper.map('h')   # returns 'go_home'
        cmd = mapper.map('x')   # returns None
    """

    def __init__(self, keymap: Optional[Dict[str, str]] = None) -> None:
        merged = dict(DEFAULT_KEYMAP)
        if keymap:
            for k, v in keymap.items():
                k = str(k)
                v = str(v) if v else ""
                if k in RESERVED_KEYS:
                    continue  # silently ignore attempts to remap reserved keys
                merged[k] = v
        self._keymap: Dict[str, str] = merged

    def map(self, key: str) -> Optional[str]:
        """Return the command name for *key*, or ``None`` if unmapped.

        Matching is case-sensitive by default, with a single-letter
        case-insensitive fallback: pressing ``"J"`` will match a keymap
        entry ``"j"`` and vice versa.
        """
        if not isinstance(key, str) or not key:
            return None
        cmd = self._keymap.get(key)
        if cmd is not None:
            return str(cmd)
        # Case-insensitive fallback for single alphabetic characters
        if len(key) == 1 and key.isalpha():
            alt = key.lower() if key.isupper() else key.upper()
            cmd = self._keymap.get(alt)
            if cmd is not None:
                return str(cmd)
        # Treat any whitespace-only input as pause_resume (includes
        # full-width space U+3000 and other Unicode spaces).
        if all(char.isspace() for char in key):
            return "pause_resume"
        return None

    def items(self) -> list[tuple[str, str]]:
        """Return sorted (key, command) pairs of the current keymap."""
        return sorted((k, v) for k, v in self._keymap.items() if v)

    def update_keymap(self, new_keymap: Dict[str, str]) -> None:
        """Merge *new_keymap* entries into the current keymap."""
        if not isinstance(new_keymap, dict):
            return
        self._keymap.update({str(k): str(v) for k, v in new_keymap.items()})
