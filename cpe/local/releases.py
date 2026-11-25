"""Institute original-text releases (ZIP or XML files) converted into the text folder.

Inspected releases from NIKH, ITKC, AKS and NAHF are recognised from the first
XML root and release identity. Source-specific readers retain their own scope,
so sharing an XML shape does not grant another release the same interpretation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

from cpe.cancellation import Checkpoint
from cpe.local.file_lock import FileBusyError, exclusive
from cpe.local.paths import is_directory_link
from cpe.local.txt import BUNDLE_SUFFIX, MANIFEST_SUFFIX, InputError, iter_bundle_ids

_SAVE_LOCK = threading.Lock()

# A release ZIP is read into memory member by member. These bounds stop a damaged
# or hostile archive from exhausting the window; they are well above the largest
# inspected institute volume. The Sillok release 15053647 is 143 MB compressed but
# 853 MB of XML across 673 members, so the total bound is 1 GiB; members are still
# read one at a time and each stays under the 32 MiB member bound.
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_STANDALONE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_COMPRESSION_RATIO = 100
_READ_CHUNK = 64 * 1024


def is_release_member(name: str) -> bool:
    """A ZIP member that can hold a release XML document.

    macOS writes its resource forks under __MACOSX/ and as ._name companions. They
    are not release text, and __MACOSX sorts before ordinary folders, so keeping
    them would make the first XML look unreadable.
    """
    normalized = name.replace("\\", "/")
    if normalized.endswith("/") or not normalized.lower().endswith(".xml"):
        return False
    if normalized.startswith("__MACOSX/"):
        return False
    return not normalized.rsplit("/", 1)[-1].startswith("._")


class ReleaseError(ValueError):
    """A file that is not a readable release; `code` is the page's catalog suffix."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ReleaseControl:
    """Thread-safe progress and cancellation, independent of the import lock."""

    cancel: threading.Event = field(default_factory=threading.Event)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _progress: dict[str, Any] = field(
        default_factory=lambda: {
            "phase": "waiting",
            "received": 0,
            "total": 0,
            "member": "",
            "done_files": 0,
            "total_files": 0,
            "size_known": False,
        }
    )

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise ReleaseError("release_cancelled")

    def update(self, **values: Any) -> None:
        self.checkpoint()
        with self._lock:
            self._progress.update(values)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self._progress, "cancelling": self.cancel.is_set()}


def _member_path(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace(chr(92), "/")
    parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or ".." in parts
        or ":" in name
        or chr(0) in info.orig_filename
        or stat.S_ISLNK(info.external_attr >> 16)
    ):
        raise ReleaseError("release_unreadable")
    return "/".join(part for part in parts if part and part != ".")


def _check_duplicate_members(members: Iterable[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for count, info in enumerate(members, 1):
        if count > MAX_ARCHIVE_ENTRIES:
            raise ReleaseError("release_large")
        normalized = _member_path(info)
        if not normalized or normalized in seen:
            raise ReleaseError("release_unreadable")
        seen.add(normalized)


def read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
    total_remaining: int,
    checkpoint: Checkpoint = None,
) -> bytes:
    """Bound actual member reads and expansion before allocating the next chunk."""
    _member_path(info)
    if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
        raise ReleaseError("release_large")
    limit = min(max_bytes, total_remaining)
    if limit < 0 or info.file_size > limit:
        raise ReleaseError("release_large")
    data = bytearray()
    with archive.open(info) as stream:
        while True:
            if checkpoint:
                checkpoint()
            chunk = stream.read(min(_READ_CHUNK, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit or (
                len(data) > 1024 * 1024
                and len(data) > MAX_COMPRESSION_RATIO * info.compress_size
            ):
                raise ReleaseError("release_large")
    if checkpoint:
        checkpoint()
    return bytes(data)


def documents(
    path: Path,
    *,
    checkpoint: Checkpoint = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> Generator[bytes, None, None]:
    """The XML documents in name order, with bounded reads and no extraction."""
    try:
        if checkpoint:
            checkpoint()
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                _check_duplicate_members(archive.infolist())
                members = [
                    info
                    for info in archive.infolist()
                    if is_release_member(info.filename)
                ]
                if sum(info.file_size for info in members) > MAX_TOTAL_BYTES:
                    raise ReleaseError("release_large")
                remaining = MAX_TOTAL_BYTES
                if progress:
                    progress(0, len(members), "")
                for index, info in enumerate(
                    sorted(members, key=lambda item: item.filename), 1
                ):
                    if checkpoint:
                        checkpoint()
                    data = read_zip_member(
                        archive,
                        info,
                        max_bytes=MAX_MEMBER_BYTES,
                        total_remaining=remaining,
                        checkpoint=checkpoint,
                    )
                    remaining -= len(data)
                    if progress:
                        progress(index, len(members), info.filename)
                    yield data
        else:
            if path.stat().st_size > MAX_STANDALONE_BYTES:
                raise ReleaseError("release_large")
            if progress:
                progress(0, 1, path.name)
            standalone = bytearray()
            with path.open("rb") as stream:
                while True:
                    if checkpoint:
                        checkpoint()
                    chunk = stream.read(
                        min(_READ_CHUNK, MAX_STANDALONE_BYTES + 1 - len(standalone))
                    )
                    if not chunk:
                        break
                    standalone.extend(chunk)
                    if len(standalone) > MAX_STANDALONE_BYTES:
                        raise ReleaseError("release_large")
            if progress:
                progress(1, 1, path.name)
            yield bytes(standalone)
        if checkpoint:
            checkpoint()
    except InterruptedError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        NotImplementedError,
        RuntimeError,
        EOFError,
        zlib.error,
    ):
        raise ReleaseError("release_unreadable") from None


def parse(document: bytes) -> ET.Element:
    try:
        # A DOCTYPE may name an external DTD (history.dtd), which is never fetched:
        # ElementTree does not load external DTDs or entities.
        return ET.fromstring(document)
    except ET.ParseError:
        pass
    try:
        return ET.fromstring(join_surrogates(document))
    except ET.ParseError:
        raise ReleaseError("release_unreadable") from None


# Digits are bounded so that a malformed reference never reaches int() unbounded.
_CHAR_REF = re.compile(rb"&#(x[0-9A-Fa-f]{1,6}|[0-9]{1,7});")


def _code_point(reference: bytes) -> int:
    return int(reference[1:], 16) if reference.startswith(b"x") else int(reference)


def join_surrogates(document: bytes) -> bytes:
    """The document with each UTF-16 surrogate pair of character references joined.

    Some releases write a character outside the Basic Multilingual Plane as two
    references, &#55381;&#56892; for U+2563C (조선왕조실록 15053647). XML forbids
    surrogate references, so without this the whole document is unreadable. Only a
    high surrogate directly followed by a low one is joined; a lone surrogate is left
    as it is and the document stays unreadable.
    """
    parts: list[bytes] = []
    last = 0
    references = list(_CHAR_REF.finditer(document))
    for high, low in zip(references, references[1:], strict=False):
        if high.start() < last or high.end() != low.start():
            continue
        first, second = _code_point(high.group(1)), _code_point(low.group(1))
        if 0xD800 <= first <= 0xDBFF and 0xDC00 <= second <= 0xDFFF:
            joined = 0x10000 + (first - 0xD800) * 0x400 + (second - 0xDC00)
            parts += [document[last : high.start()], b"&#%d;" % joined]
            last = low.end()
    parts.append(document[last:])
    return b"".join(parts)


def save_bundles(
    target: Path,
    groups: dict[str, list[dict[str, Any]]],
    id_prefix: str,
    *,
    report: str | None = None,
    keep_existing: bool = False,
) -> None:
    """Stage and validate all volumes and the report before replacing the work.

    Existing unrelated files and volumes absent from this input are retained.
    Legacy article files have no ownership receipt, so conflicting IDs are refused.
    `keep_existing` makes an existing target that already holds exactly these bytes
    a no-op and any other existing target a conflict; the check runs under the same
    lock as the install, so another process cannot publish between them.
    """
    contents: dict[str, str] = {}
    ids: set[str] = set()
    group_ids: dict[str, set[str]] = {}
    for group, records in groups.items():
        name = bundle_name(group)
        if name in contents:
            raise ReleaseError("release_existing_conflict")
        for record in records:
            text_id = record.get("text_id")
            if (
                not isinstance(text_id, str)
                or not text_id.startswith(id_prefix)
                or text_id in ids
            ):
                raise ReleaseError("release_existing_conflict")
            ids.add(text_id)
        group_ids[name] = {record["text_id"] for record in records}
        contents[name] = "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        )
    if report is not None:
        contents["import-report.json"] = report
    # The lock file lives beside the target, so its parent must exist first;
    # nothing inside the target is read or written before the lock is held.
    target.parent.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        try:
            with exclusive(target.with_name(f".{target.name}.lock")):
                if not keep_existing or not _current(target, contents):
                    _install_bundles(target, contents, ids, group_ids)
        except FileBusyError:
            # Another window or process is installing into this folder; its
            # half-written state stays untouched and the page can say so with
            # the same catalog entry it uses while an import is running.
            raise ReleaseError("local_busy") from None


def save_streamed_bundles(
    target: Path,
    volume_files: dict[str, Path],
    id_prefix: str,
    *,
    volume_ids: dict[str, set[str]] | None = None,
    report: str | None = None,
    keep_existing: bool = False,
    checkpoint: Checkpoint = None,
) -> None:
    """Install pre-staged volume files under the existing release transaction."""
    contents: dict[str, str | Path] = {}
    ids: set[str] = set()
    groups: dict[str, set[str]] = {}
    for volume, source in volume_files.items():
        if checkpoint:
            checkpoint()
        name = bundle_name(volume)
        if name in contents or is_directory_link(source) or not source.is_file():
            raise ReleaseError("release_existing_conflict")
        found: set[str] = set()
        try:
            for text_id in iter_bundle_ids(source):
                if checkpoint:
                    checkpoint()
                if not text_id.startswith(id_prefix) or text_id in ids:
                    raise ReleaseError("release_existing_conflict")
                ids.add(text_id)
                found.add(text_id)
        except InputError:
            raise ReleaseError("release_unreadable") from None
        if volume_ids is not None and volume_ids.get(volume) != found:
            raise ReleaseError("release_existing_conflict")
        groups[name] = found
        contents[name] = source
    if report is not None:
        contents["import-report.json"] = report
    if checkpoint:
        checkpoint()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        try:
            with exclusive(target.with_name(f".{target.name}.lock")):
                current = keep_existing and _current(target, contents, checkpoint)
                if checkpoint:
                    checkpoint()
                if not current:
                    _install_bundles(target, contents, ids, groups, checkpoint)
        except FileBusyError:
            raise ReleaseError("local_busy") from None


def _same_content(
    path: Path, content: str | Path, checkpoint: Checkpoint = None
) -> bool:
    if isinstance(content, Path):
        if path.stat().st_size != content.stat().st_size:
            return False
        with path.open("rb") as first, content.open("rb") as second:
            while chunk := first.read(65536):
                if checkpoint:
                    checkpoint()
                if chunk != second.read(65536):
                    return False
            return not second.read(1)
    expected = content.encode("utf-8")
    if path.stat().st_size != len(expected):
        return False
    with path.open("rb") as stream:
        for offset in range(0, len(expected), 65536):
            if checkpoint:
                checkpoint()
            if stream.read(65536) != expected[offset : offset + 65536]:
                return False
        return not stream.read(1)


def _copy_content(source: Path, target: Path, checkpoint: Checkpoint) -> None:
    with source.open("rb") as incoming, target.open("wb") as output:
        while True:
            if checkpoint:
                checkpoint()
            chunk = incoming.read(65536)
            if not chunk:
                break
            output.write(chunk)


def _current(
    target: Path, contents: Mapping[str, str | Path], checkpoint: Checkpoint = None
) -> bool:
    """Whether target already holds exactly `contents`, restoring a stopped install.

    Called while the target's lock is held: another process publishing the same
    folder renames the old one to `.previous` and back, so inspecting that fixed
    name without the lock could move a folder still being published.
    """
    backup = target.with_name(f".{target.name}.previous")
    if is_directory_link(target) or is_directory_link(backup):
        raise ReleaseError("release_existing_conflict")
    if backup.exists():
        if target.exists():
            raise ReleaseError("release_existing_conflict")
        backup.rename(target)
    if not target.exists():
        return False
    if (
        not target.is_dir()
        or {path.name for path in target.iterdir()} != contents.keys()
        or any(
            (target / name).is_symlink()
            or not (target / name).is_file()
            or not _same_content(target / name, value, checkpoint)
            for name, value in contents.items()
        )
    ):
        raise ReleaseError("release_existing_conflict")
    return True


def _install_bundles(
    target: Path,
    contents: Mapping[str, str | Path],
    ids: set[str],
    group_ids: dict[str, set[str]],
    checkpoint: Checkpoint = None,
) -> None:
    backup = target.with_name(f".{target.name}.previous")
    if is_directory_link(target) or is_directory_link(backup):
        raise ReleaseError("release_existing_conflict")
    if backup.exists():
        if target.exists():
            # A published target plus a leftover backup means only the old copy's
            # deletion failed; the target is the newer result. Retire the backup
            # instead of refusing every later import.
            _retire_backup(backup)
            if backup.exists():
                raise ReleaseError("release_existing_conflict")
        else:
            # A stopped process may have moved the old work before publishing the
            # new one. Restore it before starting another import.
            backup.rename(target)
    if target.is_dir():
        for parent, directories, _ in os.walk(target, followlinks=False):
            if any(is_directory_link(Path(parent) / name) for name in directories):
                raise ReleaseError("release_existing_conflict")
    _check_article_files(target, ids)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=target.parent) as temp:
        staged = Path(temp) / target.name
        if target.exists():
            shutil.copytree(target, staged, symlinks=True)
        else:
            staged.mkdir()
        for name, content in contents.items():
            path = staged / name
            if is_directory_link(path):
                raise ReleaseError("release_existing_conflict")
            if path.exists() and name.endswith(BUNDLE_SUFFIX):
                try:
                    previous_ids = set(_bundle_ids(path, checkpoint))
                except InputError:
                    raise ReleaseError("release_existing_conflict") from None
                # A same-name bundle may receive a full replacement, a superset,
                # or a subset when the importer deliberately excludes an article.
                # A disjoint set is a different volume or split part and would drop
                # every earlier article, so refuse it.
                if previous_ids and not previous_ids & group_ids[name]:
                    raise ReleaseError("release_existing_conflict")
            if checkpoint:
                checkpoint()
            if isinstance(content, Path):
                _copy_content(content, path, checkpoint)
            else:
                with path.open("w", encoding="utf-8", newline="") as stream:
                    stream.write(content)
        seen: set[str] = set()
        for bundle in staged.glob("*" + BUNDLE_SUFFIX):
            try:
                bundle_ids = list(_bundle_ids(bundle, checkpoint))
            except InputError:
                if bundle.name in contents:
                    raise ReleaseError("release_unreadable") from None
                continue
            for text_id in bundle_ids:
                if text_id in seen:
                    raise ReleaseError("release_existing_conflict")
                seen.add(text_id)
        if not ids <= seen:
            raise ReleaseError("release_unreadable")
        if checkpoint:
            checkpoint()
        if is_directory_link(target) or is_directory_link(backup):
            raise ReleaseError("release_existing_conflict")
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            _retire_backup(backup)


def _bundle_ids(path: Path, checkpoint: Checkpoint) -> Generator[str, None, None]:
    for text_id in iter_bundle_ids(path):
        if checkpoint:
            checkpoint()
        yield text_id


def _retire_backup(backup: Path) -> None:
    """Move a published backup out of its fixed name, then delete it.

    Windows can keep a just-replaced directory busy (antivirus, indexer). Deleting
    it in place would report a failed import after the new text is already
    published, and would leave the fixed name to block the next import. Renaming
    first frees that name; a failed delete leaves only an orphan directory.
    """
    orphan = backup.with_name(f"{backup.name}.{os.getpid()}.{time.time_ns()}")
    try:
        backup.rename(orphan)
    except OSError:
        return
    try:
        shutil.rmtree(orphan, ignore_errors=True)
    except OSError:
        # Cleanup is not part of the import's result.
        pass


def bundle_name(group: str) -> str:
    """The file name save_bundles gives a group's bundle."""
    name = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-") or "texts"
    return f"{name}{BUNDLE_SUFFIX}"


def _check_article_files(target: Path, ids: set[str]) -> None:
    """A matching ID is a conflict, not proof that the importer owns the file."""
    for manifest in target.rglob("*" + MANIFEST_SUFFIX):
        text = manifest.with_name(manifest.name.removesuffix(MANIFEST_SUFFIX))
        try:
            with manifest.open("rb") as stream:
                content = tomllib.load(stream)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        text_id = content.get("text_id")
        if isinstance(text_id, str) and text_id in ids and text.is_file():
            raise ReleaseError("release_existing_conflict")


def import_release(
    path: Path,
    folder: Path,
    *,
    checkpoint: Checkpoint = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    """Convert a release into folder; {book, articles, folder} for the status line."""
    from cpe.local.titled_releases import NAHF_BY_ITEM

    if checkpoint:
        checkpoint()
    nahf_codes: set[str] = set()
    if path.is_file() and zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                _check_duplicate_members(archive.infolist())
                names = archive.namelist()
        except (
            OSError,
            zipfile.BadZipFile,
            NotImplementedError,
            RuntimeError,
            EOFError,
            zlib.error,
        ):
            # is_zipfile() only reads the end record; a damaged archive that still
            # passes it must reach the caller as an unreadable release, not as a
            # raw ZIP exception the page does not translate.
            raise ReleaseError("release_unreadable") from None
        for name in names:
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if not base.lower().endswith(".xml"):
                continue
            code = base.split(".", 1)[0]
            if code in NAHF_BY_ITEM and base.startswith(f"{code}.d_"):
                nahf_codes.add(code)
        if nahf_codes and (
            len(nahf_codes) > 1 or next(iter(nahf_codes)) not in {"dh", "jo", "jb"}
        ):
            from cpe.local.titled_releases import import_nahf_zip

            return import_nahf_zip(path, folder)
    with closing(documents(path, checkpoint=checkpoint, progress=progress)) as reader:
        first = next(reader, None)
        if first is None:
            raise ReleaseError("release_empty")
        root = parse(first)
        found: Iterable[bytes] = chain((first,), reader)
        if root.tag == "금위영등록":
            from cpe.aks import importer as aks

            return aks.import_documents(found, folder)
        if root.tag == "자료":
            # Both inspected AKS releases share the escaped-article wrapper; the
            # source identity prefix selects the reader. An unlisted prefix reaches
            # the Jangyongyeong reader, which refuses it instead of reinterpreting it.
            identity = (root.findtext("기사/AA20_NO") or "").strip()
            if identity.startswith("23335_"):
                from cpe.aks import sunmuyeong

                return sunmuyeong.import_documents(found, folder)
            from cpe.aks import jangyongyeong

            return jangyongyeong.import_documents(found, folder)
        if root.tag == "아이템":
            from cpe.itkc import release as itkc

            return itkc.import_documents(found, folder)
        if root.tag == "item" or re.match(r"level\d+$", root.tag):
            identity = root.get("id", "")
            if identity == "muds" or identity.startswith(
                (
                    "muds_",
                    "dh.d_",
                    "dh.k_",
                    "dh.m_",
                    "jb.",
                    "jo.",
                    *tuple(f"{code}." for code in NAHF_BY_ITEM),
                )
            ):
                from cpe.local import titled_releases

                return titled_releases.import_documents(found, folder)
            from cpe.nikh import importer as nikh

            saryo = root.get("name") == "한국사료총서" or identity.startswith("sa_")
            del root
            return nikh.import_documents_streamed(
                found, folder, saryo=saryo, checkpoint=checkpoint
            )
        raise ReleaseError("release_unknown")
