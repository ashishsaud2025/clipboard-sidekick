"""Anthropic Messages API backend, brings your own key, no SDK.

POSTs to ``https://api.anthropic.com/v1/messages`` with ``x-api-key`` from the
constructor ``api_key`` or ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import json
import os

from clipboard_sidekick.llm.client import LLMError, _BaseBackend

_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"


class AnthropicBackend(_BaseBackend):
    name = "anthropic"
    is_remote = True

    def _resolved_key(self) -> str:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError(
                "anthropic: ANTHROPIC_API_KEY is not set, "
                "set the environment variable or pass api_key=..."
            )
        return key

    def _preflight(self) -> None:
        self._resolved_key()

    def _url(self) -> str:
        return _URL

    def _headers(self) -> dict:
        return {
            "x-api-key": self._resolved_key(),
            "anthropic-version": _VERSION,
            "content-type": "application/json",
        }

    def _payload(self, system: str, prompt: str, *, stream: bool) -> dict:
        return {
            "model": self._client.model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }

    def _parse_response(self, data) -> str:
        if not isinstance(data, dict):
            raise LLMError(f"anthropic: ask returned an unexpected payload type: "
                           f"{type(data).__name__}")
        content = data.get("content")
        if not isinstance(content, list) or not content:
            raise LLMError("anthropic: response is missing 'content' "
                           "(expected a non-empty list)")
        for block in content:  # first text block wins
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
                raise LLMError("anthropic: 'content[].text' block is present but "
                               f"'text' is not a string (got {type(text).__name__})")
        raise LLMError("anthropic: response contains no text block in 'content'")

    def _parse_stream_line(self, line: str):
        # The `event:` line carries the same type as the JSON `type` field, so
        # parsing `data:` lines alone is sufficient and robust to keep-alives.
        if not line.startswith("data:"):
            return ""  # skip event:/keep-alive/comment lines
        payload = line[len("data:"):].strip()
        if not payload:
            return ""
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return ""  # skip invalid JSON lines
        if not isinstance(obj, dict):
            return ""
        kind = obj.get("type")
        if kind == "message_stop":
            return None  # end of stream
        if kind != "content_block_delta":
            return ""  # ping, message_start, content_block_start/stop, ...
        delta = obj.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return ""  # e.g. input_json_delta for tool use; skipped
        text = delta.get("text")
        return text if isinstance(text, str) and text else ""
