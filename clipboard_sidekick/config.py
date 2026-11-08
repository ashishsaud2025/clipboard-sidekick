"""JSON-backed settings for Clipboard Sidekick.

`load_settings` returns defaults for a missing file or missing keys, ignores
unknown keys, and raises a `ValueError` naming the file if the JSON is
malformed. `save_settings` writes UTF-8 JSON (indent=2) via a temp file that
is then atomically replaced onto the target path.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    hotkey: str = "ctrl+alt+space"
    backend: str = "ollama"
    model: str = "llama3.2"
    blocklist: list[str] = field(default_factory=list)
    prefetch_on: bool = True
    max_text_length: int = 4000
    cache_expiry_days: int = 30
    popup_position: str = "cursor"
    theme: str = "system"
    start_with_windows: bool = False
    history_enabled: bool = True


def load_settings(path: str | Path) -> Settings:
    p = Path(path)
    if not p.exists():
        return Settings()

    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"Could not read settings file {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed settings JSON in {p}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Malformed settings file {p}: expected a JSON object at the top level")

    known = {f.name for f in fields(Settings)}
    kwargs: dict[str, Any] = {}
    for key in known:
        if key in data:
            kwargs[key] = data[key]
    return Settings(**kwargs)


def save_settings(settings: Settings, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
