"""Selected AKS Jangyongyeong Gosa articles from official release 15052761.

The inspected wrapper and conservative body mapping are recorded in
docs/research/aks-jangyongyeong-import.md. Notes and unverified inline structures
exclude complete articles; they never join previously separated body text.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cpe.local.releases import ReleaseError, bundle_name, parse, save_bundles

BOOK = "壯勇營故事"
SOURCE = "https://www.data.go.kr/data/15052761/fileData.do"
EDITION = "AKS Jangseogak, release 15052761 (20200929), K2-3368"
PREFIX = "aks:jangyongyeong:"
_ID = re.compile(r"23368_[0-9]{3}_[0-9]{4}")
_INDENT = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_REVIEW = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af■□]")
_LOCK = threading.Lock()


class _Excluded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == "page":
            if (
                set(child.attrib) != {"id"}
                or not child.get("id")
                or len(child)
                or (child.text or "").strip(" \t\r\n")
            ):
                raise _Excluded("unsupported_page_marker")
        elif child.tag == "p" and child.attrib == {"align": "left"}:
            parts.append(_text(child))
        elif child.tag == "quo" and child.attrib in ({"genre": ""}, {"genre": " "}):
            parts.append(_text(child))
        else:
            raise _Excluded("unsupported_inline")
        parts.append(child.tail or "")
    return "".join(parts)


def _item(item: ET.Element) -> tuple[str, str]:
    if item.get("type") != "일반본문":
        raise _Excluded("unsupported_item_type")
    if [child.tag for child in item] != ["title", "body"]:
        raise _Excluded("unsupported_item_structure")
    if any((value or "").strip() for value in (item.text, *[c.tail for c in item])):
        raise _Excluded("text_outside_title_body")
    if item.find(".//imghj") is not None:
        raise _Excluded("unresolved_glyph")
    if item.find(".//note") is not None:
        raise _Excluded("unsupported_note")
    title, body = item
    if title.attrib or len(title) or body.attrib:
        raise _Excluded("unsupported_item_structure")
    heading = _INDENT.sub("", title.text or "").strip(" \t\r\n")
    text = _INDENT.sub("", _text(body)).strip(" \t\r\n")
    if _REVIEW.search(heading + text):
        raise _Excluded("mixed_script_or_placeholder_review")
    if not text:
        raise _Excluded("empty_body")
    return heading, text


def import_documents(found: Iterable[bytes], folder: Path) -> dict[str, object]:
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    inputs: list[str] = []
    for raw in found:
        root = parse(raw)
        if root.tag != "자료" or root.attrib or (root.text or "").strip():
            raise ReleaseError("release_unknown")
        inputs.append(hashlib.sha256(raw).hexdigest())
        for article in root:
            if (
                article.tag != "기사"
                or article.attrib
                or [child.tag for child in article] != ["AA20_NO", "AA25_XMLCONTENTS"]
                or any(child.attrib or len(child) for child in article)
                or any(
                    (value or "").strip()
                    for value in (
                        article.text,
                        article.tail,
                        *[c.tail for c in article],
                    )
                )
            ):
                raise ReleaseError("release_unknown")
            identity, content = article
            id_ = (identity.text or "").strip(" \t\r\n")
            if not _ID.fullmatch(id_):
                raise ReleaseError("release_unknown")
            if id_ in seen:
                raise ReleaseError("release_existing_conflict")
            seen.add(id_)
            try:
                item = ET.fromstring(content.text or "")
            except ET.ParseError:
                skipped.append(
                    {"id": id_, "reason": "malformed_inner_xml", "source": SOURCE}
                )
                continue
            if item.tag != "item" or item.get("기사서지ID") != id_:
                raise ReleaseError("release_unknown")
            try:
                title, body = _item(item)
            except _Excluded as error:
                skipped.append({"id": id_, "reason": error.reason, "source": SOURCE})
                continue
            records.append(
                {
                    "text_id": PREFIX + id_,
                    "title": " · ".join(part for part in (BOOK, title) if part),
                    "source": SOURCE,
                    "edition": EDITION,
                    "range": "excerpt",
                    "boundary_start": "truncated",
                    "boundary_end": "truncated",
                    "text": body,
                }
            )
    if not records:
        raise ReleaseError("release_empty")
    report = (
        json.dumps(
            {
                "schema": 1,
                "importer": "aks-jangyongyeong",
                "book": BOOK,
                "source": SOURCE,
                "input_sha256": inputs,
                "accepted": len(records),
                "skipped": len(skipped),
                "skipped_articles": skipped,
                "scope": (
                    "Supplied body order; notes and unverified structures "
                    "exclude whole articles."
                ),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    target = folder / "aks-jangyongyeong"
    contents = {
        bundle_name("articles"): "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        "import-report.json": report,
    }
    with _LOCK:
        backup = target.with_name(f".{target.name}.previous")
        if backup.is_symlink() or target.is_symlink():
            raise ReleaseError("release_existing_conflict")
        if backup.exists():
            if target.exists():
                raise ReleaseError("release_existing_conflict")
            backup.rename(target)
        if target.exists():
            if (
                not target.is_dir()
                or {path.name for path in target.iterdir()} != contents.keys()
                or any(
                    (target / name).is_symlink()
                    or not (target / name).is_file()
                    or (target / name).read_bytes() != value.encode("utf-8")
                    for name, value in contents.items()
                )
            ):
                raise ReleaseError("release_existing_conflict")
        else:
            save_bundles(target, {"articles": records}, PREFIX, report=report)
    return {
        "book": BOOK,
        "articles": len(records),
        "folder": str(target),
        "skipped_articles": len(skipped),
        "report": str(target / "import-report.json"),
    }
