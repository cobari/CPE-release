"""Searches behind the GUI: a loaded library of local texts and the two search modes.

The GUI shows what the CSV shows (spec 11): marked segment, one sentence of
context each way, slot strings. Results are grouped as likely or less likely by
structural signals only (docs/decisions.md "Results that are clearly not the
structure are shown slightly lower"). Errors are returned as codes; the page turns
them into catalog text (AGENTS.md "Language").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cpe.export import csv_out
from cpe.itkc.query import QueryError
from cpe.itkc.query import parse as parse_query
from cpe.local.search import csv_row, normalize_query, search_text
from cpe.local.txt import (
    BUNDLE_SUFFIX,
    InputError,
    LocalText,
    read,
    read_bundle,
    text_options,
)
from cpe.match.dsl import DslError
from cpe.match.engine import (
    Settings,
    TextInput,
    assign_ids,
    match_text,
    sentence_delimiters,
)
from cpe.match.pair import pair_spec
from cpe.match.result import Result
from cpe.normalize.options import normalize
from cpe.normalize.text import NormalizedText

CLAUSE_MARKS = frozenset("，；：")
# The same marks in halfwidth form, for a text that declares them (spec 3.9).
HALFWIDTH_CLAUSE_MARKS = frozenset(",;:")
# Text files a folder contributes, searched in all its subfolders (Excel and other
# formats are not read: researcher decision).
TEXT_SUFFIXES = (".txt", ".md")
# Signals that put a result in the less-likely group; the page shows each as a label.
ADJACENT = "adjacent"
INDETERMINATE = "indeterminate"
SKIPS_NEARER = "skips_nearer"
CROSSES_CLAUSE = "crosses_clause"


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

    def matcher_input(self) -> TextInput:
        local = self.local
        return TextInput(
            local.text_id,
            self.normalized,
            local.range,
            local.boundary_start,
            local.boundary_end,
            local.punctuation,
            local.full_stop,
            local.halfwidth_punctuation,
        )


class Library:
    """The texts one GUI session searches, read once at start."""

    def __init__(
        self,
        texts: Sequence[Text],
        settings: Settings,
        skipped: Sequence[str] = (),
    ) -> None:
        self.texts = list(texts)
        self.settings = settings
        # Files of an opened folder that could not be read (not UTF-8, bad manifest).
        self.skipped = list(skipped)

    @classmethod
    def load(cls, paths: Iterable[Path], settings: Settings | None = None) -> Library:
        """Explicit files are read strictly; folders leniently (see `skipped`)."""
        settings = settings or Settings()
        texts: list[Text] = []
        skipped: list[str] = []
        for path in paths:
            if not path.is_dir():
                texts.extend(text_from(local, settings) for local in _read_file(path))
                continue
            # One unreadable file in a folder is reported and skipped instead of
            # keeping the whole folder closed.
            for file in _folder_files(path):
                relative = file.relative_to(path).as_posix()
                try:
                    found: list[Text] = []
                    for local in _read_file(file):
                        if not local.id_from_manifest:
                            # Without a manifest id, the path inside the folder names
                            # the text, so equal file names in two subfolders stay
                            # distinct.
                            local = replace(local, text_id=relative)
                        found.append(text_from(local, settings))
                    texts.extend(found)
                except InputError:
                    skipped.append(relative)
        ids = [text.local.text_id for text in texts]
        duplicates = sorted(text_id for text_id, n in Counter(ids).items() if n > 1)
        if duplicates:
            raise InputError(f"duplicate text_id: {', '.join(duplicates)}")
        return cls(texts, settings, skipped)

    def describe(self) -> dict[str, Any]:
        return {
            "texts": [
                {"text_id": text.local.text_id, "title": text.title}
                for text in self.texts
            ],
            "skipped": self.skipped,
        }


def _read_file(path: Path) -> list[LocalText]:
    """One text from a .txt or .md file, or every text of a bundle (spec 7.1)."""
    if path.name.lower().endswith(BUNDLE_SUFFIX):
        return read_bundle(path)
    return [read(path)]


def _folder_files(folder: Path) -> list[Path]:
    """The .txt and .md files and text bundles under a folder in path order; hidden
    folders left out."""
    return sorted(
        file
        for file in folder.rglob("*")
        if file.is_file()
        and (
            file.suffix.lower() in TEXT_SUFFIXES
            or file.name.lower().endswith(BUNDLE_SUFFIX)
        )
        and not any(part.startswith(".") for part in file.relative_to(folder).parts)
    )


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


def search_pair(library: Library, first: str, second: str) -> dict[str, Any]:
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
    found = [
        (index, 0, result)
        for index, text in enumerate(library.texts)
        for result in match_text(spec, text.matcher_input(), library.settings)
    ]
    results = assign_ids(found)
    paired: dict[tuple[str, int, int], set[tuple[int, int]]] = {}
    for result in results:
        key = (result.text_id, result.segment.start, result.segment.end)
        paired.setdefault(key, set()).update(
            (hit.src.start, hit.src.end) for hit in result.fixed
        )
    by_id = {text.local.text_id: text for text in library.texts}
    likely: list[dict[str, Any]] = []
    less: list[dict[str, Any]] = []
    for result in results:
        text = by_id[result.text_id]
        key = (result.text_id, result.segment.start, result.segment.end)
        row = _pair_row(result, text, frozenset(paired[key]), seconds)
        (less if row["reasons"] else likely).append(row)
    return {"mode": "pair", "likely": likely, "less": less}


def _pair_row(
    result: Result,
    text: Text,
    paired: frozenset[tuple[int, int]],
    seconds: set[str],
) -> dict[str, Any]:
    view = text.view
    before, after = csv_out.context(
        view,
        result.segment.start,
        result.segment.end,
        result.segment_src.start,
        result.segment_src.end,
    )
    reasons = []
    if result.kind == "adjacent":
        reasons.append(ADJACENT)
    if result.kind == "indeterminate":
        reasons.append(INDETERMINATE)
    a_shown = ""
    if result.slots:
        slot = result.slots[0]
        a_shown = csv_out.shown(
            view.reference, slot.src.start, slot.src.end, view.hidden
        )
        a_norm = text.normalized.text[slot.start : slot.end]
        if any(literal in a_norm for literal in seconds):
            reasons.append(SKIPS_NEARER)
        marks = CLAUSE_MARKS | (
            HALFWIDTH_CLAUSE_MARKS if text.local.halfwidth_punctuation else frozenset()
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
        "segment": csv_out.segment_display(result, view.reference, paired, view.hidden),
        "after": after,
        "a": a_shown,
        "b": b_shown,
        "reasons": reasons,
    }


def search_words(library: Library, query: str) -> dict[str, Any]:
    """One-box word search (spec 14.2): every sentence that satisfies the query."""
    query = query.strip()
    if not query:
        return {"error": "empty"}
    try:
        tree = normalize_query(parse_query(query), library.settings.normalization)
    except QueryError:
        return {"error": "bad_query"}
    records = [
        record
        for text in library.texts
        for record in search_text(
            tree, query, text.local, text.normalized, library.settings, "sentence"
        )
    ]
    views = {text.local.text_id: text.view for text in library.texts}
    links = {text.local.text_id: text.link for text in library.texts}
    eras = {text.local.text_id: text.era for text in library.texts}
    titles = {text.local.text_id: text.title for text in library.texts}
    rows = []
    for rank, record in enumerate(records, start=1):
        record["result_id"] = f"r{rank:06d}"
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
    return {"mode": "words", "likely": rows, "less": []}
