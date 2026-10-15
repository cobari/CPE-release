"""Compute reference records by direct Cartesian-product checks."""

from collections import Counter
from dataclasses import dataclass, replace
from itertools import product
from typing import Any

from ._dsl import Expression, Gap, Slot, parse
from ._text import Options, Text, normalize
from ._text import options as normalization_options

RULES = ("clause_boundary", "connective", "final_particle", "segment_end")
Record = dict[str, Any]
PathKey = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Settings:
    """Settings shared by every pattern and input."""

    sentence: str
    clause: str
    states: int
    results: int
    long_threshold: int


@dataclass(frozen=True)
class Segment:
    """A nonempty observable range and its factual edges."""

    start: int
    end: int
    left: str
    right: str


@dataclass(frozen=True)
class Diagnostic:
    """The internal constraint replaced by adjacency."""

    index: int
    name: str
    require_tail: bool


@dataclass(frozen=True)
class Pattern:
    """An independently parsed pattern and its display heuristics."""

    identifier: str
    expression: Expression
    diagnostics: tuple[Diagnostic, ...]
    connectives: tuple[str, ...]
    particles: tuple[str, ...]
    order: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    """An exhaustive prefix's count addition or a complete result."""

    path: PathKey
    states: int
    record: Record | None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _natural(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    values = tuple(_string(item, label) for item in _array(value, label))
    if any(not item for item in values):
        raise ValueError(f"{label} cannot contain empty literals")
    return values


def _settings(raw: dict[str, Any]) -> Settings:
    return Settings(
        _string(raw.get("sentence_delimiters", "。！？"), "sentence_delimiters"),
        _string(raw.get("clause_delimiters", "。！？，；："), "clause_delimiters"),
        _natural(raw.get("state_limit", 100_000), "state_limit"),
        _natural(raw.get("result_limit", 200), "result_limit"),
        _natural(raw.get("long_segment_threshold", 200), "long_segment_threshold"),
    )


def _pattern(raw: dict[str, Any], normalization: Options) -> Pattern:
    identifier = _string(raw["id"], "pattern id")
    expression = normalization.expression(parse(_string(raw["expr"], "pattern expr")))
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for value in _array(raw.get("diagnostics", []), "diagnostics"):
        item = _object(value, "diagnostic")
        if item.get("type") != "adjacent_fixed":
            raise ValueError("diagnostic type must be adjacent_fixed")
        name = _string(item.get("between_slot"), "between_slot")
        indices = [
            index
            for index, constraint in enumerate(expression.between)
            if isinstance(constraint, Slot) and constraint.name == name
        ]
        if not indices or name in seen:
            raise ValueError("diagnostic must name a distinct internal slot")
        if _natural(item.get("gap", 0), "diagnostic gap") != 0:
            raise ValueError("diagnostic gap must be zero")
        require_tail = item.get("require_tail", False)
        if type(require_tail) is not bool:
            raise ValueError("require_tail must be boolean")
        if require_tail and expression.tail is None:
            raise ValueError("require_tail needs an open slot")
        diagnostics.append(Diagnostic(indices[0], name, require_tail))
        seen.add(name)
    heuristics = _object(raw.get("heuristics", {}), "heuristics")
    order = _strings(heuristics.get("order", list(RULES)), "heuristics order")
    if not order or len(set(order)) != len(order) or not set(order) <= set(RULES):
        raise ValueError("heuristics order requires distinct known rule ids")
    return Pattern(
        identifier,
        expression,
        tuple(diagnostics),
        tuple(
            map(
                normalization.literal,
                _strings(
                    heuristics.get("connectives", ["而", "則", "以"]), "connectives"
                ),
            )
        ),
        tuple(
            map(
                normalization.literal,
                _strings(
                    heuristics.get("final_particles", ["也", "矣", "焉", "哉", "乎"]),
                    "final_particles",
                ),
            )
        ),
        order,
    )


def _segments(
    text: str, expr: Expression, left: str, right_edge: str, cfg: Settings
) -> list[Segment]:
    segments: list[Segment] = []
    if expr.window is not None:
        for anchor in range(len(text)):
            if any(
                text.startswith(literal, anchor) for literal in expr.fixed[0].options
            ):
                end = min(anchor + expr.window, len(text))
                right = right_edge if anchor + expr.window > len(text) else "inferred"
                segments.append(Segment(anchor, end, "inferred", right))
        return segments
    delimiters = cfg.sentence if expr.mode == "sentence" else cfg.clause
    start = 0
    after_delimiter = False
    for position in range(len(text) + 1):
        excluded = position == len(text) or text[position] in delimiters
        if position < len(text):
            excluded = excluded or (after_delimiter and text[position] in "」』”’")
            after_delimiter = excluded
        if excluded:
            if start < position:
                segments.append(
                    Segment(
                        start,
                        position,
                        left if start == 0 else "editorial",
                        right_edge if position == len(text) else "editorial",
                    )
                )
            start = position + 1
    return segments


def _boundary(position: int) -> Record:
    return {"norm": position, "src": position, "exact": True}


def _representatives(
    text: str, start: int, end: int, ends: list[int], pattern: Pattern, cfg: Settings
) -> list[Record]:
    if not ends:
        return []
    proposals = {
        "clause_boundary": {e for e in ends if e < end and text[e] in cfg.clause},
        "connective": {
            e
            for e in ends
            if any(
                e + len(c) <= end and text[e : e + len(c)] == c
                for c in pattern.connectives
            )
        },
        "final_particle": {
            e
            for e in ends
            if any(
                e - len(f) >= start and text[e - len(f) : e] == f
                for f in pattern.particles
            )
        },
        "segment_end": {end} if end in ends else set(),
    }
    selected: dict[int, str] = {}
    while len(selected) < 3:
        before = len(selected)
        for rule in pattern.order:
            remaining = proposals[rule] - selected.keys()
            if remaining:
                selected[min(remaining)] = rule
            if len(selected) == 3:
                break
        if len(selected) == before:
            break
    if len(selected) < 3 and max(ends) not in selected:
        selected[max(ends)] = "max_end"
    representatives: list[Record] = []
    for e, chosen in sorted(selected.items()):
        rules = [rule for rule in pattern.order if e in proposals[rule]]
        if e == max(ends):
            rules.append("max_end")
        representatives.append({**_boundary(e), "selected_by": chosen, "rules": rules})
    return representatives


def _base_record(
    text_id: str,
    extent: str,
    pattern: Pattern,
    segment: Segment,
    long_warning: bool,
    kind: str,
    reason: str | None = None,
) -> Record:
    edges = (segment.left, segment.right)
    effective = tuple(
        "inferred" if long_warning and value == "editorial" else value
        for value in edges
    )
    context = (
        "snippet"
        if "truncated" in effective
        else "window"
        if "inferred" in effective
        else "full"
    )
    evidence = "S1" if context != "full" else "S2" if extent == "excerpt" else "S3"
    grade = (
        "indeterminate"
        if kind == "indeterminate"
        else "adjacent_fixed"
        if kind == "adjacent"
        else f"{pattern.expression.mode}_pattern"
        if context == "full"
        else f"{context}_pattern"
    )
    span = [segment.start, segment.end]
    return {
        "schema": "cpe.result/1",
        "result_id": "",
        "kind": kind,
        "pattern_id": pattern.identifier,
        "pattern": pattern.expression.canonical,
        "source": "local",
        "text_id": text_id,
        "mode": pattern.expression.mode,
        "segment": {
            "norm": span.copy(),
            "src": span.copy(),
            "src_deleted": [],
            "boundary_start": segment.left,
            "boundary_end": segment.right,
        },
        "evidence": evidence,
        "context_status": context,
        "grade": grade,
        "editorial_boundaries": "editorial" in edges,
        "long_segment_warning": long_warning,
        "fixed": [],
        "slots": [],
        "open_slot": None,
        "alt_count": 0,
        "ambiguous": False,
        "adjacency": None,
        "joined_notes": [],
        "joined_annotations": [],
        "indeterminate_reason": reason,
        "review_status": "unreviewed",
    }


def _bounds(constraint: Slot | Gap | None) -> tuple[int, int]:
    if constraint is None:
        return 0, 0
    if isinstance(constraint, Gap):
        return 0, constraint.maximum
    return constraint.minimum, constraint.maximum


def _accepts(
    text: str,
    segment: Segment,
    positions: tuple[int, ...],
    literals: tuple[str, ...],
    constraints: tuple[Slot | Gap | None, ...],
    window: bool,
    barriers: tuple[int, ...],
) -> bool:
    if window and positions[0] != segment.start:
        return False
    for position, literal in zip(positions, literals, strict=True):
        if position + len(literal) > segment.end:
            return False
        if text[position : position + len(literal)] != literal:
            return False
    for index in range(len(positions) - 1):
        begin = positions[index] + len(literals[index])
        stop = positions[index + 1]
        constraint = constraints[index]
        low, high = _bounds(constraint)
        if not low <= stop - begin <= high:
            return False
        if isinstance(constraint, Slot) and any(
            forbidden in text[begin:stop] for forbidden in constraint.forbidden
        ):
            return False
    return not barriers or not any(
        positions[0] < q < positions[-1] + len(literals[-1]) for q in barriers
    )


def _placement_record(
    text: str,
    text_id: str,
    extent: str,
    pattern: Pattern,
    cfg: Settings,
    segment: Segment,
    long_warning: bool,
    positions: tuple[int, ...],
    literals: tuple[str, ...],
    diagnostic: Diagnostic | None,
    normalized: Text,
) -> Record | None:
    expr = pattern.expression
    begin = positions[-1] + len(literals[-1])
    tail = expr.tail
    ends: list[int] = []
    kind = "matched" if diagnostic is None else "adjacent"
    reason: str | None = None
    if tail is not None:
        ends = [
            e
            for e in range(begin, segment.end + 1)
            if tail.minimum <= e - begin <= tail.maximum
            and not any(begin <= q < e for q in normalized.barriers)
            and not any(forbidden in text[begin:e] for forbidden in tail.forbidden)
        ]
        if not ends and (diagnostic is None or diagnostic.require_tail):
            if (
                segment.right == "truncated"
                and begin + tail.maximum > segment.end
                and not any(begin <= q <= segment.end for q in normalized.barriers)
                and not any(
                    forbidden in text[begin : segment.end]
                    for forbidden in tail.forbidden
                )
            ):
                kind, reason = "indeterminate", "empty_tail_truncated"
            else:
                return None
    record = _base_record(text_id, extent, pattern, segment, long_warning, kind, reason)
    record["fixed"] = [
        {
            "literal": literal,
            "norm": [position, position + len(literal)],
            "src": [position, position + len(literal)],
            "src_exact": [True, True],
        }
        for position, literal in zip(positions, literals, strict=True)
    ]
    slots: list[Record] = []
    for index, constraint in enumerate(expr.between):
        if isinstance(constraint, Slot) and (
            diagnostic is None or index != diagnostic.index
        ):
            span = [positions[index] + len(literals[index]), positions[index + 1]]
            slots.append(
                {
                    "name": constraint.name,
                    "norm": span.copy(),
                    "src": span.copy(),
                    "src_exact": [True, True],
                    "src_deleted": [],
                }
            )
    record["slots"] = slots
    if kind == "adjacent" and diagnostic is not None:
        index = diagnostic.index
        left = positions[index] + len(literals[index])
        right = positions[index + 1]
        adjacency = (
            "source"
            if normalized.boundary(left)["src"]
            == normalized.boundary(right, True)["src"]
            else "normalized"
        )
        record["adjacency"] = {"slot": diagnostic.name, "kind": adjacency}
    if tail is not None and kind != "indeterminate":
        observed = min(
            begin + tail.maximum,
            segment.end,
            *(q for q in normalized.barriers if q >= begin),
        )
        record["open_slot"] = {
            "name": tail.name,
            "b_start": _boundary(begin),
            "ends": [_boundary(e) for e in ends],
            "observed_end": _boundary(observed),
            "truncated": segment.right == "truncated" and observed == segment.end,
            "representatives": _representatives(
                text, begin, segment.end, ends, pattern, cfg
            ),
            "b_end_status": "open",
        }
    return record


def _events(
    text: str,
    text_id: str,
    extent: str,
    pattern: Pattern,
    cfg: Settings,
    segment: Segment,
    long_warning: bool,
    diagnostic: Diagnostic | None,
    normalized: Text,
) -> list[Event]:
    expr = pattern.expression
    constraints = tuple(
        None if diagnostic is not None and index == diagnostic.index else constraint
        for index, constraint in enumerate(expr.between)
    )
    window = expr.window is not None
    initial = sum(
        1
        for position in range(segment.start, segment.end)
        for literal in expr.fixed[0].options
        if (not window or position == segment.start)
        and position + len(literal) <= segment.end
        and text[position : position + len(literal)] == literal
    )
    events = [Event((), initial, None)]
    for length in range(1, len(expr.fixed) + 1):
        for positions in product(range(segment.start, segment.end), repeat=length):
            choices = (range(len(fixed.options)) for fixed in expr.fixed[:length])
            for options in product(*choices):
                literals = tuple(
                    expr.fixed[index].options[option]
                    for index, option in enumerate(options)
                )
                if not _accepts(
                    text,
                    segment,
                    positions,
                    literals,
                    constraints,
                    window,
                    normalized.barriers,
                ):
                    continue
                path = tuple(zip(positions, options, strict=True))
                if length < len(expr.fixed):
                    low, high = _bounds(constraints[length - 1])
                    begin = positions[-1] + len(literals[-1])
                    count = sum(
                        1
                        for position in range(segment.start, segment.end)
                        for literal in expr.fixed[length].options
                        if low <= position - begin <= high
                        and position + len(literal) <= segment.end
                        and text[position : position + len(literal)] == literal
                    )
                    events.append(Event(path, count, None))
                else:
                    record = _placement_record(
                        text,
                        text_id,
                        extent,
                        pattern,
                        cfg,
                        segment,
                        long_warning,
                        positions,
                        literals,
                        diagnostic,
                        normalized,
                    )
                    if record is not None:
                        events.append(Event(path, 0, record))
    # Prefix paths sort before their descendants; replay then reproduces the
    # specified budget order without a recursive or pruned placement search.
    return sorted(events, key=lambda event: event.path)


def _search_segment(
    text: str,
    text_id: str,
    extent: str,
    pattern: Pattern,
    cfg: Settings,
    segment: Segment,
    long_warning: bool,
    normalized: Text,
) -> list[Record]:
    records: list[Record] = []
    states = 0
    branches: tuple[Diagnostic | None, ...] = (None, *pattern.diagnostics)
    for diagnostic in branches:
        events = _events(
            text,
            text_id,
            extent,
            pattern,
            cfg,
            segment,
            long_warning,
            diagnostic,
            normalized,
        )
        for event in events:
            states += event.states
            reason = "state_budget" if states > cfg.states else None
            if event.record is not None and len(records) == cfg.results:
                reason = "result_budget"
            if reason is not None:
                return [
                    _base_record(
                        text_id,
                        extent,
                        pattern,
                        segment,
                        long_warning,
                        "indeterminate",
                        reason,
                    )
                ]
            if event.record is not None:
                records.append(event.record)
    return records


def _sort_key(item: tuple[int, int, Record]) -> tuple[Any, ...]:
    input_index, pattern_index, record = item
    fixed = record["fixed"]
    segment = record["segment"]["norm"]
    anchor = fixed[0]["norm"][0] if fixed else segment[0]
    kinds = {"matched": 0, "adjacent": 1, "indeterminate": 2}
    return (
        input_index,
        pattern_index,
        anchor,
        kinds[record["kind"]],
        tuple(tuple(f["norm"]) for f in fixed),
        tuple(segment),
    )


def run(wire: dict[str, Any]) -> list[Record]:
    """Return full ordered TXT records from the specification's wire input."""
    raw_settings = _object(wire.get("settings", {}), "settings")
    cfg = _settings(raw_settings)
    texts: list[tuple[str, str, str, str, str, str, str]] = []
    for value in _array(wire["texts"], "texts"):
        item = _object(value, "text")
        identifier = _string(item["text_id"], "text_id")
        reference = _string(item["reference"], "reference")
        extent = _string(item.get("range", "excerpt"), "range")
        if extent not in ("excerpt", "whole_work", "volume"):
            raise ValueError("range must be excerpt, whole_work, or volume")
        if item.get("text_type", "original") != "original":
            raise ValueError("only original text_type is supported")
        punctuation = _string(item.get("punctuation", "modern"), "punctuation")
        if punctuation not in ("modern", "pause"):
            raise ValueError("punctuation must be modern or pause")
        full_stop = _string(item.get("full_stop", "sentence"), "full_stop")
        if full_stop not in ("sentence", "reading"):
            raise ValueError("full_stop must be sentence or reading")
        left = _string(item.get("boundary_start", "truncated"), "boundary_start")
        right = _string(item.get("boundary_end", "truncated"), "boundary_end")
        if left not in ("verified", "truncated") or right not in (
            "verified",
            "truncated",
        ):
            raise ValueError("text boundaries must be verified or truncated")
        texts.append(
            (identifier, reference, extent, left, right, punctuation, full_stop)
        )
    if len({text[0] for text in texts}) != len(texts):
        raise ValueError("duplicate text_id")
    delimiters = cfg.sentence + cfg.clause
    if any(text[5] == "pause" for text in texts):
        delimiters += "、"
    normalization = normalization_options(
        raw_settings.get("normalization", {}), delimiters
    )
    patterns = [
        _pattern(_object(item, "pattern"), normalization)
        for item in _array(wire["patterns"], "patterns")
    ]
    if len({pattern.identifier for pattern in patterns}) != len(patterns):
        raise ValueError("duplicate pattern id")
    output: list[tuple[int, int, Record]] = []
    for input_index, (
        text_id,
        reference,
        extent,
        left,
        right,
        punctuation,
        full_stop,
    ) in enumerate(texts):
        text_cfg = (
            replace(cfg, clause=cfg.clause + "、") if punctuation == "pause" else cfg
        )
        if full_stop == "reading":
            text_cfg = replace(text_cfg, sentence=cfg.sentence.replace("。", ""))
        normalized = normalize(reference, normalization)
        for pattern_index, pattern in enumerate(patterns):
            segments = _segments(
                normalized.value, pattern.expression, left, right, text_cfg
            )
            long_warning = (
                pattern.expression.mode != "window"
                and bool(segments)
                and sum(s.end - s.start for s in segments)
                > cfg.long_threshold * len(segments)
            )
            for segment in segments:
                records = _search_segment(
                    normalized.value,
                    text_id,
                    extent,
                    pattern,
                    text_cfg,
                    segment,
                    long_warning,
                    normalized,
                )
                for record in records:
                    normalized.record(record)
                output.extend(
                    (input_index, pattern_index, record) for record in records
                )
    counts = Counter(
        (
            record["pattern_id"],
            record["text_id"],
            record["kind"],
            record["fixed"][0]["norm"][0],
        )
        for _, _, record in output
        if record["fixed"]
    )
    records = []
    for rank, (_, _, record) in enumerate(sorted(output, key=_sort_key), start=1):
        if record["fixed"]:
            group = (
                record["pattern_id"],
                record["text_id"],
                record["kind"],
                record["fixed"][0]["norm"][0],
            )
            record["alt_count"] = counts[group] - 1
            record["ambiguous"] = record["alt_count"] > 0
        record["result_id"] = f"r{rank:06d}"
        records.append(record)
    return records
