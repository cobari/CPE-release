"""SAT Daizōkyō search-window adapter.

The source is the University of Tokyo SAT Daizōkyō Text Database. Its public
form posts one key to ddb-sat2.php and returns the first group of thirty
line-level hits, each with a Taishō page/line range, a keyword-in-context
snippet and a detail link. This module reads that one page; it does not
paginate, fetch detail pages or store source text.

The Taishō text overlaps the CBETA canon already connected in the window. This
route adds SAT's line references and its own rendering; it is not a claim of new
holdings.
"""

from __future__ import annotations

import html
import http.client
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

BASE = "https://21dzk.l.u-tokyo.ac.jp/SAT/"
SEARCH_URL = BASE + "ddb-sat2.php"
PAGE_ITEMS = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
MAX_QUERY = 100
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]
_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_TOTAL = re.compile(r'name="totalHits"[^>]*>\s*(\d+)\s+hits?')
_NUMBER = re.compile(r"[0-9A-Za-z]{1,10}\Z")
_USEID = re.compile(r"[0-9A-Za-z_,]{1,64}\Z")
_ENTRY = re.compile(
    r"^(?P<title>.*?)\s*\(No\.\s*(?P<number>[0-9A-Za-z]+)\)\s*"
    r"(?P<pages>.*?)\s*:\s*(?P<snippet>.*?)\s*"
    r'<a href="(?P<href>[^"]*mode2=2[^"]*)">\[show\]</a>\s*'
    r"\((?P<hits>\d+)\s+hits?\)\s*$",
    re.S,
)


class SatError(RuntimeError):
    """The search page could not be read or the request failed."""


class SatCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


def _count(value: str) -> int:
    if len(value) > 1000:
        raise SatError("search_layout")
    try:
        return int(value)
    except ValueError:
        raise SatError("search_layout") from None


@dataclass(frozen=True)
class Item:
    work: str
    number: str
    pages: str
    snippet: str
    url: str
    hits: int
    glyphs: int


@dataclass(frozen=True)
class Fetched:
    # The provider counts keyword occurrences, not result lines.
    total: int | None
    items: tuple[Item, ...]


def _clean(raw: str) -> str:
    return html.unescape(_TAG.sub("", raw)).strip()


def _snippet(raw: str) -> tuple[str, int]:
    """Snippet text, with image-only glyphs kept as an explicit placeholder."""
    glyphs = len(_IMG.findall(raw))
    value = _clean(_IMG.sub("□", raw))
    if len(value) > MAX_SNIPPET:
        value = value[: MAX_SNIPPET - 1] + "…"
    return value, glyphs


def _detail_url(raw: str) -> str | None:
    """The provider's own detail-page URL for one result line."""
    href = html.unescape(raw).strip()
    try:
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, href))
        port = parts.port
    except ValueError:
        return None
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "21dzk.l.u-tokyo.ac.jp"
        or parts.path != "/SAT/ddb-sat2.php"
        or parts.username
        or parts.password
        or port not in (None, 443)
        or parts.fragment
        or query.get("mode") != ["detail"]
        or query.get("mode2") != ["2"]
        or len(query.get("useid", [])) != 1
        or not _USEID.fullmatch(query["useid"][0])
    ):
        return None
    return urllib.parse.urlunsplit(
        ("https", "21dzk.l.u-tokyo.ac.jp", parts.path, parts.query, "")
    )


def parse(raw: bytes) -> Fetched:
    """The first result group, validated field by field; drift fails closed."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SatError("search_layout")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SatError("search_layout") from None
    total_match = _TOTAL.search(text)
    if total_match is None:
        raise SatError("search_layout")
    total = _count(total_match.group(1))
    marker = text.find("mode=detail")
    if marker < 0:
        if total == 0:
            return Fetched(0, ())
        raise SatError("search_layout")
    start = text.rfind('<td valign="top"', 0, marker)
    end = text.find("</td>", marker)
    if start < 0 or end < 0:
        raise SatError("search_layout")
    cell = text[start:end]
    items: list[Item] = []
    seen: set[str] = set()
    for chunk in re.split(r"<br\s*/?>", cell, flags=re.I):
        if "mode=detail" not in chunk:
            continue
        chunk = re.sub(r"^\s*<td\b[^>]*>", "", chunk)
        match = _ENTRY.match(chunk)
        if match is None:
            raise SatError("search_layout")
        work = _clean(match.group("title"))
        number = match.group("number")
        pages = _clean(match.group("pages"))
        if not work or not _NUMBER.fullmatch(number) or not pages:
            raise SatError("search_layout")
        url = _detail_url(match.group("href"))
        if url is None:
            raise SatError("search_layout")
        if url in seen:
            raise SatError("search_layout")
        seen.add(url)
        snippet, glyphs = _snippet(match.group("snippet"))
        if not snippet:
            raise SatError("search_layout")
        items.append(
            Item(
                work,
                number,
                pages,
                snippet,
                url,
                _count(match.group("hits")),
                glyphs,
            )
        )
    if len(items) > PAGE_ITEMS or not items or len(items) > total:
        raise SatError("search_layout")
    return Fetched(total, tuple(items))


class SatClient:
    """One-query client with request spacing and a session cap."""

    def __init__(
        self,
        transport: Transport | None = None,
        min_interval: float = 1.0,
        max_calls: int = 30,
        timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.min_interval = min_interval
        self.max_calls = max_calls
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.calls = 0
        self._last: float | None = None
        self._lock = threading.Lock()

    def _send(
        self, request: urllib.request.Request, checkpoint: Callable[[], None] | None
    ) -> bytes:
        with self._lock:
            if self.calls >= self.max_calls:
                raise SatCapReached
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            if checkpoint is not None:
                checkpoint()
            self._last = self.clock()
            self.calls += 1
            request.add_header("User-Agent", AGENT)
            try:
                if self.transport is not None:
                    data = self.transport(request, self.timeout)
                else:
                    with urllib.request.urlopen(
                        request, timeout=self.timeout
                    ) as answer:
                        data = answer.read(MAX_RESPONSE_BYTES + 1)
            except (OSError, http.client.HTTPException, ValueError) as error:
                raise SatError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise SatError("search_large")
            return data

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        """One user query; only the first result group is read."""
        if (
            not term
            or len(term) > MAX_QUERY
            or any(char in term for char in "\r\n\x00")
        ):
            raise SatError("search_query")
        url = (
            SEARCH_URL
            + "?"
            + urllib.parse.urlencode({"mode": "search", "key": term, "uop": "1"})
        )
        request = urllib.request.Request(url, headers={"Accept": "text/html"})
        return parse(self._send(request, checkpoint))
