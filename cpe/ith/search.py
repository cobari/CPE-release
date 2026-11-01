"""Academia Sinica ITH Taiwan Full-text Database as a one-page search window.

The provider has no documented JSON API. Its public form is HTML/ASP.NET: one
query page, one POST, then the first result page. This module sends that one
query, reads only the first result page, and returns titles, short excerpts and
provider links. It does not paginate, fetch article pages, or store source text.
"""

from __future__ import annotations

import html
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

BASE = "https://taicool.ith.sinica.edu.tw/"
QUERY_URL = BASE + "query-1.html"
RESULT_POST_URL = BASE + "search-result.html"
MAX_ITEMS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

Transport = Callable[[urllib.request.Request, float], bytes]
_TAG = re.compile(r"<[^>]+>")
_REDIRECT = re.compile(r"window\.location\.href='([^']+)'")
_HIDDEN = re.compile(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"[^>]*>')
_BASE_PARTS = urllib.parse.urlsplit(BASE)
# HTML void elements have no closing tag. Pushing them onto the stack makes a
# following </span> close the <br> and moves later result text into the wrong
# result. Keep them as leaf nodes instead.
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


def _article_url(href: str) -> str:
    """Only the provider's own /search-art- article links."""
    if not href:
        return ""
    absolute = urllib.parse.urljoin(BASE, href)
    parts = urllib.parse.urlsplit(absolute)
    if (
        parts.scheme != "https"
        or parts.netloc != _BASE_PARTS.netloc
        or not parts.path.startswith("/search-art-")
    ):
        return ""
    return absolute


class IthError(RuntimeError):
    """The search page could not be read or the request failed."""


class IthCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


@dataclass(frozen=True)
class Item:
    title: str
    snippet: str
    url: str


@dataclass(frozen=True)
class Fetched:
    items: tuple[Item, ...]


def _clean(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", value)).split())


class _Node:
    __slots__ = ("tag", "attrs", "parts")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parts: list[str | _Node] = []

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        return "".join(
            part if isinstance(part, str) else part.text() for part in self.parts
        )

    def walk(self) -> Iterator[_Node]:
        yield self
        for part in self.parts:
            if isinstance(part, _Node):
                yield from part.walk()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].parts.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        # Match the closing tag instead of popping the current top blindly. A
        # mismatched close must not attach a following result to the previous one.
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].parts.append(data)


def parse(raw: bytes | str) -> Fetched:
    """First-page result items, with title, short excerpt and provider link."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    builder = _Builder()
    builder.feed(text)
    items: list[Item] = []
    for node in builder.root.walk():
        if node.tag != "div" or "resultcontent" not in node.classes():
            continue
        titles: list[str] = []
        link = ""
        snippets: list[str] = []
        fulltext_snippets: list[str] = []
        for child in node.walk():
            if child.tag == "span" and "title" in child.classes():
                value = _clean(child.text())
                if value and value not in titles:
                    titles.append(value)
            elif child.tag == "a":
                href = child.attrs.get("href", "")
                if not link:
                    link = _article_url(href)
            if child.tag == "span" and "content" in child.classes():
                value = _clean(child.text())
                if value:
                    snippets.append(value)
                    if "fulltext" in child.classes():
                        fulltext_snippets.append(value)
        if not link or not titles:
            continue
        snippet = max(fulltext_snippets or snippets, key=len, default="")
        if not snippet:
            continue
        if len(snippet) > MAX_SNIPPET:
            snippet = snippet[: MAX_SNIPPET - 1] + "…"
        items.append(Item(" · ".join(titles[:3]), snippet, link))
        if len(items) >= MAX_ITEMS:
            break
    return Fetched(tuple(items))


def _context() -> ssl.SSLContext:
    """Verify the provider chain but not OpenSSL's optional X.509 strictness.

    The ITH certificate omits Subject Key Identifier. OpenSSL's strict mode
    rejects it, while browsers accept the same chain. CA and hostname checks
    remain enabled; only VERIFY_X509_STRICT is cleared.
    """
    context = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        context.verify_flags &= ~strict
    return context


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=_context()),
    )


class IthClient:
    """One-query client with the project's request spacing and caps."""

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
        self._opener = _opener() if transport is None else None

    def _send(
        self, request: urllib.request.Request, checkpoint: Callable[[], None] | None
    ) -> bytes:
        with self._lock:
            if self.calls >= self.max_calls:
                raise IthCapReached
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
                    assert self._opener is not None
                    with self._opener.open(request, timeout=self.timeout) as answer:
                        data = answer.read(MAX_RESPONSE_BYTES + 1)
            except (OSError, ValueError) as error:
                raise IthError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise IthError("search_large")
            return data

    def search(
        self, term: str, checkpoint: Callable[[], None] | None = None
    ) -> Fetched:
        """One user query; only the first result page is read."""
        if not term or len(term) > 100 or any(char in term for char in "\r\n"):
            raise IthError("search_query")
        query = self._send(
            urllib.request.Request(QUERY_URL, headers={"Accept": "text/html"}),
            checkpoint,
        )
        hidden = {
            html.unescape(name): html.unescape(value)
            for name, value in _HIDDEN.findall(query.decode("utf-8", errors="replace"))
        }
        fields = {
            "IsQuery": "1",
            "HIDMode": "adv",
            "HIDKey": term,
            "HIDField": "CFullContent",
            "HIDLogic": "",
            "HIDSearchType": "",
            "HIDSearchRange": "",
            "HIDPSDate": "",
            "HIDPEDate": "",
            "TXTKeyword1": term,
            "DDLField1": "CFullContent",
            "TXTKeyword2": "",
            "DDLField2": "All",
            "DDLLogic2": "AND",
        }
        for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            if key in hidden:
                fields[key] = hidden[key]
        answer = self._send(
            urllib.request.Request(
                RESULT_POST_URL,
                data=urllib.parse.urlencode(fields).encode("utf-8"),
                headers={
                    "Referer": QUERY_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "text/html",
                },
                method="POST",
            ),
            checkpoint,
        )
        page = answer.decode("utf-8", errors="replace")
        redirect = _REDIRECT.search(page)
        if redirect:
            target = urllib.parse.urljoin(BASE, redirect.group(1))
            if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(
                BASE
            ).netloc or not urllib.parse.urlsplit(target).path.startswith(
                "/search-result-"
            ):
                raise IthError("search_layout")
            page = self._send(
                urllib.request.Request(target, headers={"Accept": "text/html"}),
                checkpoint,
            ).decode("utf-8", errors="replace")
        if "resultcontent" not in page:
            raise IthError("search_layout")
        return parse(page)
