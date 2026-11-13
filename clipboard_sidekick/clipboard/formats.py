"""Detection of clipboard formats that must not be monitored.

Windows lets a clipboard owner mark content as excluded from clipboard
history, cloud sync, or Clipboard Viewer chains. Password managers advertise
their own markers. Content carrying any of these formats is treated as
sensitive and skipped by the watcher.

Inspection only: nothing here writes to the clipboard, the filesystem, or the
network.
"""

from __future__ import annotations

from collections.abc import Iterable

# Markers are already squashed: lowercase, letters and digits only.
_SENSITIVE_MARKERS: tuple[str, ...] = (
    "excludeclipboardcontentfrommonitorprocessing",
    "canincludeinclipboardhistory",
    "canuploadtocloudclipboard",
    "clipboardviewerignore",
    "password",
)

# Squashed (lowercase, alphanumeric-only) sensitive format names.
# Kept for compatibility; canonical markers are the values above.
SENSITIVE_FORMATS: frozenset[str] = frozenset(_SENSITIVE_MARKERS)


def _squash(format_name: object) -> str:
    """Lowercase and keep only letters/digits, so spacing and punctuation
    variants such as ``Clipboard Viewer Ignore`` still match."""
    return "".join(ch for ch in str(format_name).lower() if ch.isalnum())


def _is_sensitive_name(format_name: object) -> bool:
    squashed = _squash(format_name)
    return any(marker in squashed for marker in SENSITIVE_FORMATS)


def has_sensitive_format(
    format_names: Iterable[str] | None = None,
    *,
    clipboard=None,
) -> bool:
    """Return True if any clipboard format marks the content as sensitive.

    When *format_names* is given it is inspected directly. Otherwise the
    current clipboard's ``QMimeData.formats()`` is read through *clipboard*
    (defaulting to ``QGuiApplication.clipboard()``). Any Qt error, missing
    clipboard, or empty data degrades to False.
    """
    try:
        if format_names is None:
            if clipboard is None:
                from PySide6.QtGui import QGuiApplication

                clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return False
            mime_data = clipboard.mimeData()
            if mime_data is None:
                return False
            format_names = mime_data.formats()

        if not format_names:
            return False

        return any(_is_sensitive_name(name) for name in format_names)
    except Exception:
        return False
