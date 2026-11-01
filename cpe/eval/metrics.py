"""Evaluation against gold entries (plan 8.3; format of gold/g1/README.md).

Structural detection compares records with each entry's `expected` (required:
100 %). Grammatical usefulness compares records with the `usages`: recall of
valid usages with a Wilson interval and the list of misses, precision as a
range over the three treatments of `uncertain`, and slot accuracy. Nothing
here decides a pass or fail from a point estimate alone (decision U2).
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpe.eval import baselines

Record = dict[str, Any]
Runner = Callable[[dict[str, Any]], list[Record]]

_LABELS = ("valid", "lexicalized", "other_usage", "uncertain")
_KINDS = ("matched", "adjacent", "indeterminate")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_entries(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Gold entries with strict validation: a typo must fail loudly instead of
    silently changing the metrics (a "false" string counts as confirmed, an
    unknown label drops out of recall, a repeated id counts twice)."""
    return parse_entries((path, path.read_bytes()) for path in paths)


def parse_entries(snapshots: Iterable[tuple[Path, bytes]]) -> list[dict[str, Any]]:
    """Validate captured JSONL bytes; paths identify errors without being reread."""
    entries: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for path, content in snapshots:
        with io.StringIO(content.decode("utf-8"), newline="") as stream:
            for lineno, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                where = f"{path}:{lineno}"
                try:
                    entry = json.loads(line, object_pairs_hook=_unique_object)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{where}: not a JSON object ({error})") from None
                except ValueError as error:
                    raise ValueError(f"{where}: {error}") from None
                _validate_entry(entry, where)
                if entry["id"] in seen:
                    raise ValueError(
                        f"{where}: duplicate entry id {entry['id']!r} "
                        f"(first in {seen[entry['id']]})"
                    )
                seen[entry["id"]] = where
                entries.append(entry)
    return entries


def _validate_entry(entry: Any, where: str) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: gold entry must be an object")
    ident = entry.get("id")
    if not isinstance(ident, str) or not ident:
        raise ValueError(f"{where}: entry id must be a non-empty string")
    label = f"{where}: entry {ident!r}"
    if not isinstance(entry.get("pattern_id"), str) or not entry["pattern_id"]:
        raise ValueError(f"{label}: pattern_id must be a non-empty string")
    text = entry.get("text")
    if not isinstance(text, str):
        raise ValueError(f"{label}: text must be a string")
    if type(entry.get("confirmed")) is not bool:
        raise ValueError(f"{label}: confirmed must be true or false, without quotes")
    expected = entry.get("expected")
    if not isinstance(expected, list):
        raise ValueError(f"{label}: expected must be an array")
    for record in expected:
        _validate_record(record, len(text), label)
    usages = entry.get("usages")
    if not isinstance(usages, list):
        raise ValueError(f"{label}: usages must be an array")
    for usage in usages:
        _validate_usage(usage, len(text), label)
    for key, kind in (
        ("source", dict),
        ("input", dict),
        ("normalization", dict),
        ("case", str),
    ):
        if key in entry and not isinstance(entry[key], kind):
            raise ValueError(
                f"{label}: {key} must be a {kind.__name__}, not {entry[key]!r}"
            )
    overridden = {"reference", "text_id"} & entry.get("input", {}).keys()
    if overridden:
        raise ValueError(
            f"{label}: input cannot override {', '.join(sorted(overridden))}"
        )


def _span(span: Any, length: int, where: str, what: str) -> None:
    # bool is an int subclass: `type(...) is int` keeps [True, x] out, and
    # offsets are code points, so they must stay inside the entry text.
    if (
        not isinstance(span, list)
        or len(span) != 2
        or any(type(value) is not int for value in span)
        or not 0 <= span[0] < span[1] <= length
    ):
        raise ValueError(
            f"{where}: {what} must be a [start, end) span inside the "
            f"{length}-code-point text: {span!r}"
        )


def _validate_record(record: Any, length: int, label: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{label}: expected record must be an object")
    if record.get("kind") not in _KINDS:
        raise ValueError(
            f"{label}: record kind must be one of {', '.join(_KINDS)}, "
            f"not {record.get('kind')!r}"
        )
    fixed = record.get("fixed")
    if not isinstance(fixed, list):
        raise ValueError(f"{label}: record fixed must be an array")
    for span in fixed:
        _span(span, length, label, "fixed span")
    slots = record.get("slots")
    if not isinstance(slots, dict) or any(
        not isinstance(name, str) or not name for name in slots
    ):
        raise ValueError(f"{label}: record slots must map names to spans")
    for name, span in slots.items():
        _span(span, length, label, f"slot {name!r}")
    ends = record.get("ends")
    if ends is not None and (
        not isinstance(ends, list)
        or any(type(value) is not int or not 0 <= value <= length for value in ends)
    ):
        raise ValueError(
            f"{label}: record ends must be null or code points "
            f"inside the text: {ends!r}"
        )
    if record.get("reason") is not None and not isinstance(record.get("reason"), str):
        raise ValueError(f"{label}: record reason must be null or a string")


def _validate_usage(usage: Any, length: int, label: str) -> None:
    if not isinstance(usage, dict):
        raise ValueError(f"{label}: usage must be an object")
    fixed = usage.get("fixed_offsets")
    if not isinstance(fixed, list) or not fixed:
        raise ValueError(f"{label}: usage needs at least one fixed_offsets span")
    for span in fixed:
        _span(span, length, label, "fixed_offsets span")
    for key in ("slot_A", "slot_B"):
        spans = usage.get(key)
        if not isinstance(spans, list):
            raise ValueError(f"{label}: usage {key} must be an array")
        for span in spans:
            _span(span, length, label, f"{key} span")
    if usage.get("label") not in _LABELS:
        raise ValueError(
            f"{label}: usage label must be one of {', '.join(_LABELS)}, "
            f"not {usage.get('label')!r}"
        )
    for key in ("notes", "group"):
        if key in usage and not isinstance(usage[key], str):
            raise ValueError(f"{label}: usage {key} must be a string")


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """95 % Wilson score interval for k successes in n trials; None when n == 0."""
    if n == 0:
        return None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Count:
    k: int = 0
    n: int = 0

    def as_dict(self) -> dict[str, Any]:
        interval = wilson(self.k, self.n)
        return {
            "k": self.k,
            "n": self.n,
            "rate": None if self.n == 0 else self.k / self.n,
            "wilson95": None if interval is None else list(interval),
        }


def _precision(candidates: int, valid: int, uncertain: int) -> dict[str, Any]:
    def ratio(k: int, n: int) -> float | None:
        return None if n == 0 else k / n

    return {
        "candidates": candidates,
        "valid": valid,
        "uncertain": uncertain,
        "optimistic": ratio(valid + uncertain, candidates),
        "pessimistic": ratio(valid, candidates),
        "excluded": ratio(valid, candidates - uncertain),
    }


@dataclass
class Comparison:
    """Grammatical figures for a baseline with its explicitly named row unit."""

    name: str
    unit: str
    recall: Count = field(default_factory=Count)
    candidates: int = 0
    valid: int = 0
    uncertain: int = 0
    slot_a_exact: Count = field(default_factory=Count)
    slot_a_overlap: Count = field(default_factory=Count)
    b_in_representatives: Count = field(default_factory=Count)
    b_by_rule: dict[str, int] = field(default_factory=dict)
    misses: list[dict[str, Any]] = field(default_factory=list)
    errors: dict[str, dict[str, int]] = field(
        default_factory=lambda: {"misses": {}, "false_positives": {}}
    )
    candidate_characters: int = 0
    total_characters: int = 0
    unsupported: list[str] = field(default_factory=list)

    def space(self) -> dict[str, Any]:
        return _space(self.candidate_characters, self.total_characters)

    def as_dict(self) -> dict[str, Any]:
        slots = self.name == "regex_first_match"
        return {
            "name": self.name,
            "candidate_unit": self.unit,
            "recall": self.recall.as_dict(),
            "precision": _precision(self.candidates, self.valid, self.uncertain),
            "slot_a_exact": self.slot_a_exact.as_dict() if slots else None,
            "slot_a_overlap": self.slot_a_overlap.as_dict() if slots else None,
            "b_in_representatives": self.b_in_representatives.as_dict()
            if slots
            else None,
            "missed": self.misses,
            "search_space": self.space(),
            "error_types": self.errors,
            "unsupported_entries": self.unsupported,
        }


def _space(candidate: int, total: int) -> dict[str, Any]:
    fraction = candidate / total if total else None
    return {
        "candidate_characters": candidate,
        "total_characters": total,
        "retained_fraction": fraction,
        "reduction_rate": None if fraction is None else 1 - fraction,
    }


def _error(counts: dict[str, dict[str, int]], side: str, kind: str) -> None:
    counts[side][kind] = counts[side].get(kind, 0) + 1


@dataclass
class PatternReport:
    pattern_id: str
    entries: int = 0
    confirmed: int = 0
    structural_agree: int = 0
    structural_mismatches: list[str] = field(default_factory=list)
    interrupted: list[str] = field(default_factory=list)
    recall: Count = field(default_factory=Count)
    misses: list[dict[str, Any]] = field(default_factory=list)
    candidates: int = 0
    valid_hits: int = 0
    uncertain_hits: int = 0
    slot_a_exact: Count = field(default_factory=Count)
    slot_a_overlap: Count = field(default_factory=Count)
    b_in_representatives: Count = field(default_factory=Count)
    b_by_rule: dict[str, int] = field(default_factory=dict)
    adjacent_usages: Count = field(default_factory=Count)
    comparisons: dict[str, Comparison] = field(default_factory=dict)
    candidate_characters: int = 0
    total_characters: int = 0
    errors: dict[str, dict[str, int]] = field(
        default_factory=lambda: {"misses": {}, "false_positives": {}}
    )

    def as_dict(self) -> dict[str, Any]:
        candidates = self.candidates
        return {
            "pattern_id": self.pattern_id,
            "entries": self.entries,
            "confirmed": self.confirmed,
            "structural": {
                "agree": self.structural_agree,
                "of": self.entries,
                "mismatches": self.structural_mismatches,
                "limit_hits": self.interrupted,
            },
            "recall": self.recall.as_dict(),
            "missed": self.misses,
            "precision": _precision(candidates, self.valid_hits, self.uncertain_hits),
            "slot_a_exact": self.slot_a_exact.as_dict(),
            "slot_a_overlap": self.slot_a_overlap.as_dict(),
            "b_in_representatives": self.b_in_representatives.as_dict(),
            "b_by_rule": dict(sorted(self.b_by_rule.items())),
            "adjacent_usages_found": self.adjacent_usages.as_dict(),
            "search_space": _space(self.candidate_characters, self.total_characters),
            "error_types": self.errors,
            "baselines": {
                name: item.as_dict() for name, item in self.comparisons.items()
            },
        }


def structure(records: list[Record]) -> list[dict[str, Any]]:
    """Projection of records compared with `expected` (as tests/test_g1.py does)."""
    projected = [
        {
            "kind": record["kind"],
            "fixed": [fixed["src"] for fixed in record["fixed"]],
            "slots": {slot["name"]: slot["src"] for slot in record["slots"]},
            "ends": None
            if record["open_slot"] is None
            else [end["src"] for end in record["open_slot"]["ends"]],
            "reason": record["indeterminate_reason"],
        }
        for record in records
    ]
    return sorted(projected, key=json.dumps)


def _key(fixed: list[list[int]]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((start, end) for start, end in fixed))


def _overlaps(a: list[int], b: list[int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _adjacent_layer(
    text: str, fixed: list[list[int]], literals: list[set[str]]
) -> bool:
    """Whether a usage is the pattern's own fixed elements touching (以爲 layer).

    Touching spans of other words (such as 無不 for 非A不B) are ordinary usages.
    """
    ordered = sorted(fixed)
    if len(ordered) != len(literals) or any(
        text[start:end] not in options
        for (start, end), options in zip(ordered, literals, strict=True)
    ):
        return False
    return any(a[1] == b[0] for a, b in zip(ordered, ordered[1:], strict=False))


def evaluate(
    entries: list[dict[str, Any]],
    wire_for: Callable[[dict[str, Any]], dict[str, Any]],
    runner: Runner,
    confirmed_only: bool = False,
    literals: Callable[[dict[str, Any]], list[set[str]]] = lambda entry: [],
) -> dict[str, PatternReport]:
    reports: dict[str, PatternReport] = {}
    for entry in entries:
        if confirmed_only and not entry["confirmed"]:
            continue
        report = reports.setdefault(
            entry["pattern_id"], PatternReport(entry["pattern_id"])
        )
        report.entries += 1
        report.confirmed += bool(entry["confirmed"])
        wire = wire_for(entry)
        records = runner(wire)
        ctx = baselines.prepare(wire)
        alternatives = [u["fixed_offsets"] for u in entry["usages"]] + [
            [f["src"] for f in r["fixed"]] for r in records if r["fixed"]
        ]
        report.total_characters += len(entry["text"])
        report.candidate_characters += baselines.coverage(
            [
                tuple(r["segment"]["src"])
                for r in records
                if r["kind"] == "matched" and "segment" in r
            ],
            len(entry["text"]),
        )["candidate_characters"]
        if any(
            r["indeterminate_reason"] in ("state_budget", "result_budget")
            for r in records
        ):
            # A limit hit stays in the denominator as a failure (plan 8.3).
            report.interrupted.append(entry["id"])
        if structure(records) == sorted(entry["expected"], key=json.dumps):
            report.structural_agree += 1
        else:
            report.structural_mismatches.append(entry["id"])
        placements = {
            (r["kind"], tuple(tuple(f.get("norm", f["src"])) for f in r["fixed"])): r
            for r in records
            if r["kind"] in ("matched", "adjacent") and r["fixed"]
        }
        by_fixed: dict[tuple[tuple[int, int], ...], list[Record]] = {}
        for record in placements.values():
            by_fixed.setdefault(_key([f["src"] for f in record["fixed"]]), []).append(
                record
            )
        labelled: dict[tuple[tuple[int, int], ...], set[str]] = {}
        for usage in entry["usages"]:
            labelled.setdefault(_key(usage["fixed_offsets"]), set()).add(usage["label"])
        for record in placements.values():
            if record["kind"] != "matched":
                continue
            key = _key([f["src"] for f in record["fixed"]])
            report.candidates += 1
            labels = labelled.get(key, set())
            if "valid" in labels:
                report.valid_hits += 1
            elif "uncertain" in labels:
                report.uncertain_hits += 1
            else:
                _error(
                    report.errors,
                    "false_positives",
                    baselines.error_type(
                        ctx, [f["src"] for f in record["fixed"]], alternatives
                    ),
                )
        for usage in entry["usages"]:
            found = by_fixed.get(_key(usage["fixed_offsets"]), [])
            if _adjacent_layer(entry["text"], usage["fixed_offsets"], literals(entry)):
                report.adjacent_usages.n += 1
                report.adjacent_usages.k += any(r["kind"] == "adjacent" for r in found)
                continue
            if usage["label"] != "valid":
                continue
            report.recall.n += 1
            normal = [r for r in found if r["kind"] == "matched"]
            if not normal:
                report.misses.append(
                    {
                        "id": entry["id"],
                        "text": entry["text"],
                        "fixed": usage["fixed_offsets"],
                    }
                )
                _error(
                    report.errors,
                    "misses",
                    baselines.error_type(ctx, usage["fixed_offsets"], alternatives),
                )
                continue
            report.recall.k += 1
            _slots(report, normal, usage)
        if ctx is not None:
            _compare(report, entry, ctx, literals(entry))
    return reports


def _slots(
    report: PatternReport | Comparison,
    record: Record | list[Record],
    usage: dict[str, Any],
) -> None:
    records = record if isinstance(record, list) else [record]
    slots = [s["src"] for r in records for s in r["slots"] if s["name"] == "A"]
    if usage["slot_A"] and slots:
        report.slot_a_exact.n += 1
        report.slot_a_overlap.n += 1
        report.slot_a_exact.k += any(
            list(span) == slot_a for span in usage["slot_A"] for slot_a in slots
        )
        report.slot_a_overlap.k += any(
            _overlaps(span, slot_a) for span in usage["slot_A"] for slot_a in slots
        )
    opened = [r["open_slot"] for r in records if r["open_slot"] is not None]
    if usage["slot_B"] and opened:
        report.b_in_representatives.n += 1
        ends = {span[1] for span in usage["slot_B"]}
        chosen = [
            r for slot in opened for r in slot["representatives"] if r["src"] in ends
        ]
        if chosen:
            report.b_in_representatives.k += 1
            rule = chosen[0]["selected_by"]
            report.b_by_rule[rule] = report.b_by_rule.get(rule, 0) + 1


def _compare(
    report: PatternReport,
    entry: dict[str, Any],
    ctx: baselines.Context,
    literals: list[set[str]],
) -> None:
    usages = [
        u
        for u in entry["usages"]
        if not _adjacent_layer(entry["text"], u["fixed_offsets"], literals)
    ]
    alternatives = [u["fixed_offsets"] for u in entry["usages"]]
    methods = {
        "regex_first_match": ("placement", baselines.regex_candidates(ctx)),
        "keyword_cooccurrence": ("segment", baselines.keyword_candidates(ctx)),
    }
    for name, (unit, candidates) in methods.items():
        target = report.comparisons.setdefault(name, Comparison(name, unit))
        if (name == "regex_first_match" and not baselines.applicable(ctx)) or len(
            ctx.expression.fixed
        ) != 2:
            target.unsupported.append(entry["id"])
            continue
        target.total_characters += len(entry["text"])
        target.candidate_characters += baselines.coverage(
            [c.segment for c in candidates], len(entry["text"])
        )["candidate_characters"]

        def detects(
            candidate: baselines.Candidate, usage: dict[str, Any], method: str = name
        ) -> bool:
            if method == "regex_first_match":
                return _key(list(map(list, candidate.fixed))) == _key(
                    usage["fixed_offsets"]
                )
            return all(
                candidate.segment[0] <= a < b <= candidate.segment[1]
                for a, b in usage["fixed_offsets"]
            )

        for candidate in candidates:
            target.candidates += 1
            labels = {u["label"] for u in usages if detects(candidate, u)}
            if "valid" in labels:
                target.valid += 1
            elif "uncertain" in labels:
                target.uncertain += 1
            else:
                fixed = list(map(list, candidate.fixed))
                if not fixed:
                    # A segment row has no selected placement; use only actual
                    # literal spans to describe a feature of this negative row.
                    possible = [
                        span
                        for span in ctx.segments
                        if ctx.source(*span) == candidate.segment
                    ]
                    if possible:
                        start, end = possible[0]
                        fixed = [
                            list(ctx.source(*ctx.occurrences(i, start, end)[0]))
                            for i in (0, 1)
                        ]
                _error(
                    target.errors,
                    "false_positives",
                    baselines.error_type(ctx, fixed, alternatives),
                )
        for usage in usages:
            if usage["label"] != "valid":
                continue
            target.recall.n += 1
            found = next((c for c in candidates if detects(c, usage)), None)
            if found is None:
                target.misses.append(
                    {
                        "id": entry["id"],
                        "text": entry["text"],
                        "fixed": usage["fixed_offsets"],
                    }
                )
                _error(
                    target.errors,
                    "misses",
                    baselines.error_type(
                        ctx,
                        usage["fixed_offsets"],
                        alternatives
                        + [list(map(list, c.fixed)) for c in candidates if c.fixed],
                    ),
                )
            else:
                target.recall.k += 1
                if name == "regex_first_match":
                    _slots(target, found.record(), usage)
