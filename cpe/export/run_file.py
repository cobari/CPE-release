"""The `.run.json` file that records run conditions (plan section 9)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpe.local.txt import LocalText
from cpe.match.engine import KIND_ORDER, Settings
from cpe.match.pattern_file import PatternSpec
from cpe.match.result import Result
from cpe.normalize.options import Options

ENGINE_VERSION = "cpe-match/0.1"
# Version of the representative rule semantics of spec 6; the rule lists and
# order themselves are recorded per pattern.
HEURISTICS_VERSION = "cpe-heuristics/1"
SORT_RULE = "input, pattern, anchor, kind, fixed spans, segment span (spec 8.6)"
TRAVERSAL_RULE = (
    "per segment: normal branch then diagnostics in declaration order; depth-first, "
    "candidates by ascending position, alt options in declaration order (spec 8.5)"
)


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    pattern_file: Path
    # The bytes the specs were parsed from; hashing them again from disk could
    # record a different snapshot if the file changed during the run.
    pattern_bytes: bytes
    texts: Sequence[LocalText]
    specs: Sequence[PatternSpec]
    settings: Settings
    normalization: str
    # (text_id, pattern_id) pairs whose text exceeds the long-segment threshold.
    long_segment_warnings: Sequence[tuple[str, str]] = ()


def build(info: RunInfo, results: Sequence[Result]) -> dict[str, Any]:
    settings = info.settings
    options = settings.normalization
    kinds = Counter(result.kind for result in results)
    return {
        "schema": "cpe.run/1",
        "run_id": info.run_id,
        "engine_version": ENGINE_VERSION,
        "mode": "reproducible",
        "pattern_file": {
            "path": info.pattern_file.as_posix(),
            "sha256": hashlib.sha256(info.pattern_bytes).hexdigest(),
        },
        "patterns": [
            {
                "id": spec.id,
                "pattern": spec.pattern.canonical(),
                "diagnostics": [
                    {"between_slot": d.between_slot, "require_tail": d.require_tail}
                    for d in spec.diagnostics
                ],
                "heuristics": {
                    "connectives": list(spec.heuristics.connectives),
                    "final_particles": list(spec.heuristics.final_particles),
                    "order": list(spec.heuristics.order),
                },
            }
            for spec in info.specs
        ],
        "inputs": inputs_section(info.texts),
        "normalization": normalization_section(options),
        "heuristics_version": HEURISTICS_VERSION,
        "settings": {
            **settings_section(settings),
            "state_limit": settings.state_limit,
            "result_limit": settings.result_limit,
            "time_limit": None,
        },
        "sort_rule": SORT_RULE,
        "traversal_rule": TRAVERSAL_RULE,
        "counts": {kind: kinds.get(kind, 0) for kind in KIND_ORDER},
        "limit_hits": [
            {
                "result_id": result.result_id,
                "text_id": result.text_id,
                "pattern_id": result.pattern_id,
                "segment": [result.segment.start, result.segment.end],
                "reason": result.indeterminate_reason,
            }
            for result in results
            if result.indeterminate_reason in ("state_budget", "result_budget")
        ],
        "long_segment_warnings": [
            {"text_id": text_id, "pattern_id": pattern_id}
            for text_id, pattern_id in info.long_segment_warnings
        ],
        "itkc": LOCAL_ITKC,
    }


# A local run sends no request; recorded so every run file has the same shape.
LOCAL_ITKC = {"calls": 0, "stopped": False, "reason": None}


def inputs_section(texts: Sequence[LocalText]) -> list[dict[str, Any]]:
    return [
        {
            "path": text.path.as_posix(),
            "text_id": text.text_id,
            "sha256": text.sha256,
            "bom": text.bom,
            "range": text.range,
            "boundary_start": text.boundary_start,
            "boundary_end": text.boundary_end,
            "punctuation": text.punctuation,
            "full_stop": text.full_stop,
            "text_type": text.text_type,
            "title": text.title,
            "source": text.source,
            "edition": text.edition,
        }
        for text in texts
    ]


def normalization_section(options: Options) -> dict[str, Any]:
    return {
        "name": options.name,
        "variation_selectors": options.variation_selectors,
        "whitespace": options.whitespace,
        "line_breaks": options.line_breaks,
        "notes": options.notes,
        "note_open": options.note_open,
        "note_close": options.note_close,
        "annotation_open": options.annotation_open,
        "annotation_close": options.annotation_close,
        "variant_table": None if options.variants is None else options.variants.id,
        "variant_table_version": None
        if options.variants is None
        else options.variants.version,
    }


def settings_section(settings: Settings) -> dict[str, Any]:
    return {
        "sentence_delimiters": "".join(sorted(settings.sentence_delimiters)),
        "clause_delimiters": "".join(sorted(settings.clause_delimiters)),
        "long_segment_threshold": settings.long_segment_threshold,
    }


def write(path: Path, info: RunInfo, results: Sequence[Result]) -> None:
    text = json.dumps(build(info, results), ensure_ascii=False, indent=2) + "\n"
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
