"""Conventional regex and segment co-occurrence comparisons (plan 8.3).

These are usefulness baselines, not structural oracles. Their candidates use
the same normalized text and segment delimiters, but regex selects one match
per start and co-occurrence has no order or slot constraints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cpe.eval.reference._dsl import Expression, Slot, parse
from cpe.eval.reference._text import Options, Text, normalize, options

ERROR_TYPES = (
    "boundary",
    "adjacent_fixed",
    "variant_spelling",
    "length_limit",
    "note_barrier",
    "multiple_placement",
    "other",
)


@dataclass(frozen=True)
class Candidate:
    """Source spans for an actual regex hit, or an unslotted segment candidate."""

    segment: tuple[int, int]
    fixed: tuple[tuple[int, int], ...] = ()
    a: tuple[int, int] | None = None
    b_end: int | None = None

    def record(self) -> dict[str, Any]:
        return {
            "slots": [] if self.a is None else [{"name": "A", "src": list(self.a)}],
            "open_slot": None
            if self.b_end is None
            else {
                "representatives": [
                    {"src": self.b_end, "selected_by": "regex_first_match"}
                ],
            },
        }


@dataclass(frozen=True)
class Context:
    """One evaluation snapshot and its independently derived segment spans."""

    reference: str
    text: Text
    expression: Expression
    original: Expression
    cfg: Options
    delimiters: str
    clause: str
    segments: tuple[tuple[int, int], ...]

    def source(self, start: int, end: int) -> tuple[int, int]:
        mapped = self.text.span(start, end)["src"]
        return int(mapped[0]), int(mapped[1])

    def normalized(self, start: int, end: int) -> tuple[int, int] | None:
        if self.text.sources is None:
            return start, end
        retained = [
            i for i, source in enumerate(self.text.sources) if start <= source < end
        ]
        return (retained[0], retained[-1] + 1) if retained else None

    def crosses(self, start: int, end: int) -> bool:
        return any(start < q < end for q in self.text.barriers)

    def occurrences(self, index: int, start: int, end: int) -> list[tuple[int, int]]:
        return sorted(
            {
                (p, p + len(literal))
                for literal in self.expression.fixed[index].options
                for p in range(start, end - len(literal) + 1)
                if self.text.value[p : p + len(literal)] == literal
                and not self.crosses(p, p + len(literal))
            }
        )


def prepare(wire: dict[str, Any]) -> Context | None:
    """Prepare the one-text, one-pattern wire used by the gold evaluator."""
    if len(wire.get("patterns", [])) != 1 or len(wire.get("texts", [])) != 1:
        return None
    item, pattern = wire["texts"][0], wire["patterns"][0]
    raw = wire.get("settings", {})
    sentence, clause = (
        raw.get("sentence_delimiters", "。！？"),
        raw.get("clause_delimiters", "。！？，；："),
    )
    if item.get("punctuation") == "pause":
        clause += "、"
    full_stop = item.get("full_stop", "sentence")
    if full_stop not in ("sentence", "reading"):
        raise ValueError("invalid full_stop")
    cfg = options(raw.get("normalization", {}), sentence + clause)
    if full_stop == "reading":
        sentence = sentence.replace("。", "")
    original = parse(pattern["expr"])
    expression = cfg.expression(original)
    reference = item["reference"]
    text = normalize(reference, cfg)
    delimiters = sentence if expression.mode == "sentence" else clause
    segments: list[tuple[int, int]] = []
    if expression.window is not None:
        segments = [
            (a, min(a + expression.window, len(text.value)))
            for a, _ in Context(
                reference, text, expression, original, cfg, delimiters, clause, ()
            ).occurrences(0, 0, len(text.value))
        ]
    else:
        start = 0
        for end in range(len(text.value) + 1):
            if end == len(text.value) or text.value[end] in delimiters:
                if start < end:
                    segments.append((start, end))
                start = end + 1
    return Context(
        reference,
        text,
        expression,
        original,
        cfg,
        delimiters,
        clause,
        tuple(sorted(set(segments))),
    )


def applicable(ctx: Context) -> bool:
    expr = ctx.expression
    return (
        len(expr.fixed) == 2
        and len(expr.between) == 1
        and isinstance(expr.between[0], Slot)
        and expr.tail is not None
    )


def regex_candidates(ctx: Context) -> list[Candidate]:
    if not applicable(ctx):
        return []
    expr = ctx.expression
    a, b = expr.between[0], expr.tail
    assert isinstance(a, Slot) and b is not None
    fixed = [
        "(?:" + "|".join(re.escape(x) for x in f.options) + ")" for f in expr.fixed
    ]
    tail = "[^" + re.escape(ctx.clause) + "]" if ctx.clause else "[\\s\\S]"
    compiled = re.compile(
        f"(?P<f1>{fixed[0]})(?P<a>.{{{a.minimum},{a.maximum}}}?)(?P<f2>{fixed[1]})(?P<b>{tail}{{{b.minimum},{b.maximum}}})"
    )
    found: dict[tuple[tuple[int, int], ...], Candidate] = {}
    for start, end in ctx.segments:
        # A note barrier bounds the observable tail and never becomes a source
        # punctuation edge. Splitting here only bounds the regex search region.
        cuts = [start, *(q for q in ctx.text.barriers if start < q < end), end]
        for left, right in zip(cuts, cuts[1:], strict=False):
            starts = (
                range(left, right)
                if expr.window is None
                else range(start, start + 1)
                if left == start
                else range(0)
            )
            for position in starts:
                match = compiled.match(ctx.text.value, position, right)
                if (
                    match is None
                    or any(x in match["a"] for x in a.forbidden)
                    or any(x in match["b"] for x in b.forbidden)
                ):
                    continue
                spans = tuple(ctx.source(*match.span(name)) for name in ("f1", "f2"))
                # Window overlap can expose the same placement more than once.
                found.setdefault(
                    tuple(match.span(name) for name in ("f1", "f2")),
                    Candidate(
                        ctx.source(start, end),
                        spans,
                        ctx.source(*match.span("a")),
                        ctx.text.boundary(match.end("b"))["src"],
                    ),
                )
    return list(found.values())


def keyword_candidates(ctx: Context) -> list[Candidate]:
    if len(ctx.expression.fixed) != 2:
        return []
    return [
        Candidate(ctx.source(start, end))
        for start, end in ctx.segments
        if all(ctx.occurrences(i, start, end) for i in (0, 1))
    ]


def coverage(spans: list[tuple[int, int]], total: int) -> dict[str, Any]:
    """Union source characters, counted once per entry; punctuation stays in total."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    characters = sum(end - start for start, end in merged)
    retained = None if not total else characters / total
    return {
        "candidate_characters": characters,
        "total_characters": total,
        "retained_fraction": retained,
        "reduction_rate": None if retained is None else 1 - retained,
    }


def error_type(
    ctx: Context | None, fixed: list[list[int]], alternatives: list[list[list[int]]]
) -> str:
    """Classify observed features in a fixed priority, not a causal diagnosis.

    Case names and prose annotations are deliberately not used. Unexplained
    grammatical differences remain other instead of manufacturing a cause.
    """
    if ctx is None or len(fixed) != len(ctx.expression.fixed):
        return "other"
    if fixed != sorted(fixed):
        return "other"
    if any(a[1] > b[0] for a, b in zip(fixed, fixed[1:], strict=False)):
        # A source alias inside an expansion does not reveal which normalized
        # positions were intended by the annotation; do not invent a diagnosis.
        return "other"
    ordered = sorted(fixed)
    norm = [ctx.normalized(*span) for span in ordered]
    if any(span is None for span in norm):
        return "other"
    positions = [span for span in norm if span is not None]
    if ctx.expression.window is None and any(
        c in ctx.delimiters for c in ctx.text.value[positions[0][0] : positions[-1][1]]
    ):
        return "boundary"
    if any(
        a[1] == b[0] for a, b in zip(positions, positions[1:], strict=False)
    ) and all(
        ctx.text.value[s:e] in element.options
        for (s, e), element in zip(positions, ctx.expression.fixed, strict=True)
    ):
        return "adjacent_fixed"
    if any(
        any(c in ctx.cfg.mapping for c in ctx.reference[s:e])
        and ctx.reference[s:e] not in element.options
        and ctx.cfg.characters(ctx.reference[s:e]) in normalized.options
        for (s, e), element, normalized in zip(
            ordered, ctx.original.fixed, ctx.expression.fixed, strict=True
        )
    ):
        return "variant_spelling"
    for index, constraint in enumerate(ctx.expression.between):
        if isinstance(constraint, Slot):
            length = positions[index + 1][0] - positions[index][1]
            if length < constraint.minimum or length > constraint.maximum:
                return "length_limit"
    tail = ctx.expression.tail
    if tail is not None:
        containing = next(
            (e for s, e in ctx.segments if s <= positions[-1][0] < e), positions[-1][1]
        )
        if containing - positions[-1][1] < tail.minimum:
            return "length_limit"
    if ctx.crosses(positions[0][0], positions[-1][1]):
        return "note_barrier"
    if tail is not None and any(
        positions[-1][1] <= q < positions[-1][1] + tail.minimum
        for q in ctx.text.barriers
    ):
        return "note_barrier"
    if any(
        other != fixed and other and ordered and sorted(other)[0] == ordered[0]
        for other in alternatives
    ):
        return "multiple_placement"
    return "other"
