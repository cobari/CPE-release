"""Searches behind the GUI: a loaded library of local texts and the two search modes.

The GUI shows what the CSV shows (spec 11): marked segment, one sentence of
context each way, slot strings. Results are grouped as likely or less likely by
structural signals only (docs/decisions.md "Results that are clearly not the
structure are shown slightly lower"). Errors are returned as codes; the page turns
them into catalog text (AGENTS.md "Language").
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cpe.cancellation import Checkpoint
from cpe.export import csv_out, jsonl
from cpe.itkc.query import QueryError
from cpe.itkc.query import parse as parse_query
from cpe.local.paths import is_directory_link, is_within
from cpe.local.search import csv_row, normalize_query, search_text
from cpe.local.txt import (
    BUNDLE_SUFFIX,
    InputError,
    LocalText,
    manifest_path,
    parse_bundle,
    parse_manifest_bytes,
    parse_text_bytes,
    read,
    read_bundle,
    read_manifest_bytes,
    text_options,
)
from cpe.match.dsl import DslError
from cpe.match.engine import (
    Settings,
    TextInput,
    assign_ids,
    check_variants,
    clause_delimiters,
    match_text,
    prepare_pattern,
    sentence_delimiters,
)
from cpe.match.pair import pair_spec
from cpe.match.pattern_file import PatternFileError
from cpe.match.result import Result
from cpe.normalize.options import NormalizationError, normalize
from cpe.normalize.text import NormalizedText

_ORIG_READ = read
_ORIG_READ_BUNDLE = read_bundle
_INCREMENTAL_STATE = threading.local()


_HASH_CHUNK = 64 * 1024


def _sha256_stream(path: Path, *, required: bool = False) -> str | None:
    """SHA-256 of `path` read in bounded chunks; None when it is absent.

    With `required`, an absent file raises `OSError` instead (a main text file
    that vanished is unreadable, not "no manifest").
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
    except FileNotFoundError:
        if required:
            raise
        return None
    return digest.hexdigest()


@contextmanager
def incremental_previous(previous: Library | None) -> Iterator[None]:
    """Supply a prior `Library` snapshot for `Library.load` on the current thread."""
    stack: list[Library | None] | None = getattr(
        _INCREMENTAL_STATE, "previous_stack", None
    )
    if stack is None:
        stack = []
        _INCREMENTAL_STATE.previous_stack = stack
    stack.append(previous)
    try:
        yield
    finally:
        stack.pop()


# Text files a folder contributes, searched in all its subfolders (Excel and other
# formats are not read: researcher decision).
TEXT_SUFFIXES = (".txt", ".md")
# Signals that put a result in the less-likely group; the page shows each as a label.
ADJACENT = "adjacent"
INDETERMINATE = "indeterminate"
STATE_BUDGET = "state_budget"
RESULT_BUDGET = "result_budget"
SKIPS_NEARER = "skips_nearer"
CROSSES_CLAUSE = "crosses_clause"

_BUDGET_REASON_CODES = {
    "state_budget": STATE_BUDGET,
    "result_budget": RESULT_BUDGET,
}


def indeterminate_reason_code(result: Result) -> str:
    """Map an indeterminate match record to its GUI reason key."""
    return _BUDGET_REASON_CODES.get(result.indeterminate_reason or "", INDETERMINATE)


@dataclass(frozen=True)
class Text:
    local: LocalText
    normalized: NormalizedText
    view: csv_out.TextView
    # When the text dates from, as its source records it (see cpe.itkc.adapter).
    era: dict[str, Any] | None = None

    @property
    def title(self) -> str:
        return self.local.title or self.local.text_id

    @property
    def link(self) -> str:
        """The source's web address (manifest `source` or a DB record), if it is one."""
        source = self.local.source
        return source if source.startswith(("https://", "http://")) else ""

    def matcher_input(self, settings: Settings | None = None) -> TextInput:
        local = self.local
        result = TextInput(
            local.text_id,
            self.normalized,
            local.range,
            local.boundary_start,
            local.boundary_end,
            local.punctuation,
            local.full_stop,
            local.halfwidth_punctuation,
        )
        if settings is not None:
            delimiters = sentence_delimiters(
                settings, local.full_stop, local.halfwidth_punctuation
            )
            result._segments[delimiters] = self.view.sentences
        return result


class Library:
    """One independently searched local folder or explicit set of text inputs."""

    def __init__(
        self,
        texts: Sequence[Text],
        settings: Settings,
        skipped: Sequence[str] = (),
        *,
        _file_cache: (
            dict[tuple[Path, str], tuple[str, str | None, tuple[Text, ...]]] | None
        ) = None,
    ) -> None:
        self.texts = list(texts)
        self.settings = settings
        # A variant table must not touch a delimiter of any loaded text, whatever
        # the path that built this library.
        check_variants(
            settings,
            (
                (
                    text.local.punctuation,
                    text.local.full_stop,
                    text.local.halfwidth_punctuation,
                )
                for text in self.texts
            ),
        )
        # Files and subfolders of an opened folder that could not be read (not UTF-8,
        # bad manifest, permission).
        self.skipped = list(skipped)
        self._file_cache: dict[
            tuple[Path, str], tuple[str, str | None, tuple[Text, ...]]
        ] = dict(_file_cache or {})
        # Built once by the GUI exporter; a Library is a loaded snapshot and is
        # replaced rather than mutated when settings or files change.
        self._export_provenance: dict[str, Any] | None = None

    @classmethod
    def load_incremental(
        cls,
        paths: Iterable[Path],
        settings: Settings | None = None,
        *,
        previous: Library | None = None,
    ) -> Library:
        """Load `paths`, reusing unchanged folder entries from `previous`."""
        with incremental_previous(previous):
            return cls.load(paths, settings)

    @classmethod
    def load(
        cls,
        paths: Iterable[Path],
        settings: Settings | None = None,
    ) -> Library:
        """Explicit files are read strictly; folders leniently (see `skipped`).

        When invoked inside `incremental_previous(previous)` with matching
        normalization and sentence-delimiter settings, unchanged files reuse
        cached `Text` objects.
        """
        settings = settings or Settings()
        stack: list[Library | None] = getattr(_INCREMENTAL_STATE, "previous_stack", [])
        previous = stack[-1] if stack else None
        reuse_cache: dict[
            tuple[Path, str], tuple[str, str | None, tuple[Text, ...]]
        ] = {}
        if (
            previous is not None
            and previous.settings.normalization == settings.normalization
            and previous.settings.sentence_delimiters == settings.sentence_delimiters
        ):
            reuse_cache = previous._file_cache
        file_cache: dict[
            tuple[Path, str], tuple[str, str | None, tuple[Text, ...]]
        ] = {}
        use_byte_cache = read is _ORIG_READ and read_bundle is _ORIG_READ_BUNDLE
        texts: list[Text] = []
        skipped: list[str] = []
        for path in paths:
            if not path.is_dir():
                texts.extend(text_from(local, settings) for local in _read_file(path))
                continue
            # One unreadable or unparsable file, or one unlistable subfolder, in a
            # folder is reported and skipped instead of keeping the whole folder
            # closed.
            files, unreadable = _folder_files(path)
            skipped.extend(unreadable)
            for file in files:
                relative = file.relative_to(path).as_posix()
                try:
                    if not use_byte_cache:
                        found: list[Text] = []
                        for local in _read_file(file):
                            if not local.id_from_manifest:
                                local = replace(local, text_id=relative)
                            found.append(text_from(local, settings))
                        texts.extend(found)
                        continue
                    is_bundle = file.name.lower().endswith(BUNDLE_SUFFIX)
                    cache_key = (file, relative)
                    cached = reuse_cache.get(cache_key)
                    if cached is not None:
                        # Hash by bounded chunks so an unchanged large file is never
                        # held in memory just to prove it is unchanged.
                        file_sha = _sha256_stream(file, required=True)
                        manifest_sha = (
                            None if is_bundle else _sha256_stream(manifest_path(file))
                        )
                        if cached[0] == file_sha and cached[1] == manifest_sha:
                            texts.extend(cached[2])
                            file_cache[cache_key] = cached
                            continue
                    # The cache records the hash of the bytes actually parsed, not of
                    # the probe: a file edited between probe and read would otherwise
                    # be cached under a hash of content that was never parsed.
                    raw_bytes = file.read_bytes()
                    manifest_bytes = None if is_bundle else read_manifest_bytes(file)
                    file_sha = hashlib.sha256(raw_bytes).hexdigest()
                    manifest_sha = (
                        None
                        if manifest_bytes is None
                        else hashlib.sha256(manifest_bytes).hexdigest()
                    )
                    local_texts = (
                        parse_bundle(file, raw_bytes)
                        if is_bundle
                        else [
                            parse_text_bytes(
                                file,
                                raw_bytes,
                                parse_manifest_bytes(
                                    manifest_path(file), manifest_bytes
                                ),
                            )
                        ]
                    )
                    built: list[Text] = []
                    for local in local_texts:
                        if not local.id_from_manifest:
                            # Without a manifest id, the path inside the folder names
                            # the text, so equal file names in two subfolders stay
                            # distinct.
                            local = replace(local, text_id=relative)
                        built.append(text_from(local, settings))
                    built_tuple = tuple(built)
                    texts.extend(built_tuple)
                    file_cache[cache_key] = (file_sha, manifest_sha, built_tuple)
                except (InputError, NormalizationError, OSError):
                    skipped.append(relative)
        ids = [text.local.text_id for text in texts]
        duplicates = sorted(text_id for text_id, n in Counter(ids).items() if n > 1)
        if duplicates:
            raise InputError(f"duplicate text_id: {', '.join(duplicates)}")
        return cls(texts, settings, skipped, _file_cache=file_cache)

    def describe(self) -> dict[str, Any]:
        """The full text list, for callers that need titles or ids.

        The window asks for `summary()` instead: sending every title over the
        bridge costs milliseconds per thousand texts and the page only needs counts.
        """
        return {
            "texts": [
                {"text_id": text.local.text_id, "title": text.title}
                for text in self.texts
            ],
            "skipped": self.skipped,
        }

    def summary(self) -> dict[str, Any]:
        """Counts and only the skipped paths the window can show.

        The page prints at most five skipped names; sending the whole list would
        make a folder with many unreadable files as expensive as the old describe().
        """
        return {
            "text_count": len(self.texts),
            "skipped": self.skipped[:5],
            "skipped_count": len(self.skipped),
        }


def _read_file(path: Path) -> list[LocalText]:
    """One text from a .txt or .md file, or every text of a bundle (spec 7.1)."""
    if path.name.lower().endswith(BUNDLE_SUFFIX):
        return read_bundle(path)
    return [read(path)]


def _folder_files(folder: Path) -> tuple[list[Path], list[str]]:
    """The .txt and .md files and text bundles under a folder in path order, plus the
    relative paths of subfolders that could not be listed.

    Hidden components are left out. Directory symlinks and Windows junctions are
    not followed: a loop through one would
    scan until it failed rather than finish, and a link pointing outside the
    folder would load texts the researcher did not open. The resolved path is
    also checked against the opened root, so another outside-root reparse form is
    skipped even when the link-type check misses it. A subfolder that cannot be
    listed, or an entry that disappears while the scan runs, is reported instead
    of aborting the whole folder; `Library.load` records its path in `skipped`.
    Unlike directory links, readable file symlinks remain permitted in GUI
    `Library.load`, whereas MCP enforces `PathPolicy.check_library`.
    """
    files: list[Path] = []
    unreadable: list[str] = []
    try:
        root_resolved = folder.resolve()
    except (OSError, RuntimeError):
        root_resolved = folder.absolute()
    pending = [folder]
    while pending:
        directory = pending.pop()
        try:
            # scandir returns each entry's type with the listing (and, on Windows,
            # its stat data), so a large or network folder is not stat-ed per test.
            with os.scandir(directory) as listing:
                entries = list(listing)
        except OSError:
            unreadable.append(directory.relative_to(folder).as_posix())
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            path = Path(entry.path)
            try:
                # Follows symlinks, so a broken link is reported as unreadable.
                entry.stat()
            except OSError:
                unreadable.append(path.relative_to(folder).as_posix())
                continue
            if entry.is_dir():
                # `entry.stat()` above already reported a broken link as
                # unreadable. A live directory link or an outside-root path is
                # skipped silently, like the documented directory-symlink rule.
                if is_directory_link(
                    path, entry.stat(follow_symlinks=False)
                ) or not is_within(root_resolved, path):
                    continue
                pending.append(path)
            elif entry.is_file() and (
                path.suffix.lower() in TEXT_SUFFIXES
                or entry.name.lower().endswith(BUNDLE_SUFFIX)
            ):
                files.append(path)
    return sorted(files), sorted(unreadable)


def text_from(
    local: LocalText, settings: Settings, era: dict[str, Any] | None = None
) -> Text:
    """A searchable text: normalized, with its sentences and hidden spans."""
    if local.text_type != "original":
        # Spec 3.6: only original text is matched in MVP.
        raise InputError(f"{local.path}: text_type {local.text_type!r} not matched")
    normalized = normalize(local.reference, text_options(local, settings.normalization))
    view = csv_out.text_view(
        local.reference,
        normalized,
        sentence_delimiters(settings, local.full_stop, local.halfwidth_punctuation),
        local.boundary_start,
        local.boundary_end,
    )
    if era is None and (local.era or local.year is not None):
        # A local text's era comes from its manifest (docs/spec/m0-spec.md 7.1).
        era = {
            "kind": "manifest",
            "label": local.era,
            "from": local.year,
            "to": local.year_end,
            "year": local.year,
        }
    return Text(local, normalized, view, era)


def search_pair(
    library: Library,
    first: str,
    second: str,
    *,
    checkpoint: Checkpoint = None,
    include_records: bool = False,
) -> dict[str, Any]:
    """Two-box search (spec 14.1): FIRST {A} SECOND {B}, FIRST always first."""
    first, second = first.strip(), second.strip()
    if not first or not second:
        return {"error": "empty"}
    try:
        spec = pair_spec(first, second)
    except DslError:
        return {"error": "bad_box"}
    options = library.settings.normalization
    seconds = {options.literal(option) for option in spec.pattern.fixed[1].options}
    try:
        prepared = prepare_pattern(spec, options) if library.texts else None
        found = [
            (index, 0, result)
            for index, text in enumerate(library.texts)
            for result in match_text(
                spec,
                text.matcher_input(library.settings),
                library.settings,
                checkpoint=checkpoint,
                prepared=prepared,
            )
        ]
    except PatternFileError:
        # A box that normalization empties, or two alternatives it makes equal,
        # cannot be searched; it is a bad box, not an internal failure (14.1).
        return {"error": "bad_box"}
    results = assign_ids(found)
    paired: dict[tuple[str, int, int], set[tuple[int, int]]] = {}
    for result in results:
        key = (result.text_id, result.segment.start, result.segment.end)
        paired.setdefault(key, set()).update(
            (hit.src.start, hit.src.end) for hit in result.fixed
        )
    paired_sets = {key: frozenset(spans) for key, spans in paired.items()}
    by_id = {text.local.text_id: text for text in library.texts}
    rendered: dict[tuple[str, int, int], tuple[str, str, str]] = {}
    likely: list[dict[str, Any]] = []
    less: list[dict[str, Any]] = []
    for result in results:
        if checkpoint:
            checkpoint()
        text = by_id[result.text_id]
        key = (result.text_id, result.segment.start, result.segment.end)
        if key not in rendered:
            view = text.view
            before, after = csv_out.context(
                view,
                result.segment.start,
                result.segment.end,
                result.segment_src.start,
                result.segment_src.end,
            )
            rendered[key] = (
                before,
                csv_out.segment_display(
                    result, view.reference, paired_sets[key], view.hidden
                ),
                after,
            )
        row = _pair_row(
            result,
            text,
            paired_sets[key],
            seconds,
            library.settings,
            rendered=rendered[key],
        )
        if include_records:
            row["record"] = jsonl.to_record(result)
        (less if row["reasons"] else likely).append(row)
    return {"mode": "pair", "likely": likely, "less": less}


def _pair_row(
    result: Result,
    text: Text,
    paired: frozenset[tuple[int, int]],
    seconds: set[str],
    settings: Settings,
    *,
    rendered: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    view = text.view
    if rendered is None:
        before, after = csv_out.context(
            view,
            result.segment.start,
            result.segment.end,
            result.segment_src.start,
            result.segment_src.end,
        )
        segment_shown = csv_out.segment_display(
            result, view.reference, paired, view.hidden
        )
    else:
        before, segment_shown, after = rendered
    reasons = []
    if result.kind == ADJACENT:
        reasons.append(ADJACENT)
    elif result.kind == INDETERMINATE:
        reasons.append(indeterminate_reason_code(result))
    a_shown = ""
    if result.slots:
        slot = result.slots[0]
        a_shown = csv_out.shown(
            view.reference, slot.src.start, slot.src.end, view.hidden
        )
        a_norm = text.normalized.text[slot.start : slot.end]
        if any(literal in a_norm for literal in seconds):
            reasons.append(SKIPS_NEARER)
        marks = clause_delimiters(
            settings, text.local.punctuation, text.local.halfwidth_punctuation
        )
        if any(mark in a_norm for mark in marks):
            reasons.append(CROSSES_CLAUSE)
    b_shown: list[str] = []
    if result.open_slot is not None:
        b = result.open_slot.b_start.src
        b_shown = [
            csv_out.shown(view.reference, b, rep.end.src, view.hidden)
            for rep in result.open_slot.representatives
        ]
    return {
        "id": result.result_id,
        "text_id": result.text_id,
        "title": text.title,
        "link": text.link,
        "era": text.era,
        "kind": result.kind,
        "before": before,
        "segment": segment_shown,
        "after": after,
        "a": a_shown,
        "b": b_shown,
        "reasons": reasons,
    }


def search_words(
    library: Library,
    query: str,
    *,
    checkpoint: Checkpoint = None,
    include_records: bool = False,
) -> dict[str, Any]:
    """One-box word search (spec 14.2): every sentence that satisfies the query."""
    query = query.strip()
    if not query:
        return {"error": "empty"}
    try:
        tree = normalize_query(parse_query(query), library.settings.normalization)
    except QueryError:
        return {"error": "bad_query"}
    views = {text.local.text_id: text.view for text in library.texts}
    links = {text.local.text_id: text.link for text in library.texts}
    eras = {text.local.text_id: text.era for text in library.texts}
    titles = {text.local.text_id: text.title for text in library.texts}
    rows: list[dict[str, Any]] = []
    # Convert one text's records before searching the next, so record metadata
    # for the entire library is not retained beside the finished display rows.
    try:
        for text in library.texts:
            if checkpoint:
                checkpoint()
            for record in search_text(
                tree,
                query,
                text.local,
                text.normalized,
                library.settings,
                "sentence",
                checkpoint=checkpoint,
                segments=text.view.sentences,
            ):
                record["result_id"] = f"r{len(rows) + 1:06d}"
                row = csv_row(record, views, titles, "", "")
                rows.append(
                    {
                        "id": record["result_id"],
                        "text_id": record["text_id"],
                        "title": titles[record["text_id"]],
                        "link": links[record["text_id"]],
                        "era": eras[record["text_id"]],
                        "kind": "occurrence",
                        "before": row["context_before"],
                        "segment": row["segment"],
                        "after": row["context_after"],
                        "a": "",
                        "b": [],
                        "reasons": [],
                    }
                )
                if include_records:
                    rows[-1]["record"] = record
    except QueryError:
        return {"error": "query_limit"}
    return {"mode": "words", "likely": rows, "less": []}
