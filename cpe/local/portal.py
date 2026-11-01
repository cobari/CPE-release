"""Download an institute's original-text file release from data.go.kr.

The window offers a fixed list of releases (DATASETS); the researcher picks one and a
folder, and the file is fetched the way the portal's own download button fetches it:
the dataset page names the file's detail key, `selectFileDataDownload.do` turns it
into a file id, `check-limit.json` says whether the portal wants a CAPTCHA, and
`fileDownload.do` returns ZIP or XML. Checked by hand on 2026-10-10 (robots.txt only
restricts Googlebot, on other paths). When the portal asks for a CAPTCHA the download
stops and the researcher is sent to the dataset page: it is never answered or worked
around (docs/decisions.md "Research use through the tool: which terms matter").

Requests keep the project safeguards: one at a time, at least 1 s apart, a cap per
session. The release goes to a temporary file that the caller deletes after converting.
"""

from __future__ import annotations

import http.client
import http.cookiejar
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

from cpe.local.releases import ReleaseError

PORTAL = "https://www.data.go.kr"
AGENT = "classic-pattern-explorer (research tool; https://github.com/cobari/CPE)"
MAX_CALLS = 60
# A portal download is streamed to disk. Stop a damaged or hostile response before
# it fills the disk; metadata responses are much smaller.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Dataset:
    """A release the window offers. Its name and description come from the page's
    catalogs (release.ds.<pk>); `rights` is a catalog suffix too."""

    pk: str
    institute: str
    rights: str


# Each release was downloaded and converted before being listed here; what was checked
# and when is recorded in docs/research/corpus-import-expansion.md,
# docs/research/aks-geumwiyeong-import.md, docs/research/aks-sunmuyeong-import.md,
# docs/research/gojong-sidae-sa-inspection.md and
# docs/research/goryeosa-jeolyo-import.md. NIKH releases may
# leave out articles with unresolved glyph references (cpe.nikh.importer), so a listed
# release is not promised in full.
DATASETS = (
    Dataset("15052758", "aks", "unrestricted"),  # 금위영등록
    Dataset("15052764", "aks", "unrestricted"),  # 순무영등록
    Dataset("15053635", "nikh", "kogl1"),  # 삼국사기
    Dataset("15053634", "nikh", "kogl1"),  # 삼국유사
    Dataset("15053629", "nikh", "kogl1"),  # 해동고승전
    Dataset("15053637", "nikh", "kogl1"),  # 고려사
    Dataset("15115521", "nikh", "kogl1_description_conflict"),  # 고려사절요
    Dataset("15053636", "nikh", "kogl1"),  # 비변사등록
    Dataset("15053638", "nikh", "kogl1"),  # 각사등록
    Dataset("15053639", "nikh", "unrestricted"),  # 각사등록 근대편
    Dataset("15053646", "nikh", "kogl4"),  # 고종·순종실록
    Dataset("15053647", "nikh", "kogl1"),  # 조선왕조실록 (태조~철종)
    Dataset("15138768", "nikh", "kogl1"),  # 고종시대사
    Dataset("15053631", "nikh", "kogl1"),  # 한국고대사료집성 중국편
    Dataset("15053632", "nikh", "kogl1"),  # 한국사료총서
    Dataset("15022432", "itkc", "unrestricted"),  # 고전원문 (古芸堂筆記)
    Dataset("15141448", "itkc", "unrestricted"),  # 임하필기
    Dataset("15141472", "itkc", "unrestricted"),  # 신증동국여지승람
    Dataset("15141469", "itkc", "unrestricted"),  # 영남인물고
    Dataset("15141458", "itkc", "unrestricted"),  # 세자행적
    Dataset("15096232", "itkc", "unrestricted"),  # 회당집
    Dataset("15096009", "itkc", "unrestricted"),  # 농산집
    Dataset("15096008", "itkc", "unrestricted"),  # 사복재집
    Dataset("15096001", "itkc", "unrestricted"),  # 심석재집
    Dataset("15095996", "itkc", "unrestricted"),  # 총쇄
)
_BUTTON = re.compile(r"fn_fileDataDown\('(\d+)',\s*'([^']+)'")

Transport = Callable[[urllib.request.Request, float], bytes]


def dataset(pk: str) -> Dataset:
    for entry in DATASETS:
        if entry.pk == pk:
            return entry
    raise ReleaseError("portal_unknown_dataset")


def page_url(pk: str) -> str:
    return f"{PORTAL}/data/{pk}/fileData.do"


@dataclass
class PortalClient:
    transport: Transport | None = None
    min_interval: float = 1.0
    max_calls: int = MAX_CALLS
    timeout: float = 300.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    calls: int = 0
    _last: float | None = field(default=None, repr=False)
    _opener: urllib.request.OpenerDirector | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def _send(
        self, request: urllib.request.Request, destination: Path | None = None
    ) -> bytes:
        with self._lock:
            return self._send_locked(request, destination)

    def _send_locked(
        self, request: urllib.request.Request, destination: Path | None = None
    ) -> bytes:
        if self.calls >= self.max_calls:
            raise ReleaseError("portal_cap")
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.calls += 1
        request.add_header("User-Agent", AGENT)
        try:
            if self.transport is not None:
                content = self.transport(request, self.timeout)
                if len(content) > MAX_DOWNLOAD_BYTES:
                    raise ReleaseError("release_large")
                if destination is not None:
                    destination.write_bytes(content)
                    return b""
                return content
            if self._opener is None:
                # The portal ties the three download steps together by its session
                # cookie, as in a browser.
                self._opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
                )
            with self._opener.open(request, timeout=self.timeout) as response:
                expected = response.length
                if destination is not None:
                    written = 0
                    try:
                        with destination.open("wb") as stream:
                            while chunk := response.read(1024 * 1024):
                                written += len(chunk)
                                if written > MAX_DOWNLOAD_BYTES:
                                    raise ReleaseError("release_large")
                                stream.write(chunk)
                        if expected is not None and written != expected:
                            raise ReleaseError("portal_failed")
                    except BaseException:
                        destination.unlink(missing_ok=True)
                        raise
                    return b""
                data: bytes = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ReleaseError("release_large")
                if expected is not None and len(data) != expected:
                    raise ReleaseError("portal_failed")
                return data
        except (OSError, http.client.HTTPException):
            raise ReleaseError("portal_failed") from None

    def get(self, url: str, referer: str = "") -> bytes:
        request = urllib.request.Request(url)
        if referer:
            request.add_header("Referer", referer)
        return self._send(request)

    def post(self, url: str, fields: dict[str, str], referer: str) -> bytes:
        request = urllib.request.Request(
            url, data=urllib.parse.urlencode(fields).encode("ascii"), method="POST"
        )
        request.add_header("Referer", referer)
        request.add_header("X-Requested-With", "XMLHttpRequest")
        return self._send(request)


def _json(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(data)
    except ValueError:
        raise ReleaseError("portal_failed") from None
    if not isinstance(value, dict):
        raise ReleaseError("portal_failed")
    return value


def download(pk: str, client: PortalClient) -> Path:
    """A listed release's ZIP or XML, saved to a temporary file."""
    entry = dataset(pk)
    referer = page_url(entry.pk)
    page = client.get(referer).decode("utf-8", errors="replace")
    button = _BUTTON.search(page)
    if button is None or button.group(1) != entry.pk:
        raise ReleaseError("portal_changed")
    answer = _json(
        client.post(
            f"{PORTAL}/tcs/dss/selectFileDataDownload.do",
            {
                "publicDataDetailPk": button.group(2),
                "publicDataPk": entry.pk,
                "atchFileId": "",
                "fileDetailSn": "1",
                "publicDataTyCode": "PR0051",
            },
            referer,
        )
    )
    file_id, serial = answer.get("atchFileId"), answer.get("fileDetailSn")
    if not isinstance(file_id, str) or not file_id or serial is None:
        raise ReleaseError("portal_changed")
    fields = {"atchFileId": file_id, "fileDetailSn": str(serial)}
    limit = _json(client.post(f"{PORTAL}/cmm/cmm/check-limit.json", fields, referer))
    if limit.get("needCaptcha"):
        raise ReleaseError("portal_captcha")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as stream:
        path = Path(stream.name)
    try:
        request = urllib.request.Request(
            f"{PORTAL}/cmm/cmm/fileDownload.do?{urllib.parse.urlencode(fields)}",
            headers={"Referer": referer},
        )
        client._send(request, path)
        with path.open("rb") as saved:
            prefix = saved.read(128).lstrip(b"\xef\xbb\xbf \t\r\n")
            if not prefix.startswith((b"PK", b"<")):
                raise ReleaseError("portal_changed")
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
