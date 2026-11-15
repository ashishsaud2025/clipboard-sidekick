"""Tests for the popup surface and the global-hotkey plumbing.

Headless: the offscreen Qt platform is selected before any Qt import. No real
global hotkey is registered and no real system clipboard is touched outside
the offscreen application's in-memory clipboard.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ctypes
from ctypes import wintypes

import pytest
from PySide6 import QtCore
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    yield _APP


@pytest.fixture
def popup(qapp):
    from clipboard_sidekick.popup import Popup

    p = Popup()
    yield p
    p.close_popup()
    p.deleteLater()
    qapp.processEvents()


# Popup: flags, focus policy, construction


def test_popup_flags_no_focus(qapp):
    from clipboard_sidekick.popup import Popup
    p = Popup()
    flags = p.windowFlags()
    assert bool(flags & QtCore.Qt.Tool)
    assert bool(flags & QtCore.Qt.FramelessWindowHint)


def test_popup_flags_stay_on_top_and_never_accept_focus(qapp):
    from clipboard_sidekick.popup import Popup

    p = Popup()
    flags = p.windowFlags()
    assert bool(flags & Qt.WindowStaysOnTopHint)
    assert bool(flags & Qt.WindowDoesNotAcceptFocus)
    assert p.focusPolicy() == Qt.NoFocus


def test_popup_child_typing_widget_uses_click_focus_not_strong_focus(qapp):
    from clipboard_sidekick.popup import Popup

    p = Popup()
    # The window must never take focus; only the follow-up box may accept a
    # click focus so the user can type, and never a strong TabFocus.
    assert p.follow_up.focusPolicy() == Qt.ClickFocus
    assert p.answer_view.focusPolicy() == Qt.NoFocus
    assert p.copy_button.focusPolicy() in (Qt.NoFocus, Qt.ClickFocus)


# clamp_position: pure geometry helper


def test_clamp_position_inside_is_unchanged():
    from clipboard_sidekick.popup import clamp_position

    available = QRect(0, 0, 1920, 1080)
    assert clamp_position(QPoint(300, 200), QSize(400, 300), available) == QPoint(300, 200)


def test_clamp_position_right_and_bottom_overflow():
    from clipboard_sidekick.popup import clamp_position

    available = QRect(0, 0, 1920, 1080)
    # 1900 + 400 would overflow right; 1000 + 300 would overflow bottom.
    assert clamp_position(QPoint(1900, 1000), QSize(400, 300), available) == QPoint(1512, 772)


def test_clamp_position_negative_coordinates():
    from clipboard_sidekick.popup import clamp_position

    available = QRect(100, 50, 800, 600)
    assert clamp_position(QPoint(-200, -80), QSize(300, 200), available) == QPoint(108, 58)


def test_clamp_position_larger_than_available_uses_top_left():
    from clipboard_sidekick.popup import clamp_position

    available = QRect(10, 20, 400, 300)
    assert clamp_position(QPoint(100, 100), QSize(900, 800), available) == QPoint(18, 28)


def test_clamp_position_respects_custom_margin():
    from clipboard_sidekick.popup import clamp_position

    available = QRect(0, 0, 1000, 800)
    assert clamp_position(QPoint(995, 795), QSize(300, 200), available, margin=25) == QPoint(675, 575)


# show_for / set_answer / set_remote / cached status


def test_show_for_calls_show_inactive_once_and_never_activates(popup, monkeypatch):
    calls = []
    popup.showInactive = lambda: calls.append("showInactive")
    popup.activateWindow = lambda: calls.append("activateWindow")

    popup.show_for("question", "answer")

    assert calls == ["showInactive"]


def test_show_for_sets_answer_and_remote_indicator(popup):
    popup.showInactive = lambda: None

    popup.show_for("what is this?", "It is a test.", remote=True)

    assert popup.answer_view.toPlainText() == "It is a test."
    assert "remote" in popup.remote_label.text().casefold()
    assert not popup.remote_label.isHidden()


def test_show_for_without_answer_shows_thinking_placeholder(popup):
    popup.showInactive = lambda: None

    popup.show_for("question")

    assert "thinking" in popup.answer_view.toPlainText().casefold()
    assert popup.remote_label.isHidden()


def test_set_answer_updates_text_in_place(popup):
    popup.showInactive = lambda: None
    popup.show_for("question")

    popup.set_answer("final answer", remote=True)

    assert popup.answer_view.toPlainText() == "final answer"
    assert not popup.remote_label.isHidden()


def test_show_for_cached_only_changes_status_text(popup):
    popup.showInactive = lambda: None

    popup.show_for("question", "answer", cached=True)

    assert "cached" in popup.status_label.text().casefold()
    assert popup.answer_view.toPlainText() == "answer"


def test_show_for_does_not_set_focus_or_activate(popup):
    popup.showInactive = lambda: None
    calls = []
    popup.setFocus = lambda *a, **k: calls.append("setFocus")
    popup.activateWindow = lambda *a, **k: calls.append("activateWindow")
    popup.raise_ = lambda *a, **k: calls.append("raise")

    popup.show_for("question", "answer", remote=True)

    assert "setFocus" not in calls
    assert "activateWindow" not in calls


# set_remote


def test_set_remote_toggles_indicator_visibility(qapp, popup):
    popup.showInactive()
    qapp.processEvents()
    assert popup.isVisible()

    popup.set_remote(True)
    assert popup.remote_label.isVisibleTo(popup)
    assert "remote" in popup.remote_label.text().casefold()

    popup.set_remote(False)
    assert not popup.remote_label.isVisibleTo(popup)


# copy_answer


def test_copy_answer_copies_and_returns_true(qapp, popup):
    QGuiApplication.clipboard().setText("")
    popup.showInactive = lambda: None
    popup.show_for("question", "copy me")

    assert popup.copy_answer() is True
    assert QGuiApplication.clipboard().text() == "copy me"
    assert "copied" in popup.status_label.text().casefold()


def test_copy_answer_without_answer_returns_false(qapp, popup):
    assert popup.copy_answer() is False


# follow-up box


def test_follow_up_emits_stripped_text_and_clears(popup):
    received = []
    popup.follow_up_submitted.connect(received.append)

    popup.follow_up.setText("  explain more  ")
    popup.follow_up.returnPressed.emit()

    assert received == ["explain more"]
    assert popup.follow_up.text() == ""


def test_follow_up_empty_or_whitespace_emits_nothing(popup):
    received = []
    popup.follow_up_submitted.connect(received.append)

    popup.follow_up.setText("   ")
    popup.follow_up.returnPressed.emit()
    popup.follow_up.setText("")
    popup.ask_button.click()

    assert received == []


def test_follow_up_ask_button_emits(popup):
    received = []
    popup.follow_up_submitted.connect(received.append)

    popup.follow_up.setText("and then?")
    popup.ask_button.click()

    assert received == ["and then?"]


# closing: Esc, click-away, close_popup


def test_escape_key_closes_popup(qapp, popup):
    popup.showInactive()
    qapp.processEvents()
    assert popup.isVisible()

    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    popup.keyPressEvent(event)

    assert not popup.isVisible()


def test_click_outside_closes_popup(qapp, popup):
    popup.show_for("question", "answer")
    qapp.processEvents()
    assert popup.isVisible()

    outside = popup.frameGeometry().bottomRight() + QPoint(80, 80)
    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(1, 1),
        QPointF(outside),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    target = QWidget()
    qapp.sendEvent(target, event)
    qapp.processEvents()

    assert not popup.isVisible()


def test_click_inside_does_not_close_popup(qapp, popup):
    popup.show_for("question", "answer")
    qapp.processEvents()
    assert popup.isVisible()

    inside = popup.frameGeometry().center()
    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(inside),
        QPointF(inside),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    qapp.sendEvent(popup.answer_view, event)
    qapp.processEvents()

    assert popup.isVisible()


def test_close_popup_hides_without_quitting(qapp, popup):
    popup.showInactive()
    qapp.processEvents()
    assert popup.isVisible()

    popup.close_popup()

    assert not popup.isVisible()
    assert QApplication.instance() is qapp


# Hotkey: parse_combo


def test_parse_combo_default():
    from clipboard_sidekick.hotkey import parse_combo

    assert parse_combo("ctrl+alt+space") == (0x0002 | 0x0001, 0x20)


def test_parse_combo_case_and_whitespace_insensitive():
    from clipboard_sidekick.hotkey import parse_combo

    assert parse_combo("  Ctrl + ALT + Space  ") == (0x0002 | 0x0001, 0x20)
    assert parse_combo("CONTROL+SHIFT+SPACE") == (0x0002 | 0x0004, 0x20)


def test_parse_combo_named_keys_and_digits():
    from clipboard_sidekick.hotkey import parse_combo

    assert parse_combo("shift+f5") == (0x0004, 0x74)
    assert parse_combo("win+a") == (0x0008, ord("A"))
    assert parse_combo("super+z") == (0x0008, ord("Z"))
    assert parse_combo("enter") == (0, 0x0D)
    assert parse_combo("escape") == (0, 0x1B)
    assert parse_combo("tab") == (0, 0x09)
    assert parse_combo("alt+7") == (0x0001, ord("7"))


def test_parse_combo_f24_and_duplicate_modifier():
    from clipboard_sidekick.hotkey import parse_combo

    assert parse_combo("ctrl+ctrl+f24") == (0x0002, 0x70 + 24 - 1)


def test_parse_combo_unknown_token_names_it():
    from clipboard_sidekick.hotkey import parse_combo

    with pytest.raises(ValueError) as excinfo:
        parse_combo("ctrl+nope")
    assert "nope" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        parse_combo("hyper+x")
    assert "hyper" in str(excinfo.value)


def test_parse_combo_empty_raises():
    from clipboard_sidekick.hotkey import parse_combo

    with pytest.raises(ValueError):
        parse_combo("")
    with pytest.raises(ValueError):
        parse_combo("   ")
    with pytest.raises(ValueError):
        parse_combo("ctrl+")


# Hotkey: registration plumbing (mocked, never real)


class _FakeNative:
    def __init__(self, ok=True):
        self.ok = ok
        self.registers = []
        self.unregisters = []

    def register(self, hwnd, hotkey_id, modifiers, vk):
        self.registers.append((hwnd, hotkey_id, modifiers, vk))
        return self.ok

    def unregister(self, hwnd, hotkey_id):
        self.unregisters.append((hwnd, hotkey_id))
        return True


@pytest.fixture
def hotkey_mod(monkeypatch):
    import clipboard_sidekick.hotkey as mod

    mod.unregister()
    monkeypatch.setattr(mod, "_is_windows", lambda: True)
    yield mod
    mod.unregister()


def _install_recorder(monkeypatch, mod):
    installed = []
    removed = []

    def fake_install(app, native_filter):
        installed.append(native_filter)

    def fake_remove(app, native_filter):
        removed.append(native_filter)

    monkeypatch.setattr(mod, "_install_native_filter", fake_install)
    monkeypatch.setattr(mod, "_remove_native_filter", fake_remove)
    return installed, removed


def test_register_on_non_windows_raises_before_native(monkeypatch):
    import clipboard_sidekick.hotkey as mod

    mod.unregister()
    monkeypatch.setattr(mod, "_is_windows", lambda: False)
    fake = _FakeNative()
    monkeypatch.setattr(mod, "_native", fake)

    with pytest.raises(RuntimeError, match="Windows-only"):
        mod.register_hotkey(lambda: None)

    assert fake.registers == []


def test_register_calls_native_with_parsed_combo(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)
    installed, _removed = _install_recorder(monkeypatch, hotkey_mod)

    hotkey_mod.register_hotkey(lambda: None, "shift+f5", hotkey_id=7)

    assert fake.registers == [(None, 7, 0x0004, 0x74)]
    assert len(installed) == 1
    hotkey_mod.unregister()


def test_native_filter_invokes_callback_only_for_matching_event(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)
    _installed, _removed = _install_recorder(monkeypatch, hotkey_mod)
    calls = []

    hotkey_mod.register_hotkey(lambda: calls.append("hit"), "ctrl+alt+space", hotkey_id=7)
    native_filter = hotkey_mod._native_filter
    assert native_filter is not None

    def send(message_id, wparam):
        msg = wintypes.MSG()
        msg.message = message_id
        msg.wParam = wparam
        return native_filter.nativeEventFilter(
            b"windows_generic_MSG", ctypes.addressof(msg)
        )

    assert send(0x0312, 8) == (False, 0)
    assert calls == []

    assert send(0x0313, 7) == (False, 0)
    assert calls == []

    handled, _result = send(0x0312, 7)
    assert handled is True
    assert calls == ["hit"]

    hotkey_mod.unregister()


def test_unregister_calls_native_and_removes_filter(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)
    installed, removed = _install_recorder(monkeypatch, hotkey_mod)

    hotkey_mod.register_hotkey(lambda: None, "ctrl+alt+space", hotkey_id=3)
    hotkey_mod.unregister()

    assert fake.unregisters == [(None, 3)]
    assert removed == installed
    assert hotkey_mod._native_filter is None


def test_unregister_is_idempotent_and_safe_before_register(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)

    hotkey_mod.unregister()
    hotkey_mod.unregister()

    assert fake.unregisters == []


def test_reregister_replaces_previous_hotkey(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)
    installed, _removed = _install_recorder(monkeypatch, hotkey_mod)

    hotkey_mod.register_hotkey(lambda: None, "ctrl+alt+space", hotkey_id=1)
    hotkey_mod.register_hotkey(lambda: None, "shift+f5", hotkey_id=2)

    assert fake.unregisters == [(None, 1)]
    assert [entry[1] for entry in fake.registers] == [1, 2]
    assert fake.registers[1] == (None, 2, 0x0004, 0x74)
    assert len(installed) == 2

    hotkey_mod.unregister()


def test_register_failure_raises_and_leaves_no_state(hotkey_mod, monkeypatch):
    fake = _FakeNative(ok=False)
    monkeypatch.setattr(hotkey_mod, "_native", fake)

    with pytest.raises(RuntimeError, match="ctrl\\+alt\\+space"):
        hotkey_mod.register_hotkey(lambda: None, "ctrl+alt+space")

    assert hotkey_mod._native_filter is None


def test_register_bad_combo_raises_value_error_without_native(hotkey_mod, monkeypatch):
    fake = _FakeNative()
    monkeypatch.setattr(hotkey_mod, "_native", fake)

    with pytest.raises(ValueError, match="bogus"):
        hotkey_mod.register_hotkey(lambda: None, "ctrl+bogus")

    assert fake.registers == []
