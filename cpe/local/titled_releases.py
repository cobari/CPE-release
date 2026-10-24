"""Source-scoped historical XML subsets from NIKH and NAHF.

Only these release identities enable title-bearing paragraphs and the observed
nontext link forms. Notes exclude whole articles; no witness reading is chosen.
Format and edition evidence: docs/research/titled-history-releases.md and
docs/research/northern-joseon-import.md and
docs/research/foreign-histories-import.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpe.local.releases import ReleaseError, bundle_name, parse, save_bundles

_LOCK = threading.Lock()
_LEVEL = re.compile(r"level[1-9][0-9]*$")
_INDENT = re.compile(r"[ \t\r]*\n[ \t\r\n]*")
_REVIEW = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af□]")
_DATE = re.compile(r"^(-?\d{1,4})-\d{2}-\d{2}")


@dataclass(frozen=True)
class Profile:
    key: str
    book: str
    namespace: str
    source: str
    edition: str
    identity: re.Pattern[str]
    halfwidth: bool = False


DONGIN = Profile(
    "nikh-muds",
    "東人之文四六",
    "nikh:",
    "https://www.data.go.kr/data/15138770/fileData.do",
    "NIKH Dongin Jimun Saryuk, official XML release 20241015",
    re.compile(r"muds_(?:\d{3}(?:_\d{4})*|\$s\d{2})$"),
    True,
)
DONGMUN = Profile(
    "nahf-dongmunhwigo",
    "同文彙考",
    "nahf:",
    "https://www.data.go.kr/data/15051070/fileData.do",
    "NAHF Dongmun Hwigo, official XML release 20220830; NIKH 1978 base edition",
    re.compile(r"dh\.d_\d{4}(?:_\d{4})*$"),
)
NORTHERN = Profile(
    "nahf-joseon-northern",
    "조선시대북방사자료집",
    "nahf:",
    "https://www.data.go.kr/data/15051064/fileData.do",
    "NAHF Joseon Northern History Source Collection, official XML release 20220830",
    re.compile(r"jb\.d_\d{4}(?:_\d{4})*$"),
)
FOREIGN = Profile(
    "nahf-foreign-histories",
    "중국정사외국전",
    "nahf:",
    "https://www.data.go.kr/data/15051068/fileData.do",
    "NAHF Annotated Foreign Accounts in Chinese Official Histories, "
    "original jo.d series, official XML release 20220830",
    re.compile(r"jo\.d_\d{4}(?:_\d{4})*$"),
    True,
)


class _Excluded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _profile(root: ET.Element) -> Profile:
    id_ = root.get("id", "")
    if root.tag == "item" and id_ == "muds":
        if root.get("org") == "국사편찬위원회" and root.get("name") == DONGIN.book:
            return DONGIN
    if root.tag == "level1" and re.fullmatch(r"muds_\d{3}", id_):
        return DONGIN
    if root.tag == "item" and re.fullmatch(r"dh\.[dkm]_\d{4}", id_):
        if (
            root.get("org") == "동북아역사재단"
            and root.get("itemID") == "dh"
            and root.get("itemName") == "동문휘고"
        ):
            return DONGMUN
    if root.tag == "item" and id_ == "jb.d":
        if (
            root.get("org") == "동북아역사재단"
            and root.get("itemID") == "jb"
            and root.get("itemName") == NORTHERN.book
            and root.get("name") == NORTHERN.book
        ):
            return NORTHERN
    if root.tag == "item" and re.fullmatch(r"jo\.[dk]_\d{4}", id_):
        if (
            root.get("org") == "동북아역사재단"
            and root.get("itemID") == "jo"
            and root.get("itemName") == FOREIGN.book
        ):
            return FOREIGN
    raise ReleaseError("release_unknown")


def _layout(value: str | None) -> bool:
    return not (value or "").strip(" \t\r\n")


def _link(element: ET.Element, parent: str, profile: Profile) -> None:
    if profile == DONGIN:
        if (
            parent == "text"
            and set(element.attrib) == {"uri", "value"}
            and element.get("uri") == "hoi"
            and element.get("value")
            and not len(element)
        ):
            return
    elif (
        profile == DONGMUN
        and parent == "pTitle"
        and not element.attrib
        and _layout(element.text)
        and len(element)
        and all(
            child.tag == "linkid"
            and set(child.attrib) == {"id", "title"}
            and child.get("id")
            and not len(child)
            and _layout(child.text)
            and _layout(child.tail)
            for child in element
        )
    ):
        return
    raise _Excluded("unsupported_link")


def _inline(element: ET.Element, profile: Profile) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == "pTitle" and element.tag == "paragraph":
            if profile == FOREIGN:
                raise _Excluded("unsupported_heading")
            attrs = {"type": "주제목"}
            if profile != DONGIN:
                attrs["align"] = "left"
            if child.attrib != attrs:
                raise _Excluded("unsupported_heading")
            parts.append(_inline(child, profile))
        elif child.tag == "index" and profile != DONGIN:
            kinds = {"이름", "지명", "서명"}
            if profile == DONGMUN:
                kinds |= {"관서", "기타"}
            if (
                set(child.attrib) != {"type"}
                or child.get("type") not in kinds
                or len(child)
            ):
                raise _Excluded("unsupported_index")
            parts.append(child.text or "")
        elif child.tag == "br" and not child.attrib and not len(child):
            if not _layout(child.text):
                raise _Excluded("unsupported_inline")
        elif child.tag == "link":
            _link(child, element.tag, profile)
        else:
            raise _Excluded("unsupported_inline")
        parts.append(child.tail or "")
    return "".join(parts)


def _body(text: ET.Element, profile: Profile) -> str:
    if text.find(".//newChar") is not None:
        raise _Excluded("unresolved_glyph")
    if text.find(".//annotation") is not None:
        raise _Excluded("unsupported_note")
    if text.attrib or not _layout(text.text):
        raise _Excluded("unsupported_body_structure")
    paragraphs: list[str] = []
    prose: list[str] = []
    contents = 0
    for child in text:
        if not _layout(child.tail):
            raise _Excluded("text_outside_paragraphs")
        if child.tag == "link":
            _link(child, "text", profile)
            continue
        if child.tag != "content" or child.attrib or not _layout(child.text):
            raise _Excluded("unsupported_body_structure")
        contents += 1
        for paragraph in child:
            allowed = {} if profile == DONGIN else {"align": "left"}
            if (
                paragraph.tag != "paragraph"
                or paragraph.attrib != allowed
                or not _layout(paragraph.tail)
            ):
                raise _Excluded("unsupported_paragraph")
            value = _INDENT.sub("", _inline(paragraph, profile)).strip(" \t\r\n")
            paragraphs.append(value)
            # Title-only cross references do not establish that article's body.
            prose.append(paragraph.text or "")
            for inline in paragraph:
                if inline.tag != "pTitle":
                    prose.append(_inline(inline, profile))
                prose.append(inline.tail or "")
    if contents != 1:
        raise _Excluded("unsupported_body_structure")
    body = "\n".join(value for value in paragraphs if value)
    if not "".join(prose).strip():
        raise _Excluded("title_only_or_empty_body")
    if _REVIEW.search(body):
        raise _Excluded("mixed_script_or_placeholder_review")
    if "&#" in body:
        raise _Excluded("literal_character_reference")
    return body


def _title(level: ET.Element) -> str:
    title = level.find("front/biblioData/title/mainTitle")
    if title is None:
        return ""
    if len(title) or title.attrib:
        raise _Excluded("unsupported_title")
    return _INDENT.sub("", title.text or "").strip(" \t\r\n")


def import_documents(found: Iterable[bytes], folder: Path) -> dict[str, object]:
    profile: Profile | None = None
    groups: dict[str, list[dict[str, Any]]] = {}
    skipped: list[dict[str, str]] = []
    excluded_documents: list[dict[str, str]] = []
    hashes: list[str] = []
    seen: set[str] = set()
    documents_seen: set[str] = set()

    def visit(level: ET.Element, titles: list[str], volume: str) -> None:
        assert profile is not None
        id_ = level.get("id", "")
        if not profile.identity.fullmatch(id_):
            raise ReleaseError("release_unknown")
        try:
            title = _title(level)
            here = [*titles, *([title] if title else [])]
        except _Excluded:
            here = []
            # An unsupported ancestor title also excludes its descendants.
            for entry in level.iter():
                if entry.find("text") is not None:
                    skipped.append(
                        {"id": entry.get("id", ""), "reason": "unsupported_title"}
                    )
            return
        texts = level.findall("text")
        if texts:
            try:
                if len(texts) != 1:
                    raise _Excluded("unsupported_body_structure")
                body = _body(texts[0], profile)
            except _Excluded as error:
                skipped.append({"id": id_, "reason": error.reason})
            else:
                record: dict[str, Any] = {
                    "text_id": profile.namespace + id_,
                    "title": " · ".join([profile.book, *here]),
                    "source": profile.source,
                    "edition": profile.edition,
                    "range": "excerpt",
                    "boundary_start": "truncated",
                    "boundary_end": "truncated",
                    "text": body,
                }
                if profile.halfwidth:
                    record["halfwidth_punctuation"] = True
                date = level.find("front/biblioData/date/dateOccured")
                if date is not None:
                    if len(date):
                        skipped.append({"id": id_, "reason": "unsupported_date"})
                    else:
                        match = _DATE.match(date.get("date", ""))
                        if match and int(match[1]) != 9999:
                            record["year"] = int(match[1])
                        if date.text:
                            record["era"] = date.text.strip()
                        groups.setdefault(volume, []).append(record)
                else:
                    groups.setdefault(volume, []).append(record)
        for child in level:
            if _LEVEL.fullmatch(child.tag):
                if not child.get("id", "").startswith(id_ + "_"):
                    raise ReleaseError("release_unknown")
                visit(child, here, volume)

    for raw in found:
        root = parse(raw)
        current = _profile(root)
        if profile is not None and current != profile:
            raise ReleaseError("release_unknown")
        profile = current
        hashes.append(hashlib.sha256(raw).hexdigest())
        root_id = root.get("id", "")
        if root_id in documents_seen:
            raise ReleaseError("release_existing_conflict")
        documents_seen.add(root_id)
        if (profile == DONGMUN and not root_id.startswith("dh.d_")) or (
            profile == FOREIGN and not root_id.startswith("jo.d_")
        ):
            excluded_documents.append(
                {"id": root_id, "reason": "translation_or_modified_layer"}
            )
            continue
        for element in root.iter():
            if _LEVEL.fullmatch(element.tag):
                id_ = element.get("id", "")
                if not profile.identity.fullmatch(id_):
                    raise ReleaseError("release_unknown")
                if id_ in seen:
                    raise ReleaseError("release_existing_conflict")
                seen.add(id_)
        if root.tag == "item" and any(
            child.tag != "front" and not _LEVEL.fullmatch(child.tag) for child in root
        ):
            raise ReleaseError("release_unknown")
        if root.tag == "level1":
            visit(root, [], root_id)
        else:
            for child in root:
                if _LEVEL.fullmatch(child.tag):
                    if profile in (DONGMUN, FOREIGN) and (
                        child.tag != "level1" or child.get("id") != root_id
                    ):
                        raise ReleaseError("release_unknown")
                    if profile == NORTHERN and (
                        child.tag != "level1"
                        or not re.fullmatch(r"jb\.d_\d{4}", child.get("id", ""))
                    ):
                        raise ReleaseError("release_unknown")
                    visit(
                        child,
                        [],
                        root_id
                        if profile in (DONGMUN, FOREIGN)
                        else child.get("id", ""),
                    )
    if profile is None or not groups:
        raise ReleaseError("release_empty")
    accepted = sum(len(records) for records in groups.values())
    report = (
        json.dumps(
            {
                "schema": 1,
                "importer": profile.key,
                "book": profile.book,
                "source": profile.source,
                "input_sha256": hashes,
                "accepted": accepted,
                "skipped": len(skipped),
                "skipped_articles": skipped,
                "excluded_documents": excluded_documents,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    target = folder / profile.key
    contents = {
        bundle_name(group): "".join(
            json.dumps(r, ensure_ascii=False) + "\n" for r in records
        )
        for group, records in groups.items()
    }
    contents["import-report.json"] = report
    with _LOCK:
        backup = target.with_name(f".{target.name}.previous")
        if target.is_symlink() or backup.is_symlink():
            raise ReleaseError("release_existing_conflict")
        if backup.exists():
            if target.exists():
                raise ReleaseError("release_existing_conflict")
            backup.rename(target)
        if target.exists():
            if (
                not target.is_dir()
                or {p.name for p in target.iterdir()} != contents.keys()
                or any(
                    (target / name).is_symlink()
                    or not (target / name).is_file()
                    or (target / name).read_bytes() != value.encode("utf-8")
                    for name, value in contents.items()
                )
            ):
                raise ReleaseError("release_existing_conflict")
        else:
            save_bundles(target, groups, profile.namespace, report=report)
    return {
        "book": profile.book,
        "articles": accepted,
        "folder": str(target),
        "skipped_articles": len(skipped),
        "report": str(target / "import-report.json"),
    }
