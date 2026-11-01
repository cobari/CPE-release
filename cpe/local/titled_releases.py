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
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cpe.local.releases import (
    MAX_MEMBER_BYTES,
    MAX_TOTAL_BYTES,
    ReleaseError,
    parse,
    save_bundles,
)

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
JC = Profile(
    "nahf-jocheong-border-talks",
    "조청국경회담자료집",
    "nahf:",
    "https://www.data.go.kr/data/15163258/fileData.do",
    "NAHF Jocheong Border Talks, original jc.d series, official XML release 20260901",
    re.compile(r"jc\.d_\d{4}(?:_\d{4})*$"),
    True,
)
JD = Profile(
    "nahf-chinese-official-histories-dongyi",
    "중국정사동이전",
    "nahf:",
    "https://www.data.go.kr/data/15163258/fileData.do",
    "NAHF Dongyi Accounts in Chinese Official Histories, "
    "original jd.d series, official XML release 20260901",
    re.compile(r"jd\.d_\d{4}(?:_\d{4})*$"),
    True,
)
SG = Profile(
    "nahf-siku-korean-ancient-history",
    "사고전서 속의 한국고대사",
    "nahf:",
    "https://www.data.go.kr/data/15163258/fileData.do",
    "NAHF Korean Ancient History in the Siku Quanshu, "
    "original sg.d series, official XML release 20260901",
    re.compile(r"sg\.d_\d{4}(?:_\d{4})*$"),
    True,
)
NAHF_SOURCE = "https://www.data.go.kr/data/15163258/fileData.do"


def _nahf_profile(code: str, key: str, book: str) -> Profile:
    return Profile(
        key,
        book,
        "nahf:",
        NAHF_SOURCE,
        f"NAHF East Asian Historical Sources, original {code}.d series, "
        "official XML release 20260901",
        re.compile(rf"{code}\.d_\d{{4}}(?:_\d{{4}})*$"),
        True,
    )


GD = _nahf_profile("gd", "nahf-gamgyesa", "감계사등록")
BD = _nahf_profile("bd", "nahf-baekdusan-border-stele", "백두산정계비자료집")
SS = _nahf_profile("ss", "nahf-shinsen-shoujiroku", "신찬성씨록")
JT = _nahf_profile("jt", "nahf-continuation-zizhi-tongjian", "속자치통감장편")
BH = _nahf_profile("bh", "nahf-balhae-sources", "발해사 자료집")
GB = _nahf_profile("gb", "nahf-gojoseon-dangun-buyeo", "고조선단군부여자료집")
CJ = _nahf_profile("cj", "nahf-qing-japan-korea-documents", "청계중일한관계사료")
SDMG = _nahf_profile("sdmg", "nahf-sadaemungwe", "사대문궤")
NS = _nahf_profile("ns", "nahf-nihon-shoki", "일본서기")
DH_DB = Profile(
    "nahf-15163258-dongmunhwigo",
    "동문휘고",
    "nahf15163258:",
    NAHF_SOURCE,
    "NAHF East Asian Historical Sources, original dh.d series, "
    "official XML release 20260901",
    re.compile(r"dh\.d_\d{4}(?:_\d{4})*$"),
)
JO_DB = Profile(
    "nahf-15163258-foreign-histories",
    "중국정사외국전",
    "nahf15163258:",
    NAHF_SOURCE,
    "NAHF East Asian Historical Sources, original jo.d series, "
    "official XML release 20260901",
    re.compile(r"jo\.d_\d{4}(?:_\d{4})*$"),
    True,
)
JB_DB = Profile(
    "nahf-15163258-joseon-northern",
    "조선시대북방사자료집",
    "nahf15163258:",
    NAHF_SOURCE,
    "NAHF East Asian Historical Sources, original jb.d series, "
    "official XML release 20260901",
    re.compile(r"jb\.d_\d{4}(?:_\d{4})*$"),
)
NAHF_BY_ITEM = {
    "jc": JC,
    "jd": JD,
    "sg": SG,
    "gd": GD,
    "bd": BD,
    "ss": SS,
    "jt": JT,
    "bh": BH,
    "gb": GB,
    "cj": CJ,
    "sdmg": SDMG,
    "ns": NS,
    "dh": DH_DB,
    "jo": JO_DB,
    "jb": JB_DB,
}
NAHF_KEYS = frozenset(profile.key for profile in NAHF_BY_ITEM.values())
NAHF_AUTO_BY_ITEM = {
    code: profile
    for code, profile in NAHF_BY_ITEM.items()
    if code not in {"dh", "jo", "jb"}
}
NAHF_EXTENDED_KEYS = frozenset(
    profile.key
    for profile in (GD, BD, SS, JT, BH, GB, CJ, SDMG, NS, DH_DB, JO_DB, JB_DB)
)
JD_NOTE_TYPES = frozenset({"교감주", "각주"})
NAHF_NOTE_TYPES = {
    JC.key: frozenset({"교감주"}),
    JD.key: JD_NOTE_TYPES,
    SG.key: frozenset({"교감주"}),
    GD.key: frozenset({"교감주", "편자주"}),
    BD.key: frozenset({"교감주"}),
    JT.key: frozenset({"각주"}),
    BH.key: frozenset({"각주"}),
    GB.key: frozenset(),
    CJ.key: frozenset({"각주"}),
    SDMG.key: frozenset({"각주", "소자쌍행"}),
    NS.key: frozenset({"교감주"}),
    DH_DB.key: frozenset({"소자쌍행", "교감주"}),
    JO_DB.key: frozenset({"교감주", "번역주", "각주"}),
    JB_DB.key: frozenset({"소자쌍행", "교감주"}),
}
NAHF_HEADING_ATTRS = {
    BD.key: ({"align": "left", "type": "주제목"},),
    BH.key: (
        {"align": "left", "type": "주제목"},
        {"align": "left", "type": "소제목"},
    ),
    SS.key: ({"type": "주제목"},),
    SDMG.key: ({"type": "소제목"},),
    CJ.key: (
        {"align": "left", "type": "주제목"},
        {"align": "left", "type": "소제목"},
        {"type": "중제목"},
    ),
    NS.key: ({"align": "left", "type": "주제목"},),
    DH_DB.key: ({"align": "left", "type": "주제목"},),
    JB_DB.key: (
        {"align": "left", "type": "주제목"},
        {"align": "left", "type": "소제목"},
    ),
}
NAHF_EMPH_KEYS = frozenset({GD.key, BH.key, GB.key, JB_DB.key})
NAHF_LINK_KEYS = frozenset({JC.key, BD.key, CJ.key, DH_DB.key})
NAHF_PREFIX_RELAXED = frozenset({JD.key}) | NAHF_EXTENDED_KEYS
NAHF_ANNEX_KEYS = frozenset({CJ.key})
JC_INDEX_KINDS = frozenset({"이름", "지명", "서명", "관서"})
NAHF_INDEX_KINDS = {
    DH_DB.key: frozenset({"이름", "지명", "서명", "관서", "기타"}),
}


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
    if root.tag == "item":
        for code, candidate in NAHF_AUTO_BY_ITEM.items():
            if (
                id_ == f"{code}.d"
                and root.get("org") == "동북아역사재단"
                and root.get("itemID") == code
                and root.get("itemName") == candidate.book
            ):
                return candidate
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
            if profile == JC:
                if child.attrib != {"align": "left"}:
                    raise _Excluded("unsupported_heading")
            elif profile == JD:
                if child.attrib not in (
                    {"type": "주제목", "align": "left"},
                    {"type": "소제목", "align": "left"},
                ):
                    raise _Excluded("unsupported_heading")
            elif profile.key in NAHF_HEADING_ATTRS:
                if child.attrib not in NAHF_HEADING_ATTRS[profile.key]:
                    raise _Excluded("unsupported_heading")
            else:
                attrs = {"type": "주제목"}
                if profile != DONGIN:
                    attrs["align"] = "left"
                if child.attrib != attrs:
                    raise _Excluded("unsupported_heading")
            parts.append(_inline(child, profile))
        elif child.tag == "index" and profile != DONGIN:
            kinds: set[str]
            if profile.key in NAHF_KEYS:
                kinds = set(NAHF_INDEX_KINDS.get(profile.key, JC_INDEX_KINDS))
            elif profile == DONGMUN:
                kinds = {"이름", "지명", "서명", "관서", "기타"}
            else:
                kinds = {"이름", "지명", "서명"}
            if set(child.attrib) != {"type"} or child.get("type") not in kinds:
                raise _Excluded("unsupported_index")
            if len(child):
                note_types = NAHF_NOTE_TYPES.get(profile.key, frozenset())
                for note in child:
                    if note.tag != "annotation" or note.get("type") not in note_types:
                        raise _Excluded("unsupported_index")
                parts.append(child.text or "")
                for note in child:
                    parts.append(note.tail or "")
            else:
                parts.append(child.text or "")
        elif child.tag == "annotation" and profile.key in NAHF_NOTE_TYPES:
            if child.get("type") not in NAHF_NOTE_TYPES[profile.key]:
                raise _Excluded("unsupported_note")
        elif child.tag == "name" and profile in (JC, GB):
            allowed = (
                {"align": "left"}
                if profile == JC
                else {"align": "right", "role": "저필자"}
            )
            if child.attrib != allowed or len(child):
                raise _Excluded("unsupported_inline")
            parts.append(child.text or "")
        elif child.tag == "emph" and (profile == JC or profile.key in NAHF_EMPH_KEYS):
            emph_attrs = (
                ({"type": "bold", "align": "left"},)
                if profile == JC
                else (
                    {"type": "bold", "align": "left"},
                    {"type": "bold", "align": "right"},
                )
            )
            if child.attrib not in emph_attrs or len(child):
                raise _Excluded("unsupported_inline")
            parts.append(child.text or "")
        elif child.tag in ("link", "linkid") and profile.key in NAHF_LINK_KEYS:
            pass
        elif child.tag == "pDate" and profile == BH:
            if child.attrib != {"align": "left", "type": "작성일"} or len(child):
                raise _Excluded("unsupported_inline")
            parts.append(child.text or "")
        elif child.tag == "quotation" and profile == BH:
            if child.attrib or len(child):
                raise _Excluded("unsupported_inline")
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


def _nahf_paragraph_ok(attributes: dict[str, str]) -> bool:
    if set(attributes) - {"align", "indent"}:
        return False
    if attributes.get("align", "left") not in {"left", "right", "center"}:
        return False
    if "indent" in attributes and not re.fullmatch(r"[1-9]", attributes["indent"]):
        return False
    return True


def _body(text: ET.Element, profile: Profile) -> str:
    if text.find(".//newChar") is not None:
        raise _Excluded("unresolved_glyph")
    if profile.key not in NAHF_NOTE_TYPES and text.find(".//annotation") is not None:
        raise _Excluded("unsupported_note")
    if profile.key in NAHF_NOTE_TYPES:
        note_types = NAHF_NOTE_TYPES[profile.key]
        for annotation in text.iter("annotation"):
            if annotation.get("type") not in note_types:
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
        if child.tag == "content":
            if child.attrib or not _layout(child.text):
                raise _Excluded("unsupported_body_structure")
            contents += 1
        elif child.tag == "annex" and profile.key in NAHF_ANNEX_KEYS:
            if child.attrib or not _layout(child.text):
                raise _Excluded("unsupported_body_structure")
        else:
            raise _Excluded("unsupported_body_structure")
        for paragraph in child:
            if profile == JC:
                if (
                    paragraph.tag != "paragraph"
                    or paragraph.attrib not in ({"align": "left"}, {"align": "right"})
                    or not _layout(paragraph.tail)
                ):
                    raise _Excluded("unsupported_paragraph")
            elif profile.key in NAHF_EXTENDED_KEYS:
                if (
                    paragraph.tag != "paragraph"
                    or not _nahf_paragraph_ok(paragraph.attrib)
                    or not _layout(paragraph.tail)
                ):
                    raise _Excluded("unsupported_paragraph")
            else:
                paragraph_attrs = {} if profile == DONGIN else {"align": "left"}
                if (
                    paragraph.tag != "paragraph"
                    or paragraph.attrib != paragraph_attrs
                    or not _layout(paragraph.tail)
                ):
                    raise _Excluded("unsupported_paragraph")
            value = _INDENT.sub("", _inline(paragraph, profile)).strip(" \t\r\n")
            paragraphs.append(value)
            # Title-only cross references do not establish that article's body.
            prose.append(paragraph.text or "")
            for inline in paragraph:
                if inline.tag == "annotation" and profile.key in NAHF_NOTE_TYPES:
                    prose.append(inline.tail or "")
                    continue
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


def import_documents(
    found: Iterable[bytes], folder: Path, expected: Profile | None = None
) -> dict[str, object]:
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
                if profile.key not in NAHF_PREFIX_RELAXED and not child.get(
                    "id", ""
                ).startswith(id_ + "_"):
                    raise ReleaseError("release_unknown")
                visit(child, here, volume)

    for raw in found:
        root = parse(raw)
        if expected is not None:
            if root.tag != "item" or root.get("itemName") != expected.book:
                raise ReleaseError("release_unknown")
            current = expected
        else:
            current = _profile(root)
        if profile is not None and current != profile:
            raise ReleaseError("release_unknown")
        profile = current
        hashes.append(hashlib.sha256(raw).hexdigest())
        root_id = root.get("id", "")
        doc_key = root_id
        if profile.key in NAHF_KEYS:
            level1_ids = [
                child.get("id", "") for child in root if child.tag == "level1"
            ]
            doc_key = root_id + ":" + ",".join(level1_ids)
        if doc_key in documents_seen:
            raise ReleaseError("release_existing_conflict")
        documents_seen.add(doc_key)
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
                    if profile == JC and (
                        child.tag != "level1"
                        or not re.fullmatch(r"jc\.d_\d{4}", child.get("id", ""))
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
    save_bundles(
        target,
        groups,
        profile.namespace,
        report=report,
        keep_existing=True,
    )
    return {
        "book": profile.book,
        "articles": accepted,
        "folder": str(target),
        "skipped_articles": len(skipped),
        "report": str(target / "import-report.json"),
    }


def import_nahf_zip(path: Path, folder: Path) -> dict[str, object]:
    """The supported .d collections in a NAHF 15163258 ZIP, ignoring others."""
    profiles = dict(NAHF_BY_ITEM)
    try:
        with zipfile.ZipFile(path) as archive:
            groups: dict[str, list[zipfile.ZipInfo]] = {}
            for info in archive.infolist():
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                match = re.fullmatch(r"([a-z]+)\.d_\d{4}\.xml", base)
                if match and match.group(1) in profiles:
                    groups.setdefault(match.group(1), []).append(info)
            if not groups:
                raise ReleaseError("release_empty")
            members = [info for code in groups for info in groups[code]]
            # The archive is read member by member; the same bounds protect this
            # route as the single-document release route in cpe.local.releases.
            if sum(info.file_size for info in members) > MAX_TOTAL_BYTES:
                raise ReleaseError("release_large")
            if any(info.file_size > MAX_MEMBER_BYTES for info in members):
                raise ReleaseError("release_large")
            results = [
                import_documents(
                    (
                        archive.read(info)
                        for info in sorted(groups[code], key=lambda item: item.filename)
                    ),
                    folder,
                    expected=profiles[code],
                )
                for code in sorted(groups)
            ]
    except (
        OSError,
        zipfile.BadZipFile,
        NotImplementedError,
        RuntimeError,
        EOFError,
        zlib.error,
    ):
        raise ReleaseError("release_unreadable") from None
    if len(results) == 1:
        return results[0]
    return {
        "book": " · ".join(str(result["book"]) for result in results),
        "articles": sum(cast(int, result["articles"]) for result in results),
        "folder": str(folder),
        "skipped_articles": sum(
            cast(int, result["skipped_articles"]) for result in results
        ),
        "reports": [result["report"] for result in results],
    }
