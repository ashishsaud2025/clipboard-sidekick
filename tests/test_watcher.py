"""Tests for the clipboard watcher and sensitive-format detection.

Headless: the offscreen Qt platform is selected before any Qt import.
No real system clipboard contents are read or written in assertions.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


def test_ignores_duplicate_and_tiny(qapp, monkeypatch):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append)
    # simulate two identical copies
    w._handle_change("hi")
    w._handle_change("hi")
    w._handle_change("x")  # under 2 chars
    # First "hi" is a fresh copy and is emitted exactly once; the duplicate
    # and the tiny copy are both dropped.
    assert calls == ["hi"]


def test_tiny_copies_ignored_and_two_chars_accepted(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append)
    w._handle_change("")
    w._handle_change("x")
    assert calls == []

    w._handle_change("hi")
    assert calls == ["hi"]


def test_duplicate_ignored_after_skipped_copy(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append)
    w._handle_change("hello")
    w._handle_change("x")  # skipped, must not disturb dedupe state
    w._handle_change("hello")  # still a duplicate of the last accepted text
    assert calls == ["hello"]


def test_truncates_long_text_and_dedupes_truncated_value(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append, max_text_length=100)
    big = "a" * 5000

    w._handle_change(big)
    assert calls == ["a" * 100]
    assert w._last_text == "a" * 100

    # The truncated value is what dedupe remembers.
    w._handle_change(big)
    w._handle_change("a" * 100)
    assert calls == ["a" * 100]


def test_blocked_check_drops_without_touching_last_text(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    blocked = [True]
    w = ClipboardWatcher(on_change=calls.append, blocked_check=lambda: blocked[0])

    w._handle_change("hello")
    assert calls == []
    assert w._last_text is None

    blocked[0] = False
    w._handle_change("hello")
    assert calls == ["hello"]


def test_sensitive_format_dropped(qapp, monkeypatch):
    from clipboard_sidekick.clipboard import watcher as watcher_module

    calls = []
    w = watcher_module.ClipboardWatcher(on_change=calls.append)

    monkeypatch.setattr(watcher_module, "has_sensitive_format", lambda: True)
    w._handle_change("secret text")
    assert calls == []
    assert w._last_text is None

    # A later non-sensitive copy of the same text is still accepted.
    monkeypatch.setattr(watcher_module, "has_sensitive_format", lambda: False)
    w._handle_change("secret text")
    assert calls == ["secret text"]


def test_non_str_input_ignored_without_raising(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append)
    w._handle_change(None)
    w._handle_change(b"bytes")
    w._handle_change(12345)
    assert calls == []
    assert w._last_text is None


def test_callback_exception_does_not_propagate(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    def boom(text):
        raise RuntimeError("callback failure")

    w = ClipboardWatcher(on_change=boom)
    w._handle_change("hello")  # must not raise
    assert w._last_text == "hello"


def test_echo_prints_classification_and_still_calls_callback(qapp, capsys):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    calls = []
    w = ClipboardWatcher(on_change=calls.append, echo=True)
    w._handle_change("https://example.com/page")

    assert capsys.readouterr().out.strip() == "url"
    assert calls == ["https://example.com/page"]


def test_start_stop_idempotent(qapp):
    from clipboard_sidekick.clipboard.watcher import ClipboardWatcher

    w = ClipboardWatcher(on_change=lambda text: None)
    w.stop()  # safe when never started
    w.start()
    w.start()  # no-op
    w.stop()
    w.stop()  # no-op


def test_has_sensitive_format_markers():
    from clipboard_sidekick.clipboard.formats import has_sensitive_format

    assert has_sensitive_format(["text/plain", "ExcludeClipboardContentFromMonitorProcessing"])
    assert has_sensitive_format(["text/plain", "CanIncludeInClipboardHistory"])
    assert has_sensitive_format(["text/plain", "CanUploadToCloudClipboard"])
    assert has_sensitive_format(["text/plain", "ClipboardViewerIgnore"])
    assert has_sensitive_format(["text/plain", "application/x-1password-password"])
    # Case-insensitive matching.
    assert has_sensitive_format(["excludeclipboardcontentfrommonitorprocessing"])
    assert not has_sensitive_format(["text/plain", "text/html"])
    assert not has_sensitive_format([])


def test_has_sensitive_format_reads_clipboard_mime_data():
    from clipboard_sidekick.clipboard.formats import has_sensitive_format

    class _Mime:
        def __init__(self, formats):
            self._formats = formats

        def formats(self):
            return self._formats

    class _Clipboard:
        def __init__(self, formats):
            self._mime = _Mime(formats)

        def mimeData(self):
            return self._mime

    assert has_sensitive_format(clipboard=_Clipboard(["text/plain", "text/html"])) is False
    assert has_sensitive_format(
        clipboard=_Clipboard(["text/plain", "ExcludeClipboardContentFromMonitorProcessing"])
    ) is True

    class _BrokenClipboard:
        def mimeData(self):
            raise RuntimeError("clipboard unavailable")

    assert has_sensitive_format(clipboard=_BrokenClipboard()) is False
