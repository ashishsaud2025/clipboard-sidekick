"""Ollama backend, the default, fully local.

POSTs to ``{OLLAMA_HOST}/api/generate`` (default ``http://localhost:11434``).
No authentication is sent (the ``Authorization`` header is left unset).
"""

from __future__ import annotations

import json
import os

from clipboard_sidekick.llm.client import LLMError, _BaseBackend

_DEFAULT_HOST = "http://localhost:11434"


class OllamaBackend(_BaseBackend):
    name = "ollama"
    is_remote = False

    def __init__(self, client, *, base_url=None, api_key=None, **_ignored) -> None:
        super().__init__(client, base_url=base_url, api_key=api_key)
        if self.base_url is None:
            self.base_url = os.environ.get("OLLAMA_HOST", _DEFAULT_HOST)
        self.base_url = self.base_url.rstrip("/")

    def _url(self) -> str:
        return f"{self.base_url}/api/generate"

    def _headers(self) -> dict:
        return {}  # no auth; Authorization intentionally omitted

    def _payload(self, system: str, prompt: str, *, stream: bool) -> dict:
        return {"model": self._client.model, "prompt": prompt,
                "system": system, "stream": stream}

    def _unreachable(self, operation: str, exc: Exception) -> LLMError:
        reason = exc.__class__.__name__ + ((": " + str(exc)) if str(exc) else "")
        return LLMError(
            f"Cannot reach Ollama at {self.base_url} ({reason}), "
            f"is the Ollama service running?"
        )

    def _parse_response(self, data) -> str:
        if not isinstance(data, dict):
            raise LLMError(f"ollama: ask returned an unexpected payload type: "
                           f"{type(data).__name__}")
        value = data.get("response", "")
        if not isinstance(value, str):
            raise LLMError("ollama: ask returned a malformed 'response' field "
                           f"(expected str, got {type(value).__name__})")
        return value.strip()

    def _parse_stream_line(self, line: str):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return ""  # skip unparseable NDJSON lines
        if not isinstance(obj, dict):
            return ""
        if obj.get("done") is True:
            return None  # end of stream
        chunk = obj.get("response", "")
        return chunk if isinstance(chunk, str) else ""
