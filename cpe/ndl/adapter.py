"""NDL Next Digital Library (lab.ndl.go.jp) as a search source over OCR excerpts.

The Lab API's `book/search` answers a keyword with books and, per book, OCR excerpts
("highlights") around the hits, with the hit marked by escaped <em> text. Checked by
hand on 2026-10-10 (five requests): a single character finds nothing; space-separated
words are not combined with AND (they find nothing either); a two-character phrase
works; the excerpts are unpunctuated OCR with recognition errors. So a search sends one
phrase of two or more characters and the matcher decides on each excerpt. Every
excerpt is OCR text with truncated edges: it is never presented as the printed text or
as a whole sentence (docs/decisions.md "Source databases are connected one at a time").
Nothing is cached; each book keeps its NDL link.
"""

from __future__ import annotations

import html
import http.client
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.cancellation import Checkpoint, pause
from cpe.local.txt import LocalText

API_URL = "https://lab.ndl.go.jp/dl/api/book/search"
PAGE_SIZE = 50
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
_HIGHLIGHT = re.compile(r"</?em\b[^>]*>")

Transport = Callable[[str, dict[str, Any], float], bytes]


class NdlCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


class NdlError(RuntimeError):
    pass


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise NdlError("response exceeds 32 MB")
        return data


@dataclass
class NdlClient:
    """Project safeguards, not published NDL limits: 1 s between requests, 200 a
    session, no retry loop (a failure is reported to the researcher)."""

    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = 200
    timeout: float = 30.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def request(
        self,
        url: str,
        params: dict[str, Any],
        transport_override: Transport | None = None,
        *,
        checkpoint: Callable[[], None] | None = None,
        wait: Callable[[float], None] | None = None,
    ) -> bytes:
        """Search and selected OCR imports share one serialized session budget."""
        with self._lock:
            return self._request(url, params, transport_override, checkpoint, wait)

    def _request(
        self,
        url: str,
        params: dict[str, Any],
        transport_override: Transport | None = None,
        checkpoint: Callable[[], None] | None = None,
        wait: Callable[[float], None] | None = None,
    ) -> bytes:
        if checkpoint:
            checkpoint()
        if self.calls >= self.max_calls:
            raise NdlCapReached(f"request cap {self.max_calls} reached")
        if self._last is not None:
            delay = self.min_interval - (self.clock() - self._last)
            if delay > 0:
                pause(delay, wait or self.sleep, checkpoint)
        # A queued import may have been cancelled while a search held the lock
        # or while the interval elapsed; it must not spend a call or send next.
        if checkpoint:
            checkpoint()
        self._last = self.clock()
        self.calls += 1
        try:
            return (transport_override or self.transport)(url, params, self.timeout)
        except (OSError, http.client.HTTPException) as error:
            raise NdlError(str(error)) from None

    def search(
        self, phrase: str, start: int = 0, checkpoint: Checkpoint = None
    ) -> dict[str, Any]:
        params = {
            "keyword": phrase,
            "fc-isClassic": "true",
            "size": PAGE_SIZE,
            "from": start,
        }
        try:
            data = json.loads(self.request(API_URL, params, checkpoint=checkpoint))
        except InterruptedError:
            raise
        except (OSError, ValueError) as error:
            raise NdlError(str(error)) from None
        if not isinstance(data, dict):
            raise NdlError("unexpected response")
        return data


def excerpt_text(highlight: str) -> str:
    return html.unescape(_HIGHLIGHT.sub("", highlight)).strip()


def book_link(book: dict[str, Any]) -> str:
    book_id = str(book.get("id", ""))
    return f"https://dl.ndl.go.jp/pid/{book_id}" if book_id.isdigit() else ""


def book_texts(book: dict[str, Any]) -> list[LocalText]:
    title = " ".join(
        str(part) for part in (book.get("title"), book.get("volume")) if part
    )
    edition = " ".join(
        str(part) for part in (book.get("publisher"), book.get("published")) if part
    )
    texts = []
    highlights = book.get("highlights")
    if highlights is None:
        return []
    if not isinstance(highlights, list) or any(
        not isinstance(item, str) for item in highlights
    ):
        raise NdlError("highlights must be a list of source strings")
    for index, highlight in enumerate(highlights, start=1):
        text = excerpt_text(highlight)
        if not text:
            continue
        texts.append(
            LocalText(
                path=Path(),
                text_id=f"ndl:{book.get('id', '')}#{index}",
                reference=text,
                range="excerpt",
                boundary_start="truncated",
                boundary_end="truncated",
                punctuation="modern",
                full_stop="sentence",
                text_type="original",
                title=title or str(book.get("id", "")),
                source=book_link(book),
                edition=edition,
                sha256="",
                bom=False,
            )
        )
    return texts


@dataclass(frozen=True)
class Fetched:
    total: int
    books: int
    texts: list[LocalText]
    # When a text dates from, as the source records it: a dict with `kind` and its
    # fields, worded by the page from its catalog. Kept beside the texts, keyed by
    # text_id, so the texts stay plain LocalText values.
    eras: dict[str, dict[str, Any]] = field(default_factory=dict)


def book_era(book: dict[str, Any]) -> dict[str, Any] | None:
    """The edition's publication year and its own wording (such as 道光16)."""
    year = book.get("publishyear")
    if not isinstance(year, int) or year <= 0:
        return None
    published = book.get("published")
    return {
        "kind": "published",
        "year": year,
        "text": published if isinstance(published, str) else "",
    }


def fetch(
    phrase: str, client: NdlClient, start: int = 0, checkpoint: Checkpoint = None
) -> Fetched:
    data = (
        client.search(phrase, start, checkpoint=checkpoint)
        if checkpoint
        else client.search(phrase, start)
    )
    books = data.get("list")
    if not isinstance(books, list) or any(not isinstance(book, dict) for book in books):
        raise NdlError("books must be a list of objects")
    total = data.get("hit")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(books)
        or (books and start + len(books) > total)
        or len(books) > PAGE_SIZE
        or (start < total and not books)
    ):
        raise NdlError("invalid result counts")
    texts: list[LocalText] = []
    eras: dict[str, dict[str, Any]] = {}
    for book in books:
        era = book_era(book)
        for text in book_texts(book):
            texts.append(text)
            if era is not None:
                eras[text.text_id] = era
    return Fetched(total, len(books), texts, eras)
