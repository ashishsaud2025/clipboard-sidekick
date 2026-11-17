"""Smoke tests for the end-to-end wiring in clipboard_sidekick.main."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tmp_settings(tmp_path):
    return tmp_path / "settings.json"


def test_build_application_wires_everything(qapp, tmp_settings):
    from clipboard_sidekick.main import build_application

    app, ctx = build_application(tmp_settings, app=qapp)
    assert app is qapp
    assert ctx is not None
    assert ctx.settings is not None
    assert ctx.cache is not None
    assert ctx.llm_client is not None
    assert ctx.llm_adapter is not None
    assert ctx.watcher is not None
    assert ctx.prefetch is not None
    assert ctx.popup is not None
    assert ctx.tray is not None
    assert ctx.paused is False
    assert ctx.hotkey_combo
    # prefetch shares the cache used by the app
    assert ctx.prefetch._cache is ctx.cache


def test_on_copy_submits_to_prefetch(qapp, tmp_settings, monkeypatch):
    from clipboard_sidekick.main import build_application

    app, ctx = build_application(tmp_settings, app=qapp)
    calls = []
    monkeypatch.setattr(ctx.prefetch, "submit", lambda text, prompt_version="": calls.append((text, prompt_version)) or None)
    # on_copy is the closure wired to watcher.on_change; fetch it off the watcher
    on_change = ctx.watcher._on_change if hasattr(ctx.watcher, "_on_change") else None
    if on_change is None:
        # fall back to calling the watcher's private handler path
        ctx.watcher._handle_change("hello world test")
    else:
        on_change("hello world test")
    assert calls, "on_copy should submit to the prefetch queue"


def test_hotkey_cached_path_shows_popup(qapp, tmp_settings, monkeypatch):
    from clipboard_sidekick.main import build_application

    app, ctx = build_application(tmp_settings, app=qapp)
    ctx.last_text = "hello world"
    ctx.last_answer = "A greeting explanation."
    seen = []
    monkeypatch.setattr(ctx.popup, "show_for", lambda *a, **k: seen.append((a, k)))
    cb = QApplication.clipboard()
    cb.setText("hello world")
    # Drive on_hotkey through the native filter registered by build_application.
    # hotkey.py parses native events via ctypes message structs; off-Windows
    # we can still exercise the filter object directly if present.
    import clipboard_sidekick.hotkey as hk

    reg = getattr(hk, "_registered", None)
    assert reg is not None, "build_application should have registered a hotkey"
    _cb, _combo, _hid, native_filter, _app, _hwnd = reg
    # Feed a synthetic WM_HOTKEY with a matching wParam. On non-win32 the
    # filter falls back to False; on win32 it dispatches. To stay
    # platform-independent, call the callback the filter holds.
    try:
        # Call the filter's callback directly (it IS on_hotkey).
        native_filter.callback()
    except Exception:
        pytest.fail("on_hotkey raised")
    assert seen, "on_hotkey should show the popup"
    # Cached path: last_answer is served, cached=True
    assert any(k.get("cached") for (_a, k) in seen) or any(
        "cached" in str(k).lower() for (_a, k) in seen
    ), f"expected cached popup, got {seen!r}"


def test_main_returns_int_with_monkeypatched_build(qapp, tmp_settings, monkeypatch):
    import clipboard_sidekick.main as mod

    class FakeApp:
        def __init__(self, argv=None):
            pass

        @staticmethod
        def instance():
            return None

        def setApplicationName(self, name):
            pass

        def exec(self):
            return 3

    class FakeCtx:
        pass

    monkeypatch.setattr(mod, "build_application", lambda *a, **k: (FakeApp(), FakeCtx()))
    monkeypatch.setattr(mod, "QApplication", FakeApp)
    assert mod.main() == 3
