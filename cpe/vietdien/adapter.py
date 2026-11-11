"""Việt Điện Hán-Nôm OCR search adapter.

The source is the public, unauthenticated JSON API at https://www.viet-dien.com,
documented at /api/openapi.json (Digitizing Vietnam's Hán-Nôm OCR corpus).
/api/v1/search returns one page of page-level hits with a snippet and viewer
link. The OCR is machine-generated and uncorrected, and the provider matches
other glyph forms and simplified/traditional variants by default, so a returned
hit is only an exact literal match when the snippet contains the sent characters.

One query, one page: this module does not paginate, fetch work pages in bulk or
store source text.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BASE = "https://www.viet-dien.com"
SEARCH_URL = BASE + "/api/v1/search"
MAX_ITEMS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
MAX_QUERY = 100
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class VietDienError(RuntimeError):
    """The response could not be read, or the request failed."""


class VietDienCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


@dataclass(frozen=True)
class Item:
    text_id: int
    page_number: int
    item_id: str
    title: str
    title_han: str
    snippet: str
    url: str


@dataclass(frozen=True)
class Fetched:
    # The provider's count is the number of returned rows, not matching pages.
    count: int
    items: tuple[Item, ...]


def _text(value: object) -> str:
    """A returned text field; null means the catalogue left it empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise VietDienError("search_layout")
    return value


def _number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VietDienError("search_layout")
    return value


def _viewer_url(raw: str, text_id: int, page_number: int) -> str | None:
    """The provider's own viewer URL for this text and page, or nothing."""
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "www.viet-dien.com"
        or parts.username
        or parts.password
        or port not in (None, 443)
        or parts.fragment
        or parts.path != f"/read/{text_id}/{page_number}"
    ):
        return None
    return urllib.parse.urlunsplit(
        ("https", "www.viet-dien.com", parts.path, parts.query, "")
    )


def parse(raw: bytes) -> Fetched:
    """One search page, validated field by field; layout drift fails closed."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise VietDienError("search_layout")
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise VietDienError("search_layout") from None
    if not isinstance(payload, dict):
        raise VietDienError("search_layout")
    results = payload.get("results")
    count = payload.get("count")
    if (
        not isinstance(results, list)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(results)
        or len(results) > MAX_ITEMS
    ):
        raise VietDienError("search_layout")
    items: list[Item] = []
    seen: set[tuple[int, int]] = set()
    for row in results:
        if not isinstance(row, dict):
            raise VietDienError("search_layout")
        text_id = _number(row.get("text_id"))
        page_number = _number(row.get("page_number"))
        item_id = _text(row.get("item_id")).strip()
        if text_id <= 0 or page_number <= 0 or not _ITEM_ID.fullmatch(item_id):
            raise VietDienError("search_layout")
        snippet = _text(row.get("snippet")).strip()
        if not snippet:
            # An OCR page with no readable text carries no candidate.
            continue
        if len(snippet) > MAX_SNIPPET:
            snippet = snippet[: MAX_SNIPPET - 1] + "…"
        url = _viewer_url(_text(row.get("viewer_url")), text_id, page_number)
        if url is None:
            raise VietDienError("search_layout")
        key = (text_id, page_number)
        if key in seen:
            raise VietDienError("search_layout")
        seen.add(key)
        items.append(
            Item(
                text_id,
                page_number,
                item_id,
                _text(row.get("title")),
                _text(row.get("title_han")),
                snippet,
                url,
            )
        )
    if results and not items:
        raise VietDienError("search_layout")
    return Fetched(count, tuple(items))


class VietDienClient:
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
                raise VietDienCapReached
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
                raise VietDienError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise VietDienError("search_large")
            return data

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        """One user query; only the first page is read."""
        if (
            not term
            or len(term) > MAX_QUERY
            or any(char in term for char in "\r\n\x00")
        ):
            raise VietDienError("search_query")
        url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": term, "limit": MAX_ITEMS})
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        return parse(self._send(request, checkpoint))
