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
- <annotation type="원주"> and "소자쌍행": original interlinear notes (割註),
  written between note markers so they stay barriers (spec 2.5);
- every other annotation (교감주, 각주, DB주석) is modern editorial apparatus and is
  left out, and so are <reference> blocks (the database's links to related articles,
  in Korean; checked on the 고려사 file);
- an article with a <newChar> (an unresolved glyph reference) in its text, an original
  note, its titles or its date is left out whole and listed in the import report
  (import-report.json beside the bundles), so no character is silently dropped and no
  text joins characters the source keeps apart; a <newChar> in apparatus that is left
  out anyway changes nothing (checked on the 해동고승전 and 조선왕조실록 15053647
  files and the seven listed releases); an import whose left-out article an earlier
  import kept in a bundle it would not overwrite is refused (check_existing);
- articles whose id ends in $<n>int, $<n>rem or $<n>exp are the modern printed
  edition's preface (序), explanatory notes (凡例) and introduction (解題), written in
  Korean by the institute (checked on the 각사등록 file), and are left out. Other $
  ids are the work's own front matter (the 고려사 箋, 世系, 修史官 and its own 凡例)
  and are kept.
Whitespace runs that contain a line break are the XML file's indentation, not text, and
are removed; other spaces (after punctuation) are kept.

Character references written wrongly are restored in two mechanical steps, each once,
and nothing else is unescaped: a document that does not parse is parsed again with
each surrogate pair of references joined (cpe.local.releases.join_surrogates), and a
numeric reference escaped exactly twice in the parsed text becomes its character
(_characters). Only references to CJK ideographs are restored; any other reference
stays as written. The release file itself is never changed; the written text is the
reference text of the search, with one character for each restored reference, so its
offsets count restored characters.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.local.releases import (
    ReleaseError,
    bundle_name,
    documents,
    parse,
    save_bundles,
)
from cpe.local.txt import BUNDLE_SUFFIX, InputError, read_bundle

ARTICLE_LINK = "https://db.history.go.kr/id/"
# Interlinear-note markers written into the text and declared in each manifest; chosen
# because they do not occur in these releases.
NOTE_OPEN, NOTE_CLOSE = "〘", "〙"
ORIGINAL_NOTE = "원주"
# The Sillok construction rules identify small double-line text as original notes;
# treating it as apparatus deletes source commentary (offline-editorial-options.md).
ORIGINAL_NOTES = frozenset({ORIGINAL_NOTE, "소자쌍행"})
# An unresolved glyph reference: a character the release writes only as a pointer to
# the institute's glyph (<newChar href="KC04089"/>; whether Unicode has it is not
# checked). It has no text, so dropping it would join the characters around it; the
# text model cannot yet carry it.
NEW_CHARACTER = "newChar"
REPORT_NAME = "import-report.json"
# Digits are bounded so that a malformed reference never reaches int() unbounded.
_LITERAL_REF = re.compile(r"&#(x[0-9A-Fa-f]{1,6}|[0-9]{1,7});")
# The CJK ideograph blocks of Unicode 17.0
# (https://www.unicode.org/Public/17.0.0/ucd/Blocks.txt): Extension A, the unified
# block, compatibility ideographs, Extensions B to J and the compatibility supplement.
# The gaps between them and the plane-end noncharacters stay as written, and so do the
# private-use references (U+F0852, U+F0853) seen in 조선왕조실록 15053647; the other
# twice-escaped references seen in the releases are in Extensions B and C.
_IDEOGRAPHS = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x2EBF0, 0x2EE5F),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
    (0x323B0, 0x3347F),
)
_INDENT = re.compile(r"[ \t\r\n]*\n[ \t\r\n]*")
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


@dataclass(frozen=True)
class Skipped:
    """An article left out whole; `reason` is a code the import report records."""

    article_id: str
    reason: str


@dataclass(frozen=True)
class Release:
    prefix: str
    book: str
    articles: list[Article]
    skipped: list[Skipped] = field(default_factory=list)


class _Unresolved(Exception):
    """A <newChar> in text that would be written; the article is left out."""


class _Unsupported(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _strict_text(
    element: ET.Element, in_note: bool = False, in_paragraph: bool = False
) -> None:
    """A partial import excludes editorial choices instead of flattening them.

    docs/research/offline-editorial-options.md records why small double-line
    annotations are original notes and proofreading needs a witness policy.
    """
    if element.tag == "proofreading":
        raise _Unsupported("proofreading_policy")
    if element.tag == "reference":
        return
    if element.tag == "annotation":
        if element.get("type") not in ORIGINAL_NOTES:
            return
        if (
            not in_paragraph
            or in_note
            or len(element) != 1
            or element[0].tag != "noteContent"
            or (element.text or "").strip()
            or (element[0].tail or "").strip()
        ):
            raise _Unsupported("original_note_structure")
        in_note = True
    if element.tag not in {
        "text",
        "content",
        "paragraph",
        "postScript",
        "index",
        "annotation",
        "noteContent",
        "br",
        NEW_CHARACTER,
    }:
        raise _Unsupported("unsupported_inline")
    if element.tag == "paragraph":
        in_paragraph = True
    if element.tag == NEW_CHARACTER and not in_paragraph:
        raise _Unresolved
    if element.tag == "br" and ((element.text or "").strip() or len(element)):
        raise _Unsupported("unsupported_inline")
    # Only outer paragraphs are extracted. Meaningful container text or tails
    # would otherwise disappear while the article still appeared complete.
    if not in_paragraph and (element.text or "").strip():
        raise _Unsupported("text_outside_paragraphs")
    for child in element:
        _strict_text(child, in_note, in_paragraph)
        if not in_paragraph and (child.tail or "").strip():
            raise _Unsupported("text_outside_paragraphs")


def _characters(text: str) -> str:
    """Text with twice-escaped character references read as the characters.

    Some releases escape a reference twice (&amp;#x29876; in 조선왕조실록 15053647), so
    the parsed text holds the nine characters "&#x29876;" instead of one: the
    character would be lost and the reference counted as nine in slot lengths. Only
    a reference to a CJK ideograph is restored; anything else (punctuation such as
    &#46;, U+FFFF, controls) stays as written, so no mark or boundary is created.
    """
    if "&#" not in text:
        return text
    return _LITERAL_REF.sub(_reference, text)


def _reference(match: re.Match[str]) -> str:
    value = match.group(1)
    code = int(value[1:], 16) if value.startswith("x") else int(value)
    if any(low <= code <= high for low, high in _IDEOGRAPHS):
        return chr(code)
    return match.group(0)


def _flat(element: ET.Element | None) -> str:
    if element is None:
        return ""
    if element.find(f".//{NEW_CHARACTER}") is not None:
        raise _Unresolved
    return _characters(_INDENT.sub("", "".join(element.itertext())).strip(" \t\r\n"))


def _outer_paragraphs(element: ET.Element) -> list[ET.Element]:
    """Paragraphs not inside another paragraph, in document order.

    A paragraph may hold paragraphs (the 史論 of 조선왕조실록 sits in <postScript>
    inside one); its text already includes them, so listing them again would write
    them twice. Annotations and <reference> blocks are not searched for paragraphs:
    they are left out of the text.
    """
    found: list[ET.Element] = []
    for child in element:
        if child.tag == "paragraph":
            found.append(child)
        elif child.tag not in ("annotation", "reference"):
            found += _outer_paragraphs(child)
    return found


def _paragraph_text(element: ET.Element) -> str:
    """Text of a paragraph: original notes between markers, editorial notes left out."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == "annotation":
            if child.get("type") in ORIGINAL_NOTES:
                note = child.find("noteContent")
                parts.append(NOTE_OPEN + _note_text(note) + NOTE_CLOSE)
        elif child.tag in ("br", "reference"):
            pass
        elif child.tag == NEW_CHARACTER:
            raise _Unresolved
        else:
            parts.append(_paragraph_text(child))
        parts.append(child.tail or "")
    return _INDENT.sub("", "".join(parts))


def _note_text(note: ET.Element | None) -> str:
    if note is None:
        return ""
    parts = [note.text or ""]
    for child in note:
        if child.tag == NEW_CHARACTER:
            raise _Unresolved
        if child.tag not in ("illustration", "annotation"):
            parts.append(_paragraph_text(child))
        parts.append(child.tail or "")
    text = _INDENT.sub("", "".join(parts)).strip(" \t\r\n")
    # Some releases (고종·순종실록) also write the note's own 【 】 inside it; the note
    # markers replace them.
    if text.startswith("【") and text.endswith("】"):
        text = text[1:-1]
    return text


def _articles(
    level: ET.Element,
    titles: list[str] | None,
    release: Release,
    document: str,
    strict: bool = False,
) -> None:
    """Collect the articles under level. `titles` is None when an enclosing title
    holds an unresolved character: every article below would carry that title."""
    level_id = level.get("id", "")
    front = level.find("front")
    here: list[str] | None = None
    if titles is not None:
        try:
            title = (
                _flat(front.find("biblioData/title/mainTitle"))
                if front is not None
                else ""
            )
            series = (
                _flat(front.find("biblioData/title/seriesTitle"))
                if front is not None
                else ""
            )
            here = [*titles, *(part for part in (series, title) if part)]
        except _Unresolved:
            here = None
    text = level.find("text")
    if text is not None and not _EDITION_MATTER.search(level_id):
        try:
            if strict:
                _strict_text(text)
            paragraphs = [
                _paragraph_text(p).strip(" \t\r\n") for p in _outer_paragraphs(text)
            ]
            body = _characters("\n".join(p for p in paragraphs if p))
            if strict and re.search(
                r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af□]", body
            ):
                raise _Unsupported("mixed_script_or_placeholder_review")
        except _Unresolved:
            release.skipped.append(Skipped(level_id, "new_character_text"))
        except _Unsupported as error:
            release.skipped.append(Skipped(level_id, error.reason))
        else:
            date = (
                front.find("biblioData/date/dateOccured") if front is not None else None
            )
            match = _DATE.match(date.get("date", "")) if date is not None else None
            year = int(match.group(1)) if match else None
            if year == UNKNOWN_YEAR:
                year = None
            try:
                date_text: str | None = _flat(date)
            except _Unresolved:
                date_text = None
            if not body:
                pass
            elif here is None:
                release.skipped.append(Skipped(level_id, "new_character_title"))
            elif date_text is None:
                release.skipped.append(Skipped(level_id, "new_character_date"))
            else:
                release.articles.append(
                    Article(level_id, here, body, year, date_text, document)
                )
    for child in level:
        if _LEVEL.match(child.tag):
            _articles(child, here, release, document, strict)


def read_release(path: Path) -> tuple[str, str, list[Article]]:
    """The id prefix, book name and articles of a release ZIP, or of one XML file."""
    return read_documents(documents(path))


def read_documents(found: Iterable[bytes]) -> tuple[str, str, list[Article]]:
    release = read_all(found)
    return release.prefix, release.book, release.articles


def read_all(
    found: Iterable[bytes], strict: bool = True, *, allow_excluded_only: bool = False
) -> Release:
    """The release's articles and the articles left out.

    All supported import routes use conservative editorial exclusions by default;
    otherwise ZIP and directory imports could flatten readings rejected by preview.
    A diagnostic preview may retain an excluded-only result without saving text.
    With no article left, the release is refused (release_new_character when
    articles were left out, release_empty otherwise), before anything is written.
    """
    release = Release("", "", [])
    book = ""
    for document in found:
        root = parse(document)
        if root.tag == "item":
            # The release's descriptor (sg_000.xml for 삼국사기) names the book.
            book = root.get("name", "") or book
            for child in root:
                if _LEVEL.match(child.tag):
                    _articles(child, [], release, child.get("id", ""), strict)
            continue
        if not _LEVEL.match(root.tag):
            raise ReleaseError("release_unknown")
        _articles(root, [], release, root.get("id", ""), strict)
    if not release.articles and (not allow_excluded_only or not release.skipped):
        raise ReleaseError(
            "release_new_character" if release.skipped else "release_empty"
        )
    first = (release.articles or release.skipped)[0]
    prefix = first.article_id.split("_")[0] or "nikh"
    return Release(prefix, book, release.articles, release.skipped)


def target_folder(folder: Path, prefix: str) -> Path:
    return folder / f"nikh-{re.sub(r'[^a-z0-9]+', '-', prefix.lower()).strip('-')}"


def check_existing(target: Path, release: Release) -> None:
    """Refuse a partial import whose left-out articles an earlier import kept.

    An article left out this time stays searchable if an earlier import wrote it into
    a bundle this import does not overwrite (its whole volume is left out now), and
    the report would then wrongly say it was left out. Nothing is deleted: the
    release is refused (release_existing_conflict) before anything is written, and
    the researcher imports into a new folder. A bundle that does not read as a bundle
    is not searched (the folder skips it) and is ignored here.
    """
    if not release.skipped or not target.is_dir():
        return
    overwritten = {bundle_name(article.volume) for article in release.articles}
    left_out = {"nikh:" + item.article_id for item in release.skipped}
    for bundle in target.glob("*" + BUNDLE_SUFFIX):
        if bundle.name in overwritten:
            continue
        try:
            texts = read_bundle(bundle)
        except InputError:
            continue
        if any(text.text_id in left_out for text in texts):
            raise ReleaseError("release_existing_conflict")


def save(
    prefix: str,
    articles: list[Article],
    folder: Path,
    book: str,
    *,
    report: str | None = None,
) -> Path:
    """One text bundle per volume under folder/nikh-<prefix>/ (spec 7.1)."""
    target = target_folder(folder, prefix)
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
    save_bundles(target, groups, "nikh:", report=report)
    return target


def _report_content(release: Release) -> str:
    """The import report beside the bundles: counts and the articles left out.

    It holds no source text, no absolute path and no time, so importing the same
    release again writes the same bytes.
    """
    report = {
        "schema": 1,
        "importer": "nikh",
        "book": release.book,
        "accepted": len(release.articles),
        "skipped": len(release.skipped),
        "skipped_articles": [
            {
                "id": item.article_id,
                "reason": item.reason,
                "source": ARTICLE_LINK + item.article_id,
            }
            for item in release.skipped
        ],
    }
    return json.dumps(report, ensure_ascii=False, indent=1) + "\n"


def import_documents(found: Iterable[bytes], folder: Path) -> dict[str, object]:
    release = read_all(found)
    check_existing(target_folder(folder, release.prefix), release)
    target = save(
        release.prefix,
        release.articles,
        folder,
        release.book,
        report=_report_content(release),
    )
    report = target / REPORT_NAME
    return {
        "book": release.book,
        "articles": len(release.articles),
        "folder": str(target),
        # Articles left out; distinct from a batch's count of files skipped.
        "skipped_articles": len(release.skipped),
        "report": str(report),
    }
