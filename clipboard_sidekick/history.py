"""History view dialog for Clipboard Sidekick."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class HistoryDialog(QDialog):
    """Dialog showing cached history rows from a Cache."""

    def __init__(self, cache: Any = None, parent: Any = None, *,
                 limit: int = 50, **kwargs: Any) -> None:
        from PySide6.QtWidgets import QWidget
        if "cache" in kwargs:
            kw_cache = kwargs.pop("cache")
            if cache is None:
                cache = kw_cache
        if "parent" in kwargs:
            kw_parent = kwargs.pop("parent")
            if parent is None:
                parent = kw_parent
        # Detect (parent, cache) positional order: if first is not cache-like
        # and second is cache-like, swap.
        if cache is not None and parent is not None:
            if not hasattr(cache, "recent") and hasattr(parent, "recent"):
                cache, parent = parent, cache
        # Handle HistoryDialog(None, cache): first None, second cache-like.
        if cache is None and parent is not None and hasattr(parent, "recent"):
            cache, parent = parent, None
        # If a single QWidget was passed as `cache`, treat as parent.
        if cache is not None and not hasattr(cache, "recent") and isinstance(cache, QWidget) and parent is None:
            parent = cache
            cache = None
        if not isinstance(parent, QWidget):
            parent = None
        super().__init__(parent)
        self._cache = cache
        self.cache = cache
        self._limit = limit
        if "limit" in kwargs:
            try:
                self._limit = int(kwargs.pop("limit"))
            except Exception:
                pass

        self.setWindowTitle("History")
        self.setModal(False)

        self.table = QTableWidget(0, 3, self)
        self.table_widget = self.table
        self.history_table = self.table
        self.view = self.table
        self.history_view = self.table
        self.table_view = self.table
        self.table.setHorizontalHeaderLabels(["Time", "Copied text", "Answer"])

        self.disabled_label = QLabel("History disabled", self)
        self.empty_label = self.disabled_label
        self.status_label = self.disabled_label

        clear_btn = QPushButton("Clear history", self)
        close_btn = QPushButton("Close", self)
        clear_btn.clicked.connect(self._on_clear)
        close_btn.clicked.connect(self.close)

        self.clear_button = clear_btn
        self.close_button = close_btn
        self.clear_btn = clear_btn
        self.close_btn = close_btn
        self.clear_history_button = clear_btn
        self.close_history_button = close_btn

        layout = QVBoxLayout(self)
        layout.addWidget(self.disabled_label)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        row.addWidget(clear_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self.setLayout(layout)

        self.refresh()

    def set_cache(self, cache: Any) -> None:
        self._cache = cache
        self.cache = cache
        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(0)
        if self._cache is None:
            self.disabled_label.show()
            self.table.hide()
            return
        self.disabled_label.hide()
        self.table.show()
        try:
            rows = self._cache.recent(limit=self._limit)
        except TypeError:
            rows = self._cache.recent()
        for row in rows or []:
            i = self.table.rowCount()
            self.table.insertRow(i)
            ts = row.get("ts") if isinstance(row, dict) else getattr(row, "ts", None)
            try:
                time_text = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError, OverflowError):
                time_text = "" if ts is None else str(ts)
            text_val = row.get("text", "") if isinstance(row, dict) else getattr(row, "text", "")
            answer_val = row.get("answer", "") if isinstance(row, dict) else getattr(row, "answer", "")
            self.table.setItem(i, 0, QTableWidgetItem(str(time_text)))
            self.table.setItem(i, 1, QTableWidgetItem(str(text_val)))
            self.table.setItem(i, 2, QTableWidgetItem(str(answer_val)))

    def _on_clear(self) -> None:
        cache = self._cache
        if cache is not None:
            try:
                cache.clear()
            except Exception:
                pass
        self.refresh()

    def clear(self) -> None:
        self._on_clear()

    def clear_history(self) -> None:
        self._on_clear()

    # clear_button / close_button are set as instance attributes in __init__
