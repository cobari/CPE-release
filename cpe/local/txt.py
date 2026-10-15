"""Read local TXT inputs and their manifests (spec 1.3 and 7.1)."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar

from cpe.match.segment import FullStop, Punctuation, Range, TextEdge
from cpe.normalize.options import NormalizationError, Options

MANIFEST_SUFFIX = ".manifest.toml"
# A file holding many texts, one JSON object per line (spec 7.1 "Text bundles").
BUNDLE_SUFFIX = ".texts.jsonl"
_RANGES: tuple[Range, ...] = ("whole_work", "volume", "excerpt")
_TEXT_EDGES: tuple[TextEdge, ...] = ("verified", "truncated")
_PUNCTUATIONS: tuple[Punctuation, ...] = ("modern", "pause")
_FULL_STOPS: tuple[FullStop, ...] = ("sentence", "reading")
T = TypeVar("T", bound=str)
_BOM = "\ufeff"


class InputError(ValueError):
    """An input file or manifest that cannot be used as evidence."""


@dataclass(frozen=True)
class LocalText:
    path: Path
    text_id: str
    reference: str
    range: Range
    boundary_start: TextEdge
    boundary_end: TextEdge
    punctuation: Punctuation
    full_stop: FullStop
    text_type: str
    title: str
    source: str
    edition: str
    sha256: str
    bom: bool
    # When the text dates from, as the researcher records it in the manifest:
    # an era name and the first and last years (negative before the common era).
    era: str = ""
    year: int | None = None
    year_end: int | None = None
    # Interlinear-note markers this text itself uses (spec 7.1); they replace the
    # run's markers for this text only, so a converted release keeps its notes
    # as barriers whatever the run sets.
    note_open: str | None = None
    note_close: str | None = None
    # Halfwidth . ? ! , ; : are punctuation in this text (spec 3.9).
    halfwidth_punctuation: bool = False
    # Whether the manifest sets text_id itself; comparing the id with the file stem
    # would misjudge a manifest that sets the id to exactly the stem.
    id_from_manifest: bool = False


def read(path: Path) -> LocalText:
    """Decode strictly with line endings kept; never replace undecodable bytes.

    A leading byte-order mark is dropped from the reference text and reported, so
    offsets count from the first character of the text itself.
    """
    data = path.read_bytes()
    try:
        # newline="" semantics: bytes.decode keeps CR and CRLF as they are.
        reference = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise InputError(f"{path}: not valid UTF-8 ({error})") from None
    bom = reference.startswith(_BOM)
    if bom:
        reference = reference[1:]
    return _local_text(
        path, reference, hashlib.sha256(data).hexdigest(), bom, _manifest(path)
    )


def read_bundle(path: Path) -> list[LocalText]:
    """The texts of a bundle: each line is one text's manifest keys plus `text`.

    Every line needs its own text_id; one bad line rejects the whole file, so a
    bundle is never half read.
    """
    try:
        lines = path.read_bytes().decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise InputError(f"{path}: not valid UTF-8 ({error})") from None
    texts = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = Path(f"{path}:{number}")
        try:
            entry = json.loads(line)
        except ValueError as error:
            raise InputError(f"{where}: not a JSON object ({error})") from None
        if not isinstance(entry, dict):
            raise InputError(f"{where}: not a JSON object")
        reference = entry.pop("text", None)
        if not isinstance(reference, str):
            raise InputError(f"{where}: text must be a string")
        if "text_id" not in entry:
            raise InputError(f"{where}: a bundle entry needs a text_id")
        digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
        texts.append(_local_text(path, reference, digest, False, entry, where))
    return texts


def _local_text(
    path: Path,
    reference: str,
    sha256: str,
    bom: bool,
    manifest: dict[str, object],
    where: Path | None = None,
) -> LocalText:
    """A text with its manifest keys checked; `where` (a bundle line) names it in
    errors instead of the file."""
    label = where or path
    range_ = _choice(manifest, "range", _RANGES, "excerpt", label)
    start = _choice(manifest, "boundary_start", _TEXT_EDGES, "truncated", label)
    end = _choice(manifest, "boundary_end", _TEXT_EDGES, "truncated", label)
    punctuation = _choice(manifest, "punctuation", _PUNCTUATIONS, "modern", label)
    full_stop = _choice(manifest, "full_stop", _FULL_STOPS, "sentence", label)
    text_id = _string(manifest, "text_id", path.stem, label)
    if not text_id:
        raise InputError(f"{label}: manifest text_id must not be empty")
    return LocalText(
        path=path,
        text_id=text_id,
        reference=reference,
        range=range_,
        boundary_start=start,
        boundary_end=end,
        punctuation=punctuation,
        full_stop=full_stop,
        text_type=_string(manifest, "text_type", "original", label),
        title=_string(manifest, "title", "", label),
        source=_string(manifest, "source", "", label),
        edition=_string(manifest, "edition", "", label),
        sha256=sha256,
        bom=bom,
        era=_string(manifest, "era", "", label),
        year=_year(manifest, "year", label),
        year_end=_year(manifest, "year_end", label),
        note_open=_marker(manifest, "note_open", label),
        note_close=_marker(manifest, "note_close", label),
        halfwidth_punctuation=_flag(manifest, "halfwidth_punctuation", label),
        id_from_manifest="text_id" in manifest,
    )


def _string(manifest: dict[str, object], key: str, default: str, path: Path) -> str:
    value = manifest.get(key, default)
    # Converting a wrong type with str() would record e.g. "['A', 'B']" as a
    # title: metadata the researcher never wrote.
    if not isinstance(value, str):
        raise InputError(f"{path}: manifest {key} must be a string: {value!r}")
    return value


def _flag(manifest: dict[str, object], key: str, path: Path) -> bool:
    value = manifest.get(key, False)
    if not isinstance(value, bool):
        raise InputError(f"{path}: manifest {key} must be true or false: {value!r}")
    return value


def _marker(manifest: dict[str, object], key: str, path: Path) -> str | None:
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InputError(
            f"{path}: manifest {key} must be a non-empty string: {value!r}"
        )
    return value


def text_options(text: LocalText, options: Options) -> Options:
    """The run's normalization, with the note markers this text declares, if any."""
    if text.note_open is None and text.note_close is None:
        return options
    if text.note_open is None or text.note_close is None:
        raise InputError(f"{text.path}: manifest needs both note_open and note_close")
    try:
        return replace(options, note_open=text.note_open, note_close=text.note_close)
    except NormalizationError as error:
        raise InputError(f"{text.path}: {error}") from None


def _year(manifest: dict[str, object], key: str, path: Path) -> int | None:
    value = manifest.get(key)
    if value is None:
        return None
    # bool is an int subclass: `year = true` must not read as the year 1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise InputError(f"{path}: manifest {key} must be an integer year: {value!r}")
    return value


def _choice(
    manifest: dict[str, object],
    key: str,
    allowed: tuple[T, ...],
    default: T,
    path: Path,
) -> T:
    value = manifest.get(key, default)
    for option in allowed:
        if value == option:
            return option
    raise InputError(f"{path}: manifest {key} must be one of {allowed}: {value!r}")


def manifest_path(path: Path) -> Path:
    return path.with_name(path.name + MANIFEST_SUFFIX)


def _manifest(path: Path) -> dict[str, object]:
    sidecar = manifest_path(path)
    if not sidecar.exists():
        return {}
    try:
        with sidecar.open("rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise InputError(f"{sidecar}: not a valid TOML manifest ({error})") from None
