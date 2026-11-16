"""Platform helpers for non-Windows (Linux/macOS) targets.

Import-safe: no ctypes, no winreg, no OS-specific I/O. The foreground-window
helpers are stubs that always report ``None``; ``set_start_with_windows`` is
a no-op returning ``False``.

Matching logic is shared with Windows: :func:`is_blocked` and
:data:`DEFAULT_BLOCKLIST` are re-exported from :mod:`.windows` so there is a
single implementation.
"""

from __future__ import annotations

from .windows import DEFAULT_BLOCKLIST, is_blocked


def get_foreground_process_name() -> None:
    return None


def get_foreground_window_title() -> None:
    return None


def set_start_with_windows(enabled: bool) -> bool:
    return False


__all__ = [
    "DEFAULT_BLOCKLIST",
    "get_foreground_process_name",
    "get_foreground_window_title",
    "is_blocked",
    "set_start_with_windows",
]
