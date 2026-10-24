"""Immutable import parts: add missing units without rewriting earlier imports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from cpe.local.external import Download, ExternalError
from cpe.local.file_lock import FileBusyError, exclusive
from cpe.local.txt import InputError, read_bundle

BOOK_NAME = (
    r"(?:(?:gutenberg|ndl)-[1-9][0-9]{0,11}"
    r"|(?:cbeta|nikh-partial|rise)-[a-f0-9]{24})"
)


def _text_id(provider: str, book_id: str, unit_id: str) -> str:
    # XML IDs can repeat across works/revisions; the input checksum keeps those
    # records distinct while their original XML locators remain in the receipt.
    namespace = (
        f"{provider}:{book_id}:"
        if provider in {"cbeta", "nikh-partial", "rise"}
        else f"{provider}:"
    )
    return namespace + unit_id


def _read_part(part: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if part.is_symlink():
        raise ExternalError("external_history_conflict")
    try:
        bundle = part / "originals.texts.jsonl"
        if bundle.is_symlink() or (part / "import-receipt.json").is_symlink():
            raise ValueError
        receipt = json.loads((part / "import-receipt.json").read_text(encoding="utf-8"))
        read_bundle(bundle)
        records = [
            json.loads(line)
            for line in bundle.read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        if (
            not isinstance(receipt, dict)
            or receipt.get("original_reviewed") is not True
        ):
            raise ValueError
        provider, id_ = receipt["provider"], receipt["id"]
        if not re.fullmatch(BOOK_NAME, f"{provider}-{id_}"):
            raise ValueError
        selected = receipt["selected"]
        if (
            not isinstance(selected, list)
            or not selected
            or not all(isinstance(unit, str) for unit in selected)
        ):
            raise ValueError
        if len(selected) != len(set(selected)) or len(records) != len(selected):
            raise ValueError
        if {record["text_id"] for record in records} != {
            _text_id(provider, id_, unit) for unit in selected
        }:
            raise ValueError
        if (
            not isinstance(receipt.get("edition"), str)
            or not receipt["edition"].strip()
        ):
            raise ValueError
        if any(record.get("edition") != receipt["edition"] for record in records):
            raise ValueError
        downloads = receipt["downloads"]
        if not isinstance(downloads, list) or not downloads:
            raise ValueError
        for number, entry in enumerate(downloads, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                raise ValueError
            raw = part / f"download-{number}.source"
            if (
                raw.is_symlink()
                or hashlib.sha256(raw.read_bytes()).hexdigest() != entry["sha256"]
            ):
                raise ValueError
        return records, receipt
    except (OSError, ValueError, KeyError, TypeError, InputError):
        raise ExternalError("external_history_conflict") from None


def _book(target: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    paths = [target]
    if target.is_symlink():
        raise ExternalError("external_history_conflict")
    paths += sorted(target.glob("part-*"))
    records: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    for part in paths:
        found, receipt = _read_part(part)
        if f"{receipt['provider']}-{receipt['id']}" != target.name:
            raise ExternalError("external_history_conflict")
        if receipts and any(
            receipt[key] != receipts[0][key] for key in ("provider", "id", "edition")
        ):
            raise ExternalError("external_history_conflict")
        for record in found:
            if record["text_id"] in records:
                raise ExternalError("external_history_conflict")
            records[record["text_id"]] = record
        receipts.append({**receipt, "part": part.name})
    return records, receipts


def history(folder: Path) -> list[dict[str, Any]]:
    """Return local provenance without making any provider request."""
    if not folder.is_dir():
        raise ExternalError("release_no_folder")
    roots = (
        [folder]
        if (folder / "import-receipt.json").exists()
        else sorted(folder.iterdir())
    )
    result = []
    for target in roots:
        if not re.fullmatch(BOOK_NAME, target.name):
            continue
        try:
            records, receipts = _book(target)
            first = next(iter(records.values()))
            result.append(
                {
                    "book": target.name,
                    "title": first["title"],
                    "edition": receipts[0]["edition"],
                    "units": len(records),
                    "parts": len(receipts),
                    "receipts": receipts,
                    "sources": sorted(
                        {record["source"] for record in records.values()}
                    ),
                    "folder": str(target),
                }
            )
        except ExternalError as error:
            result.append(
                {"book": target.name, "error": error.code, "folder": str(target)}
            )
    return result


def save_report(download: Download, folder: Path) -> Path:
    """Save preview diagnostics without admitting any units into the library."""
    if not download.details.get("policy"):
        raise ExternalError("external_offline_format")
    if not folder.is_dir():
        raise ExternalError("release_no_folder")
    name = f"{download.provider}-{download.id}"
    if not re.fullmatch(BOOK_NAME, name):
        raise ExternalError("external_offline_format")
    report = {
        "provider": download.provider,
        "id": download.id,
        "edition": download.edition,
        "accepted": len(download.units),
        "saved": 0,
        "downloads": [
            {"url": url, "sha256": hashlib.sha256(raw).hexdigest()}
            for url, raw in download.payloads
        ],
        **download.details,
    }
    content = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    reports = folder / ".external-reports"
    if reports.is_symlink() or (reports.exists() and not reports.is_dir()):
        raise ExternalError("external_history_conflict")
    reports.mkdir(exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()[:24]
    path = reports / f"{name}-{digest}.json"
    try:
        with exclusive(reports / ".report.lock"):
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_bytes() != content:
                    raise ExternalError("external_history_conflict")
            else:
                with tempfile.TemporaryDirectory(
                    prefix=".report-", dir=reports
                ) as temporary:
                    stage = Path(temporary) / path.name
                    stage.write_bytes(content)
                    stage.rename(path)
    except FileBusyError:
        raise ExternalError("external_busy") from None
    return path


def save(
    download: Download, selected: list[str], folder: Path, edition: str, reviewed: bool
) -> dict[str, Any]:
    if not reviewed or not edition.strip():
        raise ExternalError("external_review")
    chosen = set(selected)
    if not chosen or not chosen <= {unit.id for unit in download.units}:
        raise ExternalError("external_selection")
    if not folder.is_dir():
        raise ExternalError("release_no_folder")
    name = f"{download.provider}-{download.id}"
    if not re.fullmatch(BOOK_NAME, name):
        raise ExternalError("external_offline_format")
    if len({unit.id for unit in download.units}) != len(download.units):
        raise ExternalError("external_offline_format")
    target = folder / name
    lock = folder / f".external-{name}.lock"
    try:
        with exclusive(lock):
            return _save_locked(download, chosen, target, edition.strip())
    except FileBusyError:
        raise ExternalError("external_busy") from None


def _save_locked(
    download: Download, chosen: set[str], target: Path, edition: str
) -> dict[str, Any]:
    previous: dict[str, dict[str, Any]] = {}
    if target.exists() or target.is_symlink():
        if not (target / "import-receipt.json").is_file():
            raise ExternalError("external_exists")
        previous, receipts = _book(target)
        if any(
            receipts[0][key] != value
            for key, value in (
                ("provider", download.provider),
                ("id", download.id),
                ("edition", edition),
            )
        ):
            raise ExternalError("external_edition_conflict")
    title = download.title + (" [NDL OCR]" if download.provider == "ndl" else "")
    incoming = [
        {
            "text_id": _text_id(download.provider, download.id, unit.id),
            "text": unit.text,
            "title": title,
            "source": unit.source,
            "edition": edition,
            "range": "excerpt",
            "boundary_start": "truncated",
            "boundary_end": "truncated",
            "punctuation": "pause",
            "full_stop": "reading",
            "text_type": "original",
            **{
                key: value
                for key, value in unit.metadata.items()
                if key
                in {
                    "title",
                    "era",
                    "year",
                    "year_end",
                    "note_open",
                    "note_close",
                    "halfwidth_punctuation",
                }
            },
        }
        for unit in download.units
    ]
    # Downloads describe the complete supported book snapshot. A saved page that
    # disappears (including newly empty OCR) must not survive a later addition.
    if not previous.keys() <= {record["text_id"] for record in incoming}:
        raise ExternalError("external_text_conflict")
    # Compare overlapping units even when they were not selected for this import.
    for record in incoming:
        old = previous.get(record["text_id"])
        if old is not None and any(
            old.get(key) != value for key, value in record.items()
        ):
            raise ExternalError("external_text_conflict")
    selected_ids = {
        _text_id(download.provider, download.id, unit_id) for unit_id in chosen
    }
    records = [
        record
        for record in incoming
        if record["text_id"] in selected_ids and record["text_id"] not in previous
    ]
    skipped = len(chosen) - len(records)
    if not records:
        return {
            "title": download.title,
            "units": 0,
            "skipped": skipped,
            "folder": str(target),
        }
    saved_ids = {record["text_id"] for record in records}
    receipt = {
        "provider": download.provider,
        "id": download.id,
        "edition": edition,
        "selected": [
            unit.id
            for unit in download.units
            if _text_id(download.provider, download.id, unit.id) in saved_ids
        ],
        "original_reviewed": True,
        "ocr": download.provider == "ndl",
        "details": download.details,
        "downloads": [
            {"url": url, "sha256": hashlib.sha256(raw).hexdigest()}
            for url, raw in download.payloads
        ],
    }
    content = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    ).encode("utf-8")
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    digest = hashlib.sha256(content + receipt_bytes).hexdigest()[:24]
    destination = target / f"part-{digest}" if previous else target
    with tempfile.TemporaryDirectory(
        prefix=".external-", dir=target.parent
    ) as temporary:
        stage = Path(temporary) / destination.name
        stage.mkdir()
        (stage / "originals.texts.jsonl").write_bytes(content)
        (stage / "import-receipt.json").write_bytes(receipt_bytes)
        if download.details:
            report = {
                "accepted": len(download.units),
                "saved": len(records),
                **download.details,
            }
            (stage / "import-report.json").write_bytes(
                (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                )
            )
        for number, (_, raw) in enumerate(download.payloads, 1):
            (stage / f"download-{number}.source").write_bytes(raw)
        if destination.exists() or destination.is_symlink():
            raise ExternalError("external_exists")
        os.rename(stage, destination)
    return {
        "title": download.title,
        "units": len(records),
        "skipped": skipped,
        "folder": str(target),
    }
