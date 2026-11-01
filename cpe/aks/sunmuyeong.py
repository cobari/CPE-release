"""Selected AKS Sunmuyeong Deungnok articles from official release 15052764.

The inspected wrapper, container mapping and exclusions are recorded in
docs/research/aks-sunmuyeong-import.md. Line and page markers are layout; a
spacing marker stands for a space, and unverified structures exclude complete
articles instead of disappearing from an accepted body.
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

BOOK = "巡撫營謄錄"
SOURCE = "https://www.data.go.kr/data/15052764/fileData.do"
EDITION = "AKS Jangseogak, release 15052764 (20200929), K2-3335"
PREFIX = "aks:sunmuyeong:"
_ID = re.compile(r"23335_[0-9]{3}_[0-9]{4}")
_ITEM_ATTRIBUTES = frozenset({"id", "기사서지ID", "genre", "form", "type"})
# A name may carry the institute's entity metadata; the attributes are not body
# text, so they are checked and then left out of the saved string.
_NAME_ATTRIBUTES = frozenset(
    {"pid", "UCI", "type", "created", "countPid", "identifier"}
)
_INDENT = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_REVIEW = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af■□]")


class _Excluded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _clean(value: str | None) -> str:
    """One text node without XML line breaks and their indentation.

    Removal happens per node so a space inserted for a `kspace` marker is never
    swallowed together with an adjacent line-break run.
    """
    return _INDENT.sub("", value or "")


def _line_break(element: ET.Element) -> None:
    if element.attrib or len(element) or (element.text or "").strip(" \t\r\n"):
        raise _Excluded("unsupported_line_break")


def _page(element: ET.Element) -> None:
    if (
        set(element.attrib) != {"id"}
        or not element.get("id")
        or len(element)
        or (element.text or "").strip(" \t\r\n")
    ):
        raise _Excluded("unsupported_page_marker")


def _spacing(element: ET.Element) -> None:
    if (
        set(element.attrib) - {"indent"}
        or not element.get("indent", "").isdigit()
        or len(element)
        or (element.text or "").strip(" \t\r\n")
    ):
        raise _Excluded("unsupported_kspace")


def _paragraph(element: ET.Element) -> None:
    if element.get("align") != "left" or set(element.attrib) - {"align", "indent"}:
        raise _Excluded("unsupported_paragraph")
    if "indent" in element.attrib and not element.get("indent", "").isdigit():
        raise _Excluded("unsupported_paragraph")


def _name(element: ET.Element) -> None:
    if set(element.attrib) - _NAME_ATTRIBUTES:
        raise _Excluded("unsupported_name")
    if element.get("type", "성명") != "성명":
        raise _Excluded("unsupported_name")


def _text(element: ET.Element) -> str:
    parts = [_clean(element.text)]
    for child in element:
        if child.tag == "br":
            _line_break(child)
        elif child.tag == "page":
            _page(child)
        elif child.tag == "kspace":
            _spacing(child)
            # The marker stands where the transcription keeps a space; writing one
            # space keeps the neighbouring characters from becoming adjacent.
            parts.append(" ")
        elif child.tag == "p":
            _paragraph(child)
            parts.append(_text(child))
        elif child.tag == "name":
            _name(child)
            parts.append(_text(child))
        elif child.tag in ("duties", "geo", "building"):
            if child.attrib:
                raise _Excluded("unsupported_inline")
            parts.append(_text(child))
        elif child.tag == "note":
            raise _Excluded("unsupported_note")
        elif child.tag == "imghj":
            raise _Excluded("unresolved_glyph")
        else:
            raise _Excluded("unsupported_inline")
        parts.append(_clean(child.tail))
    return "".join(parts)


def _item(item: ET.Element) -> tuple[str, str]:
    if item.get("type") != "일반본문" or set(item.attrib) - _ITEM_ATTRIBUTES:
        raise _Excluded("unsupported_item_structure")
    if [child.tag for child in item] != ["title", "body"]:
        raise _Excluded("unsupported_item_structure")
    if any(
        (value or "").strip() for value in (item.text, *[child.tail for child in item])
    ):
        raise _Excluded("text_outside_title_body")
    if item.find(".//imghj") is not None:
        raise _Excluded("unresolved_glyph")
    if item.find(".//note") is not None:
        raise _Excluded("unsupported_note")
    title, body = item
    if title.attrib or len(title) or body.attrib:
        raise _Excluded("unsupported_item_structure")
    heading = _clean(title.text).strip(" \t\r\n")
    text = _text(body).strip(" \t\r\n")
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
                "importer": "aks-sunmuyeong",
                "book": BOOK,
                "source": SOURCE,
                "input_sha256": inputs,
                "accepted": len(records),
                "skipped": len(skipped),
                "skipped_articles": skipped,
                "scope": (
                    "Supplied body order; line and page markers are layout, a spacing "
                    "marker becomes one space, and notes, unresolved glyphs or "
                    "unverified structures exclude whole articles."
                ),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    target = folder / "aks-sunmuyeong"
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
