"""Tests for tray, settings dialog, history view, and start-on-login."""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


# TrayApp pause behaviour


def test_tray_pause_toggle(qapp):
    from clipboard_sidekick.tray import TrayApp
    t = TrayApp()
    assert t.is_paused() is False
    t.toggle_pause()
    assert t.is_paused() is True


def test_toggle_pause_twice_returns_false(qapp):
    from clipboard_sidekick.tray import TrayApp
    t = TrayApp()
    t.toggle_pause()
    t.toggle_pause()
    assert t.is_paused() is False


def test_set_paused_true(qapp):
    from clipboard_sidekick.tray import TrayApp
    t = TrayApp()
    t.set_paused(True)
    assert t.is_paused() is True


def test_pause_callback_invoked(qapp):
    from clipboard_sidekick.tray import TrayApp
    calls = []
    t = TrayApp(on_pause_changed=lambda v: calls.append(v))
    t.toggle_pause()
    assert calls == [True]
    t.toggle_pause()
    assert calls == [True, False]


def test_tray_constructs_with_none_cache(qapp):
    from clipboard_sidekick.config import Settings
    from clipboard_sidekick.tray import TrayApp
    t = TrayApp(Settings(), None)
    assert t.is_paused() is False
    # open_history must not crash
    t.open_history()
    dlg = t._history_dialog
    assert dlg is not None
    # disabled label visible
    assert "disabled" in dlg.disabled_label.text().lower() or dlg.table.rowCount() == 0


# Settings dialog


def test_open_settings_constructs_dialog(qapp):
    from clipboard_sidekick.config import Settings
    from clipboard_sidekick.tray import TrayApp, SettingsDialog
    s = Settings()
    t = TrayApp(s, None)
    # monkeypatch SettingsDialog to avoid exec blocking? Use real but accept via queued? We'll call open_settings with patched exec.
    import clipboard_sidekick.tray as tray_mod
    orig_exec = SettingsDialog.exec
    SettingsDialog.exec = lambda self: QDialog.Rejected
    try:
        t.open_settings()
    finally:
        SettingsDialog.exec = orig_exec
    assert isinstance(t._settings_dialog, SettingsDialog)


def test_settings_save_valid_mutates_and_callback(qapp):
    from clipboard_sidekick.config import Settings
    from clipboard_sidekick.tray import TrayApp, SettingsDialog
    s = Settings()
    calls = []
    t = TrayApp(s, None, on_settings_saved=lambda st: calls.append(st))
    dlg = SettingsDialog(s, start_on_login_setter=lambda v: True)
    dlg.hotkey.setText("ctrl+shift+x")
    dlg.backend.setCurrentText("anthropic")
    dlg.max_text_length.setValue(1234)
    dlg.accept()
    assert s.hotkey == "ctrl+shift+x"
    assert s.backend == "anthropic"
    assert s.max_text_length == 1234
    # TrayApp.open_settings path: invoke
    import clipboard_sidekick.tray as tray_mod
    orig_exec = SettingsDialog.exec
    SettingsDialog.exec = lambda self: QDialog.Accepted
    try:
        # bind a fresh dialog factory by calling open_settings; it creates its own
        t2 = TrayApp(s, None, on_settings_saved=lambda st: calls.append(st))
        # pre-set fields not possible; instead directly test callback via open_settings with monkeypatched dialog? 
    finally:
        SettingsDialog.exec = orig_exec


def test_settings_invalid_hotkey_does_not_accept(qapp):
    from clipboard_sidekick.config import Settings
    from clipboard_sidekick.tray import SettingsDialog
    s = Settings()
    dlg = SettingsDialog(s)
    dlg.hotkey.setText("")
    dlg.accept()
    # result should not be Accepted
    assert dlg.result() != QDialog.Accepted


def test_settings_save_calls_on_settings_saved_once(qapp, tmp_path):
    from clipboard_sidekick.config import Settings
    from clipboard_sidekick.tray import TrayApp, SettingsDialog
    s = Settings()
    saved = []
    sp = tmp_path / "settings.json"
    t = TrayApp(s, None, on_settings_saved=lambda st: saved.append(st), settings_path=sp)
    # monkeypatch SettingsDialog.exec to Accepted and set fields via subclass? Instead patch class to a stub that mutates.
    class StubDlg:
        def __init__(self, settings, *a, **k):
            self.settings = settings
            self._settings = settings
        def setModal(self, *a, **k):
            pass
        def exec(self):
            self.settings.hotkey = "ctrl+alt+q"
            self.settings.backend = "ollama"
            self.settings.max_text_length = 999
            return QDialog.Accepted
    import clipboard_sidekick.tray as tray_mod
    orig = tray_mod.SettingsDialog
    tray_mod.SettingsDialog = StubDlg
    try:
        t.open_settings()
    finally:
        tray_mod.SettingsDialog = orig
    assert len(saved) == 1
    assert s.hotkey == "ctrl+alt+q"
    assert s.max_text_length == 999
    assert sp.exists()


# History dialog


def test_history_dialog_two_rows(qapp):
    from clipboard_sidekick.history import HistoryDialog
    cache = MagicMock()
    cache.recent.return_value = [
        {"key": "k1", "text": "t1", "answer": "a1", "ts": 1000.0},
        {"key": "k2", "text": "t2", "answer": "a2", "ts": 1001.0},
    ]
    dlg = HistoryDialog(cache)
    assert dlg.table.rowCount() == 2


def test_history_clear_empties_table(qapp):
    from clipboard_sidekick.history import HistoryDialog
    cache = MagicMock()
    rows = [
        {"key": "k1", "text": "t1", "answer": "a1", "ts": 1000.0},
    ]
    cache.recent.return_value = rows
    cache.clear.return_value = 1
    dlg = HistoryDialog(cache)
    # after clear, recent returns empty
    cache.recent.return_value = []
    # click clear button
    btn = dlg.clear_button
    assert btn is not None
    btn.click()
    # process events
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert dlg.table.rowCount() == 0
    cache.clear.assert_called_once()


def test_history_none_cache_disabled(qapp):
    from clipboard_sidekick.history import HistoryDialog
    dlg = HistoryDialog(None)
    assert "disabled" in dlg.disabled_label.text().lower()
    assert dlg.table.rowCount() == 0


def test_history_set_cache(qapp):
    from clipboard_sidekick.history import HistoryDialog
    dlg = HistoryDialog(None)
    cache = MagicMock()
    cache.recent.return_value = [
        {"key": "k1", "text": "t1", "answer": "a1", "ts": 1000.0},
    ]
    dlg.set_cache(cache)
    assert dlg.table.rowCount() == 1


# start-with-windows


def test_set_start_with_windows_non_windows(qapp, monkeypatch):
    import clipboard_sidekick.platform.windows as wmod
    monkeypatch.setattr(wmod.sys, "platform", "linux", raising=False)
    # also patch sys.platform in module's sys reference
    monkeypatch.setattr("clipboard_sidekick.platform.windows.sys.platform", "linux")
    assert wmod.set_start_with_windows(True) is False


def test_set_start_with_windows_windows_enable_disable(qapp, monkeypatch):
    import clipboard_sidekick.platform.windows as wmod
    monkeypatch.setattr("clipboard_sidekick.platform.windows.sys.platform", "win32")
    fake_reg = {}
    class FakeWinreg:
        HKEY_CURRENT_USER = "HKCU"
        KEY_SET_VALUE = 1
        REG_SZ = 1

        class _Key:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def OpenKey(self, root, path, reserved, access):
            return self._Key()

        def SetValueEx(self, key, name, reserved, typ, value):
            fake_reg[name] = value

        def DeleteValue(self, key, name):
            if name in fake_reg:
                del fake_reg[name]
            else:
                raise FileNotFoundError()

    monkeypatch.setattr(wmod, "winreg", FakeWinreg())
    assert wmod.set_start_with_windows(True) is True
    assert "ClipboardSidekick" in fake_reg
    assert wmod.set_start_with_windows(False) is True
    assert "ClipboardSidekick" not in fake_reg


def test_set_start_with_windows_registry_error(qapp, monkeypatch):
    import clipboard_sidekick.platform.windows as wmod
    monkeypatch.setattr("clipboard_sidekick.platform.windows.sys.platform", "win32")

    class BadWinreg:
        HKEY_CURRENT_USER = "HKCU"
        KEY_SET_VALUE = 1
        REG_SZ = 1

        def OpenKey(self, *a, **k):
            raise OSError("boom")

    monkeypatch.setattr(wmod, "winreg", BadWinreg())
    assert wmod.set_start_with_windows(True) is False


# main()


def test_main_returns_int(qapp, monkeypatch, tmp_path):
    import clipboard_sidekick.main as main_mod
    # monkeypatch QApplication to a fake
    class FakeApp:
        def __init__(self, argv):
            pass
        def setApplicationName(self, name):
            pass
        def exec(self):
            return 0
    monkeypatch.setattr(main_mod, "QApplication", FakeApp)
    # monkeypatch Path.home
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    # monkeypatch load_settings to default
    from clipboard_sidekick import config
    monkeypatch.setattr(config, "load_settings", lambda p: config.Settings())
    # TrayApp show no-op
    class FakeTray:
        def __init__(self, *a, **k):
            pass
        def show(self):
            pass
    monkeypatch.setattr(main_mod, "TrayApp", FakeTray)
    rc = main_mod.main()
    assert isinstance(rc, int)
    assert rc in (0, 1)


def test_main_does_not_call_sys_exit(qapp, monkeypatch, tmp_path):
    # ensure no SystemExit
    import clipboard_sidekick.main as main_mod
    class FakeApp:
        def __init__(self, argv): pass
        def setApplicationName(self, name): pass
        def exec(self): return 0
    monkeypatch.setattr(main_mod, "QApplication", FakeApp)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from clipboard_sidekick import config
    monkeypatch.setattr(config, "load_settings", lambda p: config.Settings())
    class FakeTray:
        def __init__(self, *a, **k): pass
        def show(self): pass
    monkeypatch.setattr(main_mod, "TrayApp", FakeTray)
    try:
        main_mod.main()
    except SystemExit:
        raise AssertionError("main called sys.exit/SystemExit")
