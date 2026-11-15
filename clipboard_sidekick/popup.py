"""Frameless, always-on-top, no-focus popup for Clipboard Sidekick answers."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def clamp_position(pos: QPoint, size: QSize, available: QRect, margin: int = 8) -> QPoint:
    """Clamp *pos* so a window of *size* fits inside *available* with *margin*.

    When the window is larger than the available area, it is pinned to the
    top-left corner (plus *margin*).
    """
    min_x = available.left() + margin
    max_x = available.left() + available.width() - size.width() - margin
    if max_x < min_x:
        max_x = min_x

    min_y = available.top() + margin
    max_y = available.top() + available.height() - size.height() - margin
    if max_y < min_y:
        max_y = min_y

    x = min(max(pos.x(), min_x), max_x)
    y = min(max(pos.y(), min_y), max_y)
    return QPoint(x, y)


class Popup(QWidget):
    follow_up_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._answer: str | None = None
        self._question: str | None = None

        self.remote_label = QLabel("")
        self.remote_label.hide()

        self.answer_view = QTextEdit()
        self.answer_view.setReadOnly(True)
        self.answer_view.setFocusPolicy(Qt.NoFocus)

        self.status_label = QLabel("")

        self.copy_button = QPushButton("Copy answer")
        self.copy_button.setFocusPolicy(Qt.NoFocus)
        self.copy_button.clicked.connect(self.copy_answer)

        self.follow_up = QLineEdit()
        self.follow_up.setFocusPolicy(Qt.ClickFocus)
        self.follow_up.returnPressed.connect(self._submit_follow_up)

        self.ask_button = QPushButton("Ask")
        self.ask_button.setFocusPolicy(Qt.NoFocus)
        self.ask_button.clicked.connect(self._submit_follow_up)

        layout = QVBoxLayout(self)
        layout.addWidget(self.remote_label)
        layout.addWidget(self.answer_view)
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        row.addWidget(self.follow_up)
        row.addWidget(self.ask_button)
        layout.addLayout(row)
        layout.addWidget(self.copy_button)
        self.setLayout(layout)
        self.adjustSize()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _submit_follow_up(self) -> None:
        text = self.follow_up.text().strip()
        if text:
            self.follow_up_submitted.emit(text)
        self.follow_up.clear()

    def set_remote(self, remote: bool) -> None:
        if remote:
            self.remote_label.setText("Remote backend")
            self.remote_label.show()
        else:
            self.remote_label.hide()

    def set_answer(self, answer, remote: bool = False) -> None:
        self._answer = answer if answer is not None else None
        self.answer_view.setPlainText("" if answer is None else str(answer))
        self.set_remote(bool(remote))

    def show_for(
        self,
        question,
        answer=None,
        *,
        remote: bool = False,
        cached: bool = False,
    ) -> None:
        self._question = question
        if answer is None:
            self._answer = None
            self.answer_view.setPlainText("Thinking...")
        else:
            self._answer = answer
            self.answer_view.setPlainText(str(answer))
        self.set_remote(bool(remote))
        if cached:
            self.status_label.setText("Cached")
        else:
            self.status_label.setText("")

        pos = QCursor.pos()
        size = self.size()
        if size.isEmpty():
            size = self.sizeHint()
        screen = QGuiApplication.screenAt(pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
        else:
            available = QRect(0, 0, 1920, 1080)
        self.move(clamp_position(pos, size, available))

        self.showInactive()

    def copy_answer(self) -> bool:
        if not self._answer:
            return False
        QGuiApplication.clipboard().setText(str(self._answer))
        self.status_label.setText("Copied")
        return True

    def showInactive(self) -> None:  # type: ignore[override]
        self.show()

    def close_popup(self) -> None:
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.close_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if self.isVisible() and event.type() == QEvent.MouseButtonPress:
            try:
                gp = event.globalPos()
            except Exception:
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = None
            if gp is not None and not self.frameGeometry().contains(gp):
                self.close_popup()
        return False