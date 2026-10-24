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
from pathlib import Path
from typing import Any

from cpe.local.releases import ReleaseError, import_release
from cpe.local.txt import BUNDLE_SUFFIX, InputError, read_bundle

_RELEASE_SUFFIXES = {".zip", ".xml"}


def _find_candidate_files(source: Path) -> list[Path]:
    """Find release files (.zip, .xml) recursively in source in deterministic order.

    Symlinks are skipped to prevent circular references and directory traversal escapes.
    """
    candidates: list[Path] = []
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for name in files:
            file_path = Path(root) / name
            if file_path.is_symlink():
                continue
            if file_path.suffix.lower() in _RELEASE_SUFFIXES:
                candidates.append(file_path)
    return sorted(candidates, key=lambda p: p.as_posix())


def _validate_bundles(target: Path) -> tuple[int, str | None]:
    """Validate all .texts.jsonl bundle files in target.

    Fails if target has no bundles, bundle syntax is invalid, or duplicate text_id
    is found across bundles.
    """
    bundles = sorted(target.glob("*" + BUNDLE_SUFFIX))
    if not bundles:
        return 0, "release_empty"

    seen_ids: set[str] = set()
    total_articles = 0

    try:
        for bundle in bundles:
            texts = read_bundle(bundle)
            if not texts:
                return 0, "release_empty"
            for text in texts:
                if text.text_id in seen_ids:
                    return 0, "duplicate_text_id"
                seen_ids.add(text.text_id)
            total_articles += len(texts)
    except (InputError, OSError):
        return 0, "invalid_bundle"

    return total_articles, None


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
        if staged_entry.is_dir() != dest_entry.is_dir():
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

    total_articles, validation_error = _validate_bundles(staged_target)
    if validation_error is not None:
        return {
            "path": str(file_path),
            "status": "failed",
            "code": validation_error,
        }

    dest_target = folder / staged_target.name

    if dest_target.exists():
        if dest_target.is_dir() and _check_identical_release(
            staged_target, dest_target
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
