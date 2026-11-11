"""The NIKH Korean History Database integrated search as candidate references.

The integrated search at db.history.go.kr counts hits per database item; the rows
of one item are served by the era subsites at
``/<site>/search/searchResultList.do``. Five of the six eras answer that page with
"no material" unless a database item is named, so the window asks the researcher
for era and item and this module reads that one page. The goryeo page answers with
its own default item when none is sent, and its era page carries no item list; a
catalog that comes back empty therefore means "search this era without an item",
not a parse failure.

One user query reads the first result page of one era item: the returned title,
excerpt and the level ID that the provider's ``/id/`` citation address resolves.
It does not paginate, fetch article pages or store text (AGENTS.md "Research
data"; docs/decisions.md "Research use through the tool: which terms matter").
Requests are serialized, at least one second apart, capped per session and by
response size, and never retried. Layout changes raise instead of guessing.

The original-text tab is asked for explicitly: with an empty ``searchTypes`` the
page answers from its translation tab, which is not the source text.
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

SITES = ("diachronic", "ancient", "goryeo", "joseon", "modern", "contemp")
LIST_URL = "https://db.history.go.kr/{site}/search/searchResultList.do"
ARTICLE_LINK = "https://db.history.go.kr/id/"
PAGE_SIZE = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TYPES = frozenset({"o", "r"})
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]

_TAG = re.compile(r"<[^>]+>")
_DIV = re.compile(r"<(/?)div\b[^>]*>")
_FORM = re.compile(r'<form[^>]*id="itemSearchForm"')
_ITEM = re.compile(r'<li id="detail_search_item_li_([A-Za-z0-9_$]+)">(.*?)</li>', re.S)
_TABLE = '<div class="list-table">'
_TOTAL = re.compile(r'<div class="total-num">[^<]*<b>([\d,]+)</b>')
_ROW = '<div class="list-item">'
_ROW_LINK = re.compile(
    r"<a\b[^>]*onclick=\"fnGoItemLevel\('[^']*', '([A-Za-z0-9_$]+)', '\d+'\)"
    r'[^"]*">(.*?)</a>',
    re.S,
)
_SNIPPET = re.compile(r'<div class="simple">(.*?)</div>', re.S)
_LEVEL_ID = re.compile(r"[A-Za-z0-9_$]{2,80}")


class HistoryDbError(RuntimeError):
    """The page could not be read, or the request was refused before it was sent."""


class HistoryDbCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


@dataclass(frozen=True)
class CatalogItem:
    item_id: str
    label: str


@dataclass(frozen=True)
class Item:
    level_id: str
    title: str
    snippet: str


@dataclass(frozen=True)
class Fetched:
    total: int
    items: tuple[Item, ...]


def article_url(level_id: str) -> str:
    """The provider's own citation address for one article level.

    The ID is appended as returned: the provider's front matter and series levels
    contain ``$`` (cpe/nikh/importer.py), and the project's release links are built
    the same way.
    """
    if not _LEVEL_ID.fullmatch(level_id):
        raise HistoryDbError("search_level")
    return ARTICLE_LINK + level_id


def clean(fragment: str) -> str:
    """Result markup to readable text with entities decoded and spaces collapsed."""
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def parse_catalog(raw: bytes) -> tuple[CatalogItem, ...]:
    """Era page to its database-item list; an era without such a list yields none.

    An empty tuple is the goryeo case: that page searches its default item, so the
    window sends no item at all. Any other page shape raises, because a change here
    would otherwise look like "no items" and silently disable the era.
    """
    text = raw.decode("utf-8", errors="replace")
    if not _FORM.search(text):
        raise HistoryDbError("catalog_layout")
    found: list[CatalogItem] = []
    seen: set[str] = set()
    for match in _ITEM.finditer(text):
        item_id, block = match.groups()
        if item_id in seen:
            continue
        seen.add(item_id)
        found.append(CatalogItem(item_id, clean(block)))
    return tuple(found)


def _table(text: str) -> str:
    """The list-table element, delimited by its own nesting.

    The row markup repeats nested divs, so the table is cut where its div closes
    rather than at a `</div>` that belongs to a row.
    """
    start = text.find(_TABLE)
    if start < 0:
        raise HistoryDbError("search_result_layout")
    depth = 0
    for tag in _DIV.finditer(text, start):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return text[start : tag.end()]
    raise HistoryDbError("search_result_layout")


def _items(region: str) -> tuple[Item, ...]:
    """Rows of one page; the empty-result page's notice row has no level link."""
    found: list[Item] = []
    for chunk in region.split(_ROW)[1:]:
        link = _ROW_LINK.search(chunk)
        if link is None:
            continue
        level_id, title = link.groups()
        # article_url accepts only this ID shape; an ID outside it is an
        # unexpected page layout, reported before any row is built.
        if not _LEVEL_ID.fullmatch(level_id):
            raise HistoryDbError("search_result_layout")
        snippet = _SNIPPET.search(chunk)
        found.append(
            Item(
                level_id,
                clean(title) or level_id,
                clean(snippet.group(1)) if snippet is not None else "",
            )
        )
        if len(found) >= PAGE_SIZE:
            break
    return tuple(found)


def parse(raw: bytes) -> Fetched:
    """First-page total and rows of one era item, preserving the excerpt."""
    text = raw.decode("utf-8", errors="replace")
    region = _table(text)
    total_match = _TOTAL.search(text)
    total = int(total_match.group(1).replace(",", "")) if total_match else None
    items = _items(region)
    # A page with rows but no total, or a positive total with no linkable row, is a
    # layout this module does not know; reporting zero there would hide the change.
    if items and total is None:
        raise HistoryDbError("search_result_layout")
    if total and not items:
        raise HistoryDbError("search_result_layout")
    return Fetched(total or 0, items)


def _site(site: str) -> str:
    if site not in SITES:
        raise HistoryDbError("search_site")
    return site


def _term(term: str) -> str:
    if not term or len(term) > 100 or any(char in term for char in "\r\n"):
        raise HistoryDbError("search_query")
    return term


def _item(item: str) -> str:
    if item and not _LEVEL_ID.fullmatch(item):
        raise HistoryDbError("search_item")
    return item


def _types(types: str) -> str:
    if types not in TYPES:
        raise HistoryDbError("search_types")
    return types


class HistoryDbClient:
    """One-era, one-item search client with the project's request safeguards."""

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
        self._catalogs: dict[str, tuple[CatalogItem, ...]] = {}
        self._lock = threading.Lock()

    def _send(
        self, request: urllib.request.Request, checkpoint: Callable[[], None] | None
    ) -> bytes:
        with self._lock:
            if self.calls >= self.max_calls:
                raise HistoryDbCapReached
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
                raise HistoryDbError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise HistoryDbError("search_large")
            return data

    @staticmethod
    def _request(site: str, term: str, item: str, types: str) -> urllib.request.Request:
        fields = {
            "totalWord": term,
            "wordImages": "",
            "searchTypes": types,
            "searchItemId": item,
            "pageIndex": "1",
            "pageUnit": str(PAGE_SIZE),
            "pageSize": "1",
            "orderColumn": "levelId",
            "orderDir": "ASC",
            "synonym": "off",
            "chinessChar": "on",
        }
        return urllib.request.Request(
            LIST_URL.format(site=site),
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            method="POST",
            headers={
                "Referer": f"https://db.history.go.kr/{site}/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    def catalog(
        self, site: str, checkpoint: Callable[[], None] | None = None
    ) -> tuple[CatalogItem, ...]:
        """The era's database-item list, read once per session and site.

        The request carries no query term, so the page renders its item panel
        without running a search.
        """
        site = _site(site)
        cached = self._catalogs.get(site)
        if cached is not None:
            return cached
        raw = self._send(self._request(site, "", "", "o"), checkpoint)
        if checkpoint is not None:
            checkpoint()
        found = parse_catalog(raw)
        self._catalogs[site] = found
        return found

    def search(
        self,
        term: str,
        site: str,
        item: str,
        types: str = "o",
        checkpoint: Callable[[], None] | None = None,
    ) -> Fetched:
        """One query in one era item; at most the first page of results.

        ``types`` selects the page tab: ``o`` is the original text and the default,
        ``r`` the translation. ``item`` may be empty only for an era whose catalog
        is empty (goryeo), where the page supplies its own default item.
        """
        term = _term(term)
        site = _site(site)
        item = _item(item)
        types = _types(types)
        raw = self._send(self._request(site, term, item, types), checkpoint)
        if checkpoint is not None:
            checkpoint()
        return parse(raw)
