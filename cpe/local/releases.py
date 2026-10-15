"""Institute original-text releases (ZIP or XML files) converted into the text folder.

Two publishers' formats are read: the National Institute of Korean History
(cpe.nikh.importer) and the Institute for the Translation of Korean Classics
(cpe.itkc.release), both published on data.go.kr. The format is recognised from the
root element of the first XML document, so the window needs one import button.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from cpe.local.txt import BUNDLE_SUFFIX, MANIFEST_SUFFIX


class ReleaseError(ValueError):
    """A file that is not a readable release; `code` is the page's catalog suffix."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def documents(path: Path) -> list[bytes]:
    """The XML documents of a release ZIP in name order, or the one XML file."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = sorted(
                    n for n in archive.namelist() if n.lower().endswith(".xml")
                )
                return [archive.read(name) for name in names]
        return [path.read_bytes()]
    except (OSError, zipfile.BadZipFile):
        raise ReleaseError("release_unreadable") from None


def parse(document: bytes) -> ET.Element:
    try:
        # A DOCTYPE may name an external DTD (history.dtd), which is never fetched:
        # ElementTree does not load external DTDs or entities.
        return ET.fromstring(document)
    except ET.ParseError:
        raise ReleaseError("release_unreadable") from None


def save_bundles(
    target: Path, groups: dict[str, list[dict[str, Any]]], id_prefix: str
) -> None:
    """One text bundle per group (a volume) under target, each line one article.

    Tens of thousands of small files took minutes to read the first time (each new
    file is scanned); a bundle per volume keeps the count near the number of volumes.
    Each file's content is built before it is opened (opening empties a file).
    """
    target.mkdir(parents=True, exist_ok=True)
    _remove_article_files(target, id_prefix)
    for group, records in groups.items():
        content = "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        )
        name = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-") or "texts"
        with (target / f"{name}{BUNDLE_SUFFIX}").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            stream.write(content)


def _remove_article_files(target: Path, id_prefix: str) -> None:
    """Remove the one-file-per-article output of an earlier import into target.

    Only a .txt whose manifest names a text_id with this importer's prefix goes,
    with its manifest; left in place, those texts would repeat the bundles' ids and
    the folder would not open.
    """
    for manifest in target.glob("*.txt" + MANIFEST_SUFFIX):
        text = manifest.with_name(manifest.name.removesuffix(MANIFEST_SUFFIX))
        try:
            content = manifest.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if f'text_id = "{id_prefix}' in content and text.is_file():
            text.unlink()
            manifest.unlink()


def import_release(path: Path, folder: Path) -> dict[str, object]:
    """Convert a release into folder; {book, articles, folder} for the status line."""
    found = documents(path)
    if not found:
        raise ReleaseError("release_empty")
    root = parse(found[0])
    if root.tag == "아이템":
        from cpe.itkc import release as itkc

        return itkc.import_documents(found, folder)
    if root.tag == "item" or re.match(r"level\d+$", root.tag):
        from cpe.nikh import importer as nikh

        return nikh.import_documents(found, folder)
    raise ReleaseError("release_unknown")
