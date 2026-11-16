"""Entry point for Clipboard Sidekick."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .cache import Cache
from .clipboard.watcher import ClipboardWatcher
from .clipboard.formats import has_sensitive_format as _qt_sensitive
from .config import load_settings, save_settings
from .hotkey import register_hotkey, unregister
from .llm.client import LLMClient, LLMError
from .popup import Popup
from .prefetch import PrefetchQueue
from .redaction import redact
from .router import classify
from .tray import TrayApp

if sys.platform == "win32":
    from .platform import windows as _platform
else:
    from .platform import common as _platform


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(prompt_type: str) -> tuple[str, str]:
    """Return (system_text, version) for *prompt_type*.

    Reads ``clipboard_sidekick/prompts/<file>`` resolved relative to this
    package (never CWD). The first line is ``VERSION: n``; it and the
    following blank line are stripped from the body.
    """
    try:
        from .router import PROMPT_FOR

        filename = PROMPT_FOR.get(prompt_type, "fallback.txt")
    except Exception:
        filename = "fallback.txt"
    path = _PROMPTS_DIR / filename
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ("You are a helpful assistant.", "0")
    lines = text.splitlines()
    version = "0"
    body = lines
    if lines and lines[0].strip().upper().startswith("VERSION:"):
        try:
            version = lines[0].split(":", 1)[1].strip() or "0"
        except Exception:
            version = "0"
        body = lines[1:]
        if body and body[0].strip() == "":
            body = body[1:]
    system = "\n".join(body).strip()
    if not system:
        system = "You are a helpful assistant."
    return (system, version)


class _LlmAdapter:
    """Adapter between PrefetchQueue and LLMClient.

    Classifies the text, loads the matching prompt, applies redaction as a
    guard, and streams the answer. ``cancel`` is a no-op when nothing is
    active, so it is safe to call from the wiring layer at any time.

    Streaming is attempted first; backends that do not support it (notably
    ``cli_agent``, which raises ``LLMError`` with "streaming is not
    supported" in the message) fall back to a non-streaming request so the
    popup still answers.
    """

    def __init__(self, client, *, classify_fn=classify, prompt_loader=_load_prompt):
        self.client = client
        self.classify_fn = classify_fn
        self.prompt_loader = prompt_loader
        self._active = False

    def __call__(self, text):
        self._active = True
        try:
            ctype = self.classify_fn(text)
            system, _version = self.prompt_loader(ctype)
            cleaned, triggered = redact(text)
            if triggered:
                raise RuntimeError("redacted content")
            try:
                answer = self.client.ask(system, cleaned, stream=True)
            except LLMError as exc:
                if "stream" in str(exc).lower():
                    answer = self.client.ask(system, cleaned, stream=False)
                else:
                    raise
            if isinstance(answer, str):
                self._active = False
                return answer

            def gen():
                try:
                    for chunk in answer:
                        yield chunk
                finally:
                    self._active = False

            return gen()
        except BaseException:
            self._active = False
            raise

    def cancel(self):
        if self._active:
            try:
                self.client.cancel()
            except Exception:
                pass

    def reset_active(self):
        self._active = False


class AppContext:
    """Mutable container for the wired application state."""

    def __init__(self):
        self.app = None
        self.settings = None
        self.settings_path = None
        self.cache = None
        self.llm_client = None
        self.llm_adapter = None
        self.watcher = None
        self.prefetch = None
        self.popup = None
        self.tray = None
        self.paused = False
        self.last_text = None
        self.last_answer = None
        self.pending_sensitive_text = None
        self.hotkey_combo = None


def _make_blocked_check(settings):
    def blocked_check():
        try:
            proc = _platform.get_foreground_process_name()
            title = _platform.get_foreground_window_title()
        except Exception:
            proc = None
            title = None
        try:
            blocklist = getattr(settings, "blocklist", None)
            if not blocklist:
                blocklist = list(getattr(_platform, "DEFAULT_BLOCKLIST", ()))
            else:
                blocklist = list(blocklist)
        except Exception:
            blocklist = []
        try:
            if _platform.is_blocked(proc, blocklist, window_title=title):
                return True
        except Exception:
            return True
        try:
            if _qt_sensitive():
                return True
        except Exception:
            return True  # fail-closed (matches the watcher's check)
        return False

    return blocked_check


def build_application(settings_path=None, app=None):
    """Wire all components and return the application context.

    Tests can call this with a temp settings path and an existing QApplication
    to avoid ``exec()``.
    """
    if app is None:
        try:
            app = QApplication.instance()
        except Exception:
            app = None
        if app is None:
            app = QApplication(sys.argv)
    if settings_path is None:
        settings_path = Path.home() / ".clipboard-sidekick" / "settings.json"
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    ctx = AppContext()
    ctx.app = app
    ctx.settings_path = settings_path

    settings = load_settings(settings_path)
    ctx.settings = settings
    ctx.hotkey_combo = getattr(settings, "hotkey", None)

    cache = Cache(
        settings_path.parent / "cache.db",
        expiry_days=getattr(settings, "cache_expiry_days", 30),
        history_enabled=getattr(settings, "history_enabled", True),
    )
    ctx.cache = cache

    llm_client = LLMClient(
        getattr(settings, "backend", "ollama"),
        getattr(settings, "model", "llama3.2"),
        timeout=30.0,
    )
    ctx.llm_client = llm_client

    adapter = _LlmAdapter(llm_client)
    ctx.llm_adapter = adapter

    # Callbacks (closures over ctx)
    def on_copy(text):
        if ctx.paused:
            return
        try:
            _cleaned, triggered = redact(text)
        except Exception:
            triggered = False
            _cleaned = text
        if triggered:
            ctx.pending_sensitive_text = text
            return
        ctx.pending_sensitive_text = None
        ctx.last_text = text
        try:
            ctype = classify(text)
            _system, version = _load_prompt(ctype)
        except Exception:
            version = "0"
        try:
            ctx.prefetch.submit(text, prompt_version=version)
        except Exception:
            pass

    def on_prefetch_result(text, answer):
        ctx.last_text = text
        ctx.last_answer = answer
        try:
            if ctx.popup is not None and ctx.popup.isVisible():
                ctx.popup.set_answer(answer, remote=ctx.llm_client.is_remote)
        except Exception:
            pass

    def on_prefetch_chunk(text, chunk):
        try:
            if (
                ctx.popup is not None
                and ctx.popup.isVisible()
                and ctx.last_text == text
            ):
                ctx.popup.answer_view.insertPlainText(str(chunk))
        except Exception:
            pass

    def on_prefetch_error(text, exc):
        try:
            if ctx.popup is not None and ctx.popup.isVisible():
                ctx.popup.set_answer(
                    f"Error: {exc}", remote=ctx.llm_client.is_remote
                )
        except Exception:
            pass

    def on_hotkey():
        try:
            cb = QGuiApplication.clipboard()
            text = cb.text() if cb is not None else ""
        except Exception:
            text = ""
        if not text:
            return
        remote = False
        try:
            remote = ctx.llm_client.is_remote
        except Exception:
            pass
        version = "0"
        try:
            ctype = classify(text)
            _system, version = _load_prompt(ctype)
        except Exception:
            pass
        answer = None
        if text == ctx.last_text and ctx.last_answer is not None:
            answer = ctx.last_answer
        elif ctx.cache is not None:
            try:
                key = ctx.cache.key_for(text, version)
                answer = ctx.cache.get(key)
            except Exception:
                answer = None
        sensitive = False
        try:
            _c, triggered = redact(text)
            sensitive = bool(triggered)
        except Exception:
            sensitive = False
        if sensitive:
            ctx.pending_sensitive_text = text
            try:
                ctx.popup.show_for(
                    text, "Sensitive content - ask anyway?",
                    remote=remote, cached=False,
                )
            except Exception:
                pass
            return
        if answer is None:
            if ctx.paused:
                try:
                    ctx.popup.show_for(
                        text,
                        "Paused \u2014 resume from tray to prefetch.",
                        remote=remote,
                        cached=False,
                    )
                except Exception:
                    pass
                return
            try:
                ctx.popup.show_for(text, None, remote=remote, cached=False)
            except Exception:
                pass
            try:
                ctx.prefetch.submit(text, prompt_version=version)
            except Exception:
                pass
        else:
            try:
                ctx.popup.show_for(text, answer, remote=remote, cached=True)
            except Exception:
                pass


    def on_follow_up(question):
        q = (question or "").strip() if isinstance(question, str) else ""
        if not q:
            return
        if ctx.pending_sensitive_text:
            try:
                cleaned = redact(ctx.pending_sensitive_text)[0]
            except Exception:
                cleaned = "[redacted]"
            q = f"Context (redacted):\n{cleaned}\n\n{q}"
            ctx.pending_sensitive_text = None
        remote = False
        try:
            remote = ctx.llm_client.is_remote
        except Exception:
            pass
        try:
            if ctx.popup is not None:
                ctx.popup.set_answer("Thinking...", remote=remote)
        except Exception:
            pass
        try:
            system = "You are a helpful assistant. Answer concisely."
            ans = ctx.llm_client.ask(system, q)
            if ctx.popup is not None:
                ctx.popup.set_answer(ans, remote=remote)
        except Exception as exc:
            try:
                if ctx.popup is not None:
                    ctx.popup.set_answer(f"Error: {exc}", remote=remote)
            except Exception:
                pass

    def on_pause_changed(paused):
        ctx.paused = bool(paused)

    def on_settings_saved(s):
        try:
            if ctx.llm_adapter is not None:
                ctx.llm_adapter.cancel()
        except Exception:
            pass
        ctx.settings = s
        try:
            if ctx.settings_path is not None:
                save_settings(s, ctx.settings_path)
        except Exception:
            pass
        try:
            if ctx.watcher is not None:
                ctx.watcher._max_text_length = getattr(s, "max_text_length", 4000)
                ctx.watcher.blocked_check = _make_blocked_check(s)
        except Exception:
            pass
        try:
            unregister()
        except Exception:
            pass
        try:
            register_hotkey(on_hotkey, getattr(s, "hotkey", "ctrl+alt+space"))
            ctx.hotkey_combo = getattr(s, "hotkey", None)
        except Exception:
            pass
        try:
            ctx.llm_client = LLMClient(
                getattr(s, "backend", "ollama"),
                getattr(s, "model", "llama3.2"),
                timeout=30.0,
            )
            if ctx.llm_adapter is not None:
                ctx.llm_adapter.client = ctx.llm_client
                ctx.llm_adapter.reset_active()
        except Exception:
            pass
        try:
            if ctx.cache is not None:
                try:
                    ctx.cache.close()
                except Exception:
                    pass
            ctx.cache = Cache(
                ctx.settings_path.parent / "cache.db",
                expiry_days=getattr(s, "cache_expiry_days", 30),
                history_enabled=getattr(s, "history_enabled", True),
            )
            if ctx.prefetch is not None:
                ctx.prefetch.set_cache(ctx.cache)
        except Exception:
            pass
        try:
            if ctx.tray is not None:
                ctx.tray.settings = s
                ctx.tray.cache = ctx.cache
        except Exception:
            pass

    # Construct components
    watcher = ClipboardWatcher(
        on_change=on_copy,
        blocked_check=_make_blocked_check(settings),
        max_text_length=getattr(settings, "max_text_length", 4000),
    )
    ctx.watcher = watcher

    popup = Popup()
    ctx.popup = popup
    try:
        popup.follow_up_submitted.connect(on_follow_up)
    except Exception:
        pass

    prefetch = PrefetchQueue(
        llm=adapter,
        cache=cache,
        delay=0.4,
        on_result=on_prefetch_result,
        on_chunk=on_prefetch_chunk,
        on_error=on_prefetch_error,
    )
    ctx.prefetch = prefetch

    tray = TrayApp(
        settings,
        cache,
        settings_path=settings_path,
        on_pause_changed=on_pause_changed,
        on_settings_saved=on_settings_saved,
    )
    ctx.tray = tray

    watcher.start()
    try:
        register_hotkey(on_hotkey, getattr(settings, "hotkey", "ctrl+alt+space"))
        ctx.hotkey_combo = getattr(settings, "hotkey", None)
    except Exception as exc:
        print(f"hotkey registration failed: {exc}")

    try:
        tray.show()
    except Exception:
        pass

    return app, ctx


def main() -> int:
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        try:
            app.setApplicationName("Clipboard Sidekick")
        except Exception:
            pass
        settings_path = Path.home() / ".clipboard-sidekick" / "settings.json"
        app, ctx = build_application(settings_path, app=app)
        return int(app.exec())
    except Exception as exc:
        print(f"Clipboard Sidekick failed to start: {exc}")
        return 1


if __name__ == "__main__":
    main()
