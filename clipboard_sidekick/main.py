"""Entry point for Clipboard Sidekick."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from clipboard_sidekick import config as _config
from clipboard_sidekick.cache import Cache as _Cache
from clipboard_sidekick.config import load_settings as _original_load_settings
from clipboard_sidekick.tray import TrayApp as _TrayApp

_ORIGINAL_LOAD_SETTINGS = _original_load_settings

# Public names kept for patchability / compatibility.
TrayApp = _TrayApp
Cache = _Cache
QApplication = QApplication
Path = Path
load_settings = _original_load_settings


def main() -> int:
    try:
        _Path = globals().get("Path", Path)
        _QApplication = globals().get("QApplication", QApplication)
        _CacheCls = globals().get("Cache", _Cache)
        _TrayCls = globals().get("TrayApp", _TrayApp)

        app = _QApplication(sys.argv)
        app.setApplicationName("Clipboard Sidekick")

        override = globals().get("settings_path", None)
        if override is not None:
            settings_path = _Path(override)
        else:
            settings_path = _Path.home() / ".clipboard-sidekick" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        loader = globals().get("load_settings", _ORIGINAL_LOAD_SETTINGS)
        if loader is _ORIGINAL_LOAD_SETTINGS:
            cfg_loader = getattr(_config, "load_settings", None)
            if cfg_loader is not None and cfg_loader is not _ORIGINAL_LOAD_SETTINGS:
                loader = cfg_loader
        settings = loader(settings_path)

        cache = _CacheCls(
            settings_path.parent / "cache.db",
            expiry_days=settings.cache_expiry_days,
            history_enabled=settings.history_enabled,
        )
        tray = _TrayCls(settings, cache, settings_path=settings_path)
        tray.show()
        # TODO (Task 11): wire clipboard watcher -> prefetch -> popup here.
        # TODO (Task 11): register global hotkey -> popup toggle here.
        return int(app.exec())
    except Exception as exc:
        print(f"Clipboard Sidekick failed to start: {exc}")
        return 1


if __name__ == "__main__":
    main()
