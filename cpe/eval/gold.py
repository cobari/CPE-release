"""Read-only passage preparation and provenance checks for G2 evaluation."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from cpe.eval.metrics import iter_entries, validate_span
from cpe.eval.report import patterns_from, wire_builder
from cpe.local.txt import LocalText, parse_text_bytes, read, text_options
from cpe.match import segment
from cpe.match.api import validate_wire
from cpe.match.engine import Settings, sentence_delimiters
from cpe.normalize.options import Options, normalize, note_spans

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_EXPECTED_FIELDS = {"kind", "fixed", "slots", "ends", "reason"}


def reading_spans(passage: LocalText) -> Iterator[tuple[int, int]]:
    """Partition the original passage using the edition's sentence boundaries."""
    settings = Settings()
    normalized = normalize(
        passage.reference, text_options(passage, settings.normalization)
    )
    delimiters = sentence_delimiters(
        settings, passage.full_stop, passage.halfwidth_punctuation
    )
    start = 0
    have_text = False
    for unit in segment.split(normalized.text, delimiters):
        if not normalized.text[unit.start : unit.end].strip():
            continue
        if have_text:
            # Keep the source material between running-text segments in the list.
            end = normalized.src_start(unit.start)
            yield start, end
            start = end
        have_text = True
    if start < len(passage.reference):
        yield start, len(passage.reference)


def validate_reading_span(
    passage: LocalText, span: list[int] | None = None
) -> tuple[Options, list[tuple[int, int, str]]]:
    """Validate an optional reading span and return note options and spans."""
    selection = [0, len(passage.reference)] if span is None else span
    validate_span(selection, len(passage.reference), str(passage.path), "reading span")
    options = text_options(passage, Options())
    hidden = note_spans(passage.reference, options)
    if span is not None and any(
        start < edge < end for start, end, _ in hidden for edge in span
    ):
        raise ValueError(f"{passage.path}: selected span cuts through a declared note")
    return options, hidden


def reading_units(
    passage: LocalText, span: list[int] | None = None
) -> Iterator[tuple[int, int, int]]:
    """Yield selected source ranges with their whole-file sentence numbers."""
    validate_reading_span(passage, span)
    first, last = [0, len(passage.reference)] if span is None else span

    def _iter() -> Iterator[tuple[int, int, int]]:
        for number, (start, end) in enumerate(reading_spans(passage), start=1):
            if end <= first:
                continue
            if start >= last:
                break
            yield number, max(start, first), min(end, last)

    return _iter()


def reading_list(path: Path, span: list[int] | None = None) -> str:
    """Render original text with whole-file numbering and source offsets."""
    passage = read(path)
    output = [f"Source SHA-256 {passage.sha256}\n"]
    for number, start, end in reading_units(passage, span):
        output.append(f"{number} [{start}, {end})\n{passage.reference[start:end]}\n")
    return "".join(output)


def validate(gold_paths: Sequence[Path], patterns_path: Path) -> tuple[int, int]:
    """Check G2 JSONL and its source slices without running a matcher."""
    patterns = patterns_from(patterns_path)
    wire_for = wire_builder(patterns)
    sources: dict[Path, LocalText] = {}
    entries = 0
    snapshots = ((path, path.read_bytes()) for path in gold_paths)
    for path, lineno, entry in iter_entries(snapshots):
        where = f"{path}:{lineno}: entry {entry['id']!r}"
        if entry["pattern_id"] not in patterns:
            raise ValueError(f"{where}: unknown pattern_id {entry['pattern_id']!r}")
        for index, record in enumerate(entry["expected"]):
            if record.keys() != _EXPECTED_FIELDS:
                raise ValueError(
                    f"{where}: expected[{index}] must contain exactly "
                    f"{', '.join(sorted(_EXPECTED_FIELDS))}"
                )
        source = entry.get("source", {})
        for key in ("work", "section", "edition", "path"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{where}: source.{key} must be a non-empty string")
        recorded_hash = source.get("sha256")
        if not isinstance(recorded_hash, str) or not _SHA256.fullmatch(recorded_hash):
            raise ValueError(
                f"{where}: source.sha256 must be a 64-digit SHA-256 hex digest"
            )
        source_path = path.parent / source["path"]
        try:
            source_path = source_path.resolve(strict=True)
            if source_path not in sources:
                if not source_path.is_file():
                    raise ValueError(f"{source_path}: expected a file")
                sources[source_path] = parse_text_bytes(
                    source_path, source_path.read_bytes(), {}
                )
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError(f"{where}: cannot read source.path ({error})") from error

        captured = sources[source_path]
        if recorded_hash.lower() != captured.sha256:
            raise ValueError(f"{where}: source.sha256 does not match {source_path}")
        span = source.get("span")
        validate_span(span, len(captured.reference), where, "source.span")
        start, end = span
        if entry["text"] != captured.reference[start:end]:
            raise ValueError(
                f"{where}: text does not match source.span in {source_path}"
            )
        try:
            validate_wire(wire_for(entry))
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from error
        entries += 1
    if not entries:
        raise ValueError("no gold entries found")
    return entries, len(sources)
