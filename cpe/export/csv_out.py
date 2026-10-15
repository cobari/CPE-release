"""CSV output (plan section 9, spec section 11)."""

from __future__ import annotations

import bisect
import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cpe.match import segment as seg
from cpe.match.dsl import parse
from cpe.match.result import Result
from cpe.normalize.text import NormalizedText

Spans = Sequence[tuple[int, int]]

COLUMNS = (
    "result_id",
    "context_status",
    "match_status",
    "review_status",
    "run_id",
    "source",
    "text_id",
    "pattern_id",
    "pattern",
    "context_before",
    "segment",
    "context_after",
    "slot_A",
    "slot_B_candidates",
    "b_end_status",
    "b_start",
    "b_observed_end",
    "b_truncated",
    "alt_count",
    "offset_start",
    "offset_end",
    "title",
    "author",
    "author_birth",
    "link",
    "link_status",
    "grade",
    "evidence",
    "boundary_mode",
    "boundary_start",
    "boundary_end",
    "normalization",
)


def display(text: str) -> str:
    """Text as shown to the researcher: line breaks are layout, not shown."""
    return text.replace("\r", "").replace("\n", "")


def hidden_spans(normalized: NormalizedText) -> tuple[tuple[int, int], ...]:
    """Source spans never shown: notes and attached annotations, markers included.

    They are not running text (researcher decision, spec 11); offsets stay source
    positions, so a reader still finds them in the reference text.
    """
    found = (*normalized.notes, *normalized.annotations)
    return tuple(sorted((start, end) for _, start, end in found))


def shown(reference: str, start: int, end: int, hidden: Spans = ()) -> str:
    """`reference[start:end]` as shown: hidden spans and line breaks left out.

    `hidden` must be sorted and non-overlapping, as `hidden_spans` returns it.
    """
    parts = []
    cursor = start
    for span_start, span_end in hidden:
        if span_end <= cursor or span_start >= end:
            continue
        parts.append(reference[cursor:span_start])
        cursor = min(span_end, end)
    parts.append(reference[cursor:end])
    return display("".join(parts))


@dataclass(frozen=True)
class TextView:
    """One text as the CSV shows it: source, sentences and hidden spans."""

    reference: str
    normalized: NormalizedText
    # Sentence segments of the whole normalized text (spec 3.2, with the text's
    # full-stop role, 3.8), whatever the run's scope: context is read by sentence.
    sentences: tuple[seg.Segment, ...]
    hidden: tuple[tuple[int, int], ...]


def text_view(
    reference: str,
    normalized: NormalizedText,
    sentence_delimiters: frozenset[str],
    text_start: seg.TextEdge,
    text_end: seg.TextEdge,
) -> TextView:
    sentences = seg.split(normalized.text, sentence_delimiters, text_start, text_end)
    return TextView(reference, normalized, tuple(sentences), hidden_spans(normalized))


def context(
    view: TextView, norm_start: int, norm_end: int, src_start: int, src_end: int
) -> tuple[str, str]:
    """Source text before and after a segment, one sentence each way (spec 11).

    Before: from the start of the sentence preceding the one that holds the
    segment start, up to the segment. After: from the segment end through the
    sentence following the one that holds its last code point, with the
    boundary after it (delimiter and closing quotation marks, spec 3.2). Only
    text the source holds is shown: at an edge of the obtained text there is
    nothing more, and a truncated edge gets `…`.
    """
    sentences = view.sentences
    if not sentences:
        return "", ""
    norm = view.normalized
    starts = [sentence.start for sentence in sentences]
    first = max(bisect.bisect_right(starts, norm_start) - 1, 0)
    last = max(bisect.bisect_right(starts, max(norm_start, norm_end - 1)) - 1, 0)
    opening = sentences[max(first - 1, 0)]
    after_closing = min(last + 2, len(sentences))
    closing = sentences[after_closing - 1]
    begin = min(norm.src_start(opening.start), src_start)
    stop = (
        sentences[after_closing].start
        if after_closing < len(sentences)
        else len(norm.text)
    )
    finish = max(norm.src_span(closing.start, stop).end, src_end)
    before = escape(shown(view.reference, begin, src_start, view.hidden))
    after = escape(shown(view.reference, src_end, finish, view.hidden))
    if before and opening.boundary_start == "truncated":
        before = "…" + before
    if after and closing.boundary_end == "truncated":
        after = after + "…"
    return before, after


def row(
    result: Result,
    reference: str,
    run_id: str,
    title: str,
    normalization: str,
    paired: frozenset[tuple[int, int]] = frozenset(),
    hidden: Spans = (),
    around: tuple[str, str] = ("", ""),
) -> dict[str, str]:
    open_slot = result.open_slot
    values: dict[str, str] = {column: "" for column in COLUMNS}
    values.update(
        result_id=result.result_id,
        context_status=result.context_status,
        match_status=result.kind,
        review_status=result.review_status,
        run_id=run_id,
        source=result.source,
        text_id=result.text_id,
        pattern_id=result.pattern_id,
        pattern=result.pattern,
        context_before=around[0],
        segment=segment_display(result, reference, paired, hidden),
        context_after=around[1],
        alt_count=str(result.alt_count),
        title=title,
        grade=result.grade,
        evidence=result.evidence,
        boundary_mode=result.mode,
        boundary_start=result.segment.boundary_start,
        boundary_end=result.segment.boundary_end,
        normalization=normalization,
    )
    if result.slots:
        first = result.slots[0]
        values["slot_A"] = shown(reference, first.src.start, first.src.end, hidden)
    if result.fixed:
        values["offset_start"] = str(result.fixed[0].src.start)
        values["offset_end"] = str(result.fixed[-1].src.end)
    if open_slot is not None:
        b = open_slot.b_start.src
        values.update(
            slot_B_candidates="|".join(
                shown(reference, b, rep.end.src, hidden)
                for rep in open_slot.representatives
            ),
            b_end_status=open_slot.b_end_status,
            b_start=str(b),
            b_observed_end=str(open_slot.observed_end.src),
            b_truncated="true" if open_slot.truncated else "false",
        )
    return values


def segment_display(
    result: Result,
    reference: str,
    paired: frozenset[tuple[int, int]] = frozenset(),
    hidden: Spans = (),
) -> str:
    """Source text of the segment with every search term marked (spec 11)."""
    start, end = result.segment_src.start, result.segment_src.end
    strong = set(paired) | {(hit.src.start, hit.src.end) for hit in result.fixed}
    literals = pattern_literals(result.pattern)
    text = mark_terms(reference, start, end, strong, literals, hidden)
    if result.segment.boundary_start == "truncated":
        text = "…" + text
    if result.segment.boundary_end == "truncated":
        text = text + "…"
    return text


MARKUP = "【】〖〗\\"


def mark_terms(
    reference: str,
    start: int,
    end: int,
    strong: set[tuple[int, int]],
    literals: list[str],
    hidden: Spans = (),
) -> str:
    """`reference[start:end]` with search terms marked (spec 11).

    【】 marks the `strong` spans: terms that belong to a placement. 〖〗 marks,
    one level lower, other occurrences of the pattern's literals spelled as
    written; an occurrence overlapping a strong span is not shown, so a strong
    mark is never widened by a literal that no placement uses. Overlapping spans
    of one level share a bracket, touching ones (以爲) keep their own. Source
    characters that are themselves markup, 【 】 〖 〗 and the backslash, are
    shown preceded by a backslash so they cannot be read as marks. `hidden`
    spans (notes, attached annotations) and line breaks are left out, and so
    is any occurrence inside a hidden span.
    """
    strong_spans = _merge(span for span in strong if start <= span[0] < end)
    weak: set[tuple[int, int]] = set()
    body = reference[start:end]
    for literal in literals:
        index = body.find(literal)
        while index >= 0:
            span = (start + index, start + index + len(literal))
            overlaps = [*strong_spans, *hidden]
            if not any(span[0] < e and s < span[1] for s, e in overlaps):
                weak.add(span)
            index = body.find(literal, index + 1)
    marks = [(s, e, True) for s, e in strong_spans]
    marks += [(s, e, False) for s, e in _merge(weak)]
    parts = []
    cursor = start
    for span_start, span_end, is_strong in sorted(marks):
        opening, closing = ("【", "】") if is_strong else ("〖", "〗")
        parts.append(escape(shown(reference, cursor, span_start, hidden)))
        inner = shown(reference, span_start, span_end, hidden)
        parts.append(opening + escape(inner) + closing)
        cursor = span_end
    parts.append(escape(shown(reference, cursor, end, hidden)))
    return "".join(parts)


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for span_start, span_end in sorted(spans):
        if merged and span_start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span_end)
        else:
            merged.append([span_start, span_end])
    return [(s, e) for s, e in merged]


def escape(text: str) -> str:
    """Source markup characters preceded by a backslash (spec 11)."""
    return "".join("\\" + c if c in MARKUP else c for c in text)


def pattern_literals(pattern: str) -> list[str]:
    """Every option of every fixed element, from the parsed expression.

    Parsing (not splitting on spaces) keeps `not` literals and other slot
    syntax out of the search terms.
    """
    return [option for fixed in parse(pattern).fixed for option in fixed.options]


def write(
    path: Path,
    results: Iterable[Result],
    views: Mapping[str, TextView],
    titles: Mapping[str, str],
    run_id: str,
    normalization: str,
) -> None:
    results = list(results)
    # Fixed spans of every placement per pattern and segment, so each row can
    # tell terms that belong to some placement from stray occurrences.
    paired: dict[tuple[str, str, int, int], set[tuple[int, int]]] = {}
    for result in results:
        key = (
            result.text_id,
            result.pattern_id,
            result.segment.start,
            result.segment.end,
        )
        paired.setdefault(key, set()).update(
            (hit.src.start, hit.src.end) for hit in result.fixed
        )
    rows = [
        row(
            result,
            views[result.text_id].reference,
            run_id,
            titles.get(result.text_id, ""),
            normalization,
            frozenset(
                paired[
                    (
                        result.text_id,
                        result.pattern_id,
                        result.segment.start,
                        result.segment.end,
                    )
                ]
            ),
            views[result.text_id].hidden,
            context(
                views[result.text_id],
                result.segment.start,
                result.segment.end,
                result.segment_src.start,
                result.segment_src.end,
            ),
        )
        for result in results
    ]
    # utf-8-sig writes the BOM that lets Excel detect UTF-8 (plan 9).
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
