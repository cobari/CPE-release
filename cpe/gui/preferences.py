"""What the search window remembers between launches: language and text folder.

Stored as JSON in the user's configuration folder of each platform. A missing or broken
file means "nothing remembered": the window then starts in the OS language with no
texts, never with an error.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

APP = "classic-pattern-explorer"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP


class Preferences:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "gui.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> str:
        value = self.load().get(key, "")
        return value if isinstance(value, str) else ""

    def set(self, key: str, value: str) -> None:
        data = self.load()
        data[key] = value
        content = json.dumps(data, ensure_ascii=False, indent=1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary file and moved into place, so a crash mid-write
        # leaves the old file instead of an empty one.
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temporary, self.path)
