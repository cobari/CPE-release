"""AKS Korean Historical Manuscript Archive search-window adapter.

The source is the Academy of Korean Studies manuscript archive at
https://archive.aks.ac.kr. Its public search form posts one query string to
search/list.do and returns the first page of original-text hits as
server-rendered HTML. Each row carries a title, a highlighted transcription
excerpt, a date and a JavaScript record route keyed by the record's UCI.

This module reads one page of the ``anciTXT`` original-text group. It does not
paginate, fetch record bodies or image viewers, or store source text.
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

BASE = "https://archive.aks.ac.kr"
SEARCH_URL = BASE + "/search/list.do"
PAGE_ITEMS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
MAX_QUERY = 100
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]
_TOTAL = re.compile(r"총<span[^>]*>([\d,]+)</span>건")
_ROW = re.compile(r"<li>(.*?)</li>", re.S)
_TITLE_BLOCK = re.compile(r'<div class="list__title">(.*?)</div>', re.S)
_JS = re.compile(r"goDataViewGisa\('([^']*)','([^']+)','([^']*)'")
_VIEW = re.compile(r"goDataView\('([^']+)'\)")
_TITLE = re.compile(r"<b>(.*?)</b>", re.S)
_SNIPPET = re.compile(r'<div class="list__txt1">(.*?)</div>', re.S)
_DATE = re.compile(r"</a>\s*([^<]*?)\s*(?:<a\b|$)", re.S)
_UCI = re.compile(r"[A-Za-z0-9+._-]{8,160}\Z")
_TAG = re.compile(r"<[^>]+>")


class AksArchiveError(RuntimeError):
    """The search page could not be read or the request failed."""


class AksArchiveCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


@dataclass(frozen=True)
class Item:
    title: str
    uci: str
    service: str
    date: str
    snippet: str
    url: str


@dataclass(frozen=True)
class Fetched:
    total: int
    items: tuple[Item, ...]


def _clean(raw: str) -> str:
    return html.unescape(_TAG.sub("", raw)).strip()


def _record_url(service: str, uci: str, ref_id: str, term: str) -> str | None:
    """The archive's own record route for the row's document or diary service.

    The search page links through goDataViewGisa; this reproduces that route
    from the same arguments. The fragment is handled by the archive's client.
    """
    if service == "문헌자료":
        path, item, depth = "/docmat/item.do", "docmat", 6
    elif service == "일기자료":
        path, item, depth = "/dy/item.do", "dy", 5
    else:
        return None
    start = 2 if service == "일기자료" else 1
    parts = ref_id.split("_")
    levels = ["_".join(parts[:index]) for index in range(start, len(parts) + 1)]
    up_path = uci
    for level in levels:
        up_path += "^" + level
    query = urllib.parse.urlencode(
        {
            "itemId": item,
            "gubun": "book",
            "depth": str(depth),
            "dg": "1",
            "upPath": up_path,
            "dataId": ref_id,
            "wordHL": term,
            "itemType": "최종정보",
        }
    )
    return BASE + path + "#/node.do?" + query


def _view_url(uci: str) -> str | None:
    """The archive's UCI resolver route used by goDataView."""
    if not _UCI.fullmatch(uci):
        return None
    return BASE + "/link.do?dataUCI=" + urllib.parse.quote(uci, safe="+.-_")


def parse(raw: bytes, term: str) -> Fetched:
    """One result page, validated field by field; layout drift fails closed."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AksArchiveError("search_layout")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AksArchiveError("search_layout") from None
    total_match = _TOTAL.search(text)
    if total_match is None:
        raise AksArchiveError("search_layout")
    count = total_match.group(1).replace(",", "")
    if len(count) > 1000:
        raise AksArchiveError("search_layout")
    try:
        total = int(count)
    except ValueError:
        raise AksArchiveError("search_layout") from None
    items: list[Item] = []
    for block in _ROW.findall(text):
        if 'class="list__title"' not in block:
            continue
        js = _JS.search(block)
        view = _VIEW.search(block)
        title_block = _TITLE_BLOCK.search(block)
        title_match = _TITLE.search(block)
        snippet_match = _SNIPPET.search(block)
        if (
            (js is None and view is None)
            or title_block is None
            or title_match is None
            or snippet_match is None
        ):
            raise AksArchiveError("search_layout")
        if js is not None:
            service, uci, ref_id = js.groups()
            url = _record_url(service, uci, ref_id, term)
        else:
            assert view is not None
            service, uci = "link", view.group(1)
            url = _view_url(uci)
        title = _clean(title_match.group(1))
        snippet = _clean(snippet_match.group(1))
        if not title or not snippet or not _UCI.fullmatch(uci) or url is None:
            raise AksArchiveError("search_layout")
        date_match = (
            _DATE.search(title_block.group(1)) if title_block is not None else None
        )
        date = date_match.group(1).strip().lstrip("/").strip() if date_match else ""
        if len(snippet) > MAX_SNIPPET:
            snippet = snippet[: MAX_SNIPPET - 1] + "…"
        items.append(Item(title, uci, service, date, snippet, url))
    if total == 0 and not items:
        return Fetched(0, ())
    if not items or len(items) > PAGE_ITEMS:
        raise AksArchiveError("search_layout")
    return Fetched(total, tuple(items))


class AksArchiveClient:
    """One-page search client with request spacing and a session cap."""

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
                raise AksArchiveCapReached
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
                raise AksArchiveError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise AksArchiveError("search_large")
            return data

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        """One user query; only the first page of the original-text group."""
        if (
            not term
            or len(term) > MAX_QUERY
            or any(char in term for char in "\r\n\x00†$")
        ):
            raise AksArchiveError("search_query")
        url = (
            SEARCH_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "q": "query†" + term,
                    "secId": "anciTXT",
                    "pageUnit": str(PAGE_ITEMS),
                }
            )
        )
        request = urllib.request.Request(url, headers={"Accept": "text/html"})
        return parse(self._send(request, checkpoint), term)
