"""Read THDL's Old Deeds collection through its native anonymous search form.

Only the first ten results of one user query are read. Returned excerpts can
contain separated contexts and editorial material, so they remain references,
never sentence-level or grammatical matches. See docs/research/thdl-search-window.md.
"""

from __future__ import annotations

import http.cookiejar
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser

from cpe.http import content_length

SEARCH_URL = "https://thdl.ntu.edu.tw/THDL/RetrieveDocs.php"
CORPUS = "OldDeeds"
PAGE_ITEMS = 10
MAX_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
AGENT = "classic-pattern-explorer (personal non-commercial research)"
Transport = Callable[[urllib.request.Request, float], bytes]
_FILE = re.compile(r"[A-Za-z0-9_.-]{1,200}\Z")
_TOTAL = re.compile(r"找到筆數\s*:\s*([0-9,]+)")
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class ThdlError(RuntimeError):
    """The native search failed or its result layout was not recognized."""


class ThdlCapReached(RuntimeError):
    """No further provider request is allowed in this client session."""


@dataclass(frozen=True)
class Item:
    file_id: str
    title: str
    snippet: str
    reference: str
    url: str


@dataclass(frozen=True)
class Fetched:
    total: int
    items: tuple[Item, ...]


class _Element:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parts: list[str | _Element] = []

    def walk(self) -> Iterator[_Element]:
        yield self
        for part in self.parts:
            if isinstance(part, _Element):
                yield from part.walk()

    def text(self) -> str:
        # The provider's single-result HTML can leave its Content cell open.
        # A following row is metadata or a related-document list, never body.
        text = []
        for part in self.parts:
            if isinstance(part, str):
                text.append(part)
            elif part.tag == "br":
                text.append("\n")
            elif part.tag == "img":
                text.append("□")
            elif part.tag not in ("tr", "script", "style"):
                text.append(part.text())
        return "".join(text)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Element(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].parts.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.stack[-1].parts.append(data)


def _record_url(file_id: str) -> str:
    # The file identifier is returned in UserAssess[], and the native title
    # links select that identifier in braces. Never infer a file from its title.
    return (
        SEARCH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "username": "*anonymous*",
                "in_corpus": CORPUS,
                "text_query": "{" + file_id + "}",
                "single_doc": "1",
                "viewing_option": "Extract",
                "no_update_query_history": "0",
            }
        )
    )


def parse(raw: bytes, term: str | None = None) -> Fetched:
    try:
        html = raw.decode("utf-8")
    except UnicodeError as error:
        raise ThdlError("search_layout") from error
    page = _Page()
    page.feed(html)
    nodes = list(page.root.walk())
    if term is not None:
        echoed = {
            node.attrs.get("value", "")
            for node in nodes
            if node.tag == "input" and node.attrs.get("id") == "ss_text_query"
        }
        if echoed != {term}:
            raise ThdlError("search_layout")
    if not any(
        node.tag == "input"
        and node.attrs.get("name") == "in_corpus"
        and node.attrs.get("value") == CORPUS
        for node in nodes
    ):
        raise ThdlError("search_layout")
    items: list[Item] = []
    for record in nodes:
        if record.tag != "table" or not re.fullmatch(
            r"doc1_x_[0-9]+", record.attrs.get("id", "")
        ):
            continue
        children = list(record.walk())
        file_ids = [
            node.attrs.get("value", "")
            for node in children
            if node.tag == "input" and node.attrs.get("name") == "UserAssess[]"
        ]
        if len(file_ids) != 1 or not _FILE.fullmatch(file_ids[0]):
            raise ThdlError("search_layout")
        title = next(
            (
                node.text().strip()
                for node in children
                if node.tag == "a" and node.attrs.get("class") == "ViewDocExtractFormat"
            ),
            "",
        )
        if not title:
            title = next(
                (
                    node.text().strip()
                    for node in children
                    if node.tag == "td"
                    and node.attrs.get("class") == "ViewDocSourceFormat"
                    and "style" not in node.attrs
                    and "width" not in node.attrs
                ),
                "",
            )
        snippets = [
            node.text().strip()
            for node in children
            if node.tag == "td" and node.attrs.get("class") == "Content"
        ]
        references = [
            node.text().strip()
            for node in children
            if node.tag == "td"
            and node.attrs.get("class") == "Aux"
            and node.attrs.get("align") == "right"
        ]
        if not title or len(snippets) != 1:
            raise ThdlError("search_layout")
        items.append(
            Item(
                file_ids[0],
                title,
                snippets[0][:MAX_SNIPPET],
                " · ".join(filter(None, references)),
                _record_url(file_ids[0]),
            )
        )
        if len(items) >= PAGE_ITEMS:
            break
    # Totals are taken only from the provider's paging controls, not from a
    # number embedded in a returned historical document or facet count.
    counts = set()
    for node in nodes:
        if node.attrs.get("class") == "PagingRowHead":
            for match in _TOTAL.finditer(node.text()):
                number = match.group(1).replace(",", "")
                if len(number) > 12:
                    raise ThdlError("search_layout")
                counts.add(int(number))
    if len(counts) != 1:
        raise ThdlError("search_layout")
    total = counts.pop()
    if len(items) != min(total, PAGE_ITEMS):
        raise ThdlError("search_layout")
    return Fetched(total, tuple(items))


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
        # An unexpected redirect must not add uncounted requests or move the
        # anonymous session to a login service or another host.
        return None


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    # THDL's chain omits Subject Key Identifier, like the existing ITH route.
    # Match Python 3.11's X.509 policy while retaining CA and hostname checks.
    context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=context),
    )


class ThdlClient:
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
        self._opener = _opener() if transport is None else None

    def _send(
        self, request: urllib.request.Request, checkpoint: Callable[[], None] | None
    ) -> bytes:
        if self.calls >= self.max_calls:
            raise ThdlCapReached
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        if checkpoint:
            checkpoint()
        self._last = self.clock()
        self.calls += 1
        request.add_header("User-Agent", AGENT)
        try:
            if self.transport is not None:
                data = self.transport(request, self.timeout)
            else:
                assert self._opener is not None
                with self._opener.open(request, timeout=self.timeout) as response:
                    content_length(response.headers.get("Content-Length"), MAX_BYTES)
                    data = response.read(MAX_BYTES + 1)
        except (OSError, ValueError, OverflowError) as error:
            raise ThdlError("search_failed") from error
        if len(data) > MAX_BYTES:
            raise ThdlError("search_large")
        return data

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
            or any(char in term for char in '+-|{}()@"?*/\\[]<>=!~^:$&\x7f')
        ):
            raise ThdlError("search_query")
        # Anonymous entry can contain the account's previous results. Only the
        # subsequent POST response, with its query echo checked, is a result.
        with self._lock:
            if self.max_calls - self.calls < 2:
                raise ThdlCapReached
            self._send(
                urllib.request.Request(SEARCH_URL + "?username=*anonymous*"), checkpoint
            )
            fields = {
                "in_corpus": CORPUS,
                "text_query": term,
                "GeneralSearchQuery": "送出",
                "viewing_option": "Extract",
                "spotlighting_option": "PlainList",
                "cur_page": "1",
                "page_size": str(PAGE_ITEMS),
                "no_update_query_history": "0",
            }
            data = self._send(
                urllib.request.Request(
                    SEARCH_URL,
                    data=urllib.parse.urlencode(fields).encode("utf-8"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                ),
                checkpoint,
            )
            if checkpoint:
                checkpoint()
            return parse(data, term)
