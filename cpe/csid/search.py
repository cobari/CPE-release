"""Read one CSID transcription-search page as metadata references only.

The native list returns titles and catalogue fields, without transcription
excerpts. It cannot establish a sentence, literal occurrence or grammatical
match. Scope and observed request fields: docs/research/csid-search-window.md.
"""

from __future__ import annotations

import http.client
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from cpe.http import content_length

BASE = "http://csid.zju.edu.cn/tomb/"
SEARCH_URL = BASE + "stone/list"
PAGE_ITEMS = 15
MAX_BYTES = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (personal non-commercial research)"
COLLECTIONS = (
    "歷代墓誌",
    "館藏拓片",
    "中國宗教",
    "名家書法",
    "鴛鴦墓誌",
    "女性墓誌",
    "石刻美術",
)
Transport = Callable[[urllib.request.Request, float], bytes]
_OPEN = re.compile(r"\s*window\.parent\.open\(\s*'([^']+)'\s*\)\s*;?\s*\Z")
_ID = re.compile(r"[a-fA-F0-9]{32}\Z")


class CsidError(RuntimeError):
    """The provider request failed or the native list was not recognized."""


class CsidCapReached(RuntimeError):
    """The session request cap was reached; no request was sent."""


@dataclass(frozen=True)
class Item:
    record_id: str
    rubbing_id: str
    title: str
    dynasty: str
    date: str
    kind: str
    number: str
    url: str


@dataclass(frozen=True)
class Fetched:
    items: tuple[Item, ...]
    pages: int


def _record_link(action: str) -> tuple[str, str, str]:
    match = _OPEN.fullmatch(action)
    if not match:
        raise CsidError("search_layout")
    parts = urllib.parse.urlsplit(match.group(1))
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or parts.path not in ("detail", "stone/detail")
    ):
        raise CsidError("search_layout")
    fields = urllib.parse.parse_qs(parts.query)
    if set(fields) != {"id", "rubbingId"} or any(
        len(value) != 1 for value in fields.values()
    ):
        raise CsidError("search_layout")
    record_id, rubbing_id = fields["id"][0], fields["rubbingId"][0]
    if not _ID.fullmatch(record_id) or not _ID.fullmatch(rubbing_id):
        raise CsidError("search_layout")
    url = (
        BASE
        + "stone/detail?"
        + urllib.parse.urlencode({"id": record_id, "rubbingId": rubbing_id})
    )
    return record_id, rubbing_id, url


class _List(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.table_seen = False
        self.row_action: str | None = None
        self.cell: list[str] | None = None
        self.cells: list[str] = []
        self.items: list[Item] = []
        self.empty_seen = False
        self.pages: str | None = None
        self.in_form = False
        self.state: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = {name: value or "" for name, value in attrs}
        if tag == "table":
            if self.table_depth:
                raise CsidError("search_layout")
            if "m-table1" in fields.get("class", "").split():
                if self.table_seen:
                    raise CsidError("search_layout")
                self.table_seen = True
                self.table_depth = 1
        if tag == "form" and fields.get("id") == "pageForm":
            self.in_form = True
        if tag == "input":
            if fields.get("id") == "goTotalPage":
                self.pages = fields.get("value", "")
            if self.in_form and fields.get("name"):
                name, value = fields["name"], fields.get("value", "")
                if name in self.state:
                    raise CsidError("search_layout")
                self.state[name] = value
        if self.table_depth and tag == "tr":
            if self.row_action is not None:
                raise CsidError("search_layout")
            self.cells = []
            if "bline" in fields.get("class", "").split():
                self.row_action = fields.get("onclick", "")
        if self.table_depth and tag == "td":
            if self.cell is not None:
                raise CsidError("search_layout")
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False
        if self.table_depth and tag == "td" and self.cell is not None:
            value = "".join(self.cell).strip()
            self.cells.append(value)
            if value == "未找到任何数据":
                self.empty_seen = True
            self.cell = None
        if self.table_depth and tag == "tr" and self.row_action is not None:
            if self.cell is not None or len(self.cells) != 6 or not self.cells[1]:
                raise CsidError("search_layout")
            record_id, rubbing_id, url = _record_link(self.row_action)
            self.items.append(
                Item(
                    record_id,
                    rubbing_id,
                    self.cells[1],
                    self.cells[2],
                    self.cells[3],
                    self.cells[4],
                    self.cells[5],
                    url,
                )
            )
            self.row_action = None
        if self.table_depth and tag == "table":
            self.table_depth = 0

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def parse(raw: bytes, term: str) -> Fetched:
    page = _List()
    try:
        page.feed(raw.decode("utf-8"))
    except UnicodeError as error:
        raise CsidError("search_layout") from error
    expected = {
        "tag": "3",
        "smSearchSelect": "subStoneArticle",
        "smSearType": "0",
        "smInputText": term,
        "pageNumber": "1",
        "pageSize": str(PAGE_ITEMS),
        "subLibType": ",".join(COLLECTIONS),
    }
    if (
        not page.table_seen
        or page.table_depth
        or page.row_action is not None
        or any(page.state.get(key) != value for key, value in expected.items())
    ):
        raise CsidError("search_layout")
    if not page.items:
        if not page.empty_seen or page.pages is not None:
            raise CsidError("search_layout")
        return Fetched((), 0)
    if (
        page.empty_seen
        or not page.pages
        or not re.fullmatch(r"[1-9][0-9]{0,8}", page.pages)
    ):
        raise CsidError("search_layout")
    pages = int(page.pages)
    if len(page.items) > PAGE_ITEMS or (pages > 1 and len(page.items) != PAGE_ITEMS):
        raise CsidError("search_layout")
    # A page count is not a record count. Preserve it separately; multiplying
    # by page size would invent an unknown last-page size and duplicate scope.
    return Fetched(tuple(page.items), pages)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class CsidClient:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        min_interval: float = 1.0,
        max_calls: int = 30,
        timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.min_interval = max(1.0, min_interval)
        self.max_calls = max_calls
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.calls = 0
        self._last: float | None = None
        self._lock = threading.Lock()
        self._opener = (
            urllib.request.build_opener(_NoRedirect()) if transport is None else None
        )

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        if (
            not term
            or len(term) > 80
            or any(
                char.isspace() or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                for char in term
            )
            or any(char in term for char in '+-|{}()@"?*/\\[]<>=!~^:$&%_\x7f')
        ):
            raise CsidError("search_query")
        fields: dict[str, str | tuple[str, ...]] = {
            "tag": "3",
            "smSearchSelect": "subStoneArticle",
            "smSearType": "0",
            "smInputText": term,
            "pageNumber": "1",
            "pageSize": str(PAGE_ITEMS),
            "subLibType": COLLECTIONS,
        }
        request = urllib.request.Request(
            SEARCH_URL + "?" + urllib.parse.urlencode(fields, doseq=True),
            headers={"User-Agent": AGENT},
        )
        with self._lock:
            if self.calls >= self.max_calls:
                raise CsidCapReached
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            if checkpoint:
                checkpoint()
            self._last = self.clock()
            self.calls += 1
            try:
                if self.transport is not None:
                    raw = self.transport(request, self.timeout)
                else:
                    assert self._opener is not None
                    with self._opener.open(request, timeout=self.timeout) as response:
                        content_length(
                            response.headers.get("Content-Length"), MAX_BYTES
                        )
                        raw = response.read(MAX_BYTES + 1)
            except (
                OSError,
                http.client.HTTPException,
                ValueError,
                OverflowError,
            ) as error:
                raise CsidError("search_failed") from error
            if len(raw) > MAX_BYTES:
                raise CsidError("search_large")
            if checkpoint:
                checkpoint()
            return parse(raw, term)
