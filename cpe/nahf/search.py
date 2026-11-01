"""NAHF Northeast Asian History Net search window.

The public site exposes a Solr-backed JSON endpoint for one collection item at
a time. The main search page returns only aggregate counts, so this adapter asks
the user to choose one collection and then sends one article query for the first
20 original-text rows. It does not paginate, fetch article pages, or store text.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BASE = "https://contents.nahf.or.kr/"
SEARCH_URL = BASE + "search/article/list.do"
_BASE_PARTS = urllib.parse.urlsplit(BASE)
MAX_ITEMS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SNIPPET = 600
AGENT = "classic-pattern-explorer (research tool; personal non-commercial use)"

# Collection ids from the public 사료라이브러리: classical books only.
ITEMS = (
    ("gd", "감계사등록"),
    ("gt", "고구려통사"),
    ("gb", "고조선단군부여자료집"),
    ("kn", "근세 한일관계 사료집"),
    ("dh", "동문휘고"),
    ("bh", "발해사자료집"),
    ("bd", "백두산정계비자료집"),
    ("sg", "사고전서 속의 한국고대사"),
    ("sdmg", "사대문궤"),
    ("sd", "사료로 보는 독도"),
    ("jt", "속자치통감장편"),
    ("ss", "신찬성씨록"),
    ("hw", "역주 한원"),
    ("ud", "울릉도·독도 일본사료집"),
    ("jj", "의성 경덕사 장시규·장한상 자료"),
    ("ns", "일본서기"),
    ("jb", "조선시대북방사자료집"),
    ("jc", "조청국경회담자료집"),
    ("jd", "중국정사동이전"),
    ("jo", "중국정사외국전"),
    ("cj", "청계중일한관계사료"),
)
ITEM_IDS = frozenset(item_id for item_id, _ in ITEMS)
_TAG = re.compile(r"<[^>]+>")
_MAX_QUERY = 100

Transport = Callable[[urllib.request.Request, float], bytes]


class NahfError(RuntimeError):
    """The NAHF search page could not be read or the request failed."""


class NahfCapReached(RuntimeError):
    """The session reached its request cap."""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow only same-host HTTPS redirects; refuse everything else.

    A redirect to another host or to plain HTTP would turn the one official
    query into a request the source address check never approved.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            parts = urllib.parse.urlsplit(newurl)
            port = parts.port
        except ValueError:
            return None
        if (
            parts.scheme != "https"
            or (parts.hostname or "").lower() != _BASE_PARTS.netloc
            or parts.username
            or parts.password
            or port not in (None, 443)
        ):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Item:
    level_id: str
    title: str
    snippet: str
    url: str
    item_id: str
    item_name: str


@dataclass(frozen=True)
class Fetched:
    total: int
    items: tuple[Item, ...]


def clean(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", value)).split())


def article_url(level_id: str) -> str:
    return BASE + "item/level.do?levelId=" + urllib.parse.quote(level_id, safe="")


def _text_field(row: dict[str, object], key: str) -> str:
    """A provider text field, or a layout error instead of a Python repr.

    The search payload is undocumented. Treating a list or object as a string
    would display ``['A', 'B']`` as if it were source text; fail closed instead.
    """
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NahfError("search_layout")
    return value


def parse(raw: bytes) -> Fetched:
    try:
        payload = json.loads(raw.decode("utf-8"))
        data = payload["data"]
        rows = data["list"]
        if not isinstance(data, dict) or not isinstance(rows, list):
            raise TypeError
        count = data.get("totalCnt", 0)
        if isinstance(count, str):
            count = count.strip()
            if not count.isdecimal() or len(count) > 1000:
                raise ValueError
            total = int(count)
        elif type(count) is int and count >= 0:
            total = count
        else:
            raise ValueError
    except (UnicodeDecodeError, KeyError, TypeError, ValueError):
        raise NahfError("search_layout") from None
    items: list[Item] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        level_id = _text_field(row, "level_id").strip()
        if not level_id:
            continue
        titles = [
            clean(_text_field(row, key))
            for key in ("main_title", "main_title1", "main_title2")
        ]
        title = " · ".join(part for part in titles if part) or level_id
        snippet = clean(_text_field(row, "level_text"))
        if not snippet:
            continue
        if len(snippet) > MAX_SNIPPET:
            snippet = snippet[: MAX_SNIPPET - 1] + "…"
        items.append(
            Item(
                level_id,
                title,
                snippet,
                article_url(level_id),
                _text_field(row, "item_id"),
                _text_field(row, "item_name"),
            )
        )
        if len(items) >= MAX_ITEMS:
            break
    if total and not items:
        raise NahfError("search_layout")
    return Fetched(total, tuple(items))


class NahfClient:
    """One-item, one-page search client with request spacing and a cap."""

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
                raise NahfCapReached
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
                    opener = urllib.request.build_opener(SafeRedirectHandler())
                    with opener.open(request, timeout=self.timeout) as answer:
                        data = answer.read(MAX_RESPONSE_BYTES + 1)
            except (OSError, ValueError) as error:
                raise NahfError("search_failed") from error
            if len(data) > MAX_RESPONSE_BYTES:
                raise NahfError("search_large")
            return data

    def search(
        self,
        item_id: str,
        term: str,
        checkpoint: Callable[[], None] | None = None,
    ) -> Fetched:
        if item_id not in ITEM_IDS:
            raise NahfError("search_item")
        if not term or len(term) > _MAX_QUERY or any(char in term for char in "\r\n"):
            raise NahfError("search_query")
        fields = {
            "srchMode": "article",
            "all": term,
            "q": "",
            "hq": "",
            "itemId": item_id,
            "itemSearch": "Y",
            "types": "d",
            "facetField": "item_category,item_category_sub,item_id",
            "hlField": "main_title,main_title1,main_title2,level_text",
            "hlPre": "<span class='f_srch'>",
            "hlPost": "</span>",
            "newSearch": "Y",
            "totalCnt": "0",
            "size": str(MAX_ITEMS),
            "page": "1",
            "sort": "level_id asc",
        }
        request = urllib.request.Request(
            SEARCH_URL,
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            headers={
                "Referer": BASE + "search/main/list.do",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
            method="POST",
        )
        return parse(self._send(request, checkpoint))
