"""Tests for clipboard_sidekick.prefetch.

Every test is deterministic: the injected ``ManualScheduler`` never runs a
callback on its own, so there are no sleeps, no threads and no network.
"""

import pytest

from clipboard_sidekick.cache import Cache
from clipboard_sidekick.prefetch import PrefetchQueue


class _Handle:
    """One scheduled callback. ``cancelled`` mirrors a real timer handle."""

    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.cancelled = False


class ManualScheduler:
    """Deterministic stand-in for the production timer scheduler."""

    def __init__(self):
        self.handles = []

    def schedule(self, delay, fn):
        handle = _Handle(delay, fn)
        self.handles.append(handle)
        return handle

    def cancel(self, handle):
        handle.cancelled = True

    def last(self):
        return self.handles[-1]

    def fire(self, handle=None):
        """Fire a live handle (the latest one by default)."""
        handle = handle if handle is not None else self.handles[-1]
        if not handle.cancelled:
            handle.fn()

    def force_fire(self, handle):
        """Simulate a timer that already started before cancel() landed."""
        handle.fn()


def test_cancels_stale_on_rapid_copy(monkeypatch):
    from clipboard_sidekick.prefetch import PrefetchQueue
    ordered = []
    q = PrefetchQueue(llm=lambda t: (ordered.append(t) or "ans"), delay=0)
    q.submit("a")
    q.submit("b")  # should cancel "a"
    q.flush()
    assert ordered == ["b"]  # only latest submitted


def test_debounce_uses_configured_delay_and_waits_for_fire():
    scheduler = ManualScheduler()
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "answer"),
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("first")

    assert calls == []                      # nothing until the timer fires
    assert len(scheduler.handles) == 1
    assert scheduler.handles[0].delay == 0.4
    assert q.pending == "first"

    scheduler.fire()

    assert calls == ["first"]
    assert q.pending is None


def test_second_submit_cancels_first_timer_and_only_latest_runs():
    scheduler = ManualScheduler()
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "answer"),
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("a")
    first = scheduler.last()
    q.submit("b")

    assert first.cancelled is True
    assert scheduler.last() is not first

    # A timer that already began firing must still be discarded.
    scheduler.force_fire(first)
    assert calls == []

    scheduler.fire()
    assert calls == ["b"]


def test_flush_processes_pending_immediately_and_returns_answer():
    scheduler = ManualScheduler()
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or f"answer:{text}"),
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("hi")
    assert calls == []

    result = q.flush()

    assert result == "answer:hi"
    assert calls == ["hi"]
    assert q.pending is None
    assert scheduler.handles[0].cancelled is True


def test_flush_without_pending_returns_none():
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "answer"),
        scheduler=ManualScheduler(),
    )

    assert q.flush() is None
    assert calls == []


def test_cache_hit_returns_immediately_without_calling_llm(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    key = cache.key_for("hello", "v1")
    cache.set(key, "cached answer", text="hello")

    calls = []
    results = []
    scheduler = ManualScheduler()
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "fresh answer"),
        cache=cache,
        delay=0.4,
        on_result=lambda text, answer: results.append((text, answer)),
        scheduler=scheduler,
    )

    answer = q.submit("hello", "v1")

    assert answer == "cached answer"
    assert calls == []
    assert results == [("hello", "cached answer")]
    assert scheduler.handles == []
    assert q.pending is None


def test_cache_miss_stores_answer_with_source_text(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    scheduler = ManualScheduler()
    q = PrefetchQueue(
        lambda text: "the answer",
        cache=cache,
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("some raw text", "v1")
    scheduler.fire()

    rows = cache.recent()
    assert len(rows) == 1
    assert rows[0]["text"] == "some raw text"
    assert rows[0]["answer"] == "the answer"
    assert cache.get(cache.key_for("some raw text", "v1")) == "the answer"


def test_streaming_emits_chunks_then_one_result_and_caches(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    scheduler = ManualScheduler()
    chunks = []
    results = []

    def stream(text):
        yield "he"
        yield "llo"

    q = PrefetchQueue(
        stream,
        cache=cache,
        delay=0.4,
        on_chunk=lambda text, chunk: chunks.append((text, chunk)),
        on_result=lambda text, answer: results.append((text, answer)),
        scheduler=scheduler,
    )

    q.submit("source", "v1")
    scheduler.fire()

    assert chunks == [("source", "he"), ("source", "llo")]
    assert results == [("source", "hello")]
    assert cache.get(cache.key_for("source", "v1")) == "hello"


def test_streaming_error_calls_on_error_and_queue_stays_usable():
    scheduler = ManualScheduler()
    chunks = []
    errors = []
    results = []

    def stream(text):
        yield "partial"
        raise RuntimeError("stream broke")

    q = PrefetchQueue(
        stream,
        delay=0.4,
        on_chunk=lambda text, chunk: chunks.append(chunk),
        on_error=lambda text, exc: errors.append((text, exc)),
        on_result=lambda text, answer: results.append(answer),
        scheduler=scheduler,
    )

    q.submit("bad")
    scheduler.fire()

    assert chunks == ["partial"]
    assert results == []
    assert len(errors) == 1
    assert errors[0][0] == "bad"
    assert isinstance(errors[0][1], RuntimeError)
    assert q.pending is None

    # The queue is still usable after a failed stream.
    ok_results = []
    q2 = PrefetchQueue(
        lambda text: "ok",
        delay=0.4,
        on_result=lambda text, answer: ok_results.append(answer),
        scheduler=scheduler,
    )
    q2.submit("good")
    assert q2.flush() == "ok"
    assert ok_results == ["ok"]


def test_llm_error_calls_on_error_and_queue_stays_usable():
    scheduler = ManualScheduler()
    errors = []
    results = []

    def llm(text):
        if text == "bad":
            raise RuntimeError("boom")
        return "good answer"

    q = PrefetchQueue(
        llm,
        delay=0.4,
        on_error=lambda text, exc: errors.append((text, exc)),
        on_result=lambda text, answer: results.append((text, answer)),
        scheduler=scheduler,
    )

    q.submit("bad")
    scheduler.fire()

    assert len(errors) == 1
    assert errors[0][0] == "bad"
    assert isinstance(errors[0][1], RuntimeError)
    assert results == []
    assert q.flush() is None  # no pending work after the failure

    q.submit("fine")
    assert q.flush() == "good answer"
    assert results == [("fine", "good answer")]


def test_callback_exceptions_are_swallowed():
    def explode(*args, **kwargs):
        raise ValueError("callback exploded")

    q = PrefetchQueue(
        lambda text: "answer",
        delay=0.4,
        on_result=explode,
        on_chunk=explode,
        on_error=explode,
        scheduler=ManualScheduler(),
    )

    q.submit("a")
    q.flush()  # neither the callback nor the queue may raise

    q.submit("b")
    assert q.flush() == "answer"  # queue still works


def test_cancel_pending_drops_work_and_close_is_idempotent():
    scheduler = ManualScheduler()
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "answer"),
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("queued")
    handle = scheduler.last()
    assert q.pending == "queued"

    q.cancel_pending()

    assert q.pending is None
    scheduler.fire(handle)  # cancelled handle does nothing
    assert calls == []

    q.close()
    q.close()  # idempotent

    assert q.submit("later") is None
    assert q.flush() is None
    assert calls == []


def test_stale_scheduled_callback_never_calls_llm():
    scheduler = ManualScheduler()
    calls = []
    q = PrefetchQueue(
        lambda text: (calls.append(text) or "answer"),
        delay=0.4,
        scheduler=scheduler,
    )

    q.submit("a")
    stale = scheduler.last()
    q.submit("b")

    scheduler.force_fire(stale)  # already-running timer for "a"
    assert calls == []           # generation guard rejects it

    scheduler.fire()             # newest handle for "b"
    assert calls == ["b"]
    assert len(calls) == 1


def test_new_copy_while_running_is_deferred_and_never_overlaps():
    scheduler = ManualScheduler()
    calls = []
    holder = {}

    def llm(text):
        calls.append(text)
        if text == "a":
            holder["queue"].submit("b")   # new copy arrives mid-request
            assert calls == ["a"]         # cap 1: no nested LLM call
        return f"answer:{text}"

    q = PrefetchQueue(llm, delay=0.4, scheduler=scheduler)
    holder["queue"] = q

    q.submit("a")
    scheduler.fire()                       # "a" runs, "b" becomes pending

    assert calls == ["a"]
    assert q.pending == "b"
    # The queue armed the follow-up only after "a" finished.
    assert len(scheduler.handles) == 2
    assert scheduler.handles[-1].delay == 0.4

    scheduler.fire()
    assert calls == ["a", "b"]
    assert q.pending is None


def test_cache_requires_prompt_version(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    q = PrefetchQueue(
        lambda text: "answer",
        cache=cache,
        scheduler=ManualScheduler(),
    )

    with pytest.raises(ValueError):
        q.submit("hello")  # empty prompt_version is invalid with a cache

    assert q.pending is None


class _BrokenCache:
    """Cache whose writes fail but whose reads work."""

    def key_for(self, text, prompt_version):
        return "key"

    def get(self, key):
        return None

    def set(self, key, answer, *, text=""):
        raise RuntimeError("disk full")


def test_cache_write_failure_does_not_block_answer_delivery():
    results = []
    q = PrefetchQueue(
        lambda text: "delivered",
        cache=_BrokenCache(),
        delay=0.4,
        on_result=lambda text, answer: results.append((text, answer)),
        scheduler=ManualScheduler(),
    )

    q.submit("text", "v1")

    assert q.flush() == "delivered"
    assert results == [("text", "delivered")]
