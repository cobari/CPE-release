"""Cyber Seodang (Dongyang Classics DB, 전통문화연구회) search adapter.

Connects to the official Open API at https://db.cyberseodang.or.kr/front/openapi/sabu.do.
The API accepts query, page, and ipp parameters, returning an XML document with
record metadata: <id>, <title>, an undocumented <content> element (which may contain
author/work metadata or article titles with hyeonto, not guaranteed full body text
or sentence-bounded excerpts), <url>, and <page>.

Results expose the returned title, description, and validated links to the original
articles on the Cyber Seodang portal. When user search conditions are found within
the returned text, matches are highlighted; records whose conditions were not verified
in the returned text are preserved as unverified native candidates rather than being
discarded or promoted to grammatical matches.

Session safeguards enforce a 1 s interval between requests and a 200-call session cap,
with serialized thread locking. No fetched text is persisted.
"""

from __future__ import annotations

import html
import http.client
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

API_URL = "https://db.cyberseodang.or.kr/front/openapi/sabu.do"
PAGE_SIZE = 50
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
_TAGS = re.compile(r"<[^>]+>")

Transport = Callable[[str, dict[str, Any], float], bytes]


class CyberseodangCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


class CyberseodangError(RuntimeError):
    pass


def clean_text(raw: str | None) -> str:
    """Strip XML/HTML tags and unescape entities, normalizing whitespace."""
    if not raw:
        return ""
    stripped = _TAGS.sub("", raw)
    unescaped = html.unescape(stripped)
    return " ".join(unescaped.split())


def safe_link(raw_url: str | None) -> str:
    """Validate URL to ensure it points to exact db.cyberseodang.or.kr via HTTP(S)."""
    if not raw_url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw_url.strip())
        if parsed.scheme not in ("https", "http"):
            return ""
        if (
            parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
        ):
            return ""
        host = (parsed.hostname or "").lower()
        if host != "db.cyberseodang.or.kr":
            return ""
        port = parsed.port
    except (ValueError, AttributeError):
        return ""
    valid_ports = (None, 443) if parsed.scheme == "https" else (None, 80)
    if port not in valid_ports:
        return ""
    return raw_url.strip()


def escape_markup(text: str) -> str:
    """Escape markup characters that have special meaning in the GUI card display."""
    return (
        text.replace("\\", "\\\\")
        .replace("【", "\\【")
        .replace("】", "\\】")
        .replace("〖", "\\〖")
        .replace("〗", "\\〗")
    )


def mark_occurrences(text: str, terms: Sequence[str]) -> str:
    """Mark occurrences of search terms in text with 【...】 for card display."""
    if not text or not terms:
        return escape_markup(text)
    escaped = escape_markup(text)
    clean_terms = [t.strip() for t in terms if t.strip()]
    if not clean_terms:
        return escaped
    # Sort terms by length descending to match longer phrases first
    clean_terms.sort(key=len, reverse=True)
    pattern = "|".join(re.escape(escape_markup(t)) for t in clean_terms)
    try:
        regex = re.compile(pattern)
    except re.error:
        return escaped

    parts: list[str] = []
    last_end = 0
    for match in regex.finditer(escaped):
        start, end = match.span()
        parts.append(escaped[last_end:start])
        parts.append(f"【{match.group(0)}】")
        last_end = end
    parts.append(escaped[last_end:])
    return "".join(parts)


@dataclass(frozen=True)
class CyberseodangItem:
    item_id: str
    title: str
    content: str
    url: str
    page: int


@dataclass(frozen=True)
class Fetched:
    total: int
    items: list[CyberseodangItem]


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Ensure HTTP redirects stay within the official HTTPS endpoint."""

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
            scheme = urllib.parse.urlsplit(newurl).scheme
        except ValueError:
            raise CyberseodangError(f"malformed redirect URL: {newurl}") from None
        if scheme != "https" or safe_link(newurl) != newurl:
            raise CyberseodangError(f"rejected unsafe redirect: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    if not url.startswith("https://db.cyberseodang.or.kr/"):
        raise CyberseodangError(
            "HTTPS to db.cyberseodang.or.kr is required for Cyber Seodang API"
        )
    query_string = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query_string}", headers={"User-Agent": AGENT}
    )
    opener = urllib.request.build_opener(SafeRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        data: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise CyberseodangError("response exceeded size limit")
        return data


def parse_response(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CyberseodangError("response exceeded size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CyberseodangError(f"malformed XML UTF-8 encoding: {error}") from None

    if "\x00" in text:
        raise CyberseodangError("malformed XML: NUL bytes are forbidden")

    upper_text = text.upper()
    if "<!DOCTYPE" in upper_text or "<!ENTITY" in upper_text:
        raise CyberseodangError(
            "DOCTYPE and entity declarations are forbidden in API response"
        )
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError) as error:
        raise CyberseodangError(f"malformed XML: {error}") from None
    if root.tag != "root":
        raise CyberseodangError(f"unexpected root element: {root.tag}")

    info_el = root.find("info")
    if info_el is None:
        raise CyberseodangError("missing <info> element in API response")

    total_el = info_el.find("total")
    if total_el is None:
        raise CyberseodangError("missing <total> element in API response")
    total_text = (total_el.text or "").strip()
    if not re.fullmatch(r"[0-9]{1,9}", total_text):
        raise CyberseodangError(f"invalid total count: {total_text!r}")
    total = int(total_text)

    items_el = root.find("items")
    items: list[CyberseodangItem] = []
    seen_keys: set[tuple[str, str]] = set()
    if items_el is not None:
        for item_el in items_el.findall("item"):
            if len(items) >= PAGE_SIZE:
                break
            try:
                raw_id = clean_text(item_el.findtext("id"))
                raw_title = clean_text(item_el.findtext("title"))
                raw_content = clean_text(item_el.findtext("content"))
                raw_url = (item_el.findtext("url") or "").strip()
                page_text = (item_el.findtext("page") or "").strip()
                page_num = (
                    int(page_text) if re.fullmatch(r"[0-9]{1,9}", page_text) else 1
                )
            except (ValueError, AttributeError):
                continue

            if not raw_id and not raw_title and not raw_content:
                continue

            item_key = (raw_id, raw_url)
            if item_key in seen_keys:
                continue
            seen_keys.add(item_key)

            items.append(
                CyberseodangItem(
                    item_id=raw_id,
                    title=raw_title,
                    content=raw_content,
                    url=safe_link(raw_url),
                    page=page_num,
                )
            )

    return {"total": total, "items": items}


@dataclass
class CyberseodangClient:
    """Safeguards: serial lock, 1 s between requests, 200 a session, no retry."""

    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = 200
    timeout: float = 30.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def search(
        self,
        phrase: str,
        page: int = 1,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if checkpoint:
                checkpoint()
            if self.calls >= self.max_calls:
                raise CyberseodangCapReached(f"request cap {self.max_calls} reached")
            now = self.clock()
            if self._last is not None:
                wait = self.min_interval - (now - self._last)
                if wait > 0:
                    self.sleep(wait)
            if checkpoint:
                checkpoint()
            self._last = self.clock()
            self.calls += 1
            params = {
                "query": phrase,
                "page": page,
                "ipp": PAGE_SIZE,
            }
            try:
                raw = self.transport(API_URL, params, self.timeout)
            except (OSError, http.client.HTTPException, ValueError) as error:
                raise CyberseodangError(str(error)) from None

            return parse_response(raw)


def fetch(
    phrase: str,
    client: CyberseodangClient,
    page: int = 1,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> Fetched:
    data = client.search(phrase, page=page, checkpoint=checkpoint)
    total = data.get("total", 0)
    items = data.get("items", [])
    return Fetched(total=total, items=items)
