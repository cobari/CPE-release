"""Output files that are checked before writing and replaced only when complete."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path


def check_outputs(paths: Sequence[Path], *, force: bool) -> None:
    """Refuse to replace an existing output unless the caller asked for it.

    A CSV often carries a researcher's annotations; a silent rerun would lose
    them. The caller decides with --force.
    """
    if force:
        return
    for path in paths:
        if path.exists():
            raise FileExistsError(f"{path} already exists; use --force to replace it")


@contextmanager
def staged(paths: Sequence[Path]) -> Iterator[list[Path]]:
    """Paths to write first; each target is replaced only after its write returns.

    Writers build their whole content before opening a file, but a failure between
    two files would still leave a new file beside an old one. Keep the previous
    files under temporary names while the new set is published, and put them back
    if any replacement fails. Cleanup failures are not part of the result.

    A process killed after some replacements can leave a ``.backup-`` file; it is
    never deleted by a later run, so the previous set can be restored by hand.
    """
    temporary: list[Path] = []
    backups: list[tuple[Path | None, Path]] = []
    stamp = f"{os.getpid()}-{time.time_ns()}"
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, created = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            os.close(handle)
            temporary.append(Path(created))
        for path in paths:
            if path.exists():
                backup_path = path.with_name(f".{path.name}.backup-{stamp}")
                path.rename(backup_path)
                backups.append((backup_path, path))
            else:
                backups.append((None, path))
        yield temporary
        for temporary_path, path in zip(temporary, paths, strict=True):
            os.replace(temporary_path, path)
    except BaseException:
        for backup, path in backups:
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                if path.exists():
                    path.unlink()
                backup.rename(path)
        raise
    else:
        for backup, _ in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)
    finally:
        for temporary_path in temporary:
            temporary_path.unlink(missing_ok=True)
