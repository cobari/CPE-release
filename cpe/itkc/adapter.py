"""ITKC as a search source: query the Open API, match the returned excerpts locally.

ITKC answers a keyword query with document records whose search field holds excerpts
of about 150 characters around the hits, cut at both ends; several excerpts of one
document come joined by "... ..." (checked by hand on 2026-10-10, five requests).
No query expresses "FIRST, then SECOND in the same sentence", so the adapter asks for
documents containing both words and lets the matcher decide on each excerpt. Every
excerpt is a text with truncated edges: results never claim a whole sentence the
excerpt does not show (spec 3.3, 9). Nothing is cached; each record keeps its link.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.itkc.client import (
    FIELD_ARTICLE,
    FIELD_BOOK,
    FIELD_DATA_ID,
    FIELD_SEARCH,
    FIELD_VOLUME,
    ITKCClient,
)
from cpe.itkc.search_cli import make_link
from cpe.local.txt import LocalText

# Collections of classical originals: 고전원문 and 한국문집총간. BT_AA (translations)
# mixes Korean translation and original in one excerpt and is not searched.
COLLECTIONS = ("GO_AA", "MO_AA")
PAGE_ROWS = 100
# Excerpts of one record are joined by "... ..." (two runs of three dots).
_JOIN = re.compile(r"\s*\.{3}\s*\.{3}\s*")
# The hit highlighting arrives as escaped text, not as XML elements, so it survives
# parsing as literal "<em class=...>" characters; it is markup, never source text.
_HIGHLIGHT = re.compile(r"</?em\b[^>]*>")


@dataclass(frozen=True)
class Fetched:
    collection: str
    total: int
    texts: list[LocalText]
    # When a text dates from, as the source records it: a dict with `kind` and its
    # fields, worded by the page from its catalog. Kept beside the texts, keyed by
    # text_id, so the texts stay plain LocalText values.
    eras: dict[str, dict[str, Any]] = field(default_factory=dict)


def record_era(record: dict[str, str]) -> dict[str, Any] | None:
    """The author's life years, which ITKC gives per record (0 and 9999: unknown)."""

    def year(key: str, unknown: str) -> int | None:
        value = record.get(key, "").strip()
        return int(value) if value.isdigit() and value not in ("0", unknown) else None

    born, died = year("저자생년", "0"), year("저자몰년", "9999")
    if born is None and died is None:
        return None
    return {"kind": "author_life", "from": born, "to": died, "year": born or died}


def excerpts(record: dict[str, str]) -> list[str]:
    """The separate excerpts in a record's search field, markup already removed."""
    body = html.unescape(_HIGHLIGHT.sub("", record.get(FIELD_SEARCH, "")))
    return [part.strip() for part in _JOIN.split(body) if part.strip()]


def record_texts(record: dict[str, str], collection: str) -> list[LocalText]:
    data_id = record.get(FIELD_DATA_ID, "")
    title = " ".join(
        part
        for part in (
            record.get(FIELD_BOOK, ""),
            record.get(FIELD_VOLUME, ""),
            record.get(FIELD_ARTICLE, ""),
        )
        if part
    )
    return [
        LocalText(
            path=Path(),
            text_id=f"itkc:{data_id}#{index}",
            reference=excerpt,
            range="excerpt",
            boundary_start="truncated",
            boundary_end="truncated",
            punctuation="modern",
            # ITKC originals mark phrase after phrase with 。 (句讀), as in
            # 則扶桑一域。雖在絶海之表。而此奴肝膽。, so 。 is read as a reading mark by
            # default (researcher decision); only ！ and ？ end a sentence (spec 3.8).
            full_stop="reading",
            text_type="original",
            title=title or data_id,
            source=make_link(record),
            edition=collection,
            sha256="",
            bom=False,
        )
        for index, excerpt in enumerate(excerpts(record), start=1)
    ]


def fetch(
    keyword: str,
    client: ITKCClient,
    collections: Sequence[str] = COLLECTIONS,
    start: int = 0,
) -> list[Fetched]:
    """One page of records per collection, all through one client's safeguards.

    A single client for every collection keeps the 1 s interval and the call cap
    for the server as a whole; one client per collection would let two requests
    leave back to back.
    """
    found = []
    for collection in collections:
        params = {**client.params(keyword, start, PAGE_ROWS), "secId": collection}
        total, records = client.fetch_params(params)
        texts: list[LocalText] = []
        eras: dict[str, dict[str, Any]] = {}
        for record in records:
            era = record_era(record)
            for text in record_texts(record, collection):
                texts.append(text)
                if era is not None:
                    eras[text.text_id] = era
        found.append(Fetched(collection, total, texts, eras))
    return found


def make_client(
    log_path: Path, factory: Callable[..., ITKCClient] = ITKCClient
) -> ITKCClient:
    """The session's client: kept for the whole session so the cap counts all calls."""
    return factory(sec_id=COLLECTIONS[0], log_path=log_path, rows=PAGE_ROWS)
