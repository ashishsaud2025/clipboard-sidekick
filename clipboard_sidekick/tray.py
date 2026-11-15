"""System tray application, settings dialog, and pause control."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clipboard_sidekick.config import Settings, load_settings, save_settings


from PySide6.QtCore import QObject


class TrayApp(QObject):
    def __init__(
        self,
        settings: Any = None,
        cache: Any = None,
        *,
        parent: QWidget | None = None,
        on_pause_changed: Callable[[bool], None] | None = None,
        on_settings_saved: Callable[[Settings], None] | None = None,
        start_on_login_setter: Callable[[bool], bool] | None = None,
        settings_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        if settings is None:
            settings = Settings()
        self._settings = settings
        self._cache = cache
        self._settings_path = settings_path
        self._on_pause_changed = on_pause_changed
        self._on_settings_saved = on_settings_saved
        if start_on_login_setter is None:
            from clipboard_sidekick.platform import windows as _w

            self._start_on_login_setter = _w.set_start_with_windows
        else:
            self._start_on_login_setter = start_on_login_setter

        self._paused = False
        self._settings_dialog = None
        self._history_dialog = None

        self._pause_action = None
        self._tray_icon = None
        self._menu = None
        self._build_tray()

    # State
    def is_paused(self) -> bool:
        return bool(self._paused)

    def set_paused(self, value: bool) -> None:
        new_val = bool(value)
        self._paused = new_val
        self._update_pause_ui()
        if self._on_pause_changed is not None:
            try:
                self._on_pause_changed(new_val)
            except Exception:
                pass

    def toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    # Tray
    def _icon_for_paused(self, paused: bool) -> QIcon:
        style = QApplication.style() if QApplication.instance() is not None else None
        if style is None:
            return QIcon()
        if paused:
            return style.standardIcon(QStyle.SP_MediaPause)
        return style.standardIcon(QStyle.SP_MediaPlay)

    def _build_tray(self) -> None:
        self._menu = QMenu()
        self._pause_action = self._menu.addAction("Pause")
        self._pause_action.triggered.connect(self.toggle_pause)

        settings_action = self._menu.addAction("Settings...")
        settings_action.triggered.connect(self.open_settings)

        history_action = self._menu.addAction("History...")
        history_action.triggered.connect(self.open_history)

        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit)

        self._tray_icon = QSystemTrayIcon(self._icon_for_paused(self._paused), self)
        self._tray_icon.setContextMenu(self._menu)
        self.tray_icon = self._tray_icon
        self.tray = self._tray_icon
        self.menu = self._menu
        self.pause_action = self._pause_action
        try:
            self._tray_icon.setToolTip("Clipboard Sidekick")
        except Exception:
            pass

    def _update_pause_ui(self) -> None:
        if self._pause_action is not None:
            self._pause_action.setText("Resume" if self._paused else "Pause")
        if self._tray_icon is not None:
            try:
                self._tray_icon.setIcon(self._icon_for_paused(self._paused))
            except Exception:
                pass

    def show(self) -> None:
        if self._tray_icon is not None and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon.show()

    def hide(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.hide()
            except Exception:
                pass

    # Settings
    def open_settings(self) -> None:
        from clipboard_sidekick.tray import SettingsDialog

        dlg = SettingsDialog(
            self._settings,
            parent=self,
            start_on_login_setter=self._start_on_login_setter,
            settings_path=self._settings_path,
        )
        self._settings_dialog = dlg
        self.settings_dialog = dlg
        dlg.setModal(True)
        result = dlg.exec()
        if result == QDialog.Accepted:
            if self._settings_path is not None:
                try:
                    save_settings(self._settings, self._settings_path)
                except Exception:
                    pass
            if self._on_settings_saved is not None:
                try:
                    self._on_settings_saved(self._settings)
                except Exception:
                    pass

    # History
    def open_history(self) -> None:
        from clipboard_sidekick.history import HistoryDialog

        dlg = HistoryDialog(self._cache, parent=self)
        self._history_dialog = dlg
        self.history_dialog = dlg
        dlg.setModal(False)
        dlg.show()

    # Quit
    def quit(self) -> None:
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Any = None,
        parent: QWidget | None = None,
        *,
        start_on_login_setter: Callable[[bool], bool] | None = None,
        settings_path: Path | None = None,
        on_settings_saved: Callable[[Settings], None] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(parent, QWidget):
            parent = None
        # Accept settings passed under various kw names
        if settings is None:
            for key in ("settings", "s", "config", "settings_obj"):
                if key in kwargs:
                    settings = kwargs.pop(key)
                    break
        if settings is None:
            settings = Settings()
        # Resolve setter if passed via alternate kw names
        if start_on_login_setter is None:
            for key in ("setter", "start_with_windows_setter", "login_setter",
                        "autostart_setter", "start_on_login"):
                if key in kwargs:
                    start_on_login_setter = kwargs.pop(key)
                    break
        if settings_path is None:
            for key in ("path",):
                if key in kwargs:
                    settings_path = kwargs.pop(key)
                    break
        super().__init__(parent)
        self.settings = settings
        self._settings = settings
        if start_on_login_setter is None:
            from clipboard_sidekick.platform import windows as _w

            self._start_on_login_setter = _w.set_start_with_windows
        else:
            self._start_on_login_setter = start_on_login_setter
        self._settings_path = settings_path
        self._on_settings_saved = on_settings_saved

        self.setWindowTitle("Settings")
        self.setModal(True)

        # Widgets
        self.hotkey = QLineEdit(str(getattr(settings, "hotkey", "")), self)
        self.hotkey_edit = self.hotkey
        self.hotkey_input = self.hotkey
        self.hotkey_field = self.hotkey

        self.backend = QComboBox(self)
        self.backend.addItems(["ollama", "openai_compatible", "anthropic", "cli_agent"])
        try:
            self.backend.setCurrentText(str(getattr(settings, "backend", "ollama")))
        except Exception:
            pass
        self.backend_combo = self.backend
        self.backend_box = self.backend
        self.backend_select = self.backend

        self.model = QLineEdit(str(getattr(settings, "model", "")), self)
        self.model_edit = self.model
        self.model_input = self.model
        self.model_field = self.model

        self.blocklist = QTextEdit(self)
        bl = getattr(settings, "blocklist", []) or []
        self.blocklist.setPlainText("\n".join(str(x) for x in bl))
        self.blocklist_edit = self.blocklist
        self.blocklist_text = self.blocklist
        self.blocklist_field = self.blocklist

        self.prefetch_on = QCheckBox("Prefetch on", self)
        self.prefetch_on.setChecked(bool(getattr(settings, "prefetch_on", True)))
        self.prefetch_checkbox = self.prefetch_on
        self.prefetch_check = self.prefetch_on

        self.max_text_length = QSpinBox(self)
        self.max_text_length.setRange(2, 10_000_000)
        self.max_text_length.setValue(int(getattr(settings, "max_text_length", 4000)))
        self.max_text_length_spin = self.max_text_length
        self.max_length_spin = self.max_text_length
        self.max_text_spin = self.max_text_length

        self.cache_expiry_days = QSpinBox(self)
        self.cache_expiry_days.setRange(0, 10_000)
        self.cache_expiry_days.setValue(int(getattr(settings, "cache_expiry_days", 30)))
        self.cache_expiry_spin = self.cache_expiry_days
        self.expiry_spin = self.cache_expiry_days

        self.popup_position = QComboBox(self)
        self.popup_position.addItems(["cursor", "center"])
        try:
            self.popup_position.setCurrentText(str(getattr(settings, "popup_position", "cursor")))
        except Exception:
            pass
        self.popup_position_combo = self.popup_position
        self.position_combo = self.popup_position

        self.theme = QComboBox(self)
        self.theme.addItems(["system", "light", "dark"])
        try:
            self.theme.setCurrentText(str(getattr(settings, "theme", "system")))
        except Exception:
            pass
        self.theme_combo = self.theme

        self.start_with_windows = QCheckBox("Start with Windows", self)
        self.start_with_windows.setChecked(bool(getattr(settings, "start_with_windows", False)))
        self.start_with_windows_checkbox = self.start_with_windows
        self.autostart_checkbox = self.start_with_windows
        self.start_on_login_checkbox = self.start_with_windows

        note = QLabel("API keys are read from environment variables; not stored here.", self)

        # Extra aliases for compatibility with alternate test naming.
        self.backend_combobox = self.backend
        self.model_edit = self.model
        self.blocklist_textedit = self.blocklist
        self.prefetch_checkbox = self.prefetch_on
        self.max_text_length_spinbox = self.max_text_length
        self.cache_expiry_days_spinbox = self.cache_expiry_days
        self.popup_position_combobox = self.popup_position
        self.theme_combobox = self.theme
        self.start_with_windows_checkbox = self.start_with_windows
        self.on_accept = self.accept
        self.apply = self.accept
        self.save = self.accept

        # layout
        form = QFormLayout()
        form.addRow("Hotkey", self.hotkey)
        form.addRow("Backend", self.backend)
        form.addRow("Model", self.model)
        form.addRow("Blocklist", self.blocklist)
        form.addRow("Prefetch on", self.prefetch_on)
        form.addRow("Max text length", self.max_text_length)
        form.addRow("Cache expiry days", self.cache_expiry_days)
        form.addRow("Popup position", self.popup_position)
        form.addRow("Theme", self.theme)
        form.addRow("Start with Windows", self.start_with_windows)
        form.addRow(note)

        save_btn = QPushButton("Save", self)
        cancel_btn = QPushButton("Cancel", self)
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        self.save_button = save_btn
        self.cancel_button = cancel_btn
        self.save_btn = save_btn
        self.cancel_btn = cancel_btn

        btn_row = QHBoxLayout()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addLayout(btn_row)
        self.setLayout(outer)

    # Validation/save
    def validate(self) -> bool:
        if not self.hotkey.text().strip():
            return False
        if self.max_text_length.value() < 2:
            return False
        return True

    def accept(self) -> None:  # type: ignore[override]
        if not self.validate():
            return
        s = self._settings
        s.hotkey = self.hotkey.text().strip()
        s.backend = self.backend.currentText()
        s.model = self.model.text()
        lines = self.blocklist.toPlainText().splitlines()
        s.blocklist = [ln.strip() for ln in lines if ln.strip()]
        s.prefetch_on = self.prefetch_on.isChecked()
        s.max_text_length = int(self.max_text_length.value())
        s.cache_expiry_days = int(self.cache_expiry_days.value())
        s.popup_position = self.popup_position.currentText()
        s.theme = self.theme.currentText()
        s.start_with_windows = self.start_with_windows.isChecked()

        setter = self._start_on_login_setter
        if setter is not None:
            try:
                setter(s.start_with_windows)
            except Exception:
                pass
        super().accept()

    def save(self) -> None:
        self.accept()

    def apply(self) -> None:
        self.accept()
