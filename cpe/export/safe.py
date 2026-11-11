"""Output files that are checked before writing and replaced only when complete."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from cpe.local.file_lock import exclusive


def check_prefix(prefix: Path) -> None:
    """--out is a file-name prefix; a folder or drive root has no name to extend."""
    if not prefix.name:
        raise IsADirectoryError(f"{prefix}: --out needs a file name prefix")


def check_input_collisions(outputs: Sequence[Path], inputs: Sequence[Path]) -> None:
    """Keep an output replacement from destroying one of its input files."""
    sources = {os.path.normcase(str(path.resolve())) for path in inputs}
    for path in outputs:
        if os.path.normcase(str(path.resolve())) in sources:
            raise OSError(f"{path}: output path overlaps an input file")


def check_outputs(paths: Sequence[Path], *, force: bool) -> None:
    """Refuse to replace an existing output unless the caller asked for it.

    A CSV often carries a researcher's annotations; a silent rerun would lose
    them. The caller decides with --force.
    """
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            raise IsADirectoryError(f"{path} is a directory")
        if not force and (path.exists() or path.is_symlink()):
            raise FileExistsError(f"{path} already exists; use --force to replace it")


def _lock_path(path: Path) -> tuple[str, Path]:
    """Lock the directory entry being replaced, including a final symlink."""
    resolved = path.parent.resolve() / path.name
    key = os.path.normcase(str(resolved))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return key, resolved.parent / f".cpe-output-{digest}.lock"


@contextmanager
def staged(paths: Sequence[Path], *, force: bool = True) -> Iterator[list[Path]]:
    """Paths to write first; each target is replaced only after its write returns.

    Writers build their whole content before opening a file, but a failure between
    two files would still leave a new file beside an old one. Keep the previous
    files under temporary names while the new set is published, and put them back
    if any replacement fails. Cleanup failures are not part of the result.
    Every output's restoration is attempted; failed restores report backup paths.

    ``force=False`` is checked again inside the output lock. The earlier
    ``check_outputs`` call remains an early failure, but another process could
    create the file while the caller computes its results; the inner check closes
    that window. Per-path locks also exclude publishers whose output sets only
    partially overlap.

    A process killed after some replacements can leave a ``.backup-`` file; it is
    never deleted by a later run, so the previous set can be restored by hand.
    """
    paths = tuple(paths)
    if not paths:
        yield []
        return
    locks = sorted(_lock_path(path) for path in paths)
    if len({key for key, _ in locks}) != len(paths):
        raise ValueError("output paths must be distinct")
    with ExitStack() as stack:
        for _, lock_path in locks:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(exclusive(lock_path))
        check_outputs(paths, force=force)
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
                if path.exists() or path.is_symlink():
                    backup_path = path.with_name(f".{path.name}.backup-{stamp}")
                    path.rename(backup_path)
                    backups.append((backup_path, path))
                else:
                    backups.append((None, path))
            yield temporary
            for temporary_path, path in zip(temporary, paths, strict=True):
                os.replace(temporary_path, path)
        except BaseException as failure:
            restore_errors = []
            for backup, path in backups:
                try:
                    if backup is None:
                        path.unlink(missing_ok=True)
                    else:
                        if path.exists() or path.is_symlink():
                            path.unlink()
                        backup.rename(path)
                except OSError as error:
                    detail = f"{path}: {error}"
                    if backup is not None:
                        detail += f" (previous output: {backup})"
                    restore_errors.append(detail)
            if restore_errors:
                raise OSError(
                    f"output rollback failed after {failure}; "
                    + "; ".join(restore_errors)
                ) from failure
            raise
        else:
            for backup, _ in backups:
                if backup is not None:
                    # Publication already succeeded; a failed cleanup must not
                    # turn the whole output set into a reported failure.
                    try:
                        backup.unlink(missing_ok=True)
                    except OSError:
                        pass
        finally:
            for temporary_path in temporary:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
