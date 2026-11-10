"""Swappable LLM layer for Clipboard Sidekick.

Re-exports the public API: ``LLMClient``, ``LLMError`` and a thin module-level
``ask(system, prompt, *, stream=False)`` that builds an ``LLMClient`` from the
environment (``CLIPBOARD_SIDEKICK_BACKEND``, default ``ollama``; ``OLLAMA_HOST`` -
default ``http://localhost:11434``; ``CLIPBOARD_SIDEKICK_MODEL``, default
``llama3.2``) and forwards the call.
"""

from __future__ import annotations

import os
from typing import Iterator

from clipboard_sidekick.llm.client import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError", "ask"]


def ask(system: str, prompt: str, *, stream: bool = False) -> str | Iterator[str]:
    backend = os.environ.get("CLIPBOARD_SIDEKICK_BACKEND", "ollama")
    model = os.environ.get("CLIPBOARD_SIDEKICK_MODEL", "llama3.2")
    # OLLAMA_HOST only applies to the ollama backend; other backends keep
    # their own defaults / explicit base_url handling.
    base_url = (os.environ.get("OLLAMA_HOST") or None) if backend == "ollama" else None
    client = LLMClient(backend=backend, model=model, base_url=base_url)
    return client.ask(system, prompt, stream=stream)
