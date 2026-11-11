"""Directory-link and containment checks shared by local traversal code.

A folder scan must not follow a link out of the folder the researcher opened.
On POSIX a directory symlink is detectable with ``is_symlink``; on Windows a
junction carries a mount-point reparse tag while a cloud placeholder carries a
different tag. Treating all reparse points as links hides cloud folders. File
symlinks are intentionally not covered here: the folder scan documents that only
directory links are skipped.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_LINK_TAGS = {
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
}


def is_directory_link(path: Path, info: os.stat_result | None = None) -> bool:
    """Whether ``path`` is a symlink or a Windows mount-point directory.

    The caller must check the directory type separately; this function only
    answers the link question so a file symlink keeps its documented behaviour.
    A missing or unreadable path is not a link; the caller's existing stat or
    listing error handling reports it.
    """
    try:
        info = info if info is not None else os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in _LINK_TAGS
    )


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
