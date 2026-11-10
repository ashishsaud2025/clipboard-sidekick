"""OpenAI-compatible chat-completions backend (OpenAI, Groq, LM Studio, ...).

POSTs to ``{base_url}/chat/completions`` (default ``https://api.openai.com/v1``)
with a Bearer token from the constructor ``api_key`` or ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import json
import os

from clipboard_sidekick.llm.client import LLMError, _BaseBackend

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatBackend(_BaseBackend):
    name = "openai_compatible"
    is_remote = True

    def __init__(self, client, *, base_url=None, api_key=None, **_ignored) -> None:
        super().__init__(client, base_url=base_url, api_key=api_key)
        if self.base_url is None:
            self.base_url = _DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip("/")

    def _resolved_key(self) -> str:
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError(
                "openai_compatible: OPENAI_API_KEY is not set, "
                "set the environment variable or pass api_key=..."
            )
        return key

    def _preflight(self) -> None:
        self._resolved_key()

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._resolved_key()}",
                "Content-Type": "application/json"}

    def _payload(self, system: str, prompt: str, *, stream: bool) -> dict:
        return {
            "model": self._client.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": stream,
        }

    @staticmethod
    def _choice0(data, operation: str) -> dict:
        if not isinstance(data, dict):
            raise LLMError(f"openai_compatible: {operation} returned an unexpected "
                           f"payload type: {type(data).__name__}")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("openai_compatible: response is missing 'choices' "
                           "(expected a non-empty list)")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMError("openai_compatible: 'choices[0]' is not an object")
        return first

    def _parse_response(self, data) -> str:
        first = self._choice0(data, "ask")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMError("openai_compatible: response is missing 'choices[0].message'")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMError("openai_compatible: 'choices[0].message.content' is missing "
                           f"or not a string (got {type(content).__name__})")
        return content

    def _parse_stream_line(self, line: str):
        if not line.startswith("data:"):
            return ""  # skip SSE event/comment lines and keep-alives
        payload = line[len("data:"):].strip()
        if not payload:
            return ""
        if payload == "[DONE]":
            return None  # end of stream
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return ""  # skip invalid JSON lines
        if not isinstance(obj, dict):
            return ""
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""  # e.g. usage-only chunks; nothing to yield
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        delta = first.get("delta")
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        return content if isinstance(content, str) and content else ""
