"""Read one native Kyujanggak historical-document search page.

Only inline original-text excerpts are retained. Lazy viewer content, images,
translations and other database groups are not fetched. The results remain
references, not grammatical evidence. See docs/research/kyudb-search-window.md.
"""

from __future__ import annotations

import http.client
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser

from cpe.http import content_length

BASE = "https://kyudb.snu.ac.kr"
SEARCH_URL = BASE + "/search/search.do"
PAGE_ITEMS = 20
MAX_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
AGENT = "classic-pattern-explorer (personal non-commercial research)"
Transport = Callable[[urllib.request.Request, float], bytes]
_ACTION = re.compile(
    r"\s*(?:javascript:\s*)?fn_totalSearchResultText\(\s*'40'\s*,\s*"
    r"'([1-9][0-9]?)'\s*,\s*'GMS'\s*,\s*''\s*,\s*"
    r"'([A-Za-z0-9_-]{1,80})'\s*,\s*''\s*,\s*'index'\s*\)\s*;"
    r"\s*(?:return false;)?\s*\Z"
)
_CATALOGUE_ACTION = re.compile(
    r"\s*(?:javascript:\s*)?fn_totalSearchResultView\(\s*'40'\s*,\s*'GMS'\s*,\s*"
    r"'([1-9][0-9]?)'\s*,\s*'([A-Za-z0-9_-]{1,80})'\s*,\s*'master'\s*"
    r"\)\s*;\s*(?:return false;)?\s*\Z"
)
_LABELS = {
    "문서번호": "number",
    "발급자/수취자": "parties",
    "권수": "volume",
    "간행년": "date",
    "문서종류": "type",
    "문서분류": "classification",
}
_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


class KyudbError(RuntimeError):
    """The request failed or the native response was not recognized."""


class KyudbCapReached(RuntimeError):
    """The session cap was reached; no request was sent."""


@dataclass(frozen=True)
class Item:
    book_id: str
    title: str
    snippet: str
    citation: dict[str, str]
    url: str


@dataclass(frozen=True)
class Fetched:
    items: tuple[Item, ...]
    total: int


def search_fields(term: str) -> dict[str, str]:
    return {
        "searchArea": "40",
        "totalSearchString": term,
        "pageIndex": "1",
        "pageUnit": str(PAGE_ITEMS),
        "rebrowsing": "N",
        "dbBookCdRebrowsing": "N",
        "selSortInfo": "sortAlphabetical",
    }


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parts: list[str | _Node] = []

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def walk(self) -> Iterator[_Node]:
        pending = [self]
        while pending:
            node = pending.pop()
            yield node
            pending.extend(
                part for part in reversed(node.parts) if isinstance(part, _Node)
            )

    def text(self) -> str:
        pending: list[str | _Node] = [self]
        output = []
        while pending:
            part = pending.pop()
            if isinstance(part, str):
                output.append(part)
            elif part.tag == "br":
                output.append("\n")
            elif part.tag not in ("script", "style"):
                pending.extend(reversed(part.parts))
        return "".join(output).strip()


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.closed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.stack) >= 128:
            raise KyudbError("search_layout")
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].parts.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag == "body":
            self.closed = True
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].parts.append(data)


def _action(value: str) -> tuple[int, str]:
    match = _ACTION.fullmatch(value)
    if not match:
        raise KyudbError("search_layout")
    return int(match[1]), match[2]


def _item(row: _Node, index: int, term: str) -> Item:
    heads = [node for node in row.walk() if node.has_class("schli")]
    details = [node for node in row.walk() if node.has_class("schli02")]
    if len(heads) != 1 or len(details) != 1:
        raise KyudbError("search_layout")
    head, detail = heads[0], details[0]
    if any(node.tag == "img" for node in head.walk()):
        raise KyudbError("search_layout")
    record_index, book_id = _action(head.attrs.get("onclick", ""))
    heading = re.fullmatch(r"([1-9][0-9]?)\.\s*(.+)", head.text(), re.S)
    if record_index != index or heading is None or int(heading[1]) != index:
        raise KyudbError("search_layout")
    citation = {}
    snippets = []
    for part in detail.parts:
        if not isinstance(part, _Node):
            continue
        if part.tag == "span":
            label, separator, value = part.text().partition(":")
            key = _LABELS.get(label.strip())
            if not separator or key is None or key in citation:
                raise KyudbError("search_layout")
            citation[key] = value.strip()
        elif part.tag == "a":
            catalogue = _CATALOGUE_ACTION.fullmatch(part.attrs.get("onclick", ""))
            if catalogue is not None:
                if (int(catalogue[1]), catalogue[2]) != (index, book_id):
                    raise KyudbError("search_layout")
                # Catalogue descriptions can appear alongside original-text
                # excerpts. Their distinct native action cannot prove body text.
                continue
            if _action(part.attrs.get("onclick", "")) != (index, book_id):
                raise KyudbError("search_layout")
            snippets.append(part)
    if len(snippets) > 1 or set(citation) != set(_LABELS.values()):
        raise KyudbError("search_layout")
    snippet = ""
    if snippets:
        # A glyph image or an unknown container cannot be silently removed and
        # splice unrelated text. Keep the metadata reference without an excerpt.
        allowed = {"a", "font", "b", "span", "br", "strong", "em", "u", "sup", "sub"}
        if all(node.tag in allowed for node in snippets[0].walk()):
            snippet = snippets[0].text()[:MAX_SNIPPET]
    fields = search_fields(term)
    fields.update(
        recordIndex=str(index),
        mid="GMS",
        item_cd="",
        book_cd=book_id,
        vol_no="",
        target="index",
    )
    return Item(
        book_id,
        heading[2].strip(),
        snippet,
        citation,
        BASE + "/book/text.do?" + urllib.parse.urlencode(fields),
    )


def parse(raw: bytes, term: str) -> Fetched:
    page = _Page()
    try:
        page.feed(raw.decode("utf-8"))
    except UnicodeError as error:
        raise KyudbError("search_layout") from error
    nodes = list(page.root.walk())
    forms = [
        node
        for node in nodes
        if node.tag == "form" and node.attrs.get("id") == "frmTotalSearchInf"
    ]
    if not page.closed or len(forms) != 1:
        raise KyudbError("search_layout")
    state = {}
    for node in forms[0].walk():
        if node.tag == "input" and node.attrs.get("name") in search_fields(term):
            name = node.attrs["name"]
            if name in state:
                raise KyudbError("search_layout")
            state[name] = node.attrs.get("value", "")
    if state != search_fields(term):
        raise KyudbError("search_layout")
    counts = [
        re.search(r"고문서검색\s*\(([0-9,]{1,11})건\)", node.text())
        for node in nodes
        if node.has_class("schtit")
    ]
    matched = [match for match in counts if match is not None]
    if len(matched) != 1 or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*|[1-9][0-9]*)", matched[0][1]
    ):
        raise KyudbError("search_layout")
    total = int(matched[0][1].replace(",", ""))
    rows = [
        node
        for node in nodes
        if node.tag == "li"
        and any(
            isinstance(part, _Node) and part.has_class("schli") for part in node.parts
        )
    ]
    if len(rows) != min(total, PAGE_ITEMS):
        raise KyudbError("search_layout")
    return Fetched(
        tuple(_item(row, index, term) for index, row in enumerate(rows, 1)), total
    )


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


class KyudbClient:
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
            raise KyudbError("search_query")
        request = urllib.request.Request(
            SEARCH_URL + "?" + urllib.parse.urlencode(search_fields(term)),
            headers={"User-Agent": AGENT},
        )
        with self._lock:
            if self.calls >= self.max_calls:
                raise KyudbCapReached
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
                raise KyudbError("search_failed") from error
            if len(raw) > MAX_BYTES:
                raise KyudbError("search_large")
            if checkpoint:
                checkpoint()
            return parse(raw, term)
