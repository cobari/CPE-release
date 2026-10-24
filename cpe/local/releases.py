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
import tempfile
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Generator, Iterable
from contextlib import closing
from itertools import chain
from pathlib import Path
from typing import Any

from cpe.local.file_lock import FileBusyError, exclusive
from cpe.local.txt import BUNDLE_SUFFIX, MANIFEST_SUFFIX, InputError, read_bundle

_SAVE_LOCK = threading.Lock()

# A release ZIP is read into memory member by member. These bounds stop a damaged
# or hostile archive from exhausting the window; they are well above the largest
# inspected institute volume.
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _release_member(name: str) -> bool:
    """A ZIP member that can hold a release XML document.

    macOS writes its resource forks under __MACOSX/ and as ._name companions. They
    are not release text, and __MACOSX sorts before ordinary folders, so keeping
    them would make the first XML look unreadable.
    """
    if name.endswith("/") or not name.lower().endswith(".xml"):
        return False
    if name.startswith("__MACOSX/"):
        return False
    return not name.rsplit("/", 1)[-1].startswith("._")


class ReleaseError(ValueError):
    """A file that is not a readable release; `code` is the page's catalog suffix."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def documents(path: Path) -> Generator[bytes, None, None]:
    """The XML documents of a release ZIP in name order, or the one XML file."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                members = [
                    info
                    for info in archive.infolist()
                    if _release_member(info.filename)
                ]
                if sum(info.file_size for info in members) > MAX_TOTAL_BYTES:
                    raise ReleaseError("release_large")
                for info in sorted(members, key=lambda item: item.filename):
                    if info.file_size > MAX_MEMBER_BYTES:
                        raise ReleaseError("release_large")
                    yield archive.read(info)
        else:
            if path.stat().st_size > MAX_TOTAL_BYTES:
                raise ReleaseError("release_large")
            yield path.read_bytes()
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
) -> None:
    """Stage and validate all volumes and the report before replacing the work.

    Existing unrelated files and volumes absent from this input are retained.
    Legacy article files have no ownership receipt, so conflicting IDs are refused.
    """
    contents: dict[str, str] = {}
    ids: set[str] = set()
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
                _install_bundles(target, contents, ids)
        except FileBusyError:
            # Another window or process is installing into this folder; its
            # half-written state stays untouched and the page can say so with
            # the same catalog entry it uses while an import is running.
            raise ReleaseError("local_busy") from None


def _install_bundles(target: Path, contents: dict[str, str], ids: set[str]) -> None:
    backup = target.with_name(f".{target.name}.previous")
    if target.is_symlink() or backup.is_symlink():
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
    _check_article_files(target, ids)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=target.parent) as temp:
        staged = Path(temp) / target.name
        if target.exists():
            shutil.copytree(target, staged, symlinks=True)
        else:
            staged.mkdir()
        for name, content in contents.items():
            path = staged / name
            if path.is_symlink():
                raise ReleaseError("release_existing_conflict")
            if path.exists() and name.endswith(BUNDLE_SUFFIX):
                try:
                    previous = read_bundle(path)
                except InputError:
                    raise ReleaseError("release_existing_conflict") from None
                # A same-name bundle may receive a full replacement, a superset,
                # or a subset when the importer deliberately excludes an article.
                # A disjoint set is a different volume or split part and would drop
                # every earlier article, so refuse it.
                previous_ids = {text.text_id for text in previous}
                if previous_ids and not previous_ids & ids:
                    raise ReleaseError("release_existing_conflict")
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(content)
        seen: set[str] = set()
        for bundle in staged.glob("*" + BUNDLE_SUFFIX):
            try:
                texts = read_bundle(bundle)
            except InputError:
                if bundle.name in contents:
                    raise ReleaseError("release_unreadable") from None
                continue
            for text in texts:
                if text.text_id in seen:
                    raise ReleaseError("release_existing_conflict")
                seen.add(text.text_id)
        if not ids <= seen:
            raise ReleaseError("release_unreadable")
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


def import_release(path: Path, folder: Path) -> dict[str, object]:
    """Convert a release into folder; {book, articles, folder} for the status line."""
    with closing(documents(path)) as reader:
        first = next(reader, None)
        if first is None:
            raise ReleaseError("release_empty")
        root = parse(first)
        found: Iterable[bytes] = chain((first,), reader)
        if root.tag == "금위영등록":
            from cpe.aks import importer as aks

            return aks.import_documents(found, folder)
        if root.tag == "자료":
            from cpe.aks import jangyongyeong

            return jangyongyeong.import_documents(found, folder)
        if root.tag == "아이템":
            from cpe.itkc import release as itkc

            return itkc.import_documents(found, folder)
        if root.tag == "item" or re.match(r"level\d+$", root.tag):
            identity = root.get("id", "")
            if identity == "muds" or identity.startswith(
                ("muds_", "dh.d_", "dh.k_", "dh.m_", "jb.", "jo.")
            ):
                from cpe.local import titled_releases

                return titled_releases.import_documents(found, folder)
            from cpe.nikh import importer as nikh

            return nikh.import_documents(found, folder)
        raise ReleaseError("release_unknown")
