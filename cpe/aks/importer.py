"""A conservative subset of AKS Geumwiyeong Deungnok release 15052758.

The inspected file structure and exclusions are recorded in
docs/research/aks-geumwiyeong-import.md. Unknown editorial structures exclude
their complete item; they never disappear from an otherwise accepted body.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cpe.local.releases import ReleaseError, parse, save_bundles

ROOT = "금위영등록"
BOOK = "禁衛營謄錄"
SOURCE = "https://www.data.go.kr/data/15052758/fileData.do"
EDITION = "AKS Jangseogak, release 15052758 (20200929)"
ID_ATTRIBUTE = "기사서지ID"
PREFIX = "aks:geumwiyeong:"
_ID = re.compile(r"[0-9]+(?:_[0-9]+)+")
_INDENT = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_REVIEW = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af■□]")


class _Excluded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == "imghj":
            raise _Excluded("unresolved_glyph")
        if child.tag == "page":
            if len(child) or (child.text or "").strip(" \t\r\n"):
                raise _Excluded("nonempty_page_marker")
        elif child.tag == "building":
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
    title, body = (_INDENT.sub("", _text(child)).strip(" \t\r\n") for child in item)
    if _REVIEW.search(title + body):
        raise _Excluded("mixed_script_or_placeholder_review")
    if not body:
        raise _Excluded("empty_body")
    return title, body


def import_documents(found: Iterable[bytes], folder: Path) -> dict[str, object]:
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    inputs: list[str] = []
    outside = 0
    for raw in found:
        root = parse(raw)
        if root.tag != ROOT or any(child.tag != "item" for child in root):
            raise ReleaseError("release_unknown")
        inputs.append(hashlib.sha256(raw).hexdigest())
        outside += int(bool((root.text or "").strip()))
        for item in root:
            id_ = item.get(ID_ATTRIBUTE, "")
            if not _ID.fullmatch(id_) or id_ in seen:
                raise ReleaseError("release_existing_conflict")
            seen.add(id_)
            outside += int(bool((item.tail or "").strip()))
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
                "importer": "aks-geumwiyeong",
                "book": BOOK,
                "source": SOURCE,
                "input_sha256": inputs,
                "accepted": len(records),
                "skipped": len(skipped),
                "skipped_articles": skipped,
                "outside_item_fragments": outside,
                "scope": (
                    "Explicit item/title/body only; text outside items is not imported."
                ),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    target = folder / "aks-geumwiyeong"
    save_bundles(
        target,
        {"articles": records},
        PREFIX,
        report=report,
        keep_existing=True,
    )
    return {
        "book": BOOK,
        "articles": len(records),
        "folder": str(target),
        "skipped_articles": len(skipped),
        "report": str(target / "import-report.json"),
    }
