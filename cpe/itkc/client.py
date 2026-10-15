"""ITKC Open API client with the probe safeguards of plan 7.1.

Real calls happen only when a caller passes a real transport; tests pass a fake
transport that replays recorded or synthetic XML (AGENTS.md "Research data").
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_URL = "http://db.itkc.or.kr/openapi/search"

# Field names are the API's own identifiers and must stay as the API spells them.
FIELD_TOTAL = "totalCount"
FIELD_DATA_ID = "자료ID"
FIELD_SEARCH = "검색필드"
FIELD_ARTICLE = "기사명"
FIELD_BOOK = "서명"
FIELD_VOLUME = "권차명"
FIELD_AUTHOR = "저자"
FIELD_DCI = "DCI_s"

Transport = Callable[[str, dict[str, Any], float], bytes]


class CallCapReached(RuntimeError):
    """The run reached its call cap; no further request is sent (plan 7.1)."""


class ApiError(RuntimeError):
    pass


class LocalStorageError(RuntimeError):
    """Writing the probe log or a raw response failed; never retried."""


MAX_PAGES = 5


@dataclass(frozen=True)
class Safeguards:
    """Plan 7.1 defaults: 1 s between calls, 200 calls per run, 5 pages per sample."""

    min_interval: float = 1.0
    max_calls: int = 200
    max_pages: int = 5
    retries: int = 2


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as response:
        data: bytes = response.read()
        return data


def parse_response(content: bytes) -> tuple[int, list[dict[str, str]]]:
    root = ET.fromstring(content)
    header = root.find("header")
    head = (
        {f.get("name", ""): _field_text(f) for f in header.iter("field")}
        if header is not None
        else {}
    )
    total = int(head.get(FIELD_TOTAL) or 0)
    docs = [
        {f.get("name", ""): _field_text(f) for f in doc.iter("field")}
        for doc in root.iter("doc")
    ]
    return total, docs


def _field_text(element: ET.Element) -> str:
    # Drops <em> markup and collapses whitespace, as the original CLI did.
    return " ".join("".join(element.itertext()).split())


@dataclass
class ITKCClient:
    """API client; every request passes the interval, the cap and the log.

    `log_path` is required because plan 7.1 asks for a record of every request.
    `iter_docs` is the paging used by the original search CLI and is bounded only
    by its `limit`; probe experiments use `count` and `sample`, whose page count
    never exceeds `MAX_PAGES` (5).
    """

    sec_id: str
    log_path: Path
    op_dir: str | None = None
    rows: int = 100
    timeout: float = 30.0
    safeguards: Safeguards = field(default_factory=Safeguards)
    transport: Transport = urllib_transport
    raw_dir: Path | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    stopped: bool = False
    _last_start: float | None = None
    _counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        guard = self.safeguards
        if not 1 <= guard.max_pages <= MAX_PAGES:
            raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
        if guard.max_calls < 1 or guard.min_interval < 0 or guard.retries < 0:
            raise ValueError("invalid safeguards")

    def params(self, keyword: str, start: int, rows: int) -> dict[str, Any]:
        params: dict[str, Any] = {"secId": self.sec_id, "start": start, "rows": rows}
        if self.op_dir:
            params["q"] = f"query†{keyword}$opDir†{self.op_dir}"
        else:
            params["keyword"] = keyword
        return params

    def fetch(
        self, keyword: str, start: int, rows: int
    ) -> tuple[int, list[dict[str, str]]]:
        return self.fetch_params(self.params(keyword, start, rows))

    def fetch_params(self, params: dict[str, Any]) -> tuple[int, list[dict[str, str]]]:
        """One request with explicit query parameters (probe experiments).

        Goes through the same retries, interval, cap and log as `fetch`.
        """
        params = dict(params)
        last_error: Exception | None = None
        for attempt in range(self.safeguards.retries + 1):
            if attempt:
                self.sleep(2.0 * attempt)
            try:
                content = self._call(params)
                total, docs = parse_response(content)
            except (OSError, ET.ParseError) as error:
                # Only network and XML failures are retried. Local write failures
                # raise LocalStorageError, which is not an OSError, so a response
                # already received is never requested again because of the disk.
                last_error = error
                self._log({"event": "error", "params": params, "error": str(error)})
                continue
            self._log(
                {
                    "event": "response",
                    "params": params,
                    "attempt": attempt,
                    "total": total,
                    "docs": len(docs),
                    "bytes": len(content),
                }
            )
            return total, docs
        raise ApiError(f"API call failed ({params!r}): {last_error}")

    def count(self, keyword: str) -> int:
        """`rows=1` hit count, cached per keyword (plan 7.1 default action)."""
        if keyword not in self._counts:
            self._counts[keyword] = self.fetch(keyword, 0, 1)[0]
        return self._counts[keyword]

    def iter_docs(self, keyword: str, limit: int) -> Iterator[dict[str, str]]:
        """Documents up to `limit`, as the original search CLI collected them."""
        start = 0
        while start < limit:
            total, docs = self.fetch(keyword, start, min(self.rows, limit - start))
            if not docs:
                return
            yield from docs
            start += len(docs)
            if start >= total:
                return

    def sample(self, keyword: str) -> list[dict[str, str]]:
        """At most `max_pages` (<= 5) pages: the explicit sampling action."""
        docs: list[dict[str, str]] = []
        for page in range(self.safeguards.max_pages):
            total, batch = self.fetch(keyword, page * self.rows, self.rows)
            docs.extend(batch)
            if not batch or len(docs) >= total:
                break
        return docs

    def _call(self, params: dict[str, Any]) -> bytes:
        if self.stopped:
            raise CallCapReached(f"call cap {self.safeguards.max_calls} reached")
        if self._last_start is not None:
            wait = self.safeguards.min_interval - (self.clock() - self._last_start)
            if wait > 0:
                self.sleep(wait)
        self.calls += 1
        self._log({"event": "request", "call": self.calls, "params": params})
        # The start time is taken after logging, right before the request, so the
        # interval holds between request starts even when logging is slow.
        self._last_start = self.clock()
        try:
            content = self.transport(API_URL, params, self.timeout)
        finally:
            # The run stops the moment the last allowed call is made, whether it
            # succeeds or fails; a later call is refused without being sent.
            if self.calls >= self.safeguards.max_calls and not self.stopped:
                self.stopped = True
                self._log({"event": "call_cap", "calls": self.calls})
        if self.raw_dir is not None:
            # Raw responses are third-party text: kept locally under the
            # gitignored experiments/itkc/raw/, never committed.
            try:
                self.raw_dir.mkdir(parents=True, exist_ok=True)
                (self.raw_dir / f"call-{self.calls:04d}.xml").write_bytes(content)
            except OSError as error:
                raise LocalStorageError(f"cannot store raw response: {error}") from None
        return content

    def _log(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with self.log_path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(line)
        except OSError as error:
            raise LocalStorageError(f"cannot write probe log: {error}") from None
