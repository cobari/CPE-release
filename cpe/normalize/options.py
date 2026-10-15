"""Normalization options and the normalization pass (spec 2.3 to 2.8)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cpe.normalize.text import NormalizedText

KeepOrDelete = Literal["keep", "delete"]
NoteMode = Literal["barrier", "join"]

LINE_BREAKS = frozenset("\r\n")
WHITESPACE = frozenset(" \t" + chr(0x3000))
_SELECTOR_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))


class NormalizationError(ValueError):
    """Invalid normalization settings, variant table, or note markup."""


def is_selector(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _SELECTOR_RANGES)


def deletable(char: str) -> bool:
    """Whether any deletion option could remove the code point (spec 2.7)."""
    return char in LINE_BREAKS or char in WHITESPACE or is_selector(char)


@dataclass(frozen=True)
class VariantTable:
    id: str
    version: str
    map: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, value in self.map.items():
            if not isinstance(key, str) or len(key) != 1:
                raise NormalizationError(f"variant key must be one code point: {key!r}")
            if not isinstance(value, str) or not value:
                raise NormalizationError(f"variant value must be non-empty: {key!r}")
            if value == key:
                raise NormalizationError(f"variant maps {key!r} to itself")
            if any(deletable(char) for char in value):
                raise NormalizationError(
                    f"variant value for {key!r} contains a deletable code point"
                )


def variant_table(
    id_: object, version: object, mapping: dict[str, Any]
) -> VariantTable:
    if (
        not isinstance(id_, str)
        or not isinstance(version, str)
        or not id_
        or not version
    ):
        raise NormalizationError(
            "variant table id and version must be non-empty strings"
        )
    return VariantTable(id_, version, mapping)


def load_variant_table(path: Path) -> VariantTable:
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise NormalizationError(f"{path}: not a UTF-8 TOML file ({error})") from None
    if data.get("schema") != 1:
        raise NormalizationError(f"{path}: expected 'schema = 1'")
    mapping = data.get("map", {})
    if not isinstance(mapping, dict):
        raise NormalizationError(f"{path}: [map] must be a table")
    return variant_table(data.get("id"), data.get("version"), dict(mapping))


@dataclass(frozen=True)
class Options:
    variation_selectors: KeepOrDelete = "keep"
    whitespace: KeepOrDelete = "keep"
    # Line breaks in TXT files are layout, not text (researcher decision):
    # deleted by default so they neither count toward lengths nor split terms.
    line_breaks: KeepOrDelete = "delete"
    notes: NoteMode = "barrier"
    note_open: str | None = None
    note_close: str | None = None
    variants: VariantTable | None = None
    # Attached annotations (spec 2.9): removed, never a barrier.
    annotation_open: str | None = None
    annotation_close: str | None = None

    def __post_init__(self) -> None:
        for name in ("variation_selectors", "whitespace", "line_breaks"):
            if getattr(self, name) not in ("keep", "delete"):
                raise NormalizationError(f"{name} must be 'keep' or 'delete'")
        if self.notes not in ("barrier", "join"):
            raise NormalizationError("notes must be 'barrier' or 'join'")
        pairs = {
            "note": (self.note_open, self.note_close),
            "annotation": (self.annotation_open, self.annotation_close),
        }
        for kind, markers in pairs.items():
            if (markers[0] is None) != (markers[1] is None):
                raise NormalizationError(
                    f"{kind} opening and closing markers go together"
                )
            if markers[0] is not None:
                if not all(isinstance(m, str) and m for m in markers):
                    raise NormalizationError(
                        f"{kind} markers must be non-empty strings"
                    )
                if markers[0] == markers[1]:
                    raise NormalizationError(f"{kind} markers must differ")
        given = [m for markers in pairs.values() for m in markers if m is not None]
        if len(set(given)) != len(given):
            raise NormalizationError("note and annotation markers must all differ")

    @property
    def name(self) -> str:
        """Short label for CSV `normalization` (plan 9)."""
        parts = []
        if self.variants is not None:
            parts.append(f"{self.variants.id}-{self.variants.version}")
        for label, value in (
            ("vs", self.variation_selectors),
            ("ws", self.whitespace),
            ("lb", self.line_breaks),
        ):
            if value == "delete":
                parts.append(f"{label}-delete")
        if self.note_open is not None:
            parts.append(f"notes-{self.notes}")
        if self.annotation_open is not None:
            parts.append("annotations")
        return "+".join(parts) or "identity"

    def code_point(self, char: str) -> str:
        """The per-code-point steps of spec 2.6, in order; "" means deleted."""
        if self.line_breaks == "delete" and char in LINE_BREAKS:
            return ""
        if self.whitespace == "delete" and char in WHITESPACE:
            return ""
        if self.variation_selectors == "delete" and is_selector(char):
            return ""
        if self.variants is not None:
            return self.variants.map.get(char, char)
        return char

    def literal(self, literal: str) -> str:
        return "".join(self.code_point(char) for char in literal)


def options_from_mapping(raw: dict[str, Any]) -> Options:
    """The `settings.normalization` wire object (spec 12.5)."""
    if not isinstance(raw, dict):
        raise NormalizationError("normalization must be an object")
    unknown = set(raw) - {
        "variation_selectors",
        "whitespace",
        "line_breaks",
        "notes",
        "note_open",
        "note_close",
        "annotation_open",
        "annotation_close",
        "variants",
    }
    if unknown:
        raise NormalizationError(f"unknown normalization keys: {sorted(unknown)}")
    variants = raw.get("variants")
    table = None
    if variants is not None:
        if (
            not isinstance(variants, dict)
            or set(variants) != {"id", "version", "map"}
            or not isinstance(variants["map"], dict)
        ):
            raise NormalizationError("variants must be exactly {id, version, map}")
        table = variant_table(
            variants.get("id"), variants.get("version"), variants["map"]
        )
    return Options(
        variation_selectors=raw.get("variation_selectors", "keep"),
        whitespace=raw.get("whitespace", "keep"),
        line_breaks=raw.get("line_breaks", "delete"),
        notes=raw.get("notes", "barrier"),
        note_open=raw.get("note_open"),
        note_close=raw.get("note_close"),
        annotation_open=raw.get("annotation_open"),
        annotation_close=raw.get("annotation_close"),
        variants=table,
    )


def note_spans(reference: str, options: Options) -> list[tuple[int, int, str]]:
    """Source spans of notes and annotations, markers included (spec 2.8, 2.9).

    One left-to-right scan for both kinds: outside both, the note opening
    marker is tested first, then the annotation opening marker, then the two
    closing markers (an error there). Inside one kind, the other kind's markers
    are content; the first complete closing marker of the open kind ends it.
    """
    kinds = [
        (kind, opener, closer)
        for kind, opener, closer in (
            ("note", options.note_open, options.note_close),
            ("annotation", options.annotation_open, options.annotation_close),
        )
        if opener is not None and closer is not None
    ]
    if not kinds:
        return []
    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(reference):
        opened = next(
            ((k, o, c) for k, o, c in kinds if reference.startswith(o, index)), None
        )
        if opened is not None:
            kind, opener, closer = opened
            end = reference.find(closer, index + len(opener))
            if end < 0:
                raise NormalizationError(f"{kind} opened at {index} is never closed")
            spans.append((index, end + len(closer), kind))
            index = end + len(closer)
            continue
        for kind, _, closer in kinds:
            if reference.startswith(closer, index):
                raise NormalizationError(
                    f"closing {kind} marker outside a {kind} at {index}"
                )
        index += 1
    return spans


def normalize(reference: str, options: Options) -> NormalizedText:
    notes = note_spans(reference, options)
    text: list[str] = []
    spans: list[tuple[int, int]] = []
    removed: list[tuple[int, int, int]] = []
    attached: list[tuple[int, int, int]] = []
    note_index = 0
    position = 0
    while position < len(reference):
        if note_index < len(notes) and notes[note_index][0] == position:
            start, end, kind = notes[note_index]
            (removed if kind == "note" else attached).append((len(text), start, end))
            note_index += 1
            position = end
            continue
        output = options.code_point(reference[position])
        for char in output:
            text.append(char)
            spans.append((position, position + 1))
        position += 1
    barriers = (
        frozenset(q for q, _, _ in removed)
        if options.notes == "barrier"
        else frozenset()
    )
    return NormalizedText(
        reference=reference,
        text="".join(text),
        spans=tuple(spans),
        barriers=barriers,
        notes=tuple(removed),
        annotations=tuple(attached),
    )
