"""Segments, boundary provenance, evidence and grades (spec sections 3 and 9)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

Provenance = Literal["verified", "editorial", "inferred", "truncated"]
Range = Literal["whole_work", "volume", "excerpt"]

SENTENCE_DELIMITERS = frozenset("。！？")
CLAUSE_DELIMITERS = SENTENCE_DELIMITERS | frozenset("，；：")
# Halfwidth forms a text may declare as punctuation (spec 3.9): . ? ! like 。？！ and
# , ; : like ，；：. U+FF61 ｡ is the halfwidth form of 。 itself.
HALFWIDTH_FULL_STOPS = frozenset(".｡")
HALFWIDTH_SENTENCE = HALFWIDTH_FULL_STOPS | frozenset("?!")
HALFWIDTH_CLAUSE = HALFWIDTH_SENTENCE | frozenset(",;:")
# Closing quotation marks: directly after a delimiter they close the segment before
# it (spec 3.2); anywhere else they are segment text.
CLOSING_QUOTES = frozenset("」』”’")


@dataclass(frozen=True)
class Segment:
    """A normalized span `[start, end)` and the provenance of each edge."""

    start: int
    end: int
    boundary_start: Provenance
    boundary_end: Provenance


TextEdge = Literal["verified", "truncated"]
Punctuation = Literal["modern", "pause"]
FullStop = Literal["sentence", "reading"]
IDEOGRAPHIC_COMMA = chr(0x3001)
FULL_STOP = chr(0x3002)


@cache
def _boundary_pattern(delimiters: frozenset[str]) -> re.Pattern[str]:
    delim_chars = "".join(re.escape(c) for c in sorted(delimiters))
    trail_chars = "".join(re.escape(c) for c in sorted(delimiters | CLOSING_QUOTES))
    return re.compile(f"[{delim_chars}][{trail_chars}]*")


def split(
    text: str,
    delimiters: frozenset[str],
    text_start: TextEdge = "truncated",
    text_end: TextEdge = "truncated",
) -> list[Segment]:
    """Maximal non-empty runs of non-delimiter code points (spec 3.2, 3.3).

    A closing quotation mark right after a delimiter, or after such a mark, is
    part of the boundary like the delimiter: in `乎？」子曰` the next segment
    starts at 子, not at 」.
    """
    if not text:
        return []
    if not delimiters:
        return [Segment(0, len(text), text_start, text_end)]
    segments: list[Segment] = []
    start = 0
    for match in _boundary_pattern(delimiters).finditer(text):
        end = match.start()
        if end > start:
            segments.append(
                Segment(
                    start,
                    end,
                    text_start if start == 0 else "editorial",
                    "editorial",
                )
            )
        start = match.end()
    if start < len(text):
        segments.append(
            Segment(
                start,
                len(text),
                text_start if start == 0 else "editorial",
                text_end,
            )
        )
    return segments


def window(
    anchor: int, size: int, text_length: int, text_end: TextEdge = "truncated"
) -> Segment:
    """The window anchored at a first-fixed-element position (spec 3.4)."""
    if anchor + size > text_length:
        return Segment(anchor, text_length, "inferred", text_end)
    return Segment(anchor, anchor + size, "inferred", "inferred")


def effective(segment: Segment) -> Segment:
    """Edges used for grading in a long-segment text (spec 3.5).

    Only `editorial` edges become `inferred`; `truncated` must survive, otherwise a
    cut excerpt edge would be graded as a window instead of a snippet.
    """

    def lower(edge: Provenance) -> Provenance:
        return "inferred" if edge == "editorial" else edge

    return Segment(
        segment.start,
        segment.end,
        lower(segment.boundary_start),
        lower(segment.boundary_end),
    )


Evidence = Literal["S1", "S2", "S3"]
Context = Literal["full", "snippet", "window"]


def evidence(segment: Segment, range_: Range) -> Evidence:
    edges = (segment.boundary_start, segment.boundary_end)
    if any(edge in ("truncated", "inferred") for edge in edges):
        return "S1"
    return "S2" if range_ == "excerpt" else "S3"


def context_status(segment: Segment) -> Context:
    edges = (segment.boundary_start, segment.boundary_end)
    if "truncated" in edges:
        return "snippet"
    if "inferred" in edges:
        return "window"
    return "full"


def grade(kind: str, context: Context, mode: str) -> str:
    """Grade by result kind and context (spec 9.3)."""
    if kind == "indeterminate":
        return "indeterminate"
    if kind == "adjacent":
        return "adjacent_fixed"
    if context == "snippet":
        return "snippet_pattern"
    if context == "window":
        return "window_pattern"
    if mode == "sentence":
        return "sentence_pattern"
    if mode == "clause":
        return "clause_pattern"
    # A window never has context "full" (spec 3.4); reaching here means the
    # window edges were built wrongly, and emitting a sentence grade would
    # claim more than the evidence.
    raise ValueError(f"context 'full' is impossible in mode {mode!r}")
