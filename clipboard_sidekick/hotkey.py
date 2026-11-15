"""Global hotkey registration for Clipboard Sidekick (Windows via RegisterHotKey)."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008

_MODIFIERS = {
    "ctrl": _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "alt": _MOD_ALT,
    "shift": _MOD_SHIFT,
    "win": _MOD_WIN,
    "super": _MOD_WIN,
    "meta": _MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "tab": 0x09,
}

WM_HOTKEY = 0x0312


def _is_windows() -> bool:
    return sys.platform == "win32"


class _WinNative:
    def register(self, hwnd, hotkey_id, modifiers, vk):
        if not _is_windows():
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            return bool(user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk))
        except Exception:
            return False

    def unregister(self, hwnd, hotkey_id):
        if not _is_windows():
            return True
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            return bool(user32.UnregisterHotKey(hwnd, hotkey_id))
        except Exception:
            return True


_native = _WinNative()


def parse_combo(combo: str) -> tuple[int, int]:
    if not isinstance(combo, str) or not combo.strip():
        raise ValueError("combo must not be empty")
    tokens = [t.strip() for t in combo.split("+")]
    if any(t == "" for t in tokens):
        raise ValueError("invalid combo: empty token")

    modifiers = 0
    vk: int | None = None
    for token in tokens:
        low = token.casefold()
        if low in _MODIFIERS:
            modifiers |= _MODIFIERS[low]
            continue
        if vk is not None:
            raise ValueError(f"unexpected extra key: {token}")
        if low in _NAMED_KEYS:
            vk = _NAMED_KEYS[low]
        elif low.startswith("f") and low[1:].isdigit():
            n = int(low[1:])
            if n < 1 or n > 24:
                raise ValueError(f"unknown key: {token}")
            vk = 0x70 + n - 1
        elif len(token) == 1:
            ch = token
            if ch.isdigit():
                vk = ord(ch)
            elif ch.isalpha():
                vk = ord(ch.upper())
            else:
                raise ValueError(f"unknown key: {token}")
        else:
            raise ValueError(f"unknown token: {token}")

    if vk is None:
        raise ValueError("combo must include a key")
    return modifiers, vk


# Native event filter

try:
    from PySide6.QtCore import QAbstractNativeEventFilter
except Exception:  # pragma: no cover - Qt not available
    class QAbstractNativeEventFilter:  # type: ignore
        pass


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback, hotkey_id):
        super().__init__()
        self.callback = callback
        self.hotkey_id = hotkey_id

    def nativeEventFilter(self, eventType, message):
        try:
            if isinstance(message, int):
                addr = message
            else:
                addr = int(message)
            msg = ctypes.cast(addr, ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            return False, 0
        if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
            try:
                self.callback()
            except Exception:
                pass
            return True, 0
        return False, 0


# Registration state

_native_filter = None
_registered = None


def _app_instance():
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance()
    except Exception:
        return None


def _unregister_current() -> None:
    global _native_filter, _registered
    if _registered is None:
        return
    _cb, combo, hotkey_id, nf, app, hwnd = _registered
    try:
        _native.unregister(hwnd, hotkey_id)
    except Exception:
        pass
    try:
        _remove_native_filter(app, nf)
    except Exception:
        pass
    _registered = None
    _native_filter = None


def _install_native_filter(app, native_filter) -> None:
    # Default no-op seam; tests monkeypatch this. Real Qt install is optional.
    try:
        if app is not None:
            app.installNativeEventFilter(native_filter)
    except Exception:
        pass


def _remove_native_filter(app, native_filter) -> None:
    try:
        if app is not None:
            app.removeNativeEventFilter(native_filter)
    except Exception:
        pass


def register_hotkey(callback, combo: str = "ctrl+alt+space", *, hotkey_id: int = 1) -> None:
    global _native_filter, _registered

    if not _is_windows():
        raise RuntimeError("Windows-only: global hotkeys require Windows")

    # Parse before touching native state so bad combos raise ValueError cleanly.
    modifiers, vk = parse_combo(combo)

    if _registered is not None:
        _unregister_current()

    app = _app_instance()
    hwnd = None
    ok = _native.register(hwnd, hotkey_id, modifiers, vk)
    if not ok:
        raise RuntimeError(f"Failed to register hotkey {combo}")

    nf = _NativeHotkeyFilter(callback, hotkey_id)
    _install_native_filter(app, nf)

    _registered = (callback, combo, hotkey_id, nf, app, hwnd)
    _native_filter = nf


def unregister() -> None:
    _unregister_current()