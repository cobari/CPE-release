"""The NIKH Sillok public search window as candidate references.

The search window at sillok.history.go.kr accepts one user query and returns a
server-rendered result page. This module sends that one query, reads the first
page of the original-text result tab, and keeps the returned title, original-text
excerpt and article link. It does not paginate, fetch article pages, or crawl.

The researcher's 2026-10-25 decision allows the public search window for every
source and still prohibits crawling (docs/decisions.md "Research use through the
tool: which terms matter"). The request is serialized with at least one second
between starts, has a session cap and a response-size cap, and stores nothing.
"""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

SEARCH_URL = "https://sillok.history.go.kr/search/searchResultList.do"
VIEW_URL = "https://sillok.history.go.kr/record/recordView.do?id="
PAGE_SIZE = 50
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]
_TAG = re.compile(r"<[^>]+>")
_TOTAL = re.compile(r'name="totalCount"\s+value="(\d+)"')
_COUNT_W = re.compile(r'name="countW"\s+value="(\d+)"')
_RESULT = re.compile(
    r'<div class="result-box">\s*'
    r'<a href="javascript:goView\(\'([A-Za-z0-9_]+)\',\s*\d+\);" '
    r'class="subject">(.*?)</a>\s*'
    r'<p class="text">(.*?)</p>',
    re.S,
)


class SillokError(RuntimeError):
    """The search page could not be read or the request failed."""


class SillokCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


@dataclass(frozen=True)
class Item:
    article_id: str
    title: str
    snippet: str


@dataclass(frozen=True)
class Fetched:
    total: int
    items: tuple[Item, ...]


def article_url(article_id: str) -> str:
    """The provider's own article page for a search result ID."""
    return VIEW_URL + urllib.parse.quote(article_id, safe="")


def clean(fragment: str) -> str:
    """Result markup to readable text with entities decoded and spaces collapsed."""
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def parse(raw: bytes) -> Fetched:
    """First-page results from one search response, preserving the excerpt."""
    text = raw.decode("utf-8", errors="replace")
    # countW is the original-text tab's own total; totalCount combines translation
    # and original results, so it is only a fallback when countW is absent.
    match = _COUNT_W.search(text) or _TOTAL.search(text)
    total = int(match.group(1)) if match else 0
    items: list[Item] = []
    for found in _RESULT.finditer(text):
        article_id, title, snippet = found.groups()
        snippet = clean(snippet)
        if not snippet:
            continue
        items.append(Item(article_id, clean(title), snippet))
        if len(items) >= PAGE_SIZE:
            break
    if total and not items:
        raise SillokError("search_result_layout")
    return Fetched(total, tuple(items))


class SillokClient:
    """One-query search client with the project's request safeguards."""

    def __init__(
        self,
        transport: Transport | None = None,
        min_interval: float = 1.0,
        max_calls: int = 60,
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
                raise SillokCapReached
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
            except (OSError, ValueError) as error:
                raise SillokError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise SillokError("search_large")
            return data

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        """One original-text query; at most the first page of results."""
        if not term or len(term) > 100 or any(char in term for char in "\r\n"):
            raise SillokError("search_query")
        fields = {
            "topSearchWord": term,
            "topSearchWord_ime": term,
            "reSearchWord": "",
            "reSearchWords": term,
            "reSearchWords_ime": term,
            "pageIndex": "1",
            "pageUnit": str(PAGE_SIZE),
            "sillokType": "S",
            "type": "w",
            "searchType": "kwType",
            "sort": "period",
            "initPageUnit": "0",
        }
        request = urllib.request.Request(
            SEARCH_URL,
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            method="POST",
            headers={
                "Referer": "https://sillok.history.go.kr/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        raw = self._send(request, checkpoint)
        if checkpoint is not None:
            checkpoint()
        return parse(raw)
