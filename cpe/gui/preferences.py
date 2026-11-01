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
from copy import deepcopy
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
        self._cache_lock = threading.RLock()
        self._cache_signature: tuple[int, ...] | None = None
        self._cache_data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        with self._cache_lock:
            signature = self._signature()
            if signature is not None and signature == self._cache_signature:
                return deepcopy(self._cache_data)
            try:
                data = self._read(strict=False)
            except OSError:
                self._cache_signature = None
                return {}
            if signature is not None and signature == self._signature():
                self._cache_signature = signature
                self._cache_data = deepcopy(data)
            return data

    def _signature(self) -> tuple[int, ...] | None:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return ()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)

    def _read(self, *, strict: bool) -> dict[str, Any]:
        """Stored values; a missing file means nothing remembered.

        A write must not treat a transient read failure as an empty file, or it
        would drop every other setting. `strict` re-raises that OSError for the
        caller to report instead of overwriting. Undecodable bytes, invalid JSON,
        or a non-object root are corrupt: a write moves the file aside first so
        the bytes are kept, and starts from defaults.
        """
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return {}
        except UnicodeDecodeError:
            if strict:
                self._preserve_corrupt()
                return {}
            return {}
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            if strict:
                self._preserve_corrupt()
            return {}
        if not isinstance(data, dict):
            if strict:
                self._preserve_corrupt()
            return {}
        return data

    def _preserve_corrupt(self) -> None:
        """Keep an unreadable file beside the new one instead of deleting it."""
        target = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
        self.path.rename(target)

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
        # The lock file lives in this directory: create the directory before
        # opening the lock, or a first save with no settings folder fails.
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        # Written to a temporary file and moved into place, so a crash mid-write
        # leaves the old file instead of an empty one.
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            os.replace(temporary, self.path)
            # Writes always merge a fresh disk read under the cross-process
            # lock. Cached snapshots may never overwrite another window's data.
            with self._cache_lock:
                self._cache_data = deepcopy(data)
                self._cache_signature = self._signature()
        finally:
            Path(temporary).unlink(missing_ok=True)
