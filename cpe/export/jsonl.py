"""Result records as JSON values and JSONL files (spec section 10)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cpe.match.result import Boundary, FixedHit, OpenSlotHit, Result, SlotHit

SCHEMA = "cpe.result/1"


def to_record(result: Result) -> dict[str, Any]:
    segment = result.segment
    return {
        "schema": SCHEMA,
        "result_id": result.result_id,
        "kind": result.kind,
        "pattern_id": result.pattern_id,
        "pattern": result.pattern,
        "source": result.source,
        "text_id": result.text_id,
        "mode": result.mode,
        "segment": {
            "norm": [segment.start, segment.end],
            "src": [result.segment_src.start, result.segment_src.end],
            "src_deleted": [list(span) for span in result.segment_src.deleted],
            "boundary_start": segment.boundary_start,
            "boundary_end": segment.boundary_end,
        },
        "evidence": result.evidence,
        "context_status": result.context_status,
        "grade": result.grade,
        "editorial_boundaries": result.editorial_boundaries,
        "long_segment_warning": result.long_segment_warning,
        "fixed": [_fixed(hit) for hit in result.fixed],
        "slots": [_slot(hit) for hit in result.slots],
        "open_slot": None if result.open_slot is None else _open(result.open_slot),
        "alt_count": result.alt_count,
        "ambiguous": result.ambiguous,
        "adjacency": None
        if result.adjacency is None
        else {"slot": result.adjacency.slot, "kind": result.adjacency.kind},
        "joined_notes": [
            {"norm": position, "src": [start, end]}
            for position, start, end in result.joined_notes
        ],
        "joined_annotations": [
            {"norm": position, "src": [start, end]}
            for position, start, end in result.joined_annotations
        ],
        "indeterminate_reason": result.indeterminate_reason,
        "review_status": result.review_status,
    }


def write(path: Path, results: Iterable[Result]) -> None:
    lines = [
        json.dumps(to_record(result), ensure_ascii=False, separators=(",", ":"))
        for result in results
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write("".join(line + "\n" for line in lines))


def _fixed(hit: FixedHit) -> dict[str, Any]:
    return {
        "literal": hit.literal,
        "norm": [hit.start, hit.end],
        "src": [hit.src.start, hit.src.end],
        "src_exact": list(hit.src_exact),
    }


def _slot(hit: SlotHit) -> dict[str, Any]:
    return {
        "name": hit.name,
        "norm": [hit.start, hit.end],
        "src": [hit.src.start, hit.src.end],
        "src_exact": list(hit.src_exact),
        "src_deleted": [list(span) for span in hit.src.deleted],
    }


def _boundary(boundary: Boundary) -> dict[str, Any]:
    return {"norm": boundary.norm, "src": boundary.src, "exact": boundary.exact}


def _open(hit: OpenSlotHit) -> dict[str, Any]:
    return {
        "name": hit.name,
        "b_start": _boundary(hit.b_start),
        "ends": [_boundary(end) for end in hit.ends],
        "observed_end": _boundary(hit.observed_end),
        "truncated": hit.truncated,
        "representatives": [
            {
                **_boundary(rep.end),
                "selected_by": rep.selected_by,
                "rules": list(rep.rules),
            }
            for rep in hit.representatives
        ],
        "b_end_status": hit.b_end_status,
    }
