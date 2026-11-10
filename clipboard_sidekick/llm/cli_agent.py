"""cli_agent backend, pipe the prompt to a local CLI agent over stdin.

The command is supplied via the constructor ``command=`` kwarg or the
``CLIPBOARD_SIDEKICK_CLI_CMD`` environment variable; there is deliberately no
default. The combined ``system + "\\n\\n" + prompt`` text is written to stdin;
stdout is returned as the answer.

Streaming is not supported (``stream=True`` raises ``LLMError``).

Never run through a shell: the command is split with ``shlex.split`` and
executed with ``shell=False``. On POSIX, ``shlex.split(cmd)`` is used. On
Windows, ``shlex.split(cmd, posix=False)`` is used instead, which keeps quote
characters inside tokens, a known shlex limitation. Prefer simple commands
(``tool --flag value``) on Windows; wrap complex quoting in a batch/shell
script instead.
"""

from __future__ import annotations

import os
import shlex
import subprocess

from clipboard_sidekick.llm.client import LLMError, _BaseBackend, _truncate

_STDERR_TAIL = 300  # characters of stderr included in non-zero-exit errors


class CliAgentBackend(_BaseBackend):
    name = "cli_agent"
    is_remote = True

    def __init__(self, client, *, base_url=None, api_key=None, command=None, **_ignored) -> None:
        super().__init__(client, base_url=base_url, api_key=api_key)
        self.command = command or os.environ.get("CLIPBOARD_SIDEKICK_CLI_CMD")

    def _argv(self) -> list[str]:
        # Resolve the command lazily so constructing the client never fails.
        cmd = self.command or os.environ.get("CLIPBOARD_SIDEKICK_CLI_CMD")
        if not cmd or not cmd.strip():
            raise LLMError(
                "cli_agent backend requires --cli-cmd or CLIPBOARD_SIDEKICK_CLI_CMD"
            )
        self.command = cmd
        try:
            argv = shlex.split(cmd, posix=(os.name != "nt"))
        except ValueError as exc:
            raise LLMError(f"cli_agent: could not parse command {cmd!r}: {exc}") from exc
        if not argv:
            raise LLMError(
                "cli_agent backend requires --cli-cmd or CLIPBOARD_SIDEKICK_CLI_CMD"
            )
        return argv

    def stream_ask(self, system: str, prompt: str):
        raise LLMError(
            "cli_agent: streaming is not supported by this backend "
            "(the CLI's stdout is captured only after it exits)"
        )

    def ask(self, system: str, prompt: str) -> str:
        self._reset_cancel()
        argv = self._argv()
        stdin_text = f"{system}\n\n{prompt}"
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                f"cli_agent: command not found: {argv[0]!r}, check --cli-cmd / "
                f"CLIPBOARD_SIDEKICK_CLI_CMD"
            ) from exc
        except OSError as exc:
            raise LLMError(f"cli_agent: failed to start {argv[0]!r}: {exc}") from exc

        self._active_process = proc
        try:
            try:
                out, err = proc.communicate(input=stdin_text, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
                raise LLMError(
                    f"cli_agent: command timed out after {self.timeout}s: {argv[0]!r}"
                ) from None
        finally:
            self._active_process = None

        if self._cancelled:
            raise LLMError(f"cli_agent: cancelled while running {argv[0]!r}")
        if proc.returncode != 0:
            tail = _truncate((err or "").strip(), _STDERR_TAIL)
            raise LLMError(
                f"cli_agent: command exited with code {proc.returncode}: "
                f"{argv[0]!r}; stderr tail: {tail or '(empty)'}"
            )
        return out or ""
