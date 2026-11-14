"""Debounced prefetch queue between the clipboard watcher and the LLM.

``PrefetchQueue`` exists to make the popup feel instant without hammering the
model:

* copies are debounced (400 ms by default) so a burst of clipboard writes
  results in a single request for the last one;
* stale work is discarded when a newer copy arrives, including a scheduled
  timer that has already begun firing;
* at most one ``llm`` call is ever in flight, a newer copy waits for the
  current call to return (or fail) before it is started;
* repeat text is answered straight from :class:`clipboard_sidekick.cache.Cache`
  without touching the model.

The queue **does not** decide what content is allowed. Blocked applications
and sensitive clipboard formats are dropped by the watcher, and redaction /
prompt building happen before the callable passed as ``llm``. The wiring
layer is responsible for both.

Threading: with the default scheduler the ``llm`` call runs on a daemon
``threading.Timer`` thread, never on the caller's (Qt) thread. Shared state is
guarded by a condition variable and all callbacks fire outside it. There are
no sleeps, polls or busy-waits in this module.

``llm.cancel()`` is only called while a request is genuinely in flight, never
for a submit that merely replaces a debounce timer. A residual race remains: a
cancel can arrive just as the request finishes, leaving a sticky cancel flag on
the client for its next call. The generation guard here discards stale results
and the wiring layer (T11) is responsible for passing a ``cancel`` that is
safe to call when idle.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator

__all__ = ["PrefetchQueue"]


class _TimerScheduler:
    """Default scheduler: one daemon ``threading.Timer`` per callback."""

    def schedule(self, delay: float, fn: Callable[[], None]):
        timer = threading.Timer(max(0.0, float(delay)), fn)
        timer.daemon = True
        timer.start()
        return timer

    def cancel(self, handle) -> None:
        try:
            handle.cancel()
        except Exception:
            pass


class PrefetchQueue:
    """Debounce copies, keep one request in flight, serve cache hits instantly.

    Args:
    llm:
        Called with the raw clipboard text as its single positional argument.
        It may return the answer as a ``str`` or an iterator/generator of
        streamed chunks. A ``cancel`` attribute, if present, is invoked only
        when a request is actually running; it may race with completion, so
        if it is sticky the wiring layer must make it safe when idle.
    cache:
        Optional :class:`clipboard_sidekick.cache.Cache`. With a cache,
        ``prompt_version`` must be a non-empty string (``Cache.key_for``
        enforces it).
    delay:
        Debounce delay in seconds before ``llm`` is called. A non-positive
        delay arms no timer; call :meth:`flush` to process immediately (this
        is what deterministic tests use).
    on_result, on_chunk, on_error:
        Optional callbacks ``(text, answer)``, ``(text, chunk)`` and
        ``(text, exc)``. Exceptions raised by them are swallowed.
    scheduler:
        Injectable object with ``schedule(delay, fn) -> handle`` and
        ``cancel(handle)``. Tests pass a manual scheduler; production uses
        the daemon-timer default.
    """

    def __init__(
        self,
        llm: Callable[[str], str | Iterator[str]],
        *,
        cache=None,
        delay: float = 0.4,
        on_result: Callable[[str, str], None] | None = None,
        on_chunk: Callable[[str, str], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
        scheduler=None,
    ) -> None:
        self._llm = llm
        cancel = getattr(llm, "cancel", None)
        self._cancel_llm = cancel if callable(cancel) else None
        self._cache = cache
        self._delay = float(delay)
        self._on_result = on_result
        self._on_chunk = on_chunk
        self._on_error = on_error
        self._scheduler = scheduler if scheduler is not None else _TimerScheduler()

        self._cond = threading.Condition()
        self._pending: tuple[str, str | None, str] | None = None
        self._running = False
        self._runner: threading.Thread | None = None
        self._closed = False
        self._timer = None
        self._generation = 0
        self._last_answer: str | None = None
        self._answer_seq = 0

    # Public API
    @property
    def pending(self) -> str | None:
        """The text waiting to be processed, or ``None``."""
        with self._cond:
            return None if self._pending is None else self._pending[0]

    def submit(self, text: str, prompt_version: str = "") -> str | None:
        """Queue ``text`` for prefetching.

        Returns the cached answer (and calls ``on_result`` synchronously) when
        the text is already cached; otherwise returns ``None``. With a cache,
        an empty ``prompt_version`` raises ``ValueError`` via ``Cache.key_for``.
        """
        with self._cond:
            if self._closed:
                return None

        cache = self._cache
        key: str | None = None
        if cache is not None:
            key = cache.key_for(text, prompt_version)
            cached = cache.get(key)
            if cached is not None:
                # The cached answer is the newest work: older pending text
                # must never reach the model after this point.
                with self._cond:
                    if self._closed:
                        return None
                    self._pending = None
                    self._generation += 1
                    self._cancel_timer_locked()
                    in_flight = self._running
                if in_flight:
                    self._cancel_in_flight()
                self._notify_result(text, cached)
                return cached

        with self._cond:
            if self._closed:
                return None
            # Replacing the pending item makes any earlier scheduled callback
            # stale; the generation guard below discards it if it still fires.
            self._pending = (text, key, prompt_version)
            self._generation += 1
            self._cancel_timer_locked()
            self._arm_locked(self._generation)
            in_flight = self._running

        if in_flight:
            self._cancel_in_flight()
        return None

    def flush(self) -> str | None:
        """Process pending work now, synchronously in the calling thread.

        Cancels the debounce timer, waits (without polling) for a request that
        is already running on another thread, then processes the newest
        pending text. Returns the answer produced by this call (or by a
        request that finished while waiting), or ``None``.
        """
        with self._cond:
            if self._closed:
                return None
            self._cancel_timer_locked()
            self._generation += 1  # invalidate any timer that already fired
            start_seq = self._answer_seq
            while self._running and self._runner is not threading.current_thread():
                self._cond.wait()
            # A running request may have armed a follow-up timer on its way
            # out; flush takes over that work now.
            self._cancel_timer_locked()
            if self._closed:
                return None
            if self._pending is None:
                if self._answer_seq != start_seq:
                    return self._last_answer
                return None
            self._generation += 1
        return self._fire(None)

    def cancel_pending(self) -> None:
        """Drop queued work and cancel its timer. Does not interrupt a call."""
        with self._cond:
            self._pending = None
            self._generation += 1
            self._cancel_timer_locked()

    def close(self) -> None:
        """Stop accepting work and drop anything queued. Idempotent."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._generation += 1
            self._cancel_timer_locked()
            self._cond.notify_all()

    # Internals
    def _arm_locked(self, generation: int) -> None:
        """Schedule ``_fire`` for the current pending item, if appropriate."""
        if (
            self._closed
            or self._running
            or self._pending is None
            or self._delay <= 0
        ):
            return
        try:
            self._timer = self._scheduler.schedule(
                self._delay, lambda g=generation: self._fire(g)
            )
        except Exception:
            self._timer = None

    def _cancel_timer_locked(self) -> None:
        handle = self._timer
        self._timer = None
        if handle is not None:
            try:
                self._scheduler.cancel(handle)
            except Exception:
                pass

    def _fire(self, generation: int | None = None) -> str | None:
        """Run the pending request. Called by a timer or by ``flush``.

        ``generation`` is ``None`` for an explicit flush; otherwise a stale
        generation means a newer copy has arrived and this callback is void.
        Never raises for ``llm`` or callback failures.
        """
        with self._cond:
            if self._closed:
                return None
            if generation is not None and generation != self._generation:
                return None
            if self._running or self._pending is None:
                return None
            text, key, _prompt_version = self._pending
            self._pending = None
            self._cancel_timer_locked()
            self._running = True
            self._runner = threading.current_thread()

        answer: str | None = None
        try:
            result = self._llm(text)
            if isinstance(result, str):
                answer = result
                self._notify_result(text, answer)
            else:
                chunks: list[str] = []
                for chunk in result:
                    chunks.append(chunk)
                    self._notify_chunk(text, chunk)
                answer = "".join(chunks)
                self._notify_result(text, answer)
            if answer is not None:
                self._remember(key, text, answer)
        except Exception as exc:  # noqa: BLE001 - a failed request is data
            self._notify_error(text, exc)
            answer = None
        finally:
            with self._cond:
                self._running = False
                self._runner = None
                if answer is not None:
                    self._last_answer = answer
                    self._answer_seq += 1
                self._cond.notify_all()
                if not self._closed and self._pending is not None:
                    self._arm_locked(self._generation)

        return answer

    def _cancel_in_flight(self) -> None:
        """Ask the callable to abort the request that is currently running."""
        cancel = self._cancel_llm
        if cancel is None:
            return
        try:
            cancel()
        except Exception:
            pass

    def _remember(self, key: str | None, text: str, answer: str) -> None:
        """Write to the cache; a failing cache must not lose the answer."""
        if self._cache is None or key is None:
            return
        try:
            self._cache.set(key, answer, text=text)
        except Exception:
            pass

    # Safe callback delivery
    def _notify_result(self, text: str, answer: str) -> None:
        callback = self._on_result
        if callback is None:
            return
        try:
            callback(text, answer)
        except Exception:
            pass

    def _notify_chunk(self, text: str, chunk: str) -> None:
        callback = self._on_chunk
        if callback is None:
            return
        try:
            callback(text, chunk)
        except Exception:
            pass

    def _notify_error(self, text: str, exc: BaseException) -> None:
        callback = self._on_error
        if callback is None:
            return
        try:
            callback(text, exc)
        except Exception:
            pass
