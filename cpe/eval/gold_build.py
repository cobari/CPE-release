"""Turn human G2 annotations into evaluator JSONL with oracle expectations."""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cpe.eval import reference
from cpe.eval.gold import reading_units, validate_reading_span
from cpe.eval.metrics import USAGE_LABELS, structure
from cpe.eval.report import patterns_from, wire_builder
from cpe.export.safe import check_input_collisions, check_outputs, staged
from cpe.local.txt import LocalText, manifest_path, read
from cpe.match.dsl import parse

_REQUIRED = {
    "sentence",
    "first",
    "first_occurrence",
    "second",
    "second_occurrence",
    "a",
    "b",
    "label",
}
_OPTIONAL = {"b_alternatives", "group", "notes"}


@dataclass(frozen=True)
class Citation:
    work: str
    section: str
    edition: str


def build(
    passage_path: Path,
    annotations: Path,
    output: Path,
    *,
    passage_id: str,
    citation: Citation,
    pattern_id: str,
    patterns_path: Path,
    span: list[int] | None = None,
    source_sha256: str | None = None,
    force: bool = False,
) -> int:
    """Write one evaluator entry per selected reading unit, including empty ones."""
    check_input_collisions(
        [output],
        [passage_path, manifest_path(passage_path), annotations, patterns_path],
    )
    check_outputs([output], force=force)
    if not passage_id.strip() or any(
        not value.strip() for value in asdict(citation).values()
    ):
        raise ValueError("passage ID, work, section and edition must be non-empty")
    passage = read(passage_path)
    if passage.text_type != "original":
        raise ValueError(f"{passage_path}: expected original source text")
    if source_sha256 is not None and source_sha256.lower() != passage.sha256:
        raise ValueError(
            f"{passage_path}: source SHA-256 differs from the reading list"
        )
    patterns = patterns_from(patterns_path)
    if pattern_id not in patterns:
        raise ValueError(f"{patterns_path}: unknown pattern ID {pattern_id!r}")
    if len(parse(patterns[pattern_id]["expr"]).fixed) != 2:
        raise ValueError(
            f"{patterns_path}: G2 annotations need a pattern with two fixed elements"
        )

    options, hidden = validate_reading_span(passage, span)
    spans = {
        number: (start, end) for number, start, end in reading_units(passage, span)
    }
    wire_for = wire_builder(patterns)
    source = {
        **asdict(citation),
        "path": _source_path(passage_path, output),
        "sha256": passage.sha256,
    }
    grouped = _annotations(annotations, passage, spans, hidden)
    lines = []
    for number, (start, end) in spans.items():
        entry: dict[str, Any] = {
            "id": f"{passage_id}:{pattern_id}:{number}",
            "pattern_id": pattern_id,
            "text": passage.reference[start:end],
            "source": {**source, "span": [start, end]},
            "input": {
                "range": passage.range
                if (start, end) == (0, len(passage.reference))
                else "excerpt",
                "boundary_start": passage.boundary_start if start == 0 else "truncated",
                "boundary_end": passage.boundary_end
                if end == len(passage.reference)
                else "truncated",
                "punctuation": passage.punctuation,
                "full_stop": passage.full_stop,
                "halfwidth_punctuation": passage.halfwidth_punctuation,
            },
            "normalization": {
                "note_open": options.note_open,
                "note_close": options.note_close,
            },
            "usages": grouped.get(number, []),
            "confirmed": False,
        }
        try:
            entry["expected"] = structure(reference.run(wire_for(entry)))
        except ValueError as error:
            raise ValueError(f"{passage_path}: sentence {number}: {error}") from error
        lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
    with staged([output], force=force) as (target,):
        target.write_text("".join(lines), encoding="utf-8", newline="")
    return len(lines)


def _source_path(path: Path, output: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), output.parent.resolve())).as_posix()
    except ValueError:
        # Relative paths cannot cross drive roots on Windows.
        return path.resolve().as_posix()


def _annotations(
    path: Path,
    passage: LocalText,
    spans: dict[int, tuple[int, int]],
    hidden: list[tuple[int, int, str]],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    used: dict[tuple[int, tuple[int, ...], tuple[int, ...]], int] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, strict=True)
        try:
            fields = rows.fieldnames or []
            if (
                len(fields) != len(set(fields))
                or not _REQUIRED <= set(fields)
                or set(fields) - _REQUIRED - _OPTIONAL
            ):
                raise ValueError(
                    f"{path}:1: expected CSV columns {', '.join(sorted(_REQUIRED))}; "
                    f"optional: {', '.join(sorted(_OPTIONAL))}"
                )
            for row in rows:
                line = rows.line_num
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(
                            "row does not have the same number of fields as the header"
                        )
                    number = int(row["sentence"])
                    if number not in spans:
                        raise ValueError("sentence is outside the selected source span")
                    start, end = spans[number]
                    usage = _usage(row, passage.reference[start:end], start, hidden)
                    first, second = usage["fixed_offsets"]
                    key = (number, tuple(first), tuple(second))
                    if key in used:
                        raise ValueError(
                            f"duplicate placement (first on line {used[key]})"
                        )
                    used[key] = line
                    grouped[number].append(usage)
                except ValueError as error:
                    raise ValueError(f"{path}:{line}: {error}") from error
        except csv.Error as error:
            raise ValueError(f"{path}:{rows.line_num}: {error}") from error
    return grouped


def _occurrence(
    text: str,
    literal: str,
    ordinal: str,
    absolute_start: int,
    hidden: list[tuple[int, int, str]],
) -> list[int]:
    count = int(ordinal)
    if not literal or count < 1:
        raise ValueError(
            "fixed elements must be non-empty with positive occurrence numbers"
        )
    position = -1
    while True:
        position = text.find(literal, position + 1)
        if position < 0:
            raise ValueError(
                f"fixed element occurrence {ordinal} is missing from the sentence"
            )
        end = position + len(literal)
        if any(
            start < absolute_start + end and stop > absolute_start + position
            for start, stop, _ in hidden
        ):
            continue
        count -= 1
        if count == 0:
            return [position, end]


def _usage(
    row: dict[str, str], text: str, start: int, hidden: list[tuple[int, int, str]]
) -> dict[str, Any]:
    first = _occurrence(text, row["first"], row["first_occurrence"], start, hidden)
    second = _occurrence(text, row["second"], row["second_occurrence"], start, hidden)
    if first[1] > second[0]:
        raise ValueError("fixed elements must be in order without overlap")
    a = row["a"]
    if text[first[1] : second[0]] != a:
        raise ValueError("A differs from the source between the two fixed elements")
    b_values = [row["b"]] if row["b"] else []
    if row.get("b_alternatives"):
        extras = json.loads(row["b_alternatives"])
        if not isinstance(extras, list) or any(
            not isinstance(value, str) or not value for value in extras
        ):
            raise ValueError("b_alternatives must be a JSON array of non-empty strings")
        b_values.extend(extras)
    b_start = second[1]
    if any(not text.startswith(value, b_start) for value in b_values):
        raise ValueError(
            "B must match the source immediately after the second fixed element"
        )
    if row["label"] not in USAGE_LABELS:
        raise ValueError(f"label must be one of {', '.join(USAGE_LABELS)}")
    return {
        "fixed_offsets": [first, second],
        "slot_A": [[first[1], second[0]]] if a else [],
        "slot_B": [
            [b_start, b_start + len(value)] for value in dict.fromkeys(b_values)
        ],
        "label": row["label"],
        "group": row.get("group", ""),
        "notes": row.get("notes", ""),
    }
