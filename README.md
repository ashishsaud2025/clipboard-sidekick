# Clipboard Sidekick

A small background app. Copy anything, press a hotkey, and a popup shows a brief explanation of what you copied.

## What it is

Clipboard Sidekick is a Windows 11 desktop app (Python 3.11+, PySide6) that watches the clipboard, detects what kind of content you copied, and asks a language model for a short explanation the moment you ask for it. It is local-first: by default everything runs against a local Ollama instance and no content leaves your machine.

## Default hotkey

`Ctrl+Alt+Space` opens the explanation popup for the current clipboard contents. The hotkey is configurable in settings.

## Backends

- `ollama` (default), local models via Ollama.
- `openai_compatible`, any OpenAI-compatible HTTP endpoint (local or hosted), configured in settings.
- `anthropic`, Anthropic API; the API key is read from an environment variable and never written to the settings file.
- `cli_agent`, shells out to a locally installed CLI agent.

## Privacy notes

- Local by default: with the `ollama` backend, clipboard content and prompts stay on your machine.
- Blocked sources are skipped: entries in the `blocklist` (e.g. password managers) are never read from the clipboard or sent to any backend.
- History: copy history can be disabled or cleared at any time; nothing is retained beyond what you configure.
- The anthropic backend only authenticates via an environment variable, so keys never reach disk in the settings file.

## Wayland / global-hotkey caveat

Global hotkeys depend on the platform's ability to register a system-wide shortcut. On Wayland (and some locked-down or remote desktop sessions), global hotkey capture may be blocked by the compositor or session policy; the app may need to fall back to a focused window or a different trigger mechanism, or hotkeys may not work at all. On Windows 11 this is generally not an issue.
