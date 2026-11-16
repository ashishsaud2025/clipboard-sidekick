"""Platform helpers for non-Windows (Linux/macOS) targets.

Import-safe: no ctypes, no winreg, no OS-specific I/O. The foreground-window
helpers are stubs that always report ``None``; ``set_start_with_windows`` is
a no-op returning ``False``.

Matching logic is shared with Windows: :func:`is_blocked` and
:data:`DEFAULT_BLOCKLIST` are re-exported from :mod:`.windows` so there is a
single implementation. The import is lazy/guarded so that
``import clipboard_sidekick.platform.common`` still succeeds on a machine
where ``platform.windows`` cannot be imported at all, the app always
requires PySide6, but a missing optional Windows-only dependency must not
break stubs.
"""

from __future__ import annotations

try:
    from .windows import DEFAULT_BLOCKLIST, is_blocked
except Exception:  # keep stubs usable when windows can't load

    DEFAULT_BLOCKLIST: tuple[str, ...] = ()

    def is_blocked(process_name, blocklist, *, window_title=None) -> bool:  # type: ignore[no-redef]
        return False


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
