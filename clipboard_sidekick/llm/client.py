"""Shared LLM client machinery.

`LLMError` is the single expected-failure type for every backend: unreachable
hosts, timeouts, auth failures and malformed payloads all surface as
`LLMError` with the backend name, the failing operation and the reason.
Programming errors (bad argument types, etc.) propagate unchanged.

`_BaseBackend` holds the behavior shared by all backends (cancellation flag,
in-flight response/process tracking, timeout access, HTTP status checking).
Backends supply only the moving parts: URL, headers, payload and parsing.
"""

from __future__ import annotations

from typing import Iterator

import requests

_MAX_BODY = 300  # truncated error-body length for LLMError messages
_VALID_BACKENDS = ("ollama", "openai_compatible", "anthropic", "cli_agent")


class LLMError(Exception):
    """Expected failure from any LLM backend (unreachable, auth, bad shape...)."""


def _truncate(text: object, limit: int = _MAX_BODY) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


def _decode_line(line) -> str:
    if isinstance(line, bytes):
        return line.decode("utf-8", "replace")
    return line or ""


class _BaseBackend:
    """Common behavior for all backends; not part of the public API."""

    name = "base"
    is_remote = False

    def __init__(self, client: "LLMClient", *, base_url: str | None = None,
                 api_key: str | None = None) -> None:
        self._client = client
        self.base_url = base_url  # subclasses apply their own default
        self.api_key = api_key
        self._cancelled = False
        self._active_response = None
        self._active_process = None

    # Shared accessors
    @property
    def timeout(self) -> float:
        """Read at call time from the owning client (never copied)."""
        return self._client.timeout

    # Cancellation
    def cancel(self) -> None:
        """Request cooperative cancellation.

        Sets the flag checked between stream chunks and closes/terminates
        the in-flight HTTP response or subprocess, if any. Safe to call
        when nothing is in flight.
        """
        self._cancelled = True
        resp = self._active_response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass  # swallow close errors; cancellation is best-effort
        proc = self._active_process
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _reset_cancel(self) -> None:
        self._cancelled = False

    # Shared helpers
    def _check_status(self, response, operation: str) -> None:
        if response.status_code != 200:
            body = _truncate(getattr(response, "text", ""))
            raise LLMError(
                f"{self.name}: {operation} failed with HTTP {response.status_code}: {body}"
            )

    def _unreachable(self, operation: str, exc: Exception) -> LLMError:
        host = getattr(self, "base_url", None) or "unknown host"
        reason = exc.__class__.__name__ + ((": " + str(exc)) if str(exc) else "")
        return LLMError(f"{self.name}: cannot reach {host} during {operation} ({reason})")

    def _post(self, operation: str, url: str, **kwargs):
        try:
            return requests.post(url, **kwargs)
        except requests.RequestException as exc:  # ConnectionError/Timeout included
            raise self._unreachable(operation, exc) from exc

    def _iter_lines(self, response, operation: str) -> Iterator[str]:
        try:
            for raw in response.iter_lines(decode_unicode=True):
                line = _decode_line(raw).strip()
                if line:
                    yield line
        except requests.RequestException as exc:
            if self._cancelled:
                return  # closed underneath us by cancel(); end of stream
            raise LLMError(
                f"{self.name}: {operation} stream failed: {exc.__class__.__name__}"
            ) from exc

    def _finish_response(self, response) -> None:
        self._active_response = None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    # Template: non-stream
    def ask(self, system: str, prompt: str) -> str:
        self._reset_cancel()
        self._preflight()
        url = self._url()
        operation = "ask"
        response = self._post(
            operation, url,
            json=self._payload(system, prompt, stream=False),
            headers=self._headers(),
            timeout=self.timeout,
        )
        self._check_status(response, operation)
        try:
            data = response.json()
        except Exception as exc:
            raise LLMError(
                f"{self.name}: {operation} returned invalid JSON: {_truncate(exc)}"
            ) from exc
        return self._parse_response(data)

    # Template: stream
    def stream_ask(self, system: str, prompt: str) -> Iterator[str]:
        self._reset_cancel()
        self._preflight()
        return self._stream_generator(system, prompt)

    def _stream_generator(self, system: str, prompt: str) -> Iterator[str]:
        operation = "stream"
        response = None
        try:
            response = self._post(
                operation, self._url(),
                json=self._payload(system, prompt, stream=True),
                headers=self._headers(),
                timeout=self.timeout,
                stream=True,
            )
            self._active_response = response
            self._check_status(response, operation)
            for line in self._iter_lines(response, operation):
                if self._cancelled:
                    return
                chunk = self._parse_stream_line(line)
                if chunk is None:  # end-of-stream sentinel (e.g. [DONE], message_stop)
                    return
                if chunk:
                    yield chunk
        finally:
            self._finish_response(response)

    # Hooks for subclasses
    def _preflight(self) -> None:
        """Raise LLMError before any network I/O (e.g. missing key/command)."""

    def _url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        return {}

    def _payload(self, system: str, prompt: str, *, stream: bool) -> dict:
        raise NotImplementedError

    def _parse_response(self, data) -> str:
        raise NotImplementedError

    def _parse_stream_line(self, line: str):
        """Return the chunk text, '' to skip, or None to stop the stream."""
        raise NotImplementedError


class LLMClient:
    """Swappable LLM client. Backend picked at construction time."""

    def __init__(self, backend: str = "ollama", model: str = "llama3.2", *,
                 base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 30.0, **backend_kwargs) -> None:
        if backend not in _VALID_BACKENDS:
            raise LLMError(
                f"Unknown backend: {backend!r}. Valid backends: "
                + ", ".join(_VALID_BACKENDS)
            )
        self.backend = backend
        self.model = model
        self.timeout = timeout
        if backend == "ollama":
            from clipboard_sidekick.llm.ollama import OllamaBackend as cls
        elif backend == "openai_compatible":
            from clipboard_sidekick.llm.openai_compat import OpenAICompatBackend as cls
        elif backend == "anthropic":
            from clipboard_sidekick.llm.anthropic import AnthropicBackend as cls
        else:  # cli_agent
            from clipboard_sidekick.llm.cli_agent import CliAgentBackend as cls
        self._backend = cls(self, base_url=base_url, api_key=api_key, **backend_kwargs)

    @property
    def is_remote(self) -> bool:
        """True for backends that send data off the machine (popup indicator)."""
        return self._backend.is_remote

    def ask(self, system: str, prompt: str, *, stream: bool = False) -> str | Iterator[str]:
        if stream:
            return self._backend.stream_ask(system, prompt)
        return self._backend.ask(system, prompt)

    def cancel(self) -> None:
        """Cooperative cancellation; safe to call when nothing is in flight."""
        self._backend.cancel()
