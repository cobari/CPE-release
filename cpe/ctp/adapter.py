"""Import a work from the Chinese Text Project into the text folder.

CTP's documented JSON API (https://ctext.org/tools/api) has no full-text or title
search: `readlink` turns a ctext.org address into a CTP URN, and `gettext` returns a
chapter's paragraphs or a larger item's subsection URNs; subsections and most texts
need authentication (an API key or a registered institution). So CTP cannot be a
search source like ITKC; instead the researcher pastes a work's address and the work
is saved chapter by chapter as .txt files with manifests, then searched as local text
(docs/decisions.md "Source databases are connected one at a time").

The key is the researcher's own, kept in the window's preferences and never bundled.
CTP states the API is meant for offline use of reasonable amounts of text and asks
citations to link to the source page, which every saved chapter's manifest records.
Requests keep the project safeguards: one at a time, 1 s apart, a cap per import.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_URL = "https://api.ctext.org/"
SITE_URL = "https://ctext.org/"
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
MAX_CALLS = 400

Transport = Callable[[str, dict[str, Any], float], bytes]


class CtpError(RuntimeError):
    """A failure the page words from its catalog; `code` is the catalog suffix."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def urllib_transport(url: str, params: dict[str, Any], timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read()
        return data


@dataclass
class CtpClient:
    apikey: str = ""
    transport: Transport = urllib_transport
    min_interval: float = 1.0
    max_calls: int = MAX_CALLS
    timeout: float = 30.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)

    def call(self, function: str, **params: str) -> dict[str, Any]:
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
            data = json.loads(self.transport(API_URL + function, params, self.timeout))
        except (OSError, ValueError) as error:
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
    title = str(data.get("title", "")) or urn
    found: list[Chapter] = []
    _collect(urn, data, client, found)
    return title, found


def _collect(
    urn: str, data: dict[str, Any], client: CtpClient, out: list[Chapter]
) -> None:
    text = data.get("fulltext")
    if isinstance(text, list) and text:
        out.append(
            Chapter(urn, str(data.get("title", "")), [str(p) for p in text if str(p)])
        )
    for sub in data.get("subsections") or []:
        if isinstance(sub, str) and sub.startswith("ctp:"):
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

    Each file's content is built before it is opened (opening empties a file).
    """
    target = folder / f"ctp-{_slug(urn.removeprefix('ctp:').split('/')[0])}"
    target.mkdir(parents=True, exist_ok=True)
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
        with (target / f"{stem}.txt").open("w", encoding="utf-8", newline="") as stream:
            stream.write(body)
        with (target / f"{stem}.txt.manifest.toml").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            stream.write(manifest)
    return target


def import_work(address: str, client: CtpClient, folder: Path) -> dict[str, Any]:
    urn = work_urn(address, client)
    title, found = chapters(urn, client)
    if not found:
        raise CtpError("ctp_empty")
    target = save(title, urn, found, folder)
    return {"title": title, "chapters": len(found), "folder": str(target)}
