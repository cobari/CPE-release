"""NRICH wooden-tablet (목간) transcription search adapter.

The source is the official National Research Institute of Cultural Heritage
Open API at https://portal.nrich.go.kr/kor/openapi.do?idx=67. Each page is a
fixed inclusive row range; the response is <list><data> with the ordered
fields md_data1..md_data24. Field 20 is the raw transcription, kept as the
parser returned it. A bounded snapshot is cached for the session and searched
locally; the server has no documented text-query parameter.
"""

from __future__ import annotations

import http.client
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cpe.http import content_length

API_URL = "https://portal.nrich.go.kr/kor/openapi.do"
DATASET_IDX = "67"
PAGE_ROWS = 50
MAX_ROWS = 600
MAX_BYTES = 2 * 1024 * 1024
MIN_INTERVAL = 1.0
MAX_CALLS = 200
TIMEOUT = 35.0
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"

Transport = Callable[[str, dict[str, Any], float], bytes]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ROW = re.compile(r"[1-9][0-9]{0,8}\Z")


class NrichCapReached(RuntimeError):
    """The session request cap was reached; no further page is sent."""


class NrichError(RuntimeError):
    pass


@dataclass(frozen=True)
class NrichRow:
    row: int
    stable_id: str
    item_number: str | None
    site: str | None
    report_item: str | None
    photo: str | None
    findspot: str | None
    excavation_year: str | None
    region: str | None
    holder_a: str | None
    holder_b: str | None
    kind: str | None
    condition: str | None
    dimensions: str | None
    period: str | None
    wood: str | None
    report: str | None
    unknown: str | None
    side: str | None
    transcription: str | None
    institution: str | None
    rights_text: str | None
    opaque_field22: str | None
    url: str
    raw: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    rows: tuple[NrichRow, ...]
    pages: int
    stopped_short: bool
    capped: bool

    @property
    def no_transcription(self) -> int:
        return sum(1 for row in self.rows if row.transcription is None)


def _text(value: str) -> str | None:
    """A metadata field: the literal 'null' and empty text mean absent."""
    value = value.strip()
    return None if value in ("", "null") else value


def _safe_link(raw: str, stable_id: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host != "portal.nrich.go.kr":
        return None
    valid_ports = (None, 443) if parts.scheme == "https" else (None, 80)
    if parts.username or parts.password or port not in valid_ports:
        return None
    if parts.path != "/kor/mokganView.do" or parts.fragment:
        return None
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if query.get("idx") != [stable_id]:
        return None
    return urllib.parse.urlunsplit(
        ("https", "portal.nrich.go.kr", parts.path, parts.query, "")
    )


def _page(raw: bytes, start: int, end: int) -> list[NrichRow]:
    if len(raw) > MAX_BYTES:
        raise NrichError("response exceeded size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NrichError(f"malformed XML UTF-8: {error}") from None
    if "\x00" in text:
        raise NrichError("malformed XML: NUL bytes are forbidden")
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise NrichError("DOCTYPE and entity declarations are forbidden")
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError) as error:
        raise NrichError(f"malformed XML: {error}") from None
    if root.tag != "list" or root.attrib:
        raise NrichError("unexpected root element")
    if (root.text or "").strip():
        raise NrichError("unexpected text outside data")
    data = list(root)
    if any(item.tag != "data" or item.attrib for item in data):
        raise NrichError("unexpected list child")
    if any((item.text or "").strip() for item in data):
        raise NrichError("unexpected text inside data")
    if any((item.tail or "").strip() for item in data):
        raise NrichError("unexpected text between data")
    if len(data) > end - start + 1:
        raise NrichError("page exceeds the requested row range")
    expected = [f"md_data{number}" for number in range(1, 25)]
    rows: list[NrichRow] = []
    seen: set[str] = set()
    for offset, item in enumerate(data):
        if [child.tag for child in item] != expected:
            raise NrichError("unexpected md_data order or count")
        if any(child.attrib or len(child) for child in item):
            raise NrichError("unexpected md_data structure")
        if any((child.tail or "").strip() for child in item):
            raise NrichError("unexpected text between md_data fields")
        values = [child.text or "" for child in item]
        row_number = values[0].strip()
        if not _ROW.fullmatch(row_number) or int(row_number) != start + offset:
            raise NrichError("unexpected row identity")
        stable_id = values[1].strip()
        if not _ID.fullmatch(stable_id) or stable_id in seen:
            raise NrichError("unexpected stable identity")
        seen.add(stable_id)
        url = _safe_link(values[23], stable_id)
        if url is None:
            raise NrichError("item URL does not match the stable identity")
        raw_transcription = values[19]
        transcription = (
            None if raw_transcription.strip() in ("", "null") else raw_transcription
        )
        rows.append(
            NrichRow(
                row=int(row_number),
                stable_id=stable_id,
                item_number=_text(values[2]),
                site=_text(values[3]),
                report_item=_text(values[4]),
                photo=_text(values[5]),
                findspot=_text(values[6]),
                excavation_year=_text(values[7]),
                region=_text(values[8]),
                holder_a=_text(values[9]),
                holder_b=_text(values[10]),
                kind=_text(values[11]),
                condition=_text(values[12]),
                dimensions=_text(values[13]),
                period=_text(values[14]),
                wood=_text(values[15]),
                report=_text(values[16]),
                unknown=_text(values[17]),
                side=_text(values[18]),
                transcription=transcription,
                institution=_text(values[20]),
                rights_text=_text(values[22]),
                opaque_field22=_text(values[21]),
                url=url,
                raw=tuple(values),
            )
        )
    return rows


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Only same-host HTTPS redirects are followed; everything else is rejected."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            parts = urllib.parse.urlsplit(newurl)
            port = parts.port
        except ValueError:
            return None
        if (
            parts.scheme != "https"
            or (parts.hostname or "").lower() != "portal.nrich.go.kr"
            or parts.username
            or parts.password
            or port not in (None, 443)
        ):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    if not url.startswith("https://portal.nrich.go.kr/"):
        raise NrichError("HTTPS to portal.nrich.go.kr is required")
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": AGENT},
    )
    try:
        with urllib.request.build_opener(SafeRedirectHandler()).open(
            request, timeout=timeout
        ) as response:
            total = content_length(response.headers.get("Content-Length"), MAX_BYTES)
            data: bytes = response.read(MAX_BYTES + 1)
    except (OSError, http.client.HTTPException, ValueError, OverflowError) as error:
        raise NrichError(str(error)) from None
    if len(data) > MAX_BYTES:
        raise NrichError("response exceeded size limit")
    if total is not None and len(data) != total:
        raise NrichError("response length does not match Content-Length")
    return data


@dataclass
class NrichClient:
    """Serial lock, 1 s interval, 200 calls, 35 s timeout, no retries."""

    transport: Transport = urllib_transport
    min_interval: float = MIN_INTERVAL
    max_calls: int = MAX_CALLS
    timeout: float = TIMEOUT
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _snapshot: Snapshot | None = field(default=None, repr=False)

    def snapshot(self, checkpoint: Callable[[], None] | None = None) -> Snapshot:
        with self._lock:
            if checkpoint:
                checkpoint()
            if self._snapshot is not None:
                return self._snapshot
            rows: list[NrichRow] = []
            seen: set[str] = set()
            pages = 0
            stopped_short = False
            capped = False
            while len(rows) < MAX_ROWS:
                if checkpoint:
                    checkpoint()
                if self.calls >= self.max_calls:
                    raise NrichCapReached("request cap reached")
                wait = 0.0
                if self._last is not None:
                    wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
                if checkpoint:
                    checkpoint()
                self._last = self.clock()
                self.calls += 1
                start = len(rows) + 1
                end = min(start + PAGE_ROWS - 1, MAX_ROWS)
                try:
                    raw = self.transport(
                        API_URL,
                        {
                            "idx": DATASET_IDX,
                            "firstindex": start,
                            "recordcountperpage": end,
                        },
                        self.timeout,
                    )
                except (OSError, ValueError) as error:
                    raise NrichError(str(error)) from None
                if checkpoint:
                    checkpoint()
                page = _page(raw, start, end)
                if checkpoint:
                    checkpoint()
                pages += 1
                if not page:
                    stopped_short = True
                    break
                for row in page:
                    if row.stable_id in seen:
                        raise NrichError("duplicate stable identity across pages")
                    seen.add(row.stable_id)
                rows.extend(page)
                if len(page) < end - start + 1:
                    stopped_short = True
                    break
            if len(rows) >= MAX_ROWS:
                capped = True
            if checkpoint:
                checkpoint()
            snapshot = Snapshot(tuple(rows), pages, stopped_short, capped)
            self._snapshot = snapshot
            return snapshot
