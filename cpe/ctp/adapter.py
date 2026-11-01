"""Import a work from the Chinese Text Project into the text folder.

This adapter retrieves a supplied address with `readlink` and `gettext`; title
discovery and online full-text search are outside its scope. CTP separately
documents title-search XML. Limited chapter text can be available without a key,
while traversing a work's subsection list requires provider authentication.
Send a key only when one is configured and preserve authentication/limit errors;
an empty key must not block a chapter that the provider permits anonymously.
The retrieved chapters are saved as .txt files with manifests and searched locally
(docs/research/source-access-audit.md "Chinese Text Project").

The key is the researcher's own, kept in the window's preferences and never bundled.
CTP states the API is meant for offline use of reasonable amounts of text and asks
citations to link to the source page, which every saved chapter's manifest records.
Requests keep the project safeguards: one at a time, 1 s apart, a cap per import.
"""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.local.file_lock import FileBusyError, exclusive
from cpe.local.txt import InputError, read

API_URL = "https://api.ctext.org/"
SITE_URL = "https://ctext.org/"
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
MAX_CALLS = 400
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

Transport = Callable[[str, dict[str, Any], float], bytes]


class CtpError(RuntimeError):
    """A failure the page words from its catalog; `code` is the catalog suffix."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def urllib_transport(
    url: str,
    params: dict[str, Any],
    timeout: float,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise CtpError("ctp_response_large")
        return data


@dataclass
class CtpClient:
    apikey: str = ""
    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = MAX_CALLS
    timeout: float = 30.0
    max_response_bytes: int = MAX_RESPONSE_BYTES
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def call(self, function: str, **params: str) -> dict[str, Any]:
        with self._lock:
            return self._call(function, params)

    def _call(self, function: str, params: dict[str, str]) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise CtpError("ctp_cap")
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.calls += 1
        if self.apikey:
            params["apikey"] = self.apikey
        try:
            raw = (
                urllib_transport(
                    API_URL + function, params, self.timeout, self.max_response_bytes
                )
                if self.transport is urllib_transport
                else self.transport(API_URL + function, params, self.timeout)
            )
            if len(raw) > self.max_response_bytes:
                raise CtpError("ctp_response_large")
            data = json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise CtpError("source_failed", str(error)) from None
        if not isinstance(data, dict):
            raise CtpError("source_failed", "unexpected response")
        problem = data.get("error")
        if isinstance(problem, dict):
            code = str(problem.get("code", ""))
            if code == "ERR_REQUIRES_AUTHENTICATION":
                raise CtpError("ctp_auth", code)
            if code == "ERR_REQUEST_LIMIT":
                raise CtpError("ctp_limit", code)
            raise CtpError("ctp_failed", code)
        return data


@dataclass(frozen=True)
class Chapter:
    urn: str
    title: str
    paragraphs: list[str]


def work_urn(address: str, client: CtpClient) -> str:
    """The CTP URN of a ctext.org address (an URN itself is accepted as it is)."""
    address = address.strip()
    if address.startswith("ctp:"):
        return address
    if not re.match(r"https?://(www\.)?ctext\.org/", address):
        raise CtpError("ctp_bad_url")
    urn = client.call("readlink", url=address).get("urn")
    if not isinstance(urn, str) or not urn.startswith("ctp:"):
        raise CtpError("ctp_bad_url")
    return urn


def chapters(urn: str, client: CtpClient) -> tuple[str, list[Chapter]]:
    """The work's title and its chapters in order, walking subsections depth first."""
    data = client.call("gettext", urn=urn)
    title = _title(data) or urn
    found: list[Chapter] = []
    _collect(urn, data, client, found)
    return title, found


def _title(data: dict[str, Any]) -> str:
    title = data.get("title", "")
    if not isinstance(title, str):
        raise CtpError("source_failed", "title must be a source string")
    return title


def _collect(
    urn: str, data: dict[str, Any], client: CtpClient, out: list[Chapter]
) -> None:
    title = _title(data)
    text = data.get("fulltext")
    if text is not None and (
        not isinstance(text, list)
        or any(not isinstance(paragraph, str) for paragraph in text)
    ):
        raise CtpError("source_failed", "fulltext must be a list of source strings")
    if isinstance(text, list) and text:
        out.append(Chapter(urn, title, [p for p in text if p]))
    subsections = data.get("subsections")
    if subsections is None:
        subsections = []
    if not isinstance(subsections, list) or any(
        not isinstance(sub, str) or not sub.startswith("ctp:") for sub in subsections
    ):
        raise CtpError("source_failed", "subsections must be a list of source URNs")
    for sub in subsections:
        _collect(sub, client.call("gettext", urn=sub), client, out)


def page_link(urn: str) -> str:
    """ctext.org address of a URN (ctp:analects/xue-er → https://ctext.org/analects/xue-er)."""
    return SITE_URL + urn.removeprefix("ctp:")


def _slug(text: str) -> str:
    """Lowercase ASCII file-name part (AGENTS.md "Portability")."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "text"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def save(work_title: str, urn: str, found: list[Chapter], folder: Path) -> Path:
    """Write one .txt and one manifest per chapter under folder/ctp-<work>/.

    Existing identical imports are reused; conflicting editions are preserved.
    """
    target = folder / f"ctp-{_slug(urn.removeprefix('ctp:').split('/')[0])}"
    contents: dict[str, str] = {}
    if len({chapter.urn for chapter in found}) != len(found):
        raise CtpError("ctp_existing_conflict")
    for number, chapter in enumerate(found, start=1):
        stem = f"{number:03d}-{_slug(chapter.urn.rsplit('/', 1)[-1])}"
        body = "\n".join(chapter.paragraphs) + "\n"
        title = " · ".join(part for part in (work_title, chapter.title) if part)
        manifest = (
            f"title = {_toml_string(title)}\n"
            f"source = {_toml_string(page_link(chapter.urn))}\n"
            f'edition = "Chinese Text Project"\n'
            f"text_id = {_toml_string(chapter.urn)}\n"
        )
        contents[f"{stem}.txt"] = body
        contents[f"{stem}.txt.manifest.toml"] = manifest
    try:
        with exclusive(folder / f".{target.name}.lock"):
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_dir():
                    raise CtpError("ctp_existing_conflict")
                if {path.name for path in target.iterdir()} != contents.keys() or any(
                    (target / name).is_symlink()
                    or not (target / name).is_file()
                    or (target / name).read_bytes() != content.encode("utf-8")
                    for name, content in contents.items()
                ):
                    raise CtpError("ctp_existing_conflict")
                return target
            with tempfile.TemporaryDirectory(prefix=".ctp-", dir=folder) as temporary:
                staged = Path(temporary) / target.name
                staged.mkdir()
                for name, content in contents.items():
                    with (staged / name).open(
                        "w", encoding="utf-8", newline=""
                    ) as stream:
                        stream.write(content)
                try:
                    for path in staged.glob("*.txt"):
                        read(path)
                except InputError:
                    raise CtpError("ctp_write_failed") from None
                staged.rename(target)
    except FileBusyError:
        raise CtpError("local_busy") from None
    return target


def import_work(address: str, client: CtpClient, folder: Path) -> dict[str, Any]:
    urn = work_urn(address, client)
    title, found = chapters(urn, client)
    if not found:
        raise CtpError("ctp_empty")
    target = save(title, urn, found, folder)
    return {"title": title, "chapters": len(found), "folder": str(target)}
