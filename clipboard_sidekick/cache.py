"""SQLite-backed cache and history for Clipboard Sidekick.

Stdlib ``sqlite3`` only. One connection is kept open for the lifetime of the
``Cache``; it is created with ``check_same_thread=False`` and all statements
run under a lock, which is enough for this app (UI actions happen on the Qt
main thread; prefetch may call from a worker thread).

Entries expire ``expiry_days`` after they are written. Expired rows are
deleted with ``DELETE FROM answers WHERE ts < ?`` (``now - expiry_days*86400``)
on open and on every ``get``/``set``/``recent`` call.

Persistence can be disabled wholesale with ``history_enabled=False``
("disable history"): ``set`` then writes nothing at all, ``get`` returns
``None``, and ``recent`` returns ``[]``. ``clear`` still works in that mode so
previously stored history can be purged.

``key_for`` normalizes with Unicode NFKC, strips, and collapses every
whitespace run (including newlines and tabs) to a single ASCII space, then
hashes ``normalized + "|" + prompt_version`` with SHA-256. No case folding is
applied, code is case-sensitive.

Blocked-source copies are never stored: that is the caller's responsibility
(redaction/prefetch decides what reaches ``Cache.set``).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

_WHITESPACE_RE = re.compile(r"\s+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    key TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    answer TEXT NOT NULL,
    ts REAL NOT NULL
)
"""


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def key_for(text: str, prompt_version: str) -> str:
    """Return the SHA-256 hex key for ``text`` under ``prompt_version``."""
    if not isinstance(text, str) or not _normalize(text):
        raise ValueError("text must be a non-empty, non-whitespace string")
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        raise ValueError("prompt_version must be a non-empty string")
    normalized = _normalize(text)
    digest = hashlib.sha256(f"{normalized}|{prompt_version}".encode("utf-8"))
    return digest.hexdigest()


class Cache:
    """SQLite-backed answers cache with expiry and recent-history listing."""

    def __init__(self, path: str | Path, *, expiry_days: int = 30,
                 history_enabled: bool = True) -> None:
        self.path = Path(path)
        self.expiry_days = expiry_days
        self.history_enabled = history_enabled
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._prune_locked()

    def key_for(self, text: str, prompt_version: str) -> str:
        return key_for(text, prompt_version)

    def get(self, key: str) -> str | None:
        """Return the cached answer for ``key``, or ``None`` if missing/expired/disabled."""
        if not self.history_enabled:
            return None
        with self._lock:
            self._prune_locked()
            row = self._conn.execute(
                "SELECT answer FROM answers WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None

    def set(self, key: str, answer: str, *, text: str = "") -> None:
        """Insert or replace the answer for ``key``.

        ``text`` is the raw entered text, stored for the history view; pass it
        whenever the original text is available. When ``text`` is omitted,
        history rows store ``text=""``. When ``history_enabled`` is False this
        does nothing at all.
        """
        if not self.history_enabled:
            return
        with self._lock:
            self._prune_locked()
            self._conn.execute(
                "INSERT INTO answers(key, text, answer, ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET text = excluded.text, "
                "answer = excluded.answer, ts = excluded.ts",
                (key, text, answer, time.time()),
            )
            self._conn.commit()

    def recent(self, limit: int = 50) -> list[dict]:
        """Return up to ``limit`` newest entries, newest first.

        Each dict has keys ``key``, ``text``, ``answer``, ``ts``.
        """
        if not self.history_enabled:
            return []
        with self._lock:
            self._prune_locked()
            rows = self._conn.execute(
                "SELECT key, text, answer, ts FROM answers "
                "ORDER BY ts DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {"key": key, "text": text, "answer": answer, "ts": ts}
                for key, text, answer, ts in rows
            ]

    def clear(self) -> int:
        """Delete all rows; return the number deleted.

        Works even when ``history_enabled`` is False so previously stored
        history can still be purged.
        """
        with self._lock:
            cursor = self._conn.execute("DELETE FROM answers")
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.expiry_days * 86400
        self._conn.execute("DELETE FROM answers WHERE ts < ?", (cutoff,))
        self._conn.commit()
