"""Public DocuSky searches with source-specific original-text extraction.

The common read interface is documented by DocuWidgets and its linked SDK:
https://hackmd.io/@DocuSky/HJKNdaih4 . Only reviewed database profiles are exposed;
an OPEN catalog entry does not establish clean source text. Extraction evidence,
use conditions and coverage limits are in docs/research/docusky-connection.md.
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.local.txt import LocalText

API_URL = "https://docusky.org.tw/DocuSky/WebApi/getQueryResultDocumentsJson.php"
PAGE_SIZE = 50
MAX_BYTES = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
Transport = Callable[[str, dict[str, Any], float], bytes]


@dataclass(frozen=True)
class Profile:
    code: str
    database: str
    corpora: frozenset[str]
    project_url: str


TEMPLE = Profile(
    "temple-stories",
    "佛寺志靈應故事",
    frozenset({"普陀洛迦新志靈應故事", "清涼山志靈應故事"}),
    "https://docusky.org.tw/DocuSky/projects/ntu/BTGSRS/",
)
PROFILES = {TEMPLE.code: TEMPLE}


class DocuskyError(RuntimeError):
    """A failed public read or an invalid provider response."""


class DocuskyCapReached(RuntimeError):
    """The session request budget has been spent."""


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # This provider's chain lacks an SKI. Keep trust-chain and hostname checks;
    # Python 3.13+'s extra strict profile otherwise rejects the public endpoint.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=tls_context()
    ) as response:
        data: bytes = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise DocuskyError("response byte limit exceeded")
    return data


def literal_phrase(phrase: str) -> bool:
    """Do not let DocuSky interpret a locally literal term as query operators."""
    return 0 < len(phrase) <= 200 and all(
        unicodedata.category(char)[0] in "LN" for char in phrase
    )


@dataclass
class DocuskyClient:
    """Serialized reads: 1 s spacing, 200 attempts/session, no automatic retries."""

    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = 200
    timeout: float = 35.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def search(
        self,
        phrase: str,
        profile: Profile = TEMPLE,
        *,
        page_size: int = PAGE_SIZE,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if profile != PROFILES.get(profile.code) or not literal_phrase(phrase):
            raise DocuskyError("unsupported database or query")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= PAGE_SIZE
        ):
            raise DocuskyError("invalid page size")
        with self._lock:
            if checkpoint:
                checkpoint()
            if self.calls >= self.max_calls:
                raise DocuskyCapReached("session request cap reached")
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            if checkpoint:
                checkpoint()
            self._last = self.clock()
            self.calls += 1
            params = {
                "target": "OPEN",
                "db": profile.database,
                "corpus": "[ALL]",
                "query": phrase,
                "page": 1,
                "pageSize": page_size,
                "requester": "classic-pattern-explorer",
            }
            try:
                raw = self.transport(API_URL, params, self.timeout)
                if len(raw) > MAX_BYTES:
                    raise DocuskyError("response byte limit exceeded")
                response = json.loads(raw)
            except (OSError, http.client.HTTPException, ValueError) as error:
                raise DocuskyError(str(error)) from None
            if checkpoint:
                checkpoint()
        if (
            not isinstance(response, dict)
            or type(response.get("code")) is not int
            or response["code"] != 0
        ):
            raise DocuskyError("public query failed")
        message = response.get("message")
        if not isinstance(message, dict):
            raise DocuskyError("query message must be an object")
        if (
            message.get("target") != "OPEN"
            or message.get("db") != profile.database
            or message.get("corpus") != "[ALL]"
            or message.get("query") != phrase
            or type(message.get("page")) is not int
            or type(message.get("pageSize")) is not int
            or message.get("page") != 1
            or message.get("pageSize") != page_size
        ):
            raise DocuskyError("response query scope does not match the request")
        total = message.get("totalFound")
        docs = message.get("docList")
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(docs, list)
            or len(docs) > page_size
            or len(docs) > total
        ):
            raise DocuskyError("invalid query result counts")
        return message


def _xml(value: str) -> ET.Element | None:
    if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        return None
    try:
        return ET.fromstring(
            value,
            parser=ET.XMLParser(
                target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
            ),
        )
    except (ET.ParseError, ValueError):
        return None


def original_paragraphs(value: str) -> list[str]:
    """Keep plain source Paragraphs separately; never concatenate metadata or gaps."""
    root = _xml(value)
    if root is None or root.tag != "Content" or (root.text or "").strip():
        return []
    paragraphs: list[str] = []
    for child in root:
        if (child.tail or "").strip():
            return []
        if child.tag == "MetaTags":
            continue
        if child.tag != "Paragraph" or len(child):
            return []
        text = child.text or ""
        if text.strip():
            paragraphs.append(text)
    return paragraphs


def result_texts(info: dict[str, Any], profile: Profile = TEMPLE) -> list[LocalText]:
    if (
        profile != TEMPLE
        or not isinstance(info.get("corpus"), str)
        or info.get("corpus") not in profile.corpora
        or info.get("xmlFormatName") != "ThdlContentXml"
    ):
        return []
    doc_id, filename = info.get("docId"), info.get("docFilename")
    if (
        not isinstance(doc_id, str)
        or not re.fullmatch(r"[0-9]+", doc_id)
        or not isinstance(filename, str)
        or not filename
    ):
        return []
    raw = info.get("docContentXml")
    if not isinstance(raw, str):
        return []
    paragraphs = original_paragraphs(raw)
    title_xml = info.get("docTitleXml")
    title_root = _xml(title_xml) if isinstance(title_xml, str) else None
    title = (
        title_root.text or ""
        if title_root is not None
        and title_root.tag == "DocTitle"
        and not len(title_root)
        else ""
    )
    corpus = str(info["corpus"])
    return [
        LocalText(
            path=Path(),
            text_id=f"docusky:{profile.code}:{doc_id}#{index}",
            reference=text,
            range="excerpt",
            boundary_start="truncated",
            boundary_end="truncated",
            punctuation="modern",
            full_stop="sentence",
            text_type="original",
            title=f"{corpus} · {title.strip() or filename}",
            source=profile.project_url,
            edition=f"DocuSky {profile.database}; {corpus}; {filename}; docId={doc_id}",
            sha256="",
            bom=False,
        )
        for index, text in enumerate(paragraphs, start=1)
    ]


@dataclass(frozen=True)
class Fetched:
    total: int
    records: int
    accepted: int
    excluded: int
    texts: list[LocalText]


def fetch(
    phrase: str,
    client: DocuskyClient,
    profile: Profile = TEMPLE,
    *,
    page_size: int = PAGE_SIZE,
    checkpoint: Callable[[], None] | None = None,
) -> Fetched:
    message = client.search(phrase, profile, page_size=page_size, checkpoint=checkpoint)
    docs = message["docList"]
    texts: list[LocalText] = []
    accepted = 0
    seen: set[str] = set()
    for doc in docs:
        if checkpoint:
            checkpoint()
        if not isinstance(doc, dict) or not isinstance(doc.get("docInfo"), dict):
            raise DocuskyError("document information must be an object")
        found = result_texts(doc["docInfo"], profile)
        if found:
            ids = {text.text_id for text in found}
            if ids & seen:
                raise DocuskyError("duplicate document identity")
            seen.update(ids)
            texts.extend(found)
            accepted += 1
    return Fetched(
        message["totalFound"], len(docs), accepted, len(docs) - accepted, texts
    )
