"""Nonblocking file locks released by the OS when a process exits."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class FileBusyError(OSError):
    """Another handle owns this resource's advisory lock."""


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    # Keep the lock file's inode: unlinking it lets a second process lock a new
    # file while an earlier waiter still holds the previous one open.
    if path.is_symlink():
        raise FileBusyError("lock path is a symlink")
    with path.open("a+b") as stream:
        try:
            # On Windows a byte-range lock held by another process can refuse this
            # write; without the guard it would escape as a raw PermissionError
            # instead of the busy signal callers handle.
            if stream.seek(0, 2) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise FileBusyError("resource is locked") from None
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
