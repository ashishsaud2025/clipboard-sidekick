"""Windows-only source filtering for the clipboard funnel.

Two independent guards decide whether a copy must not be stored:

* :func:`is_blocked` is a pure, platform-independent matcher over the
  foreground process name and window title. It performs no I/O.
* :func:`has_sensitive_format` asks the clipboard formats layer whether the
  current clipboard payload carries a sensitive marker.

The foreground-window helpers are import-safe everywhere: ctypes handles are
loaded lazily inside each function and every failure degrades to ``None``.
Nothing here reads clipboard text, writes files, or touches the network.
"""

from __future__ import annotations

import ctypes
import sys

import psutil

from clipboard_sidekick.clipboard.formats import has_sensitive_format as _has_sensitive_format

# Convenience list of process-name / window-title fragments that commonly
# carry secrets or private sessions.
#
# ``Settings.blocklist`` from config is the user's own list; this constant is
# only a default. The caller decides whether to merge it with the
# user's settings, do not assume merging happens here.
DEFAULT_BLOCKLIST: tuple[str, ...] = (
    "keepass",
    "keepassxc",
    "1password",
    "bitwarden",
    "lastpass",
    "dashlane",
    "enpass",
    "keeper",
    "nordpass",
    "roboform",
    "private browsing",
    "incognito",
)

_TITLE_BUFFER_CHARS = 512


def _normalize(value: object) -> str:
    """Return a stripped, case-folded string, or "" for empty/None input."""
    if value is None:
        return ""
    return str(value).strip().casefold()


def _basename(value: str) -> str:
    """Return the final path component, accepting both Windows and POSIX paths."""
    return value.replace("/", "\\").rsplit("\\", 1)[-1]


def is_blocked(
    process_name: str | None,
    blocklist: list[str] | None,
    *,
    window_title: str | None = None,
) -> bool:
    """Return True if *process_name* or *window_title* matches the blocklist.

    Matching is case-insensitive substring matching. A blocklist entry is
    tested against the whole process name, its basename (so a full path such
    as ``C:\\Program Files\\KeePass\\KeePass.exe`` matches ``keepass.exe``),
    and the window title (so private-browsing titles can be blocked).

    Empty entries, empty blocklists, and a missing process name without a
    title all yield False. This function is pure: no I/O and no ctypes.
    """
    if not blocklist:
        return False

    process = _normalize(process_name)
    title = _normalize(window_title)

    candidates: list[str] = []
    if process:
        candidates.append(process)
        basename = _basename(process)
        if basename and basename != process:
            candidates.append(basename)
    if title:
        candidates.append(title)

    if not candidates:
        return False

    for raw_entry in blocklist:
        entry = _normalize(raw_entry)
        if not entry:
            continue
        if any(entry in candidate for candidate in candidates):
            return True

    return False


def has_sensitive_format() -> bool:
    """Return True if the live clipboard advertises a sensitive format.

    Delegates marker matching to :mod:`clipboard_sidekick.clipboard.formats`,
    the single source of truth for marker names. Any Qt-layer failure
    degrades to False and never raises.
    """
    try:
        return bool(_has_sensitive_format())
    except Exception:
        return False


def _user32():
    """Load user32 lazily. Only ever called after the win32 platform guard."""
    return ctypes.WinDLL("user32", use_last_error=True)


def get_foreground_process_name() -> str | None:
    """Return the foreground window's process name, or None.

    Windows only. Every failure, wrong platform, no foreground window,
    access denied, exited process, ctypes error, returns None.
    """
    try:
        if sys.platform != "win32":
            return None

        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        return psutil.Process(pid.value).name() or None
    except Exception:
        return None


def get_foreground_window_title() -> str | None:
    """Return the foreground window's title, or None.

    Windows only. Reads at most ``_TITLE_BUFFER_CHARS`` characters. Every
    failure returns None.
    """
    try:
        if sys.platform != "win32":
            return None

        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        buffer = ctypes.create_unicode_buffer(_TITLE_BUFFER_CHARS)
        length = user32.GetWindowTextW(hwnd, buffer, _TITLE_BUFFER_CHARS)
        if length <= 0:
            return None
        return buffer.value or None
    except Exception:
        return None
