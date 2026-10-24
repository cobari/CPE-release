"""What the search window remembers between launches: language and text folders.

Stored as JSON in the user's configuration folder of each platform. A missing or broken
file means "nothing remembered": the window then starts in the OS language with no
texts, never with an error.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from cpe.local.file_lock import FileBusyError, exclusive

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
    # Contended saves wait briefly instead of failing at once: a second window
    # may just be finishing its own write. Reads stay lock-free so a stuck
    # writer can never block the window from starting.
    _LOCK_TRIES = 10
    _LOCK_DELAY = 0.02

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "gui.json"
        self._write_lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        return self._read(strict=False)

    def _read(self, *, strict: bool) -> dict[str, Any]:
        """Stored values; a missing file means nothing remembered.

        A write must not treat a transient read failure as an empty file, or it
        would drop every other setting. `strict` re-raises that OSError for the
        caller to report instead of overwriting. Undecodable bytes or invalid
        JSON are a corrupt file: a write moves it aside first so the bytes are
        kept, and starts from defaults.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except UnicodeDecodeError:
            if strict:
                self._preserve_corrupt()
                return {}
            return {}
        except OSError:
            if strict:
                raise
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            if strict:
                self._preserve_corrupt()
            return {}
        return data if isinstance(data, dict) else {}

    def _preserve_corrupt(self) -> None:
        """Keep an unreadable file beside the new one instead of deleting it."""
        target = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
        try:
            self.path.rename(target)
        except OSError:
            pass

    def get(self, key: str) -> str:
        value = self.load().get(key, "")
        return value if isinstance(value, str) else ""

    def set(self, key: str, value: str) -> None:
        self.update({key: value})

    def update(self, values: dict[str, Any]) -> None:
        """Replace related preferences together without losing other settings."""
        with self._write_lock:
            self._update(values)

    def _update(self, values: dict[str, Any]) -> None:
        lock = self.path.with_name(self.path.name + ".lock")
        for _ in range(self._LOCK_TRIES):
            try:
                with exclusive(lock):
                    self._update_locked(values)
            except FileBusyError:
                time.sleep(self._LOCK_DELAY)
            else:
                return
        raise OSError(f"{self.path}: preferences file is locked by another process")

    def _update_locked(self, values: dict[str, Any]) -> None:
        data = self._read(strict=True)
        data.update(values)
        content = json.dumps(data, ensure_ascii=False, indent=1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary file and moved into place, so a crash mid-write
        # leaves the old file instead of an empty one.
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
