"""Tests for clipboard_sidekick.llm, fully mocked, zero network."""

from __future__ import annotations

import json

import pytest
import requests

from clipboard_sidekick.llm.client import LLMClient, LLMError


# Fakes

class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, json_data=None, lines=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self._lines = lines if lines is not None else []
        self.text = text
        self.closed = False

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data

    def iter_lines(self, decode_unicode=True, **_kwargs):
        for line in self._lines:
            yield line

    def close(self):
        self.closed = True


def _ndjson(*objects) -> list[str]:
    return [json.dumps(obj) for obj in objects]


# 1. Non-stream ask returns a string (payload adapted to the mocked backend)

def test_ask_non_stream_returns_str(monkeypatch):
    from clipboard_sidekick.llm.client import LLMClient
    client = LLMClient(backend="ollama", model="llama3.2")
    # monkeypatch the backend's ask to return a fixed string
    monkeypatch.setattr(client._backend, "ask", lambda system, prompt: "fixed answer")
    result = client.ask("sys", "user")
    assert isinstance(result, str)
    assert result == "fixed answer"


# 2. Streaming yields chunks in order

def test_stream_yields_chunks_in_order(monkeypatch):
    lines = _ndjson(
        {"response": "Hello"},
        {"response": ", "},
        {"response": "world"},
        {"response": "", "done": True},
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=lines))
    client = LLMClient(backend="ollama", model="llama3.2")
    result = client.ask("sys", "user", stream=True)
    assert iter(result) is result
    assert list(result) == ["Hello", ", ", "world"]


# 3. Connection refused -> LLMError naming the host and the Ollama hint

def test_connection_refused_raises_clear_llm_error(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    def boom(*args, **kwargs):
        raise requests.ConnectionError("Connection refused")

    monkeypatch.setattr(requests, "post", boom)
    client = LLMClient(backend="ollama", model="llama3.2")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    msg = str(excinfo.value)
    assert "localhost:11434" in msg
    assert "is the Ollama service running" in msg


# 4. cli_agent without a command -> LLMError

def test_cli_agent_without_command_raises(monkeypatch):
    monkeypatch.delenv("CLIPBOARD_SIDEKICK_CLI_CMD", raising=False)
    client = LLMClient(backend="cli_agent")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "CLIPBOARD_SIDEKICK_CLI_CMD" in str(excinfo.value)


# 5. anthropic without env key -> LLMError naming ANTHROPIC_API_KEY

def test_anthropic_without_env_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(backend="anthropic")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


# 6. Unknown backend -> LLMError naming the four valid values

def test_unknown_backend_raises_with_valid_names():
    with pytest.raises(LLMError) as excinfo:
        LLMClient(backend="bogus")
    msg = str(excinfo.value)
    for name in ("ollama", "openai_compatible", "anthropic", "cli_agent"):
        assert name in msg


# 7. cancel() mid-stream stops further chunks and closes the response

def test_cancel_mid_stream_stops_further_chunks(monkeypatch):
    lines = _ndjson(
        {"response": "a"},
        {"response": "b"},
        {"response": "c"},
        {"done": True},
    )
    fake = FakeResponse(200, lines=lines)
    monkeypatch.setattr(requests, "post", lambda *a, **k: fake)
    client = LLMClient(backend="ollama")
    collected = []
    for chunk in client.ask("sys", "user", stream=True):
        collected.append(chunk)
        client.cancel()
    assert collected == ["a"]
    assert fake.closed is True


def test_cancel_when_nothing_in_flight_is_safe():
    client = LLMClient(backend="ollama")
    client.cancel()  # must not raise


def test_pre_flight_cancel_blocks_non_stream_dispatch(monkeypatch):
    issued = []

    def fake_post(*args, **kwargs):
        issued.append((args, kwargs))
        return FakeResponse(200, json_data={"response": "never returned"})

    monkeypatch.setattr(requests, "post", fake_post)
    client = LLMClient(backend="ollama")
    client.cancel()  # cancel requested before dispatch
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "cancel" in str(excinfo.value).lower()
    assert issued == []  # no HTTP request was issued


def test_pre_flight_cancel_blocks_stream_dispatch(monkeypatch):
    issued = []
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: issued.append((a, k)))
    client = LLMClient(backend="ollama")
    client.cancel()  # cancel requested before dispatch
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user", stream=True)
    assert "cancel" in str(excinfo.value).lower()
    assert issued == []  # no HTTP request was issued


def test_cancel_during_non_stream_call_raises_and_discards_answer(monkeypatch):
    client = LLMClient(backend="ollama")

    def fake_post(*args, **kwargs):
        client.cancel()  # cancel lands mid-call, while the request is in flight
        return FakeResponse(200, json_data={"response": "should be discarded"})

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "cancel" in str(excinfo.value).lower()


# 8. is_remote mapping for all four backends

@pytest.mark.parametrize(
    "backend,expected",
    [
        ("ollama", False),
        ("openai_compatible", True),
        ("anthropic", True),
        ("cli_agent", True),
    ],
)
def test_is_remote_mapping(backend, expected):
    assert LLMClient(backend=backend).is_remote is expected


# 9. openai_compat non-200 -> status in message

def test_openai_compat_non_200_mentions_status(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(401, text="invalid api key")
    )
    client = LLMClient(backend="openai_compatible")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "401" in str(excinfo.value)


# 10. Module-level ask(...) still returns str with a mocked client

def test_module_level_ask_returns_str(monkeypatch):
    import clipboard_sidekick.llm as llm_module

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, system, prompt, *, stream=False):
            assert stream is False
            return "mocked answer"

    monkeypatch.setattr(llm_module, "LLMClient", FakeClient)
    result = llm_module.ask("sys", "user")
    assert isinstance(result, str)
    assert result == "mocked answer"


# Extra coverage: stream error paths and payload shapes

def test_ollama_non_200_includes_status_and_truncated_body(monkeypatch):
    body = "x" * 1000
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(500, text=body))
    client = LLMClient(backend="ollama")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    msg = str(excinfo.value)
    assert "500" in msg
    assert len(msg) < 1000  # body is truncated


def test_openai_stream_stops_at_done_marker(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    lines = [
        'data: {"choices": [{"delta": {"content": "Hello"}}]}',
        "data: not-json-at-all",          # invalid line skipped
        "",                                # empty line skipped
        'data: {"choices": [{"delta": {}}]}',  # no content -> skipped
        'data: {"choices": [{"delta": {"content": " world"}}]}',
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "IGNORED"}}]}',
    ]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=lines))
    client = LLMClient(backend="openai_compatible")
    assert list(client.ask("sys", "user", stream=True)) == ["Hello", " world"]


def test_anthropic_stream_yields_text_deltas_and_stops(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    lines = [
        "event: content_block_start",
        'data: {"type": "content_block_start", "index": 0}',
        "event: content_block_delta",
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}',
        'data: {"type": "ping"}',
        'data: {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}}',
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "!"}}',
        "event: message_stop",
        'data: {"type": "message_stop"}',
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "IGNORED"}}',
    ]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=lines))
    client = LLMClient(backend="anthropic")
    assert list(client.ask("sys", "user", stream=True)) == ["Hi", "!"]


def test_anthropic_non_stream_reads_first_text_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    payload = {"content": [{"type": "thinking", "thinking": "hmm"},
                           {"type": "text", "text": "answer"}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data=payload))
    client = LLMClient(backend="anthropic")
    assert client.ask("sys", "user") == "answer"


def test_anthropic_non_200_includes_status(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(429, text="rate limited"))
    client = LLMClient(backend="anthropic")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "429" in str(excinfo.value)


def test_openai_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = LLMClient(backend="openai_compatible")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_cli_agent_stream_unsupported(monkeypatch):
    monkeypatch.setenv("CLIPBOARD_SIDEKICK_CLI_CMD", "echo hi")
    client = LLMClient(backend="cli_agent")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user", stream=True)
    assert "stream" in str(excinfo.value).lower()


def test_cli_agent_non_zero_exit_raises_with_code_and_stderr(monkeypatch):
    class FakeProc:
        returncode = 3

        def communicate(self, input=None, timeout=None):
            return ("", "boom happened")

        def terminate(self):
            pass

    monkeypatch.setattr("clipboard_sidekick.llm.cli_agent.subprocess.Popen",
                        lambda *a, **k: FakeProc())
    client = LLMClient(backend="cli_agent", command="some-tool --flag")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    msg = str(excinfo.value)
    assert "3" in msg
    assert "boom happened" in msg


def test_cli_agent_success_returns_stdout(monkeypatch):
    class FakeProc:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            assert input == "sys\n\nuser"
            return ("the answer\n", "")

        def terminate(self):
            pass

    monkeypatch.setattr("clipboard_sidekick.llm.cli_agent.subprocess.Popen",
                        lambda *a, **k: FakeProc())
    client = LLMClient(backend="cli_agent", command="some-tool")
    assert client.ask("sys", "user") == "the answer\n"


def test_malformed_payload_shape_raises_llm_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data={"nope": []}))
    client = LLMClient(backend="openai_compatible")
    with pytest.raises(LLMError) as excinfo:
        client.ask("sys", "user")
    assert "choices" in str(excinfo.value)


def test_timeout_lives_on_client_and_is_read_by_backends():
    client = LLMClient(backend="ollama", timeout=12.5)
    assert client.timeout == 12.5
    assert client._backend.timeout == 12.5
