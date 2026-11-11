"""Read CanD headings as references; discard poems, readings and commentary.

The provider edits characters and punctuation and restricts copying. This route
retains bibliographic headings only. See docs/research/cand-search-window.md.
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

BASE = "https://tool.hankeidou.jp/"
SEARCH_URL = BASE + "cand/kanshi_search2.php"
DISPLAY_ITEMS = 20
MAX_BYTES = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (personal non-commercial research)"
EDITIONS = {
    "聯珠詩格": "renju-shikaku-fulltext/",
    "三體詩": "santaishi_fulltext/",
    "唐詩選": "toushisen_fulltext/",
    "和漢名詩類選評釈": "wakan-meishi-ruisen-fulltext/",
    "今體宋詩選": "soushisen-fulltext/",
    "皇朝分類名家絶句": "kouchou-bunrui-fulltext/",
    "絶句略解": "zekkuryakukai-fulltext/",
    "古文眞寶前集": "kobunshimpo-fulltext/",
    "山陽詩鈔": "sanyoshisho-fulltext/",
    "杜少陵詩集": "toshouryoushishu-fulltext/",
}
Transport = Callable[[urllib.request.Request, float], bytes]


class CandError(RuntimeError):
    """The request or native result page was not usable."""


class CandCapReached(RuntimeError):
    """The session request cap was reached; no request was sent."""


@dataclass(frozen=True)
class Item:
    reference: str
    collection: str
    url: str


@dataclass(frozen=True)
class Fetched:
    items: tuple[Item, ...]
    total: int


class _Results(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading: str | None = None
        self.parts: list[str] = []
        self.section = ""
        self.conditions: list[str] = []
        self.count_text: list[str] = []
        self.count_done = False
        self.title = ""
        self.items: list[Item] = []
        self.seen = 0
        self.closed = False
        self.sections: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h1", "h2", "h3"):
            if self.heading or self.closed:
                raise CandError("search_layout")
            self.heading, self.parts = tag, []
        elif self.heading and tag != "span":
            raise CandError("search_layout")
        elif tag == "br" and self.section == "検索条件":
            self.conditions.append("\n")
        elif tag == "br" and self.section == "検索結果":
            if "".join(self.count_text).strip():
                self.count_done = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3"):
            if tag != self.heading:
                raise CandError("search_layout")
            value = "".join(self.parts).strip()
            self.heading = None
            if tag == "h1":
                if self.title:
                    raise CandError("search_layout")
                self.title = value
            elif tag == "h2":
                self.section = value
                self.sections.append(value)
            else:
                if self.section != "検索結果":
                    raise CandError("search_layout")
                collection = next(
                    (name for name in EDITIONS if value.startswith(name + " ")),
                    None,
                )
                if collection is None or "《" not in value or "》" not in value:
                    raise CandError("search_layout")
                self.seen += 1
                if len(self.items) < DISPLAY_ITEMS:
                    self.items.append(
                        Item(value, collection, BASE + EDITIONS[collection])
                    )
        if tag == "body":
            self.closed = True

    def handle_data(self, data: str) -> None:
        if self.heading:
            self.parts.append(data)
        elif self.section == "検索条件":
            self.conditions.append(data)
        elif self.section == "検索結果" and not self.count_done and self.seen == 0:
            self.count_text.append(data)


def parse(raw: bytes, term: str) -> Fetched:
    page = _Results()
    try:
        page.feed(raw.decode("utf-8"))
    except UnicodeError as error:
        raise CandError("search_layout") from error
    conditions = {line.strip() for line in "".join(page.conditions).splitlines()}
    count = re.fullmatch(r"\s*ヒット件数：([0-9]{1,7})件\s*", "".join(page.count_text))
    if (
        page.title != "漢詩データベース CanD 検索結果"
        or page.sections != ["検索条件", "検索結果"]
        or page.heading
        or not page.closed
        or not {
            "検索対象：本文",
            "検索方式：部分一致",
            "拡張検索：OFF",
            "検索語句：" + term,
        }.issubset(conditions)
        or count is None
        or int(count[1]) != page.seen
    ):
        raise CandError("search_layout")
    return Fetched(tuple(page.items), page.seen)


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


class CandClient:
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
            or len(term) > 10
            or any(
                char.isspace() or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                for char in term
            )
            or any(char in term for char in '+-|{}()@"?*/\\[]<>=!~^:$&%_\x7f')
        ):
            raise CandError("search_query")
        fields = {
            "search_target": "本文",
            "search_method": "部分一致",
            "search_option": "OFF",
            "search_word": term,
        }
        request = urllib.request.Request(
            SEARCH_URL,
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            headers={
                "User-Agent": AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with self._lock:
            if self.calls >= self.max_calls:
                raise CandCapReached
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
                raise CandError("search_failed") from error
            if len(raw) > MAX_BYTES:
                raise CandError("search_large")
            if checkpoint:
                checkpoint()
            return parse(raw, term)
