"""One-box word search over local texts (spec 14.2): no DSL, no slots."""

from __future__ import annotations

import bisect
import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cpe.export.csv_out import TextView, context, display, escape, shown, text_view
from cpe.export.run_file import (
    LOCAL_ITKC,
    inputs_section,
    normalization_section,
    settings_section,
)
from cpe.itkc.query import (
    And,
    Near,
    Node,
    Not,
    Or,
    QueryError,
    Regex,
    Term,
    evaluate,
    leaf_spans,
    parse,
    positive_leaves,
)
from cpe.local.txt import InputError, LocalText, read, text_options
from cpe.match import segment as seg
from cpe.match.engine import (
    Settings,
    check_variants,
    clause_delimiters,
    sentence_delimiters,
)
from cpe.normalize.options import Options, normalize
from cpe.normalize.text import NormalizedText

SEARCH_VERSION = "cpe-search/1"
SORT_RULE = "input order, then segment start (spec 14.2)"
# Column contract of spec 14.2 "CSV"; differs on purpose from the match CSV.
COLUMNS = (
    "result_id",
    "context_status",
    "match_status",
    "review_status",
    "run_id",
    "source",
    "text_id",
    "query",
    "context_before",
    "segment",
    "context_after",
    "hit_count",
    "offset_start",
    "offset_end",
    "title",
    "link",
    "link_status",
    "grade",
    "evidence",
    "boundary_mode",
    "boundary_start",
    "boundary_end",
    "normalization",
)


@dataclass(frozen=True)
class SearchOutputs:
    csv: Path
    jsonl: Path
    run: Path


def normalize_query(node: Node, options: Options) -> Node:
    """Terms and phrases get the text's per-code-point steps (spec 14.2).

    Regular expressions are applied as written to the normalized text.
    """
    if isinstance(node, Term):
        text = options.literal(node.text)
        if not text:
            raise QueryError(f"term {node.text!r} is empty after normalization")
        return Term(text)
    if isinstance(node, Regex):
        return node
    if isinstance(node, Not):
        return Not(normalize_query(node.child, options))
    if isinstance(node, And | Or):
        return replace(
            node, children=tuple(normalize_query(c, options) for c in node.children)
        )
    left, right = (
        normalize_query(node.left, options),
        normalize_query(node.right, options),
    )
    assert isinstance(left, Term | Regex) and isinstance(right, Term | Regex)
    return Near(left, right, node.dist)


def _segments(
    text: LocalText, normalized: NormalizedText, settings: Settings, mode: str
) -> list[seg.Segment]:
    delimiters = (
        sentence_delimiters(settings, text.full_stop, text.halfwidth_punctuation)
        if mode == "sentence"
        else clause_delimiters(settings, text.punctuation, text.halfwidth_punctuation)
    )
    return seg.split(
        normalized.text, delimiters, text.boundary_start, text.boundary_end
    )


def long_warning(segments: list[seg.Segment], settings: Settings) -> bool:
    lengths = [s.end - s.start for s in segments]
    return (
        bool(lengths) and sum(lengths) / len(lengths) > settings.long_segment_threshold
    )


def search_text(
    tree: Node,
    query: str,
    text: LocalText,
    normalized: NormalizedText,
    settings: Settings,
    mode: str = "sentence",
) -> list[dict[str, Any]]:
    """One record per segment of `text` that satisfies the query (spec 14.2)."""
    segments = _segments(text, normalized, settings, mode)
    warned = long_warning(segments, settings)
    barriers = sorted(normalized.barriers)
    leaves = positive_leaves(tree)
    records = []
    for segment in segments:
        body = normalized.text[segment.start : segment.end]

        def allowed(start: int, end: int, offset: int = segment.start) -> bool:
            # No term, phrase, regex hit or NEAR stretch crosses a note barrier
            # (spec 2.5); in segment coordinates, so shift by the segment start.
            index = bisect.bisect_right(barriers, offset + start)
            return index >= len(barriers) or barriers[index] >= offset + end

        if not evaluate(tree, body, allowed):
            continue
        found = sorted(
            {
                (segment.start + start, segment.start + end)
                for leaf in leaves
                for start, end in leaf_spans(leaf, body, allowed)
            }
        )
        # Spec 3.5 applies to the grading of word-search records as well.
        graded = seg.effective(segment) if warned else segment
        src = normalized.src_span(segment.start, segment.end)
        records.append(
            {
                "schema": "cpe.occurrence/1",
                "result_id": "",
                "kind": "occurrence",
                "query": query,
                "source": "local",
                "text_id": text.text_id,
                "mode": mode,
                "segment": {
                    "norm": [segment.start, segment.end],
                    "src": [src.start, src.end],
                    "src_deleted": [list(span) for span in src.deleted],
                    "boundary_start": segment.boundary_start,
                    "boundary_end": segment.boundary_end,
                },
                "evidence": seg.evidence(graded, text.range),
                "context_status": seg.context_status(graded),
                "grade": "record_candidate",
                "editorial_boundaries": "editorial"
                in (segment.boundary_start, segment.boundary_end),
                "long_segment_warning": warned,
                "hits": [_hit(normalized, start, end) for start, end in found],
                "review_status": "unreviewed",
            }
        )
    return records


def _hit(normalized: NormalizedText, start: int, end: int) -> dict[str, Any]:
    src = normalized.src_span(start, end)
    return {
        "text": normalized.text[start:end],
        "norm": [start, end],
        "src": [src.start, src.end],
        "src_exact": [normalized.is_exact(start), normalized.is_exact(end)],
        "src_deleted": [list(span) for span in src.deleted],
        # Notes joined inside the hit (notes = "join"); empty under a barrier.
        "joined_notes": [
            {"norm": q, "src": [s, e]}
            for q, s, e in normalized.notes
            if start < q < end
        ],
        "joined_annotations": [
            {"norm": q, "src": [s, e]}
            for q, s, e in normalized.annotations
            if start < q < end
        ],
    }


def search_files(
    query: str,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
    mode: str = "sentence",
) -> tuple[list[dict[str, Any]], SearchOutputs]:
    settings = settings or Settings()
    options = settings.normalization
    tree = normalize_query(parse(query), options)
    texts = [read(path) for path in inputs]
    ids = [text.text_id for text in texts]
    if len(set(ids)) != len(ids):
        raise InputError("duplicate text_id among inputs")
    for text in texts:
        if text.text_type != "original":
            raise InputError(f"{text.path}: text_type {text.text_type!r} not matched")
    check_variants(settings, [text.punctuation for text in texts])
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    views: dict[str, TextView] = {}
    for text in texts:
        normalized = normalize(text.reference, text_options(text, options))
        views[text.text_id] = text_view(
            text.reference,
            normalized,
            sentence_delimiters(settings, text.full_stop, text.halfwidth_punctuation),
            text.boundary_start,
            text.boundary_end,
        )
        if long_warning(_segments(text, normalized, settings, mode), settings):
            warnings.append(text.text_id)
        records.extend(search_text(tree, query, text, normalized, settings, mode))
    for rank, record in enumerate(records, start=1):
        record["result_id"] = f"r{rank:06d}"
    outputs = SearchOutputs(
        prefix.with_name(prefix.name + ".csv"),
        prefix.with_name(prefix.name + ".jsonl"),
        prefix.with_name(prefix.name + ".run.json"),
    )
    titles = {text.text_id: text.title for text in texts}
    jsonl_text = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    rows = [csv_row(r, views, titles, run_id, options.name) for r in records]
    run = {
        "schema": "cpe.search-run/1",
        "run_id": run_id,
        "engine_version": SEARCH_VERSION,
        "mode": mode,
        "query": query,
        "inputs": inputs_section(texts),
        "normalization": normalization_section(options),
        "settings": settings_section(settings),
        "not_applicable": ["patterns", "heuristics", "state_limit", "result_limit"],
        "sort_rule": SORT_RULE,
        "counts": {"occurrence": len(records)},
        "long_segment_warnings": [{"text_id": text_id} for text_id in warnings],
        "itkc": LOCAL_ITKC,
    }
    run_text = json.dumps(run, ensure_ascii=False, indent=2) + "\n"
    # All content is built before any file is opened: opening for writing
    # empties the file.
    with outputs.jsonl.open("w", encoding="utf-8", newline="") as stream:
        stream.write(jsonl_text)
    with outputs.csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with outputs.run.open("w", encoding="utf-8", newline="") as stream:
        stream.write(run_text)
    return records, outputs


def csv_row(
    record: dict[str, Any],
    views: dict[str, TextView],
    titles: dict[str, str],
    run_id: str,
    normalization: str,
) -> dict[str, str]:
    view = views[record["text_id"]]
    reference, gone = view.reference, view.hidden
    start, end = record["segment"]["src"]
    marks: list[list[int]] = []
    for hit in record["hits"]:
        s, e = hit["src"]
        if marks and s <= marks[-1][1]:
            marks[-1][1] = max(marks[-1][1], e)
        else:
            marks.append([s, e])
    parts, cursor = [], start
    for s, e in marks:
        parts += [
            escape(shown(reference, cursor, s, gone)),
            "【",
            escape(shown(reference, s, e, gone)),
            "】",
        ]
        cursor = e
    parts.append(escape(shown(reference, cursor, end, gone)))
    text = "".join(parts)
    norm_start, norm_end = record["segment"]["norm"]
    before, after = context(view, norm_start, norm_end, start, end)
    if record["segment"]["boundary_start"] == "truncated":
        text = "…" + text
    if record["segment"]["boundary_end"] == "truncated":
        text = text + "…"
    return {
        "result_id": record["result_id"],
        "context_status": record["context_status"],
        "match_status": record["kind"],
        "review_status": record["review_status"],
        "run_id": run_id,
        "source": record["source"],
        "text_id": record["text_id"],
        "query": record["query"],
        "context_before": before,
        "segment": display(text),
        "context_after": after,
        "hit_count": str(len(record["hits"])),
        "offset_start": str(start),
        "offset_end": str(end),
        "title": titles.get(record["text_id"], ""),
        "link": "",
        "link_status": "",
        "grade": record["grade"],
        "evidence": record["evidence"],
        "boundary_mode": record["mode"],
        "boundary_start": record["segment"]["boundary_start"],
        "boundary_end": record["segment"]["boundary_end"],
        "normalization": normalization,
    }
