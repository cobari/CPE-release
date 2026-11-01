"""Selected Gutenberg ebooks and NDL OCR pages imported for local research.

Routes and evidence limits are recorded in docs/research/external-import-routes.md.
No site crawling, edition inference, OCR correction or sentence reconstruction occurs.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.http import content_length

AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
GUTENBERG = "https://gutenberg.pglaf.org/cache/epub/"
NDL = "https://lab.ndl.go.jp/dl/api/book/"
MAX_BYTES = 32 * 1024 * 1024
Transport = Callable[[str, float], bytes]


class ExternalError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class DownloadControl:
    """Cooperative cancellation and progress, independent of the request lock."""

    cancel: threading.Event = field(default_factory=threading.Event)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _progress: dict[str, Any] = field(
        default_factory=lambda: {
            "phase": "waiting",
            "received": 0,
            "total": 0,
            "requests": 0,
        }
    )

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise ExternalError("external_cancelled")

    def update(self, **values: Any) -> None:
        self.checkpoint()
        with self._lock:
            self._progress.update(values)

    def wait(self, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
        self.checkpoint()
        if sleep is time.sleep:
            self.cancel.wait(seconds)
        else:
            sleep(seconds)
        self.checkpoint()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self._progress, "cancelling": self.cancel.is_set()}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Fixed official download URLs must not redirect into website scraping.
        return None


def transport(
    url: str, timeout: float, control: DownloadControl | None = None
) -> bytes:
    if control:
        control.checkpoint()
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.build_opener(_NoRedirect()).open(
        request, timeout=timeout
    ) as response:
        return read_response(response, control)


def read_response(response: Any, control: DownloadControl | None = None) -> bytes:
    try:
        total = content_length(response.headers.get("Content-Length"), MAX_BYTES)
    except OverflowError:
        raise ExternalError("external_large") from None
    except ValueError:
        raise ExternalError("external_network") from None
    chunks = []
    received = 0
    while True:
        if control:
            control.checkpoint()
        chunk = response.read(min(64 * 1024, MAX_BYTES + 1 - received))
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
        if received > MAX_BYTES:
            raise ExternalError("external_large")
        if control:
            control.update(phase="downloading", received=received, total=total or 0)
    if total is not None and received != total:
        raise ExternalError("external_network")
    return b"".join(chunks)


@dataclass
class ExternalClient:
    """One serialized budget for both import sources; failures also spend a call."""

    transport: Transport = transport
    min_interval: float = 2.0
    max_calls: int = 200
    timeout: float = 30.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    streaming: Callable[[str, float, DownloadControl], bytes] | None = None

    def get(self, url: str, control: DownloadControl | None = None) -> bytes:
        if not url.startswith((GUTENBERG, NDL)):
            raise ExternalError("external_address")
        with self._lock:
            if control:
                control.checkpoint()
            if self.calls >= self.max_calls:
                raise ExternalError("external_cap")
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    if control:
                        control.update(phase="waiting")
                        control.wait(wait, self.sleep)
                    else:
                        self.sleep(wait)
            self._last = self.clock()
            self.calls += 1
            if control:
                control.update(
                    phase="requesting", received=0, total=0, requests=self.calls
                )
            try:
                if control and self.streaming:
                    data = self.streaming(url, self.timeout, control)
                elif control and self.transport is transport:
                    data = transport(url, self.timeout, control)
                else:
                    data = self.transport(url, self.timeout)
            except (OSError, http.client.HTTPException):
                if control:
                    control.checkpoint()
                raise ExternalError("external_network") from None
            if len(data) > MAX_BYTES:
                raise ExternalError("external_large")
            if control:
                control.checkpoint()
            return data


@dataclass(frozen=True)
class Unit:
    id: str
    text: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Download:
    provider: str
    id: str
    title: str
    edition: str
    units: tuple[Unit, ...]
    payloads: tuple[tuple[str, bytes], ...]
    details: dict[str, Any] = field(default_factory=dict)


def identifier(provider: str, address: str) -> str:
    value = address.strip()
    if re.fullmatch(r"[1-9][0-9]{0,11}", value):
        return value
    patterns = {
        "gutenberg": r"https?://(?:www\.)?gutenberg\.org/ebooks/([1-9][0-9]{0,11})/?",
        "ndl": r"https?://dl\.ndl\.go\.jp/(?:pid|info:ndljp/pid)/([1-9][0-9]{0,11})/?",
    }
    match = re.fullmatch(patterns.get(provider, r"(?!)"), value)
    if match:
        return match.group(1)
    raise ExternalError("external_address")


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ExternalError("external_format") from None
    if not isinstance(value, dict):
        raise ExternalError("external_format")
    return value


def _metadata(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def fetch(
    provider: str,
    address: str,
    client: ExternalClient,
    control: DownloadControl | None = None,
) -> Download:
    if provider not in {"gutenberg", "ndl"}:
        raise ExternalError("external_address")
    id_ = identifier(provider, address)
    if provider == "gutenberg":
        url = f"{GUTENBERG}{id_}/pg{id_}.txt"
        raw = client.get(url, control)
        if control:
            control.update(phase="parsing")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ExternalError("external_format") from None
        starts = list(
            re.finditer(
                r"(?m)^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK "
                r"[^\r\n]+\*\*\*\r?$",
                text,
            )
        )
        ends = list(
            re.finditer(
                r"(?m)^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK "
                r"[^\r\n]+\*\*\*\r?$",
                text,
            )
        )
        if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
            raise ExternalError("external_format")
        header = text[: starts[0].start()]
        if not re.search(r"(?mi)^Language:\s*Chinese\s*$", header):
            raise ExternalError("external_language")
        if re.search(r"(?mi)^Translator:", header):
            raise ExternalError("external_language")
        title_match = re.search(r"(?m)^Title:\s*([^\r\n]+)", header)
        body = text[starts[0].end() : ends[0].start()]
        # Production credits can occur inside the wrapper. Only the leading
        # explicitly labelled ASCII production paragraph is removed, never prose.
        body = re.sub(r"\A\s*Produced by [\x20-\x7e\r\n]+?(?:\r?\n){2}", "", body)
        if not body.strip():
            raise ExternalError("external_empty")
        return Download(
            provider,
            id_,
            title_match.group(1).strip() if title_match else id_,
            "Project Gutenberg ebook " + id_,
            (Unit(id_, body, f"https://www.gutenberg.org/ebooks/{id_}"),),
            ((url, raw),),
        )
    metadata_url = NDL + id_
    metadata_raw = client.get(metadata_url, control)
    metadata = _object(metadata_raw)
    if str(metadata.get("id", "")) != id_ or metadata.get("isClassic") is not True:
        raise ExternalError("external_language")
    url = NDL + "fulltext-json/" + id_
    raw = client.get(url, control)
    if control:
        control.update(phase="parsing")
    pages = _object(raw).get("list")
    if not isinstance(pages, list):
        raise ExternalError("external_format")
    units = []
    seen: set[str] = set()
    for page in pages:
        if control:
            control.checkpoint()
        if not isinstance(page, dict):
            raise ExternalError("external_format")
        number = page.get("page")
        page_id = page.get("id")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or str(page.get("book", "")) != id_
            or page_id != f"{id_}_{number}"
            or page_id in seen
        ):
            raise ExternalError("external_format")
        seen.add(page_id)
        content = page.get("contents")
        if not isinstance(content, str):
            raise ExternalError("external_format")
        if content.strip():
            units.append(
                Unit(page_id, content, f"https://dl.ndl.go.jp/pid/{id_}/1/{number}")
            )
    if not units:
        raise ExternalError("external_empty")
    units.sort(key=lambda unit: int(unit.id.rsplit("_", 1)[1]))
    title = " ".join(
        filter(None, (_metadata(metadata, "title"), _metadata(metadata, "volume")))
    )
    edition = " ".join(
        filter(
            None, (_metadata(metadata, "publisher"), _metadata(metadata, "published"))
        )
    )
    return Download(
        provider,
        id_,
        title or id_,
        edition,
        tuple(units),
        ((metadata_url, metadata_raw), (url, raw)),
    )


def save(
    download: Download, selected: list[str], folder: Path, edition: str, reviewed: bool
) -> dict[str, Any]:
    """Install missing units while preserving earlier text and provenance."""
    from cpe.local.external_store import save as save_parts

    return save_parts(download, selected, folder, edition, reviewed)
