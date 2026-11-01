"""Institute for the Translation of Korean Classics original-text XML releases.

ITKC publishes edited originals on data.go.kr as ZIPs of XML with Korean element
names (checked on 2026-10-10 with the 고전원문 release 15022432, 古芸堂筆記): one
document per volume under 아이템 > 레벨1 (the work: titles, author with life years) >
레벨2 (volume) > 레벨3 (piece), each piece's text in 본문정보/내용/단락. The researcher
downloads the ZIP; this module writes the pieces, one per bundle line, into the
open text folder, which is then searched as local text (docs/decisions.md "Research
use through the tool: which terms matter").

What is source text:
- 단락 text, including <고유명사> (names) and the text a <주석> wraps: 주석 marks the
  source characters a collation note is about, and the note itself sits apart in
  주석정보, which is left out;
- <원주>: the original interlinear note, which the release writes inside 【 】; it is
  written between note markers instead, so it stays a barrier (spec 2.5);
- left out: 해설정보 (the modern edition's 범례 and introduction), 주석정보,
  연계정보 (links to the translation), <페이지> page markers.
These editions punctuate with fullwidth modern marks (，。：“”), so 。 is read as a
full stop (the manifest default), unlike the 句讀 。 of ITKC's database originals.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.itkc.client import FIELD_DATA_ID
from cpe.itkc.search_cli import make_link
from cpe.local.releases import ReleaseError, parse, save_bundles

NOTE_OPEN, NOTE_CLOSE = "〘", "〙"
REPORT_NAME = "import-report.json"
_INDENT = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
_LEVEL = re.compile(r"레벨\d+$")
# Skipped inside a paragraph: page markers and line breaks carry no text.
_SILENT = frozenset({"페이지", "br"})
_ALLOWED_CONTAINERS = frozenset({"고유명사", "주석"})
HALFWIDTH_MARKS = frozenset(",｡")


class _UnknownElementError(ValueError):
    """An unrecognized element; `reason` is the code the import report records."""

    def __init__(self, tag: str, reason: str = "unsupported_inline") -> None:
        super().__init__(f"{reason}: {tag}")
        self.tag = tag
        self.reason = reason


@dataclass(frozen=True)
class Author:
    name: str
    born: int | None
    died: int | None


@dataclass(frozen=True)
class Piece:
    piece_id: str
    titles: list[str]
    text: str
    author: Author | None
    # The volume (레벨2) id; one bundle file is written per volume.
    volume: str


@dataclass(frozen=True)
class Release:
    work_id: str
    book: str
    collection: str
    pieces: list[Piece]
    # Pieces left out whole, with the reason and element that excluded them.
    skipped: list[dict[str, str]] = field(default_factory=list)


def _title(level: ET.Element, kind: str | None = None) -> str:
    for title in level.findall("메타정보/제목정보/제목"):
        if kind is None or title.get("type") == kind:
            return _INDENT.sub("", "".join(title.itertext())).strip(" \t\r\n")
    return ""


def _year(element: ET.Element | None) -> int | None:
    value = element.get("서기년", "") if element is not None else ""
    return int(value) if value.isdigit() and value not in ("0", "9999") else None


def _author(level: ET.Element) -> Author | None:
    person = level.find("메타정보/저자정보/저자")
    if person is None:
        return None
    name = (person.findtext("한자성명") or person.findtext("한글성명") or "").strip(
        " \t\r\n"
    )
    if not name:
        return None
    return Author(name, _year(person.find("생년")), _year(person.find("몰년")))


def _inline(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == "원주":
            note = _inline(child).strip(" \t\r\n")
            if note.startswith("【") and note.endswith("】"):
                note = note[1:-1]
            parts.append(NOTE_OPEN + note + NOTE_CLOSE)
        elif child.tag in _SILENT:
            pass
        elif child.tag == "kh.td":
            # Cells are inline in the source. A space keeps them apart after
            # _INDENT removes line breaks; a newline here would be deleted and
            # glue the cell to the next character.
            parts.append(_inline(child) + " ")
        elif child.tag in _ALLOWED_CONTAINERS:
            parts.append(_inline(child))
        else:
            raise _UnknownElementError(child.tag)
        parts.append(child.tail or "")
    return _INDENT.sub("", "".join(parts))


def _pieces(
    level: ET.Element,
    titles: list[str],
    author: Author | None,
    out: list[Piece],
    skipped: list[dict[str, str]],
    volume: str,
) -> None:
    title = _title(level)
    here = [*titles, title] if title else titles
    own = _author(level)
    if own is not None and author is not None and own.name == author.name:
        # Pieces name their writer without life years; the work's entry has them.
        own = author
    body = level.find("본문정보/내용")
    if body is not None:
        try:
            # Only paragraphs are body text. An unrecognized child could hold text
            # or join neighbors, so the piece is left out whole rather than read
            # around it, and the report says why.
            unknown = [
                child.tag for child in body if child.tag not in ("단락", "단락제목")
            ]
            if unknown:
                raise _UnknownElementError(unknown[0], "unsupported_body_element")
            paragraphs = [_inline(p).strip(" \t\r\n") for p in body]
            text = "\n".join(p for p in paragraphs if p)
            if text:
                out.append(
                    Piece(level.get("id", ""), here, text, own or author, volume)
                )
        except _UnknownElementError as error:
            # Articles with unresolved or unknown elements are excluded whole to
            # prevent silent deletion and joining of distinct characters; the
            # import report counts them instead of reporting a clean run.
            skipped.append(
                {
                    "id": level.get("id", ""),
                    "reason": error.reason,
                    "element": error.tag,
                }
            )
    for child in level:
        if _LEVEL.match(child.tag):
            _pieces(child, here, own or author, out, skipped, volume)


def read_documents(found: Iterable[bytes]) -> Release:
    """The work, titles and pieces of a release, and the pieces left out."""
    works: dict[str, tuple[str, Author | None]] = {}
    pieces: list[Piece] = []
    skipped: list[dict[str, str]] = []
    collection = ""
    for document in found:
        root = parse(document)
        if root.tag != "아이템":
            raise ReleaseError("release_unknown")
        collection = collection or root.get("name", "")
        for work in root:
            if not _LEVEL.match(work.tag):
                continue
            work_id = work.get("id", "")
            # Only the work's first document carries its titles and author; the
            # volume documents repeat the id alone.
            title = _title(work, "한자서명") or _title(work)
            if title:
                works[work_id] = (title, _author(work))
            book, author = works.setdefault(work_id, ("", None))
            for volume in work:
                if _LEVEL.match(volume.tag):
                    _pieces(
                        volume,
                        [],
                        author,
                        pieces,
                        skipped,
                        volume.get("id", ""),
                    )
    if len(works) > 1:
        raise ReleaseError("release_multiple_works")
    if not pieces:
        raise ReleaseError("release_empty")
    work_id = next(iter(works), "itkc")
    return Release(
        work_id,
        works.get(work_id, ("", None))[0],
        collection,
        pieces,
        skipped,
    )


def save(
    work_id: str,
    book: str,
    collection: str,
    pieces: list[Piece],
    folder: Path,
    *,
    report: str | None = None,
) -> Path:
    """One text bundle per volume under folder/itkc-<work>/ (spec 7.1)."""
    slug = re.sub(r"[^a-z0-9]+", "-", work_id.removeprefix("ITKC_").lower()).strip("-")
    target = folder / f"itkc-{slug}"
    edition = " ".join(part for part in ("한국고전번역원", collection) if part)
    groups: dict[str, list[dict[str, Any]]] = {}
    for piece in pieces:
        titles = piece.titles
        if book and not (titles and titles[0].startswith(book)):
            titles = [book, *titles]
        record: dict[str, Any] = {
            "text_id": "itkc:" + piece.piece_id,
            "title": " · ".join(titles),
            "source": make_link({FIELD_DATA_ID: piece.piece_id}),
            "edition": edition,
            "note_open": NOTE_OPEN,
            "note_close": NOTE_CLOSE,
        }
        if any(mark in piece.text for mark in HALFWIDTH_MARKS):
            # Some editions punctuate with halfwidth , and ｡ (嶺南人物考); they are
            # read as punctuation like the NIKH releases (spec 3.9).
            record["halfwidth_punctuation"] = True
        author = piece.author
        if author is not None and (author.born or author.died):
            # The writer's life, the only date these releases give a piece.
            record["era"] = author.name
            record["year"] = author.born or author.died
            if author.born and author.died:
                record["year_end"] = author.died
        record["text"] = piece.text
        groups.setdefault(piece.volume, []).append(record)
    save_bundles(target, groups, "itkc:", report=report)
    return target


def _report_content(release: Release) -> str:
    """The import report beside the bundles: counts and the pieces left out.

    It holds no source text, absolute path or time, so importing the same release
    again writes the same bytes.
    """
    report = {
        "schema": 1,
        "importer": "itkc",
        "book": release.book,
        "accepted": len(release.pieces),
        "skipped": len(release.skipped),
        "skipped_articles": [
            {**item, "source": make_link({FIELD_DATA_ID: item["id"]})}
            for item in release.skipped
        ],
    }
    return json.dumps(report, ensure_ascii=False, indent=1) + "\n"


def import_documents(found: Iterable[bytes], folder: Path) -> dict[str, object]:
    release = read_documents(found)
    target = save(
        release.work_id,
        release.book,
        release.collection,
        release.pieces,
        folder,
        report=_report_content(release),
    )
    return {
        "book": release.book,
        "articles": len(release.pieces),
        "folder": str(target),
        # Pieces left out; distinct from a batch's count of files skipped.
        "skipped_articles": len(release.skipped),
        "report": str(target / REPORT_NAME),
    }
