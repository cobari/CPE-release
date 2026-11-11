"""Batch import official XML releases from local directory into text bundles.

Processes official institute releases (NIKH, ITKC) in batch from a local directory.
Each .zip or .xml file is treated as a complete standalone release.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from cpe.local.file_lock import FileBusyError, exclusive
from cpe.local.paths import is_directory_link, is_within
from cpe.local.releases import ReleaseError, import_release
from cpe.local.txt import BUNDLE_SUFFIX, InputError, read_bundle

_RELEASE_SUFFIXES = {".zip", ".xml"}
_REPORT_NAME = "import-report.json"


def _destination_lock(destination: Path) -> Path:
    """The lock path releases.save_bundles uses for this work folder."""
    return destination.with_name(f".{destination.name}.lock")


def _previous_backup(destination: Path) -> Path:
    """The fixed recovery name a stopped releases.save_bundles install leaves."""
    return destination.with_name(f".{destination.name}.previous")


def _has_previous_backup(destination: Path) -> bool:
    """Whether a stopped install's recovery directory is present.

    Batch publication refuses it instead of choosing between the published work,
    the backup and the incoming staged copy. A researcher can inspect or remove
    the backup deliberately; batch never recovers or overwrites it implicitly.
    """
    backup = _previous_backup(destination)
    return backup.exists() or backup.is_symlink()


@contextmanager
def _destination_locks(destinations: list[Path]) -> Iterator[None]:
    """Hold every destination's install lock in deterministic name order.

    The same `.name.lock` path is used by releases.save_bundles, so a concurrent
    single-release install and this batch cannot read or replace the same work
    target between the identical check and the move. Acquisition is all-or-none;
    a busy lock releases the earlier ones and reaches the caller as FileBusyError.
    """
    ordered = sorted(destinations, key=lambda path: (path.name.casefold(), path.name))
    with ExitStack() as stack:
        for destination in ordered:
            stack.enter_context(exclusive(_destination_lock(destination)))
        yield


def _find_candidate_files(source: Path) -> list[Path]:
    """Find release files (.zip, .xml) recursively in source in deterministic order.

    Directory links are skipped to prevent circular references and directory
    traversal escapes. `os.walk(followlinks=False)` does not recognise a Windows
    junction as a link, and on some platforms a reparse form can still resolve
    outside the source; both are pruned before descent. File symlinks keep the
    existing rule and are skipped.
    """
    try:
        root_resolved = source.resolve()
    except (OSError, RuntimeError):
        root_resolved = source.absolute()
    candidates: list[Path] = []
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not is_directory_link(Path(root) / name)
            and is_within(root_resolved, Path(root) / name)
        ]
        for name in files:
            file_path = Path(root) / name
            if file_path.is_symlink():
                continue
            if file_path.suffix.lower() in _RELEASE_SUFFIXES:
                candidates.append(file_path)
    return sorted(candidates, key=lambda p: p.as_posix())


def _validate_bundles(target: Path) -> tuple[int, set[str], str | None]:
    """Validate all .texts.jsonl bundle files directly in one work folder.

    Fails if the folder has no bundles, bundle syntax is invalid, or a duplicate
    text_id is found across its bundles. The returned ids let a multi-work
    release reject a duplicate shared across two work folders before any move.
    """
    bundles = sorted(target.glob("*" + BUNDLE_SUFFIX))
    if not bundles:
        return 0, set(), "release_empty"

    seen_ids: set[str] = set()
    total_articles = 0

    try:
        for bundle in bundles:
            texts = read_bundle(bundle)
            if not texts:
                return 0, set(), "release_empty"
            for text in texts:
                if text.text_id in seen_ids:
                    return 0, set(), "duplicate_text_id"
                seen_ids.add(text.text_id)
            total_articles += len(texts)
    except (InputError, OSError):
        return 0, set(), "invalid_bundle"

    return total_articles, seen_ids, None


def _check_identical_release(staged: Path, dest: Path) -> bool:
    """Check if all entries in staged match all entries in dest exactly.

    Compares names, file vs directory types, and file bytes. Any extra entry,
    missing entry, or user subdirectory in dest causes collision.
    """
    try:
        staged_entries = {e.name: e for e in staged.iterdir()}
        dest_entries = {e.name: e for e in dest.iterdir()}
    except OSError:
        return False

    if not staged_entries or staged_entries.keys() != dest_entries.keys():
        return False

    for name, staged_entry in staged_entries.items():
        dest_entry = dest_entries[name]
        if is_directory_link(staged_entry) or is_directory_link(dest_entry):
            return False
        if staged_entry.is_dir() != dest_entry.is_dir():
            return False
        if staged_entry.is_file() != dest_entry.is_file():
            return False
        if staged_entry.is_file():
            try:
                if staged_entry.read_bytes() != dest_entry.read_bytes():
                    return False
            except OSError:
                return False
        elif staged_entry.is_dir():
            if not _check_identical_release(staged_entry, dest_entry):
                return False
        else:
            return False
    return True


def _extract_skipped_articles(info: dict[str, Any]) -> int:
    """Extract excluded article count as an integer."""
    for key in ("skipped_articles", "skipped", "excluded_articles"):
        val = info.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, list):
            return len(val)
    return 0


def _copy_info_metadata(
    item: dict[str, Any],
    info: dict[str, Any],
    dest_target: Path,
    fallback_articles: int,
) -> None:
    """Populate item with importer metadata, rewriting staging paths to dest."""
    for key, val in info.items():
        if key == "folder":
            item["folder"] = str(dest_target)
        elif key == "report" and val:
            item["report"] = str(dest_target / Path(str(val)).name)
        elif key not in ("skipped", "skipped_articles"):
            item[key] = val

    item["folder"] = str(dest_target)
    item["book"] = str(info.get("book", ""))
    articles_val = info.get("articles")
    item["articles"] = (
        int(articles_val) if isinstance(articles_val, (int, str)) else fallback_articles
    )
    item["skipped_articles"] = _extract_skipped_articles(info)


def _stage_is_root(staged_target: Path, stage_dir: Path) -> bool:
    """Whether an importer returned the staging root (several work folders)."""
    try:
        return staged_target.resolve() == stage_dir.resolve()
    except (OSError, RuntimeError):
        return staged_target == stage_dir


def _work_folders(stage_dir: Path) -> tuple[list[Path], str | None]:
    """The work folders directly under a multi-work staging root.

    A link or an outside-root directory there is refused: the importer creates
    ordinary directories, and following a reparse point would move data the
    researcher did not select. Other entries (the importers' lock files) are
    ignored.
    """
    try:
        root_resolved = stage_dir.resolve()
    except (OSError, RuntimeError):
        root_resolved = stage_dir.absolute()
    try:
        entries = sorted(stage_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        return [], "invalid_bundle"
    folders = []
    for entry in entries:
        if not entry.is_dir():
            continue
        if is_directory_link(entry) or not is_within(root_resolved, entry):
            return [], "invalid_bundle"
        folders.append(entry)
    if not folders:
        return [], "release_empty"
    return folders, None


def _multi_work_item(
    file_path: Path,
    folder: Path,
    info: dict[str, Any],
    plans: list[tuple[Path, Path, bool]],
    total_articles: int,
) -> dict[str, Any]:
    """One batch item for a release installed as several work folders.

    ``plans`` is ``(staged work, destination, already identical)``. All work
    folders were validated and collision-checked before the caller moved any.
    """
    new_count = sum(1 for _, _, identical in plans if not identical)
    item: dict[str, Any] = {
        "path": str(file_path),
        "status": "imported" if new_count else "skipped",
        "folder": str(folder),
        "folders": [str(destination) for _, destination, _ in plans],
        "book": str(info.get("book", "")),
        "articles": total_articles,
        "skipped_articles": _extract_skipped_articles(info),
    }
    if new_count == 0:
        item["code"] = "already_imported"
    reports = [
        str(destination / _REPORT_NAME)
        for _, destination, _ in plans
        if (destination / _REPORT_NAME).is_file()
    ]
    if reports:
        item["reports"] = reports
        item["report"] = reports[0] if len(reports) == 1 else str(folder)
    return item


def _process_multi_work_release(
    file_path: Path, folder: Path, stage_dir: Path, info: dict[str, Any]
) -> dict[str, Any]:
    """Lock, validate and commit every work folder from one multi-work release.

    All destinations are checked before the first move, so a collision or an
    invalid child leaves the existing destination untouched. Identical work
    folders are skipped in place; new ones are moved one by one and moved back
    if a later move fails. The staging root itself is never renamed.
    """
    folders, error = _work_folders(stage_dir)
    if error is not None:
        return {"path": str(file_path), "status": "failed", "code": error}
    destinations = [folder / staged.name for staged in folders]
    try:
        with _destination_locks(destinations):
            return _process_multi_work_locked(file_path, folder, info, folders)
    except FileBusyError:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "local_busy",
        }


def _process_multi_work_locked(
    file_path: Path,
    folder: Path,
    info: dict[str, Any],
    folders: list[Path],
) -> dict[str, Any]:
    """The multi-work preflight and commit, with destination locks held."""
    plans: list[tuple[Path, Path, bool]] = []
    seen_ids: set[str] = set()
    total_articles = 0
    for staged in folders:
        count, ids, validation_error = _validate_bundles(staged)
        if validation_error is not None:
            return {
                "path": str(file_path),
                "status": "failed",
                "code": validation_error,
            }
        if seen_ids & ids:
            return {
                "path": str(file_path),
                "status": "failed",
                "code": "duplicate_text_id",
            }
        seen_ids |= ids
        total_articles += count
        destination = folder / staged.name
        if _has_previous_backup(destination):
            return {
                "path": str(file_path),
                "status": "failed",
                "code": "collision",
                "folder": str(destination),
            }
        if destination.exists() or destination.is_symlink():
            if (
                destination.is_symlink()
                or not destination.is_dir()
                or not _check_identical_release(staged, destination)
            ):
                return {
                    "path": str(file_path),
                    "status": "failed",
                    "code": "collision",
                    "folder": str(destination),
                }
            plans.append((staged, destination, True))
        else:
            plans.append((staged, destination, False))

    moved: list[tuple[Path, Path]] = []
    try:
        for staged, destination, identical in plans:
            if not identical:
                staged.rename(destination)
                moved.append((staged, destination))
    except OSError:
        rollback_errors: list[str] = []
        for staged, destination in reversed(moved):
            try:
                destination.rename(staged)
            except OSError as rollback:
                rollback_errors.append(f"{destination}: {rollback}")
        item: dict[str, Any] = {
            "path": str(file_path),
            "status": "failed",
            "code": "commit_failed",
            "folder": str(folder),
        }
        if rollback_errors:
            item["rollback_errors"] = rollback_errors
        return item

    return _multi_work_item(file_path, folder, info, plans, total_articles)


def _publish_single_work(
    file_path: Path,
    staged_target: Path,
    dest_target: Path,
    info: dict[str, Any],
    total_articles: int,
) -> dict[str, Any]:
    """Publish one staged work, with its destination lock already held."""
    if _has_previous_backup(dest_target):
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "collision",
            "folder": str(dest_target),
        }

    if dest_target.exists() or dest_target.is_symlink():
        if (
            not dest_target.is_symlink()
            and dest_target.is_dir()
            and _check_identical_release(staged_target, dest_target)
        ):
            item: dict[str, Any] = {
                "path": str(file_path),
                "status": "skipped",
                "code": "already_imported",
            }
            _copy_info_metadata(item, info, dest_target, total_articles)
            return item

        return {
            "path": str(file_path),
            "status": "failed",
            "code": "collision",
            "folder": str(dest_target),
        }

    try:
        staged_target.rename(dest_target)
    except OSError:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "commit_failed",
            "folder": str(dest_target),
        }

    item = {
        "path": str(file_path),
        "status": "imported",
    }
    _copy_info_metadata(item, info, dest_target, total_articles)
    return item


def _process_staged_release(
    file_path: Path, folder: Path, stage_dir: Path
) -> dict[str, Any]:
    """Import release in stage_dir and atomically rename into folder.

    Catches expected release, parser, and OS errors per file without masking bugs.
    """
    try:
        info = import_release(file_path, stage_dir)
    except ReleaseError as error:
        return {"path": str(file_path), "status": "failed", "code": error.code}
    except (
        OSError,
        zipfile.BadZipFile,
        ET.ParseError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "release_unreadable",
        }

    raw_target = info.get("folder")
    if not raw_target:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "invalid_bundle",
        }

    staged_target = Path(str(raw_target))
    if not staged_target.is_dir():
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "invalid_bundle",
        }

    if _stage_is_root(staged_target, stage_dir):
        # Multi-work releases (for example the NAHF 15163258 collections) install
        # several sibling work folders. They are validated and committed as one
        # item; renaming the staging root would publish a hidden staging name.
        return _process_multi_work_release(file_path, folder, stage_dir, info)

    total_articles, _, validation_error = _validate_bundles(staged_target)
    if validation_error is not None:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": validation_error,
        }

    dest_target = folder / staged_target.name
    try:
        with _destination_locks([dest_target]):
            return _publish_single_work(
                file_path, staged_target, dest_target, info, total_articles
            )
    except FileBusyError:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": "local_busy",
        }


def import_directory(source: Path, folder: Path) -> dict[str, Any]:
    """Import official XML release files in batches from source into folder.

    Parameters:
        source: Directory containing official release ZIP or XML files.
        folder: Target text folder where imported work directories are placed.

    Returns:
        Structured result dictionary with summary counts and per-file item results.

    Raises:
        ReleaseError("batch_source_missing"): When source does not exist
            or is not a directory.
        ReleaseError("batch_source_conflict"): When source and folder are
            identical or nested.
    """
    if not source.exists() or not source.is_dir():
        raise ReleaseError("batch_source_missing")

    if folder.exists() and not folder.is_dir():
        raise ReleaseError("release_no_folder")

    src_resolved = source.resolve()
    dst_resolved = folder.resolve()

    if (
        src_resolved == dst_resolved
        or src_resolved in dst_resolved.parents
        or dst_resolved in src_resolved.parents
    ):
        raise ReleaseError("batch_source_conflict")

    candidates = _find_candidate_files(source)
    if not candidates:
        return {
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "excluded_articles": 0,
            "items": [],
        }

    folder.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []

    imported_count = 0
    skipped_count = 0
    failed_count = 0
    total_excluded = 0

    for file_path in candidates:
        temp_dir: str | None = None
        try:
            temp_dir = tempfile.mkdtemp(prefix=".stage-", dir=folder)
            result = _process_staged_release(file_path, folder, Path(temp_dir))
        except OSError:
            result = {
                "path": str(file_path),
                "status": "failed",
                "code": "stage_failed",
            }
        finally:
            if temp_dir is not None:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except OSError:
                    pass

        items.append(result)
        status = result.get("status")
        # Articles a release leaves out stay left out whether its files were written
        # now or by an earlier identical import, so both count toward the total.
        if status == "imported":
            imported_count += 1
            total_excluded += result.get("skipped_articles", 0)
        elif status == "skipped":
            skipped_count += 1
            total_excluded += result.get("skipped_articles", 0)
        else:
            failed_count += 1

    return {
        "total": len(items),
        "imported": imported_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "excluded_articles": total_excluded,
        "items": items,
    }
