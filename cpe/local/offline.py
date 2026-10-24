"""Source-preserving subsets of local CBETA P5 and NIKH volume XML.

Encoding evidence: docs/research/offline-editorial-options.md. No apparatus
reading is selected and no unsupported container is joined around missing text.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from cpe.local.external import MAX_BYTES, Download, DownloadControl, ExternalError, Unit
from cpe.local.releases import ReleaseError, parse

TEI = "http://www.tei-c.org/ns/1.0"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
NS = {"t": TEI}
UNITS = {"p", "l", "cell"}
CONTAINERS = {"body", "div", "lg", "table", "row"}
MARKERS = {"anchor", "lb", "pb", "milestone"}


class _Unsafe(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _mappings(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[str] = set()
    for char in root.findall("t:teiHeader/t:encodingDesc/t:charDecl/t:char", NS):
        ref = "#" + char.get(XML_ID, "")
        if ref == "#":
            continue
        if ref in seen:
            result.pop(ref, None)
            continue
        seen.add(ref)
        values = set()
        invalid = False
        for mapping in char.findall("t:mapping", NS):
            if mapping.get("type") != "unicode":
                continue
            match = re.fullmatch(r"U\+([0-9A-Fa-f]{4,6})", _text(mapping))
            if match:
                number = int(match[1], 16)
                if (
                    0x3400 <= number <= 0x4DBF
                    or 0x4E00 <= number <= 0x9FFF
                    or 0x20000 <= number <= 0x2A6DF
                    or 0x2A700 <= number <= 0x2EBEF
                    or 0x2EBF0 <= number <= 0x2EE5F
                    or 0x30000 <= number <= 0x3347F
                ):
                    values.add(chr(number))
                else:
                    invalid = True
            else:
                invalid = True
        if len(values) == 1 and not invalid:
            result[ref] = values.pop()
    return result


def _inline(element: ET.Element, mappings: dict[str, str]) -> str:
    parts = [element.text or ""]
    for child in element:
        name = _name(child)
        if child.tag == f"{{{TEI}}}g":
            if child.get("ref") not in mappings:
                raise _Unsafe("unresolved_glyph")
            parts.append(mappings[child.get("ref", "")])
        elif child.tag == f"{{{TEI}}}{name}" and name in MARKERS and not _text(child):
            pass
        elif child.tag == f"{{{TEI}}}hi":
            parts.append(_inline(child, mappings))
        else:
            raise _Unsafe(
                "inline_apparatus_or_note"
                if name in {"app", "note", "choice"}
                else "unsupported_inline"
            )
        parts.append(child.tail or "")
    return "".join(parts)


def _cbeta(
    raw: bytes, id_: str, revision: str, filename: str, control: DownloadControl | None
) -> Download:
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ExternalError("external_revision")
    root = parse(raw)
    work_id = root.get(XML_ID, "")
    identity = re.fullmatch(r"([A-Z]+)([0-9]{2,3})n[0-9]{4,5}[a-z]?", work_id)
    if root.tag != f"{{{TEI}}}TEI" or not identity:
        raise ExternalError("external_offline_format")
    availability = _text(
        root.find("t:teiHeader/t:fileDesc/t:publicationStmt/t:availability", NS)
    )
    if not availability:
        raise ExternalError("external_availability")
    title = (
        _text(root.find("t:teiHeader/t:fileDesc/t:titleStmt/t:title", NS)) or work_id
    )
    body = root.find("t:text/t:body", NS)
    if body is None:
        raise ExternalError("external_offline_format")
    mappings = _mappings(root)
    # The current design rejects a document with an unresolved glyph, even if
    # another policy would already exclude the surrounding structural unit.
    for glyph in body.findall(".//t:g", NS):
        value = mappings.get(glyph.get("ref", ""))
        if value is None or len(glyph) or (glyph.text and glyph.text != value):
            raise ExternalError("external_glyph")
    source = (
        f"https://github.com/cbeta-org/xml-p5/blob/{revision}/"
        f"{identity[1]}/{identity[1]}{identity[2]}/{work_id}.xml"
    )
    units: list[Unit] = []
    excluded: list[dict[str, str]] = []

    def walk(element: ET.Element, locator: str) -> None:
        if control:
            control.checkpoint()
        name = _name(element)
        own_id = element.get(XML_ID) or locator
        if element.tag == f"{{{TEI}}}{name}" and name in UNITS:
            try:
                text = _inline(element, mappings)
            except _Unsafe as error:
                excluded.append({"id": own_id, "reason": error.reason})
                return
            if text.strip():
                units.append(
                    Unit(own_id, text, source, {"title": title + " · " + own_id})
                )
            return
        if element.tag == f"{{{TEI}}}{name}" and name in CONTAINERS:
            if element.text and element.text.strip():
                excluded.append({"id": own_id, "reason": "text_outside_units"})
            counts: dict[str, int] = {}
            for child in element:
                child_name = _name(child)
                counts[child_name] = counts.get(child_name, 0) + 1
                walk(child, f"{locator}/{child_name}[{counts[child_name]}]")
                if child.tail and child.tail.strip():
                    excluded.append({"id": own_id, "reason": "text_outside_units"})
        elif not (
            element.tag == f"{{{TEI}}}{name}"
            and name in MARKERS
            and not len(element)
            and not (element.text or "").strip()
        ):
            excluded.append(
                {"id": own_id, "reason": "nonprose_or_unsupported_container"}
            )

    walk(body, "body")
    back = root.find("t:text/t:back", NS)
    if back is not None:
        excluded.append(
            {"id": back.get(XML_ID) or "text/back", "reason": "back_matter_unreviewed"}
        )
    if not units and not excluded:
        raise ExternalError("external_offline_empty")
    if len({unit.id for unit in units}) != len(units):
        raise ExternalError("external_offline_format")
    return Download(
        "cbeta",
        id_,
        title,
        "CBETA XML P5 supplied body · " + revision,
        tuple(units),
        ((source, raw),),
        {
            "source_file": filename,
            "source_revision": revision,
            "revision_verification": "researcher_supplied",
            "availability": availability,
            "policy": "supplied_body_no_apparatus_selection",
            "excluded": excluded,
            "back_matter": "not_imported",
            "source_work_id": work_id,
        },
    )


def fetch_file(
    provider: str,
    address: str,
    revision: str = "",
    control: DownloadControl | None = None,
) -> Download:
    path = Path(address)
    if path.suffix.lower() != ".xml":
        raise ExternalError("external_offline_format")
    if control:
        control.update(phase="reading")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise ExternalError("external_offline_unreadable") from None
    if len(raw) > MAX_BYTES:
        raise ExternalError("external_large")
    if control:
        control.update(phase="parsing", received=len(raw), total=len(raw))
    id_ = hashlib.sha256(raw).hexdigest()[:24]
    try:
        if provider == "cbeta":
            return _cbeta(raw, id_, revision.strip(), path.name, control)
        if provider != "nikh-partial":
            raise ExternalError("external_offline_format")
        from cpe.nikh.importer import ARTICLE_LINK, NOTE_CLOSE, NOTE_OPEN, read_all

        release = read_all([raw], strict=True, allow_excluded_only=True)
        units = []
        for article in release.articles:
            if control:
                control.checkpoint()
            metadata: dict[str, Any] = {
                "title": " · ".join([release.book, *article.titles]).strip(" ·")
                or article.article_id,
                "note_open": NOTE_OPEN,
                "note_close": NOTE_CLOSE,
                "halfwidth_punctuation": True,
            }
            if article.year is not None:
                metadata["year"] = article.year
            if article.date_text:
                metadata["era"] = article.date_text
            units.append(
                Unit(
                    article.article_id,
                    article.text,
                    ARTICLE_LINK + article.article_id,
                    metadata,
                )
            )
        title = release.book or release.prefix
        return Download(
            provider,
            id_,
            title,
            "NIKH XML partial · " + release.prefix,
            tuple(units),
            ((path.resolve().as_uri(), raw),),
            {
                "origin": "local_file",
                "source_file": path.name,
                "policy": "exclude_unresolved_editorial_articles",
                "excluded": [
                    {"id": item.article_id, "reason": item.reason}
                    for item in release.skipped
                ],
            },
        )
    except ReleaseError as error:
        raise ExternalError(
            "external_offline_empty"
            if error.code in {"release_empty", "release_new_character"}
            else "external_offline_format"
        ) from None
