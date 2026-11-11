"""Pattern DSL adapter for the MCP service.

Reuses the existing matcher engine, pattern file loader, and export serializers to
provide structured candidate rows with full JSONL records over MCP.
"""

from __future__ import annotations

from typing import Any

from cpe.cancellation import Checkpoint
from cpe.export import csv_out, jsonl
from cpe.gui.service import Library
from cpe.match.dsl import DslError, Pattern, Slot
from cpe.match.engine import (
    assign_ids,
    clause_delimiters,
    match_text,
)
from cpe.match.pattern_file import PatternFileError, PatternSpec, from_mapping, loads
from cpe.match.result import Result
from cpe.normalize.options import NormalizationError

ADJACENT = "adjacent"
INDETERMINATE = "indeterminate"
SKIPS_NEARER = "skips_nearer"
CROSSES_CLAUSE = "crosses_clause"


def _is_pair_like(pattern: Pattern) -> bool:
    """True if pattern has exactly two fixed terms and one internal slot."""
    return (
        len(pattern.fixed) == 2
        and len(pattern.between) == 1
        and isinstance(pattern.between[0], Slot)
    )


def search(
    library: Library,
    *,
    expression: str = "",
    pattern_content: bytes | None = None,
    checkpoint: Checkpoint = None,
) -> dict[str, Any]:
    """Search local texts using an inline DSL expression or TOML pattern content.

    Exactly one of ``expression`` or ``pattern_content`` must be supplied.
    Returns a dict with ``mode="pattern"``, ``likely`` rows, and ``less`` rows.
    """
    expr_clean = expression.strip()
    has_expr = bool(expr_clean)
    has_content = pattern_content is not None

    if not has_expr and not has_content:
        return {"error": "empty"}
    if has_expr and has_content:
        return {"error": "bad_request"}

    if checkpoint:
        checkpoint()

    specs: list[PatternSpec]
    if has_expr:
        try:
            spec = from_mapping(
                {"id": "mcp-pattern", "expr": expr_clean}, "<expression>"
            )
            specs = [spec]
        except (DslError, PatternFileError):
            return {"error": "bad_pattern"}
    else:
        assert pattern_content is not None
        if not pattern_content.strip():
            return {"error": "empty"}
        try:
            specs = loads(pattern_content, "MCP pattern file")
        except (DslError, PatternFileError, UnicodeDecodeError):
            return {"error": "bad_pattern"}
        if not specs:
            return {"error": "bad_pattern"}

    settings = library.settings
    options = settings.normalization

    try:
        found: list[tuple[int, int, Result]] = []
        for text_index, text in enumerate(library.texts):
            if checkpoint:
                checkpoint()
            matcher_input = text.matcher_input(settings)
            for pattern_index, spec in enumerate(specs):
                if checkpoint:
                    checkpoint()
                for result in match_text(
                    spec,
                    matcher_input,
                    settings,
                    checkpoint=checkpoint,
                ):
                    found.append((text_index, pattern_index, result))
    except NormalizationError:
        return {"error": "normalization"}
    except PatternFileError:
        return {"error": "bad_pattern"}

    if checkpoint:
        checkpoint()

    results = assign_ids(found)

    # Pre-collect fixed hit coordinates per (text_id, segment.start, segment.end)
    # to render uniform 【】 highlight markers across all placements in a segment.
    paired: dict[tuple[str, int, int], set[tuple[int, int]]] = {}
    for result in results:
        key = (result.text_id, result.segment.start, result.segment.end)
        paired.setdefault(key, set()).update(
            (hit.src.start, hit.src.end) for hit in result.fixed
        )

    # Cache second fixed literals for pair-like patterns to detect skips_nearer
    spec_seconds: dict[str, set[str]] = {}
    for spec in specs:
        if _is_pair_like(spec.pattern):
            spec_seconds[spec.id] = {
                options.literal(opt) for opt in spec.pattern.fixed[1].options
            }

    by_id = {text.local.text_id: text for text in library.texts}
    likely: list[dict[str, Any]] = []
    less: list[dict[str, Any]] = []

    for result in results:
        if checkpoint:
            checkpoint()

        text = by_id[result.text_id]
        view = text.view
        key = (result.text_id, result.segment.start, result.segment.end)

        before, after = csv_out.context(
            view,
            result.segment.start,
            result.segment.end,
            result.segment_src.start,
            result.segment_src.end,
        )

        reasons: list[str] = []
        if result.kind == ADJACENT:
            reasons.append(ADJACENT)
        elif result.kind == INDETERMINATE:
            reasons.append(INDETERMINATE)

        # Slot A: first internal slot of the pattern (canonical csv_out logic)
        first_name = csv_out.first_internal_slot(result.pattern)
        first_slot = (
            next((hit for hit in result.slots if hit.name == first_name), None)
            if first_name is not None
            else None
        )
        a_shown = (
            csv_out.shown(
                view.reference, first_slot.src.start, first_slot.src.end, view.hidden
            )
            if first_slot is not None
            else ""
        )

        # Slot B: open slot representative candidates
        b_shown: list[str] = []
        if result.open_slot is not None:
            b_start = result.open_slot.b_start.src
            b_shown = [
                csv_out.shown(view.reference, b_start, rep.end.src, view.hidden)
                for rep in result.open_slot.representatives
            ]

        # For pair-like patterns, check standard structural signals
        # (skips_nearer, crosses_clause)
        if result.pattern_id in spec_seconds and first_slot is not None:
            seconds = spec_seconds[result.pattern_id]
            a_norm = text.normalized.text[first_slot.start : first_slot.end]
            if any(literal in a_norm for literal in seconds):
                reasons.append(SKIPS_NEARER)
            marks = clause_delimiters(
                settings, text.local.punctuation, text.local.halfwidth_punctuation
            )
            if any(mark in a_norm for mark in marks):
                reasons.append(CROSSES_CLAUSE)

        row = {
            "id": result.result_id,
            "text_id": result.text_id,
            "title": text.title,
            "link": text.link,
            "era": text.era,
            "kind": result.kind,
            "before": before,
            "segment": csv_out.segment_display(
                result, view.reference, frozenset(paired.get(key, ())), view.hidden
            ),
            "after": after,
            "a": a_shown,
            "b": b_shown,
            "reasons": reasons,
            "record": jsonl.to_record(result),
        }

        if reasons:
            less.append(row)
        else:
            likely.append(row)

    if checkpoint:
        checkpoint()

    return {"mode": "pattern", "likely": likely, "less": less}
