"""CBETA through its CBData API: keyword-in-context excerpts matched locally.

`search/all_in_one` answers a phrase with juan-level results, each with KWIC excerpts
of `around` characters on both sides of every hit, the hit wrapped in <mark>
(https://cbdata.dila.edu.tw/stable/static_pages/search_all_in_one). Checked by hand on
2026-10-10: a single character answers in about 2 s, but NEAR between two single
characters did not answer within 120 s. So a search sends one phrase (the longer
element) and the matcher decides on each excerpt. `note=0` leaves interlinear notes
out, as the window never shows them. Excerpts have truncated edges.

CBETA texts are CC BY-NC-SA 4.0 (accepted by the researcher; docs/decisions.md
"Source databases are connected one at a time"); results show the work and a link to
CBETA Online, and nothing is cached. The server's certificate lacks a Subject Key
Identifier, which Python 3.13+ rejects in strict mode; the context keeps chain and
host-name verification and relaxes only that strict check.
"""

from __future__ import annotations

import html
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.local.txt import LocalText

API_URL = "https://cbdata.dila.edu.tw/stable/search/all_in_one"
READER_URL = "https://cbetaonline.dila.edu.tw/zh/"
PROJECT_URL = "https://github.com/cobari/CPE"
AGENT = f"classic-pattern-explorer (research tool; {PROJECT_URL})"
ROWS = 50
# Characters on each side of a hit: room for A (up to 20), the other element and B.
AROUND = 40
_MARK = re.compile(r"</?mark\b[^>]*>")

Transport = Callable[[str, dict[str, Any], float], bytes]


class CbetaCapReached(RuntimeError):
    """The session reached its request cap; nothing more is sent."""


class CbetaError(RuntimeError):
    pass


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        # CBData asks callers to send a Referer so it can see who uses the API.
        headers={"User-Agent": AGENT, "Referer": PROJECT_URL},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_context()) as reply:
        data: bytes = reply.read()
        return data


@dataclass
class CbetaClient:
    """Project safeguards, not published CBETA limits: 1 s between requests, 200 a
    session, no retry loop (a failure is reported to the researcher)."""

    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = 200
    timeout: float = 60.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)

    def search(self, phrase: str, start: int = 0) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise CbetaCapReached(f"request cap {self.max_calls} reached")
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.calls += 1
        params = {
            "q": phrase,
            "rows": ROWS,
            "start": start,
            "around": AROUND,
            "note": 0,
        }
        try:
            data = json.loads(self.transport(API_URL, params, self.timeout))
        except (OSError, ValueError) as error:
            raise CbetaError(str(error)) from None
        if not isinstance(data, dict):
            raise CbetaError("unexpected response")
        return data


def excerpt_text(kwic: str) -> str:
    return html.unescape(_MARK.sub("", kwic)).strip()


def reader_link(result: dict[str, Any]) -> str:
    work, juan = str(result.get("work", "")), result.get("juan")
    if not re.fullmatch(r"[A-Z]{1,2}[0-9]+[a-zA-Z]?", work) or not isinstance(
        juan, int
    ):
        return ""
    return f"{READER_URL}{work}_{juan:03d}"


def result_texts(result: dict[str, Any]) -> list[LocalText]:
    title = " ".join(
        str(part) for part in (result.get("title"), result.get("byline")) if part
    )
    juan = result.get("juan")
    if isinstance(juan, int):
        title = f"{title} {juan}"
    kwics = (result.get("kwics") or {}).get("results") or []
    texts = []
    for index, item in enumerate(kwics, start=1):
        text = excerpt_text(str(item.get("kwic", ""))) if isinstance(item, dict) else ""
        if not text:
            continue
        texts.append(
            LocalText(
                path=Path(),
                text_id=f"cbeta:{result.get('file', '')}:{juan}#{index}",
                reference=text,
                range="excerpt",
                boundary_start="truncated",
                boundary_end="truncated",
                punctuation="modern",
                full_stop="sentence",
                text_type="original",
                title=title or str(result.get("work", "")),
                source=reader_link(result),
                edition=f"CBETA {result.get('file', '')} {item.get('lb', '')}".strip(),
                sha256="",
                bom=False,
            )
        )
    return texts


@dataclass(frozen=True)
class Fetched:
    total: int
    juans: int
    texts: list[LocalText]
    # When a text dates from, as the source records it: a dict with `kind` and its
    # fields, worded by the page from its catalog. Kept beside the texts, keyed by
    # text_id, so the texts stay plain LocalText values.
    eras: dict[str, dict[str, Any]] = field(default_factory=dict)


def result_era(result: dict[str, Any]) -> dict[str, Any] | None:
    """The dynasty and the years CBETA records for the work's creator."""
    dynasty = result.get("time_dynasty")
    start, end = result.get("time_from"), result.get("time_to")
    start = start if isinstance(start, int) else None
    end = end if isinstance(end, int) else None
    if not dynasty and start is None:
        return None
    return {
        "kind": "dynasty",
        "dynasty": dynasty if isinstance(dynasty, str) else "",
        "from": start,
        "to": end,
        "year": start,
    }


def fetch(phrase: str, client: CbetaClient, start: int = 0) -> Fetched:
    data = client.search(phrase, start)
    results = [item for item in data.get("results") or [] if isinstance(item, dict)]
    texts: list[LocalText] = []
    eras: dict[str, dict[str, Any]] = {}
    for item in results:
        era = result_era(item)
        for text in result_texts(item):
            texts.append(text)
            if era is not None:
                eras[text.text_id] = era
    total = data.get("num_found")
    return Fetched(total if isinstance(total, int) else 0, len(results), texts, eras)
