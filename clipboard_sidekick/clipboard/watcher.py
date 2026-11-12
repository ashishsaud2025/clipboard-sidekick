"""Clipboard watcher: the single filter funnel feeding the sidekick.

Listens to ``QClipboard.dataChanged``, reads text only, and forwards accepted
text to ``on_change``. Tiny copies, duplicates, sensitive clipboard formats,
and blocked applications are dropped. No threads and no polling timers.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QGuiApplication

from clipboard_sidekick.clipboard.formats import has_sensitive_format
from clipboard_sidekick.router import classify


class ClipboardWatcher:
    """Watch the system clipboard and emit accepted text changes.

    Args:
    on_change:
        Called with the accepted text.
    max_text_length:
        Accepted text is truncated to exactly this many characters.
    blocked_check:
        Optional ``() -> bool`` seam . When it returns True the copy
        is dropped before any state is updated.
    echo:
        When True, print ``classify(text)`` for each accepted copy
        (console debug output).
    """

    def __init__(
        self,
        on_change: Callable[[str], None],
        *,
        max_text_length: int = 4000,
        blocked_check: Callable[[], bool] | None = None,
        echo: bool = False,
    ) -> None:
        self._on_change = on_change
        self._max_text_length = int(max_text_length)
        self._blocked_check = blocked_check
        self._echo = echo

        self._last_text: str | None = None
        self._clipboard = None
        self._started = False

    # Lifecycle
    def start(self) -> None:
        """Connect to ``dataChanged``. Idempotent."""
        if self._started:
            return
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return
            clipboard.dataChanged.connect(self._on_data_changed)
            self._clipboard = clipboard
            self._started = True
        except Exception:
            self._clipboard = None
            self._started = False

    def stop(self) -> None:
        """Disconnect from ``dataChanged``. Idempotent and safe if never started."""
        if not self._started:
            return
        try:
            if self._clipboard is not None:
                self._clipboard.dataChanged.disconnect(self._on_data_changed)
        except Exception:
            # Already disconnected / Qt object destroyed, nothing to do.
            pass
        finally:
            self._clipboard = None
            self._started = False

    # Qt signal
    def _on_data_changed(self) -> None:
        """Read text from the clipboard and forward it to the filter funnel."""
        try:
            clipboard = self._clipboard or QGuiApplication.clipboard()
            if clipboard is None:
                return
            text = clipboard.text()
        except Exception:
            return
        self._handle_change(text)

    # Filter funnel
    def _handle_change(self, text: str | None) -> None:
        """Apply every filter in order. Never raises."""
        if text is None:
            return
        if not isinstance(text, str):
            # Binary / non-text clipboard payloads are skipped, not coerced.
            return

        # Blocked application: drop before any state changes.
        try:
            if self._blocked_check is not None and self._blocked_check():
                return
        except Exception:
            # A broken blocklist must fail closed (drop the copy).
            return

        if len(text) < 2:
            return

        # Truncate first so the truncated value is also what dedupe remembers.
        if len(text) > self._max_text_length:
            text = text[: self._max_text_length]

        if text == self._last_text:
            return

        try:
            if has_sensitive_format():
                return
        except Exception:
            return

        self._last_text = text

        if self._echo:
            try:
                print(classify(text))
            except Exception:
                pass

        try:
            self._on_change(text)
        except Exception:
            # A failing consumer must not break clipboard monitoring.
            pass
