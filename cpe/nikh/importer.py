"""National Institute of Korean History original-text XML releases as text files.

The institute publishes its database texts (삼국사기, 삼국유사, 비변사등록 and
others) on data.go.kr as ZIP files of XML under one DTD (history.dtd): nested
level1 > level2 > level3 … elements, each with a <front> (titles, a dated
<dateOccured>) and, at article level, <text><content><paragraph>. The researcher
downloads the ZIP; this module writes the articles, one per bundle line, into the
open text folder, which is then searched as local text (docs/decisions.md "Research
use through the tool: which terms matter").

What is source text (checked on the 2026-08 삼국사기 file):
- paragraph text, including the text of <index> elements (names, places);
- <annotation type="원주">: the original interlinear notes (割註), written between note
  markers so they stay barriers (spec 2.5);
- every other annotation (교감주, 각주, DB주석) is modern editorial apparatus and is
  left out, and so are <reference> blocks (the database's links to related articles,
  in Korean; checked on the 고려사 file);
- articles whose id ends in $<n>int, $<n>rem or $<n>exp are the modern printed
  edition's preface (序), explanatory notes (凡例) and introduction (解題), written in
  Korean by the institute (checked on the 각사등록 file), and are left out. Other $
  ids are the work's own front matter (the 고려사 箋, 世系, 修史官 and its own 凡例)
  and are kept.
Whitespace runs that contain a line break are the XML file's indentation, not text, and
are removed; other spaces (after punctuation) are kept.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpe.local.releases import ReleaseError, documents, parse, save_bundles

ARTICLE_LINK = "https://db.history.go.kr/id/"
# Interlinear-note markers written into the text and declared in each manifest; chosen
# because they do not occur in these releases.
NOTE_OPEN, NOTE_CLOSE = "〘", "〙"
ORIGINAL_NOTE = "원주"
_INDENT = re.compile(r"\s*\n\s*")
_DATE = re.compile(r"^(-?\d{1,4})-(\d{2})-(\d{2})")
# The year these releases write for an unknown date (date="9999-99-99", text 미상).
UNKNOWN_YEAR = 9999
_EDITION_MATTER = re.compile(r"\$\d*(int|rem|exp)$")
_LEVEL = re.compile(r"level\d+$")


@dataclass(frozen=True)
class Article:
    article_id: str
    titles: list[str]
    text: str
    year: int | None
    date_text: str
    # The id of the volume (one XML document) the article belongs to; one bundle
    # file is written per volume.
    volume: str


def _flat(element: ET.Element | None) -> str:
    return (
        _INDENT.sub("", "".join(element.itertext())).strip()
        if element is not None
        else ""
    )


def _paragraph_text(element: ET.Element) -> str:
    """Text of a paragraph: original notes between markers, editorial notes left out."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == "annotation":
            if child.get("type") == ORIGINAL_NOTE:
                note = child.find("noteContent")
                parts.append(NOTE_OPEN + _note_text(note) + NOTE_CLOSE)
        elif child.tag in ("br", "reference"):
            pass
        else:
            parts.append(_paragraph_text(child))
        parts.append(child.tail or "")
    return _INDENT.sub("", "".join(parts))


def _note_text(note: ET.Element | None) -> str:
    if note is None:
        return ""
    parts = [note.text or ""]
    for child in note:
        if child.tag not in ("illustration", "annotation"):
            parts.append(_paragraph_text(child))
        parts.append(child.tail or "")
    text = _INDENT.sub("", "".join(parts)).strip()
    # Some releases (고종·순종실록) also write the note's own 【 】 inside it; the note
    # markers replace them.
    if text.startswith("【") and text.endswith("】"):
        text = text[1:-1]
    return text


def _articles(
    level: ET.Element, titles: list[str], out: list[Article], document: str
) -> None:
    front = level.find("front")
    title = _flat(front.find("biblioData/title/mainTitle")) if front is not None else ""
    series = (
        _flat(front.find("biblioData/title/seriesTitle")) if front is not None else ""
    )
    here = [*titles, *(part for part in (series, title) if part)]
    text = level.find("text")
    if text is not None:
        paragraphs = [
            _paragraph_text(p).strip() for p in text.iter("paragraph") if p is not None
        ]
        body = "\n".join(p for p in paragraphs if p)
        date = front.find("biblioData/date/dateOccured") if front is not None else None
        match = _DATE.match(date.get("date", "")) if date is not None else None
        year = int(match.group(1)) if match else None
        if year == UNKNOWN_YEAR:
            year = None
        if body and not _EDITION_MATTER.search(level.get("id", "")):
            out.append(
                Article(
                    level.get("id", ""),
                    here,
                    body,
                    year,
                    _flat(date),
                    document,
                )
            )
    for child in level:
        if _LEVEL.match(child.tag):
            _articles(child, here, out, document)


def read_release(path: Path) -> tuple[str, str, list[Article]]:
    """The id prefix, book name and articles of a release ZIP, or of one XML file."""
    return read_documents(documents(path))


def read_documents(found: list[bytes]) -> tuple[str, str, list[Article]]:
    articles: list[Article] = []
    book = ""
    for document in found:
        root = parse(document)
        if root.tag == "item":
            # The release's descriptor (sg_000.xml for 삼국사기) names the book.
            book = root.get("name", "") or book
            for child in root:
                if _LEVEL.match(child.tag):
                    _articles(child, [], articles, child.get("id", ""))
            continue
        if not _LEVEL.match(root.tag):
            raise ReleaseError("release_unknown")
        _articles(root, [], articles, root.get("id", ""))
    if not articles:
        raise ReleaseError("release_empty")
    prefix = articles[0].article_id.split("_")[0] or "nikh"
    return prefix, book, articles


def save(prefix: str, articles: list[Article], folder: Path, book: str) -> Path:
    """One text bundle per volume under folder/nikh-<prefix>/ (spec 7.1)."""
    target = folder / f"nikh-{re.sub(r'[^a-z0-9]+', '-', prefix.lower()).strip('-')}"
    groups: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        record: dict[str, Any] = {
            "text_id": "nikh:" + article.article_id,
            "title": " · ".join([book, *article.titles] if book else article.titles),
            "source": ARTICLE_LINK + article.article_id,
            "edition": "국사편찬위원회 한국사데이터베이스",
            "note_open": NOTE_OPEN,
            "note_close": NOTE_CLOSE,
            # These releases punctuate with halfwidth . , (researcher decision: read
            # as punctuation, the text itself unchanged; spec 3.9).
            "halfwidth_punctuation": True,
        }
        if article.date_text:
            record["era"] = article.date_text
        if article.year is not None:
            record["year"] = article.year
        record["text"] = article.text
        groups.setdefault(article.volume, []).append(record)
    save_bundles(target, groups, "nikh:")
    return target


def import_documents(found: list[bytes], folder: Path) -> dict[str, object]:
    prefix, book, articles = read_documents(found)
    target = save(prefix, articles, folder, book)
    return {"book": book, "articles": len(articles), "folder": str(target)}
