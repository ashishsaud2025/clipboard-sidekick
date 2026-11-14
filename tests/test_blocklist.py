"""Tests for the process-name blocklist and foreground-window helpers.

Deterministic: no real foreground window is queried, no real clipboard is
read, and the ctypes/psutil seams are monkeypatched. Nothing here touches the
filesystem or the network.
"""

from __future__ import annotations


def test_password_manager_blocked():
    from clipboard_sidekick.platform.windows import is_blocked
    assert is_blocked("KeePass.exe", ["keepass.exe", "1password.exe"]) is True
    assert is_blocked("notepad.exe", ["keepass.exe"]) is False


def test_case_insensitive_process_names():
    from clipboard_sidekick.platform.windows import is_blocked

    assert is_blocked("KeePassXC.EXE", ["keepassxc"]) is True
    assert is_blocked("KeePassXC.EXE", ["KeePassXC"]) is True
    assert is_blocked("1Password.exe", ["1password.exe"]) is True
    assert is_blocked("1PASSWORD.EXE", ["1Password.exe"]) is True


def test_substring_match_and_non_match():
    from clipboard_sidekick.platform.windows import is_blocked

    # A fragment matches any process whose name contains it.
    assert is_blocked("KeePassXC.exe", ["keepass"]) is True
    assert is_blocked("Bitwarden.exe", ["warden"]) is True


def test_full_path_matches_basename():
    from clipboard_sidekick.platform.windows import is_blocked

    path = r"C:\Program Files\KeePass\KeePass.exe"
    assert is_blocked(path, ["keepass.exe"]) is True
    # Basename rule also works for forward-slash paths.
    assert is_blocked("C:/Tools/Bitwarden/Bitwarden.exe", ["bitwarden.exe"]) is True


def test_empty_or_none_blocklist_and_blank_entries():
    from clipboard_sidekick.platform.windows import is_blocked

    assert is_blocked("KeePass.exe", []) is False
    assert is_blocked("KeePass.exe", None) is False
    assert is_blocked("KeePass.exe", ["", "   ", None]) is False


def test_none_or_empty_process_name_without_title():
    from clipboard_sidekick.platform.windows import is_blocked

    assert is_blocked(None, ["keepass"]) is False
    assert is_blocked("", ["keepass"]) is False
    assert is_blocked("   ", ["keepass"]) is False
    # No candidate at all even when the blocklist is non-empty.
    assert is_blocked(None, ["keepass"], window_title=None) is False


def test_window_title_matching():
    from clipboard_sidekick.platform.windows import is_blocked

    assert is_blocked(
        "chrome.exe",
        ["private browsing"],
        window_title="New Private Browsing Window",
    ) is True
    assert is_blocked(
        "chrome.exe",
        ["keepass.exe"],
        window_title="New Private Browsing Window",
    ) is False
    # Titles are case-insensitive too.
    assert is_blocked(
        "firefox.exe",
        ["PRIVATE BROWSING"],
        window_title="Mozilla Firefox (Private Browsing)",
    ) is True


def test_default_blocklist_contains_named_fragments_and_blocks_samples():
    from clipboard_sidekick.platform.windows import DEFAULT_BLOCKLIST, is_blocked

    squashed = tuple(entry.casefold() for entry in DEFAULT_BLOCKLIST)
    for fragment in (
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
    ):
        assert any(fragment in entry for entry in squashed), fragment

    assert is_blocked("KeePassXC.exe", list(DEFAULT_BLOCKLIST)) is True
    assert is_blocked("1Password.exe", list(DEFAULT_BLOCKLIST)) is True
    assert is_blocked("Bitwarden.exe", list(DEFAULT_BLOCKLIST)) is True
    assert is_blocked("notepad.exe", list(DEFAULT_BLOCKLIST)) is False


def test_has_sensitive_format_delegates_to_formats_layer(monkeypatch):
    from clipboard_sidekick.platform import windows as windows_module

    monkeypatch.setattr(windows_module, "_has_sensitive_format", lambda: True)
    assert windows_module.has_sensitive_format() is True

    monkeypatch.setattr(windows_module, "_has_sensitive_format", lambda: False)
    assert windows_module.has_sensitive_format() is False

    def boom():
        raise RuntimeError("Qt clipboard unavailable")

    monkeypatch.setattr(windows_module, "_has_sensitive_format", boom)
    assert windows_module.has_sensitive_format() is False


def test_foreground_process_name_returns_none_on_non_windows(monkeypatch):
    from clipboard_sidekick.platform import windows as windows_module

    monkeypatch.setattr(windows_module.sys, "platform", "linux")
    assert windows_module.get_foreground_process_name() is None
    assert windows_module.get_foreground_window_title() is None


class _FakeUser32:
    def __init__(self, *, hwnd=1234, pid=4321, title="Secret Title", fail=None):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.fail = fail

    def GetForegroundWindow(self):
        if self.fail == "window":
            raise OSError("no window")
        return self.hwnd

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        if self.fail == "pid":
            raise OSError("access denied")
        # ``ctypes.byref`` exposes the underlying object via ``_obj``.
        pid_ref._obj.value = self.pid
        return 1

    def GetWindowTextW(self, hwnd, buffer, size):
        if self.fail == "title":
            raise OSError("window gone")
        buffer.value = self.title
        return len(self.title)


class _FakeProcess:
    def __init__(self, pid):
        self.pid = pid

    def name(self):
        if self.pid == 9999:
            raise RuntimeError("process went away")
        return "KeePassXC.exe"


class _FakePsutil:
    Process = _FakeProcess


def _install_win32_seam(monkeypatch, windows_module, user32):
    monkeypatch.setattr(windows_module.sys, "platform", "win32")
    monkeypatch.setattr(windows_module, "_user32", lambda: user32)
    monkeypatch.setattr(windows_module, "psutil", _FakePsutil)


def test_foreground_process_name_windows_path_returns_name(monkeypatch):
    from clipboard_sidekick.platform import windows as windows_module

    _install_win32_seam(monkeypatch, windows_module, _FakeUser32())
    assert windows_module.get_foreground_process_name() == "KeePassXC.exe"


def test_foreground_process_name_failures_return_none(monkeypatch):
    from clipboard_sidekick.platform import windows as windows_module

    # No foreground window.
    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(hwnd=0))
    assert windows_module.get_foreground_process_name() is None

    # GetWindowThreadProcessId raises.
    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(fail="pid"))
    assert windows_module.get_foreground_process_name() is None

    # Foreground window lookup raises.
    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(fail="window"))
    assert windows_module.get_foreground_process_name() is None

    # The process is gone by the time psutil looks it up.
    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(pid=9999))
    assert windows_module.get_foreground_process_name() is None


def test_foreground_window_title_windows_path_and_failures(monkeypatch):
    from clipboard_sidekick.platform import windows as windows_module

    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(title="Private Browsing"))
    assert windows_module.get_foreground_window_title() == "Private Browsing"

    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(title=""))
    assert windows_module.get_foreground_window_title() is None

    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(fail="title"))
    assert windows_module.get_foreground_window_title() is None

    _install_win32_seam(monkeypatch, windows_module, _FakeUser32(fail="window"))
    assert windows_module.get_foreground_window_title() is None
