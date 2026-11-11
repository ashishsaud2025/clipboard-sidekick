"""Tests for clipboard_sidekick.cache."""

import sqlite3
from types import SimpleNamespace

import pytest

from clipboard_sidekick import cache as cache_module
from clipboard_sidekick.cache import Cache


def test_cache_store_and_get(tmp_path):
    from clipboard_sidekick.cache import Cache
    c = Cache(tmp_path / "cache.db")
    key = c.key_for("hello world", "v1")
    assert c.get(key) is None
    c.set(key, "a greeting")
    assert c.get(key) == "a greeting"


def test_key_for_collapses_all_whitespace(tmp_path):
    c = Cache(tmp_path / "cache.db")
    assert c.key_for("a  b\nc", "v1") == c.key_for("a b c", "v1")
    assert c.key_for("a\tb\r\nc", "v1") == c.key_for("a b c", "v1")
    assert c.key_for("  padded   text  ", "v1") == c.key_for("padded text", "v1")


def test_key_for_nfkc_normalization(tmp_path):
    c = Cache(tmp_path / "cache.db")
    # U+FB01 LATIN SMALL LIGATURE FI folds to "fi" under NFKC.
    assert c.key_for("\ufb01le", "v1") == c.key_for("file", "v1")


def test_key_for_nfc_nfd_equivalent(tmp_path):
    c = Cache(tmp_path / "cache.db")
    nfc = "café"          # composed e + acute
    nfd = "café"        # e + combining acute
    assert c.key_for(nfc, "v1") == c.key_for(nfd, "v1")


def test_key_for_different_prompt_version_differs(tmp_path):
    c = Cache(tmp_path / "cache.db")
    assert c.key_for("hello world", "v1") != c.key_for("hello world", "v2")


def test_key_for_is_case_sensitive(tmp_path):
    c = Cache(tmp_path / "cache.db")
    assert c.key_for("Hello", "v1") != c.key_for("hello", "v1")


def test_key_for_returns_sha256_hex(tmp_path):
    c = Cache(tmp_path / "cache.db")
    key = c.key_for("hello world", "v1")
    assert len(key) == 64
    int(key, 16)


def test_key_for_empty_text_raises(tmp_path):
    c = Cache(tmp_path / "cache.db")
    with pytest.raises(ValueError):
        c.key_for("", "v1")
    with pytest.raises(ValueError):
        c.key_for("   \n\t  ", "v1")


def test_key_for_missing_prompt_version_raises(tmp_path):
    c = Cache(tmp_path / "cache.db")
    with pytest.raises(ValueError):
        c.key_for("hello", "")
    with pytest.raises(ValueError):
        c.key_for("hello", "   ")
    with pytest.raises(ValueError):
        c.key_for("hello", None)  # type: ignore[arg-type]


def test_get_unknown_key_returns_none(tmp_path):
    c = Cache(tmp_path / "cache.db")
    assert c.get("0" * 64) is None


def test_set_overwrites_answer_and_refreshes_ts(tmp_path, monkeypatch):
    c = Cache(tmp_path / "cache.db")
    key = c.key_for("hello", "v1")
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 1000.0))
    c.set(key, "old answer", text="hello")
    first_ts = c.recent()[0]["ts"]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 2000.0))
    c.set(key, "new answer", text="hello")
    rows = c.recent()
    assert len(rows) == 1
    assert rows[0]["answer"] == "new answer"
    assert rows[0]["ts"] == 2000.0
    assert first_ts == 1000.0


def test_expired_entry_returns_none(tmp_path, monkeypatch):
    c = Cache(tmp_path / "cache.db", expiry_days=30)
    key = c.key_for("hello", "v1")
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 1000.0))
    c.set(key, "an answer", text="hello")
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 1000.0 + 31 * 86400))
    assert c.get(key) is None


def test_expired_entries_pruned_from_history(tmp_path, monkeypatch):
    c = Cache(tmp_path / "cache.db", expiry_days=30)
    key = c.key_for("hello", "v1")
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 1000.0))
    c.set(key, "an answer", text="hello")
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda: 1000.0 + 31 * 86400))
    c.get(key)  # get prunes
    assert c.recent() == []


def test_history_disabled_set_writes_nothing(tmp_path):
    c = Cache(tmp_path / "cache.db", history_enabled=False)
    key = c.key_for("hello", "v1")
    c.set(key, "an answer", text="hello")
    assert c.recent() == []
    raw = sqlite3.connect(tmp_path / "cache.db").execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    assert raw == 0


def test_history_disabled_get_returns_none(tmp_path):
    c = Cache(tmp_path / "cache.db", history_enabled=False)
    key = c.key_for("hello", "v1")
    assert c.get(key) is None


def test_history_disabled_clear_still_purges(tmp_path):
    # Data written while history was enabled must still be removable after
    # the user disables history ("Clear history" must keep working).
    path = tmp_path / "cache.db"
    enabled = Cache(path, history_enabled=True)
    enabled.set(enabled.key_for("hello", "v1"), "an answer", text="hello")
    disabled = Cache(path, history_enabled=False)
    assert disabled.clear() == 1
    assert enabled.recent() == []


def test_recent_newest_first(tmp_path, monkeypatch):
    c = Cache(tmp_path / "cache.db")
    for i in range(3):
        monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda i=i: 1000.0 + i))
        c.set(c.key_for(f"text {i}", "v1"), f"answer {i}", text=f"text {i}")
    texts = [row["text"] for row in c.recent()]
    assert texts == ["text 2", "text 1", "text 0"]


def test_recent_limit_respected(tmp_path, monkeypatch):
    c = Cache(tmp_path / "cache.db")
    for i in range(5):
        monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=lambda i=i: 1000.0 + i))
        c.set(c.key_for(f"text {i}", "v1"), f"answer {i}", text=f"text {i}")
    assert len(c.recent(limit=2)) == 2
    assert [r["text"] for r in c.recent(limit=2)] == ["text 4", "text 3"]


def test_recent_returns_expected_keys(tmp_path):
    c = Cache(tmp_path / "cache.db")
    key = c.key_for("hello", "v1")
    c.set(key, "an answer", text="hello raw  text")
    row = c.recent()[0]
    assert set(row) == {"key", "text", "answer", "ts"}
    assert row["key"] == key
    assert row["text"] == "hello raw  text"
    assert row["answer"] == "an answer"
    assert isinstance(row["ts"], float)


def test_clear_returns_count_and_empties(tmp_path):
    c = Cache(tmp_path / "cache.db")
    c.set(c.key_for("one", "v1"), "a1", text="one")
    c.set(c.key_for("two", "v1"), "a2", text="two")
    assert c.clear() == 2
    assert c.recent() == []
    assert c.clear() == 0


def test_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "nested" / "dir" / "cache.db"
    c = Cache(nested)
    c.set(c.key_for("hello", "v1"), "an answer", text="hello")
    assert nested.exists()
    assert c.get(c.key_for("hello", "v1")) == "an answer"


def test_reopen_persists_data(tmp_path):
    path = tmp_path / "cache.db"
    c1 = Cache(path)
    key = c1.key_for("hello", "v1")
    c1.set(key, "an answer", text="hello")
    c2 = Cache(path)
    assert c2.get(key) == "an answer"


def test_distinct_texts_distinct_keys(tmp_path):
    c = Cache(tmp_path / "cache.db")
    assert c.key_for("ab", "v1") != c.key_for("a b", "v1")
    assert c.key_for("Hello World", "v1") != c.key_for("hello world", "v1")
