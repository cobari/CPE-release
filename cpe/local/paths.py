"""Directory-link and containment checks shared by local traversal code.

A folder scan must not follow a link out of the folder the researcher opened.
On POSIX a directory symlink is detectable with ``is_symlink``; on Windows a
junction is a reparse point and ``is_symlink`` is false, so it needs the
``FILE_ATTRIBUTE_REPARSE_POINT`` check.  File symlinks are intentionally not
covered here: the folder scan documents that only directory links are skipped.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_directory_link(path: Path) -> bool:
    """Whether ``path`` is a symlink or a Windows reparse-point directory.

    The caller must check the directory type separately; this function only
    answers the link question so a file symlink keeps its documented behaviour.
    A missing or unreadable path is not a link; the caller's existing stat or
    listing error handling reports it.
    """
    try:
        if path.is_symlink():
            return True
        if os.name != "nt":
            return False
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT)


def is_within(root: Path, path: Path) -> bool:
    """Whether ``path`` resolves under the already-resolved ``root``.

    This is a second guard behind link detection: a mount point or another
    reparse form that is not caught by :func:`is_directory_link` must not make
    the scan leave the opened tree.  A resolution error (including a loop) is
    treated as outside, so the caller skips it instead of descending.
    """
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True
