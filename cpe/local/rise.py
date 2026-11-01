"""Selected MPIWG rare-gazetteer pages from the official SHINE API.

Route, terms and limits: docs/research/mpiwg-import.md. Only the verified MPIWG
collection is addressed, one content page at a time. Book metadata never enters
the body text, and markup the importer does not understand rejects its whole
content unit instead of being flattened or guessed.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cpe.http import content_length
from cpe.local.external import Download, DownloadControl, ExternalError, Unit

BASE = "https://rise.mpiwg-berlin.mpg.de/api"
COLLECTION = "efda876c-2191-4b43-8ce7-fd1223562fdf"
PER_PAGE = 50
TIMEOUT = 35.0
MIN_INTERVAL = 1.0
MAX_CALLS = 200
MAX_JSON = 2 * 1024 * 1024
AGENT = "classic-pattern-explorer (research tool; selected import)"
PB = "<pb />"
UUID = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
ADDRESS = re.compile(rf"({UUID})/({UUID})/([1-9][0-9]{{0,3}})")
ALLOWED = (f"{BASE}/collections", f"{BASE}/resources", f"{BASE}/sections")
TAG = re.compile(r"<[^>]*>")
SEPARATOR = re.compile(r"<pb />|[ \t\r\n]+")


@dataclass(frozen=True)
class Response:
    body: bytes
    headers: dict[str, str]


Transport = Callable[[str, float], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # The fixed catalog and body endpoints must not redirect elsewhere.
        return None


def urllib_transport(url: str, timeout: float) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.build_opener(_NoRedirect()).open(
        request, timeout=timeout
    ) as response:
        try:
            total = content_length(response.headers.get("Content-Length"), MAX_JSON)
        except OverflowError:
            raise ExternalError("external_large") from None
        except ValueError:
            raise ExternalError("external_network") from None
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = response.read(min(64 * 1024, MAX_JSON + 1 - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > MAX_JSON:
                raise ExternalError("external_large")
        if total is not None and received != total:
            raise ExternalError("external_network")
        headers = {
            str(key).lower(): str(value) for key, value in response.headers.items()
        }
        return Response(b"".join(chunks), headers)


@dataclass
class RiseClient:
    """One serialized budget for every SHINE call in this session."""

    transport: Transport = urllib_transport
    min_interval: float = MIN_INTERVAL
    max_calls: int = MAX_CALLS
    timeout: float = TIMEOUT
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def get(self, url: str, control: DownloadControl | None = None) -> Response:
        if not url.startswith(ALLOWED):
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
                answer = self.transport(url, self.timeout)
            except (OSError, http.client.HTTPException):
                if control:
                    control.checkpoint()
                raise ExternalError("external_network") from None
            if len(answer.body) > MAX_JSON:
                raise ExternalError("external_large")
            if control:
                control.checkpoint()
                control.update(
                    phase="parsing",
                    received=len(answer.body),
                    total=len(answer.body),
                )
            headers = {key.lower(): value for key, value in answer.headers.items()}
            return Response(answer.body, headers)


def _json(answer: Response) -> Any:
    try:
        return json.loads(answer.body)
    except (ValueError, UnicodeError):
        raise ExternalError("external_format") from None


def _object(answer: Response) -> dict[str, Any]:
    value = _json(answer)
    if not isinstance(value, dict):
        raise ExternalError("external_format")
    return value


def _list(answer: Response) -> list[Any]:
    value = _json(answer)
    if not isinstance(value, list):
        raise ExternalError("external_format")
    return value


def _header_int(headers: dict[str, str], name: str) -> int:
    value = headers.get(name, "")
    if not re.fullmatch(r"[0-9]+", value):
        raise ExternalError("external_format")
    return int(value)


def _page_window(page: int, total: int) -> int:
    return max(0, min(PER_PAGE, total - (page - 1) * PER_PAGE))


def _valid_page(page: Any) -> int:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ExternalError("external_address")
    return page


def _valid_uuid(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(UUID, value):
        raise ExternalError("external_address")
    return value


def books(
    client: RiseClient, page: int = 1, control: DownloadControl | None = None
) -> dict[str, Any]:
    """One page of the verified MPIWG collection's book catalog."""
    page = _valid_page(page)
    url = f"{BASE}/collections/{COLLECTION}/resources?page={page}&per_page={PER_PAGE}"
    answer = client.get(url, control)
    items = _list(answer)
    total = _header_int(answer.headers, "x-total")
    if (
        _header_int(answer.headers, "x-page") != page
        or _header_int(answer.headers, "x-per-page") != PER_PAGE
        or len(items) != _page_window(page, total)
    ):
        raise ExternalError("external_format")
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ExternalError("external_format")
        uuid, name = item.get("uuid"), item.get("name")
        if (
            not isinstance(uuid, str)
            or not re.fullmatch(UUID, uuid)
            or not isinstance(name, str)
            or not name.strip()
            or uuid in seen
        ):
            raise ExternalError("external_format")
        seen.add(uuid)
        found.append({"uuid": uuid, "name": name})
    return {"page": page, "per_page": PER_PAGE, "total": total, "books": found}


def _flatten(
    items: list[Any],
    parent: str,
    found: list[dict[str, Any]],
    seen: set[str],
) -> None:
    for item in items:
        if not isinstance(item, dict):
            raise ExternalError("external_format")
        uuid, name = item.get("uuid"), item.get("name")
        numeric = item.get("id")
        if (
            not isinstance(uuid, str)
            or not re.fullmatch(UUID, uuid)
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(numeric, int)
            or isinstance(numeric, bool)
            or uuid in seen
        ):
            raise ExternalError("external_format")
        seen.add(uuid)
        found.append(
            {
                "uuid": uuid,
                "name": name,
                "parent_uuid": parent,
                "id": numeric,
            }
        )
        children = item.get("children", [])
        if not isinstance(children, list):
            raise ExternalError("external_format")
        _flatten(children, uuid, found, seen)


def _resource(
    client: RiseClient, resource_uuid: str, control: DownloadControl | None
) -> tuple[dict[str, Any], bytes, str]:
    url = f"{BASE}/resources/{resource_uuid}"
    answer = client.get(url, control)
    data = _object(answer)
    name = data.get("name")
    uri = data.get("uri")
    if (
        data.get("uuid") != resource_uuid
        or data.get("collectionUuid") != COLLECTION
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(uri, str)
        or not uri.startswith("https://")
    ):
        raise ExternalError("external_selection")
    return data, answer.body, url


def sections(
    client: RiseClient, resource_uuid: str, control: DownloadControl | None = None
) -> dict[str, Any]:
    """The book's own section list; membership is decided by this response.

    The numeric id is the provider's section key used to check content units.
    It is not an identity: the uuid remains the selected section identity.
    """
    resource_uuid = _valid_uuid(resource_uuid)
    data, _, _ = _resource(client, resource_uuid, control)
    raw = data.get("sections")
    if not isinstance(raw, list) or not raw:
        raise ExternalError("external_format")
    found: list[dict[str, Any]] = []
    _flatten(raw, "", found, set())
    return {
        "book": {
            "uuid": resource_uuid,
            "name": data["name"],
            "uri": data["uri"],
        },
        "sections": found,
    }


def _metadata(
    client: RiseClient, resource_uuid: str, control: DownloadControl | None
) -> tuple[dict[str, Any], bytes, str]:
    url = f"{BASE}/resources/{resource_uuid}/metadata"
    answer = client.get(url, control)
    data = _object(answer)
    result: dict[str, Any] = {}
    for key in (
        "name",
        "author",
        "edition",
        "source",
        "volume",
        "bookYear",
        "editionYear",
        "period",
        "dynasty",
        "totalPage",
        "language",
        "level1",
        "level2",
    ):
        value = data.get(key)
        if isinstance(value, str):
            result[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
        else:
            result[key] = ""
    return result, answer.body, url


def _unsupported(content: str) -> str:
    """Reason code for a unit this importer must not flatten."""
    for match in TAG.finditer(content):
        tag = match.group(0)
        if tag == PB:
            continue
        if tag.startswith("<char"):
            return "unresolved_glyph"
        if tag.startswith(("<img", "<note")):
            return "nonprose_or_unsupported_container"
        return "unsupported_inline"
    remainder = content.replace(PB, "")
    return "unsupported_inline" if "<" in remainder or ">" in remainder else ""


def _separator_kind(value: str) -> str:
    if value == PB:
        return "pb"
    return "newline" if "\n" in value or "\r" in value else "space"


def _split(content: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Exact character ranges at <pb /> and whitespace runs, never joined."""
    slices: list[dict[str, Any]] = []
    leading: list[str] = []
    cursor = 0
    for match in SEPARATOR.finditer(content):
        if match.start() > cursor:
            slices.append({"start": cursor, "end": match.start(), "after": []})
            cursor = match.start()
        kind = _separator_kind(match.group(0))
        if slices:
            slices[-1]["after"].append(kind)
        else:
            leading.append(kind)
        cursor = match.end()
    if cursor < len(content):
        slices.append({"start": cursor, "end": len(content), "after": []})
    return slices, leading


def preview(
    client: RiseClient,
    address: str,
    control: DownloadControl | None = None,
) -> Download:
    """Read one selected content page and keep every accepted slice separate."""
    match = ADDRESS.fullmatch(str(address).strip())
    if not match:
        raise ExternalError("external_address")
    resource_uuid, section_uuid, page_text = match.groups()
    page = int(page_text)
    data, resource_raw, resource_url = _resource(client, resource_uuid, control)
    if control:
        control.checkpoint()
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ExternalError("external_format")
    found: list[dict[str, Any]] = []
    _flatten(raw_sections, "", found, set())
    selected = next((item for item in found if item["uuid"] == section_uuid), None)
    if selected is None:
        raise ExternalError("external_selection")
    section_name = selected["name"]
    section_number = selected["id"]
    metadata, metadata_raw, metadata_url = _metadata(client, resource_uuid, control)
    if control:
        control.checkpoint()
    url = (
        f"{BASE}/sections/{section_uuid}/content_units?page={page}&per_page={PER_PAGE}"
    )
    answer = client.get(url, control)
    items = _list(answer)
    total = _header_int(answer.headers, "x-total")
    if (
        _header_int(answer.headers, "x-page") != page
        or _header_int(answer.headers, "x-per-page") != PER_PAGE
        or len(items) != _page_window(page, total)
    ):
        raise ExternalError("external_format")
    units: list[Unit] = []
    excluded: list[dict[str, str]] = []
    slices: list[dict[str, Any]] = []
    empty = 0
    resource_uri = data["uri"]
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ExternalError("external_format")
        content = item.get("content")
        if (
            not isinstance(content, str)
            or not isinstance(item.get("section_id"), int)
            or isinstance(item.get("section_id"), bool)
            or item.get("section_id") != section_number
        ):
            raise ExternalError("external_format")
        reason = _unsupported(content)
        if reason:
            excluded.append({"id": f"unit-{number}", "reason": reason})
            continue
        ranges, leading = _split(content)
        if not ranges:
            empty += 1
            continue
        for order, span in enumerate(ranges, 1):
            unit_id = f"page-{page}-unit-{number}-slice-{order}"
            units.append(
                Unit(
                    unit_id,
                    content[span["start"] : span["end"]],
                    url,
                    {},
                )
            )
            slices.append(
                {
                    "unit": unit_id,
                    "start": span["start"],
                    "end": span["end"],
                    "after": span["after"],
                }
            )
        if leading:
            slices.append({"unit": f"page-{page}-unit-{number}", "before": leading})
    if not units and not excluded:
        raise ExternalError("external_empty")
    identity = hashlib.sha256(
        f"{resource_uuid}/{section_uuid}/{page}".encode()
    ).hexdigest()[:24]
    details: dict[str, Any] = {
        "origin": "rise_api",
        "policy": "selected_section_page_exact_slices",
        "excluded": excluded,
        "resource": {
            "uuid": resource_uuid,
            "name": data["name"],
            "uri": resource_uri,
        },
        "section": {
            "uuid": section_uuid,
            "name": section_name,
            "section_id": section_number,
        },
        "metadata": metadata,
        "coverage": {
            "mode": "single_page",
            "page": page,
            "per_page": PER_PAGE,
            "total_units": total,
            "returned_units": len(items),
            "accepted_slices": len(units),
            "empty_units": empty,
            "complete_page": True,
        },
        "slices": slices,
    }
    title = f"{data['name']} · {section_name}"
    edition = metadata.get("edition")
    edition_text = edition if isinstance(edition, str) else ""
    return Download(
        "rise",
        identity,
        title,
        edition_text,
        tuple(units),
        (
            (resource_url, resource_raw),
            (metadata_url, metadata_raw),
            (url, answer.body),
        ),
        details,
    )
