"""One-box word search over local texts (spec 14.2): no DSL, no slots."""

from __future__ import annotations

import bisect
import csv
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cpe.cancellation import Checkpoint
from cpe.export.csv_out import (
    TextView,
    context,
    display,
    escape,
    excel_safe,
    shown,
    text_view,
)
from cpe.export.run_file import (
    LOCAL_ITKC,
    inputs_section,
    normalization_section,
    settings_section,
)
from cpe.export.safe import (
    check_input_collisions,
    check_outputs,
    check_prefix,
    staged,
)
from cpe.itkc import regex_worker
from cpe.itkc.query import (
    And,
    Leaf,
    Near,
    Node,
    Not,
    Or,
    QueryError,
    Regex,
    Term,
    evaluate,
    leaf_spans,
    near_spans_match,
    parse,
    positive_leaves,
)
from cpe.local.txt import InputError, LocalText, read_inputs, text_options
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


def long_warning(segments: Sequence[seg.Segment], settings: Settings) -> bool:
    lengths = [s.end - s.start for s in segments]
    return (
        bool(lengths) and sum(lengths) / len(lengths) > settings.long_segment_threshold
    )


def _collect_regex_leaves(node: Node) -> list[Regex]:
    found: list[Regex] = []
    seen: set[Regex] = set()
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, Regex):
            if current not in seen:
                seen.add(current)
                found.append(current)
        elif isinstance(current, Not):
            stack.append(current.child)
        elif isinstance(current, Near):
            stack.extend((current.right, current.left))
        elif isinstance(current, (And, Or)):
            stack.extend(reversed(current.children))
    return found


def _segment_allowed(
    barriers: Sequence[int], offset: int
) -> Callable[[int, int], bool] | None:
    if not barriers:
        return None

    def allowed(start: int, end: int, offset: int = offset) -> bool:
        # No term, phrase, regex hit or NEAR stretch crosses a note barrier
        # (spec 2.5); in segment coordinates, so shift by the segment start.
        index = bisect.bisect_right(barriers, offset + start)
        return index >= len(barriers) or barriers[index] >= offset + end

    return allowed


def _probe_next_regex(
    node: Node,
    body: str,
    allowed_fn: Callable[[int, int], bool] | None,
    cache: dict[Leaf, list[tuple[int, int]]],
) -> bool | Regex:
    """Evaluate fast Term leaves and yield the next needed Regex left-to-right."""
    if isinstance(node, Term):
        if node not in cache:
            cache[node] = leaf_spans(node, body, allowed_fn)
        return bool(cache[node])
    if isinstance(node, Regex):
        if node not in cache:
            return node
        return bool(cache[node])
    if isinstance(node, Not):
        result = _probe_next_regex(node.child, body, allowed_fn, cache)
        return result if isinstance(result, Regex) else not result
    if isinstance(node, And):
        for child in node.children:
            result = _probe_next_regex(child, body, allowed_fn, cache)
            if isinstance(result, Regex):
                return result
            if not result:
                return False
        return True
    if isinstance(node, Or):
        for child in node.children:
            result = _probe_next_regex(child, body, allowed_fn, cache)
            if isinstance(result, Regex):
                return result
            if result:
                return True
        return False
    left_res = _probe_next_regex(node.left, body, allowed_fn, cache)
    if isinstance(left_res, Regex):
        return left_res
    if not left_res:
        return False
    right_res = _probe_next_regex(node.right, body, allowed_fn, cache)
    if isinstance(right_res, Regex):
        return right_res
    if not right_res:
        return False
    return near_spans_match(cache[node.left], cache[node.right], node.dist, allowed_fn)


def _batch_leaf_into_caches(
    leaf: Regex,
    indices: Sequence[int],
    text: NormalizedText,
    segments: Sequence[seg.Segment],
    allowed_fns: Sequence[Callable[[int, int], bool] | None],
    caches: dict[int, dict[Leaf, list[tuple[int, int]]]],
    checkpoint: Checkpoint,
) -> None:
    bodies = [text.text[segments[i].start : segments[i].end] for i in indices]
    try:
        batch = regex_worker.spans_batch(leaf.pattern, bodies, checkpoint=checkpoint)
    except (OSError, EOFError, TimeoutError, ValueError) as error:
        raise QueryError(str(error)) from None
    for idx, raw in zip(indices, batch, strict=True):
        allowed_fn = allowed_fns[idx]
        caches.setdefault(idx, {})[leaf] = (
            [span for span in raw if allowed_fn(span[0], span[1])]
            if allowed_fn is not None
            else raw
        )


def search_text(
    tree: Node,
    query: str,
    text: LocalText,
    normalized: NormalizedText,
    settings: Settings,
    mode: str = "sentence",
    checkpoint: Checkpoint = None,
    segments: Sequence[seg.Segment] | None = None,
    *,
    warn_long: bool | None = None,
) -> list[dict[str, Any]]:
    """One record per segment of `text` that satisfies the query (spec 14.2).

    `segments`, when given, must be `_segments(text, normalized, settings, mode)`;
    a caller that already split the text passes it to avoid splitting twice.
    `warn_long`, when given, is the caller's already-computed
    `long_warning(segments, settings)` result, so the segments are not rescanned.
    """
    if segments is None:
        segments = _segments(text, normalized, settings, mode)
    warned = long_warning(segments, settings) if warn_long is None else warn_long
    barriers = normalized.sorted_barriers
    leaves = positive_leaves(tree)
    regex_leaves = _collect_regex_leaves(tree)
    use_batch = (
        len(segments) > 1
        and bool(regex_leaves)
        and regex_worker.spans is regex_worker._ORIG_SPANS
    )
    allowed_fns: list[Callable[[int, int], bool] | None] = []
    caches: dict[int, dict[Leaf, list[tuple[int, int]]]] = {}
    outcomes: list[bool | None] = []
    if use_batch:
        # Demand-driven waves preserve left-to-right boolean short-circuiting so
        # regexes never run on segments already ruled out by cheaper leaves.
        allowed_fns = [
            _segment_allowed(barriers, segment.start) for segment in segments
        ]
        outcomes = [None] * len(segments)
        active = list(range(len(segments)))
        while active:
            pending: dict[Regex, list[int]] = {}
            for seg_index in active:
                if checkpoint:
                    checkpoint()
                segment = segments[seg_index]
                body = normalized.text[segment.start : segment.end]
                seg_cache = caches.setdefault(seg_index, {})
                probed = _probe_next_regex(
                    tree, body, allowed_fns[seg_index], seg_cache
                )
                if isinstance(probed, Regex):
                    pending.setdefault(probed, []).append(seg_index)
                else:
                    outcomes[seg_index] = probed
                    if not probed:
                        caches.pop(seg_index, None)
            if not pending:
                break
            for leaf, indices in pending.items():
                _batch_leaf_into_caches(
                    leaf, indices, normalized, segments, allowed_fns, caches, checkpoint
                )
            active = sorted(idx for indices in pending.values() for idx in indices)
        for highlight_leaf in leaves:
            if isinstance(highlight_leaf, Regex):
                missing = [
                    idx
                    for idx, matched in enumerate(outcomes)
                    if matched and highlight_leaf not in caches.get(idx, ())
                ]
                if missing:
                    _batch_leaf_into_caches(
                        highlight_leaf,
                        missing,
                        normalized,
                        segments,
                        allowed_fns,
                        caches,
                        checkpoint,
                    )

    records = []
    for seg_index, segment in enumerate(segments):
        if checkpoint:
            checkpoint()
        if use_batch:
            if not outcomes[seg_index]:
                continue
            allowed_fn = allowed_fns[seg_index]
            span_cache = caches.pop(seg_index, {})
        else:
            allowed_fn = _segment_allowed(barriers, segment.start)
            span_cache = {}
        body = normalized.text[segment.start : segment.end]

        def find_spans(
            leaf: Leaf,
            *,
            _body: str = body,
            _allowed: Callable[[int, int], bool] | None = allowed_fn,
            _cache: dict[Leaf, list[tuple[int, int]]] = span_cache,
        ) -> list[tuple[int, int]]:
            cached = _cache.get(leaf)
            if cached is None:
                cached = leaf_spans(leaf, _body, _allowed)
                _cache[leaf] = cached
            return cached

        if not use_batch and not evaluate(
            tree, body, allowed_fn, find_spans=find_spans
        ):
            continue
        found = sorted(
            {
                (segment.start + start, segment.start + end)
                for leaf in leaves
                for start, end in find_spans(leaf)
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
            for q, s, e in normalized.removals_between("notes", start, end)
        ],
        "joined_annotations": [
            {"norm": q, "src": [s, e]}
            for q, s, e in normalized.removals_between("annotations", start, end)
        ],
    }


def _write_jsonl(stream: Any, records: Sequence[dict[str, Any]]) -> None:
    for record in records:
        stream.write(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def search_files(
    query: str,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
    mode: str = "sentence",
    *,
    force: bool = False,
) -> tuple[list[dict[str, Any]], SearchOutputs]:
    settings = settings or Settings()
    options = settings.normalization
    check_prefix(prefix)
    outputs = SearchOutputs(
        prefix.with_name(prefix.name + ".csv"),
        prefix.with_name(prefix.name + ".jsonl"),
        prefix.with_name(prefix.name + ".run.json"),
    )
    check_input_collisions([outputs.csv, outputs.jsonl, outputs.run], inputs)
    check_outputs([outputs.csv, outputs.jsonl, outputs.run], force=force)
    tree = normalize_query(parse(query), options)
    texts = read_inputs(inputs)
    ids = [text.text_id for text in texts]
    if len(set(ids)) != len(ids):
        raise InputError("duplicate text_id among inputs")
    for text in texts:
        if text.text_type != "original":
            raise InputError(f"{text.path}: text_type {text.text_type!r} not matched")
    check_variants(
        settings,
        [
            (text.punctuation, text.full_stop, text.halfwidth_punctuation)
            for text in texts
        ],
    )
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    views: dict[str, TextView] = {}
    normalizations: dict[str, str] = {}
    for text in texts:
        opts = text_options(text, options)
        normalizations[text.text_id] = opts.name
        normalized = normalize(text.reference, opts)
        view = text_view(
            text.reference,
            normalized,
            sentence_delimiters(settings, text.full_stop, text.halfwidth_punctuation),
            text.boundary_start,
            text.boundary_end,
        )
        views[text.text_id] = view
        segments = (
            list(view.sentences)
            if mode == "sentence"
            else _segments(text, normalized, settings, mode)
        )
        warn = long_warning(segments, settings)
        if warn:
            warnings.append(text.text_id)
        records.extend(
            search_text(
                tree,
                query,
                text,
                normalized,
                settings,
                mode,
                segments=segments,
                warn_long=warn,
            )
        )
    for rank, record in enumerate(records, start=1):
        record["result_id"] = f"r{rank:06d}"
    titles = {text.text_id: text.title for text in texts}
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
    # Each output is streamed into its staged temporary file, and targets are
    # replaced only after every writer returns cleanly.
    with staged([outputs.jsonl, outputs.csv, outputs.run], force=force) as (
        jsonl_path,
        csv_path,
        run_path,
    ):
        with jsonl_path.open("w", encoding="utf-8", newline="") as stream:
            _write_jsonl(stream, records)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=COLUMNS,
                lineterminator="\n",
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            writer.writerows(
                {
                    column: excel_safe(value)
                    for column, value in csv_row(
                        record,
                        views,
                        titles,
                        run_id,
                        normalizations.get(record["text_id"], options.name),
                    ).items()
                }
                for record in records
            )
        with run_path.open("w", encoding="utf-8", newline="") as stream:
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
        # Spec 14.2: touching spans share one bracket, unlike the match CSV (11).
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
