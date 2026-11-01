"""`cpe eval`: evaluation report over gold JSONL files (plan 8.3, M4)."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from cpe.eval.metrics import evaluate, parse_entries
from cpe.export.safe import check_input_collisions, check_outputs, staged
from cpe.match.api import run as run_matcher
from cpe.match.dsl import parse as parse_expression
from cpe.match.pattern_file import loads as load_patterns


def patterns_from(path: Path) -> dict[str, dict[str, Any]]:
    return _parse_patterns(path.read_bytes(), path)


def _parse_patterns(raw: bytes, path: Path) -> dict[str, dict[str, Any]]:
    # Validate before building the ID map; duplicate IDs would replace earlier
    # patterns and hide a malformed configuration.
    load_patterns(raw, str(path))
    data = tomllib.loads(raw.decode("utf-8"))
    return {entry["id"]: entry for entry in data.get("pattern", [])}


def wire_builder(patterns: dict[str, dict[str, Any]]) -> Any:
    def wire_for(entry: dict[str, Any]) -> dict[str, Any]:
        pattern = patterns[entry["pattern_id"]]
        return {
            "texts": [
                {
                    "text_id": entry["id"],
                    "reference": entry["text"],
                    **entry.get("input", {}),
                }
            ],
            "patterns": [
                {
                    "id": pattern["id"],
                    "expr": pattern["expr"],
                    "diagnostics": pattern.get("diagnostics", []),
                    **(
                        {"heuristics": pattern["heuristics"]}
                        if "heuristics" in pattern
                        else {}
                    ),
                }
            ],
            "settings": {"normalization": entry.get("normalization", {})},
        }

    return wire_for


def build(
    gold: Sequence[Path], patterns_path: Path, confirmed_only: bool = False
) -> dict[str, Any]:
    gold_snapshots: list[dict[str, str]] = []

    def capture_gold() -> Iterator[tuple[Path, bytes]]:
        for path in gold:
            content = path.read_bytes()
            gold_snapshots.append(
                {"path": path.as_posix(), "sha256": hashlib.sha256(content).hexdigest()}
            )
            yield path, content

    entries = parse_entries(capture_gold())
    pattern_bytes = patterns_path.read_bytes()
    patterns = _parse_patterns(pattern_bytes, patterns_path)
    for entry in entries:
        if entry["pattern_id"] not in patterns:
            raise ValueError(
                f"entry {entry['id']!r}: unknown pattern_id {entry['pattern_id']!r}"
            )

    def literals(entry: dict[str, Any]) -> list[set[str]]:
        # Fixed elements come from the parsed DSL structure: splitting the raw
        # expression would also count words inside slots (such as the `not`
        # in {A not 甲}) as fixed elements.
        expr = patterns[entry["pattern_id"]]["expr"]
        return [set(fixed.options) for fixed in parse_expression(expr).fixed]

    reports = evaluate(
        entries, wire_builder(patterns), run_matcher, confirmed_only, literals
    )
    return {
        "schema": "cpe.eval/1",
        "gold": [path.as_posix() for path in gold],
        "patterns": patterns_path.as_posix(),
        "input_snapshots": {
            "gold": gold_snapshots,
            "patterns": {
                "path": patterns_path.as_posix(),
                "sha256": hashlib.sha256(pattern_bytes).hexdigest(),
            },
        },
        "confirmed_only": confirmed_only,
        "patterns_evaluated": [reports[key].as_dict() for key in sorted(reports)],
    }


def _count(value: dict[str, Any]) -> str:
    if value["n"] == 0:
        return "- (n = 0)"
    low, high = value["wilson95"]
    rate = value["rate"]
    return f"{value['k']}/{value['n']} ({rate:.1%}; 95% Wilson {low:.1%}-{high:.1%})"


def _ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"Gold: {', '.join(data['gold'])}. Patterns: {data['patterns']}. "
        f"Confirmed entries only: {'yes' if data['confirmed_only'] else 'no'}.",
        "",
        "Structural detection must agree on every entry (gate 1-A, with the oracle "
        "comparison of tests/test_differential.py). Grammatical figures are reported "
        "as counts with intervals and missed cases; no pass or fail is derived from a "
        "point estimate (decision U2).",
        "",
        "Baselines use the same input normalization and sentence/clause/window "
        "segments. The conventional regex has a non-greedy A and a greedy B that "
        "stops at a clause delimiter or the maximum length; its ordinary dot does "
        "not match LF. It selects one match per overlapping start and does not "
        "enumerate every placement at that start. Note barriers bound its search "
        "region. Keyword co-occurrence uses one candidate per segment, without "
        "fixed order or slots: precision is segment precision, not placement "
        "precision. Recall still counts individual valid usages. A segment with "
        "both valid and uncertain usages counts as valid. Adjacent-layer usages "
        "are excluded from normal-usage recall for every method.",
        "Baselines have no structural state budget or structural grade; their "
        "figures describe grammatical usefulness, not implementation correctness.",
        "",
        "Search space is the union of candidate segments' source spans, counted "
        "once within each gold entry, divided by all reference source code points "
        "including punctuation and notes. Reduction is one minus this fraction. "
        "Matcher candidates are the segments of matched records; diagnostic and "
        "interruption records are excluded. "
        "It is always shown with recall; it is not a standalone success measure.",
        "",
        "Error types describe data-derived features, not proven causes. The "
        "exclusive priority is boundary, adjacent_fixed, variant_spelling "
        "(an explicit mapping), length_limit, note_barrier, multiple_placement "
        "(a competing placement at the same first span), other. Case names and "
        "annotation prose are not used; unexplained grammatical negatives stay "
        "other. Uncertain candidates are not counted as definite false positives.",
    ]
    if snapshots := data.get("input_snapshots"):
        lines += [
            "",
            "## Input snapshots",
            "",
            "SHA-256 of the scored input files:",
            "",
        ]
        lines += [
            f"- Gold `{item['path']}`: `{item['sha256']}`" for item in snapshots["gold"]
        ]
        pattern_snapshot = snapshots["patterns"]
        lines.append(
            f"- Patterns `{pattern_snapshot['path']}`: `{pattern_snapshot['sha256']}`"
        )
    for item in data["patterns_evaluated"]:
        structural = item["structural"]
        precision = item["precision"]
        lines += [
            "",
            f"## {item['pattern_id']}",
            "",
            f"- Entries: {item['entries']} ({item['confirmed']} confirmed)",
            f"- Structural agreement: {structural['agree']}/{structural['of']}"
            + (
                f"; mismatches: {', '.join(structural['mismatches'])}"
                if structural["mismatches"]
                else ""
            )
            + (
                f"; limit hits: {', '.join(structural['limit_hits'])}"
                if structural["limit_hits"]
                else ""
            ),
            f"- Recall of valid usages: {_count(item['recall'])}",
            "- Precision (reference only): "
            f"optimistic {_ratio(precision['optimistic'])}, "
            f"pessimistic {_ratio(precision['pessimistic'])}, "
            f"uncertain excluded {_ratio(precision['excluded'])} "
            f"({precision['candidates']} candidates)",
            f"- Slot A exact: {_count(item['slot_a_exact'])}; overlapping: "
            f"{_count(item['slot_a_overlap'])}",
            f"- B end among representatives: {_count(item['b_in_representatives'])}"
            + (
                "; by rule: "
                + ", ".join(f"{k} {v}" for k, v in item["b_by_rule"].items())
                if item["b_by_rule"]
                else ""
            ),
            f"- Adjacent-layer usages found: {_count(item['adjacent_usages_found'])}",
            "",
            "### Baseline comparison",
            "",
            "| Method (candidate unit) | Recall (95% Wilson) | Precision: "
            "pessimistic / optimistic / uncertain excluded | Source characters "
            "retained; reduction | Slot A exact / overlap | B end |",
            "| --- | --- | --- | --- | --- | --- |",
            _method_row("matcher (placement)", item),
            *[
                _method_row(f"{name} ({baseline['candidate_unit']})", baseline)
                for name, baseline in item["baselines"].items()
            ],
            "",
            "Error-type breakdown (misses / definite false positives):",
            "",
            *_error_lines("matcher", item),
            *[
                line
                for name, baseline in item["baselines"].items()
                for line in _error_lines(name, baseline)
            ],
            *[
                f"- {name} unsupported entries (not scored): "
                f"{', '.join(baseline['unsupported_entries'])}"
                for name, baseline in item["baselines"].items()
                if baseline["unsupported_entries"]
            ],
            "",
            "Missed valid usages:",
            "",
        ]
        if item["missed"]:
            lines += [
                f"- {miss['id']}: `{miss['text']}` fixed {miss['fixed']}"
                for miss in item["missed"]
            ]
        else:
            lines.append("- none")
        for name, baseline in item["baselines"].items():
            lines += ["", f"Missed valid usages: {name}", ""]
            lines += [
                f"- {miss['id']}: `{miss['text']}` fixed {miss['fixed']}"
                for miss in baseline["missed"]
            ] or ["- none"]
    return "\n".join(lines) + "\n"


def _method_row(name: str, item: dict[str, Any]) -> str:
    precision, space = item["precision"], item["search_space"]
    slots = (
        "n/a"
        if item["slot_a_exact"] is None
        else f"{_count(item['slot_a_exact'])} / {_count(item['slot_a_overlap'])}"
    )
    end = (
        "n/a"
        if item["b_in_representatives"] is None
        else _count(item["b_in_representatives"])
    )
    return (
        f"| {name} | {_count(item['recall'])} | "
        f"{_ratio(precision['pessimistic'])} / "
        f"{_ratio(precision['optimistic'])} / {_ratio(precision['excluded'])} "
        f"({precision['candidates']} candidates) | "
        f"{space['candidate_characters']}/{space['total_characters']} "
        f"({_ratio(space['retained_fraction'])}); "
        f"{_ratio(space['reduction_rate'])} reduction | {slots} | {end} |"
    )


def _error_lines(name: str, item: dict[str, Any]) -> list[str]:
    errors = item["error_types"]
    return [
        f"- {name}: "
        + "; ".join(
            f"{side}: "
            + (
                ", ".join(
                    f"{kind} {count}" for kind, count in sorted(errors[side].items())
                )
                or "none"
            )
            for side in ("misses", "false_positives")
        )
    ]


def write(
    prefix: Path, data: dict[str, Any], *, force: bool = False
) -> tuple[Path, Path]:
    json_path = prefix.with_name(prefix.name + ".json")
    md_path = prefix.with_name(prefix.name + ".md")
    check_input_collisions(
        [json_path, md_path],
        [*(Path(path) for path in data["gold"]), Path(data["patterns"])],
    )
    check_outputs([json_path, md_path], force=force)
    json_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    md_text = markdown(data)
    with staged([json_path, md_path], force=force) as (json_target, md_target):
        with json_target.open("w", encoding="utf-8", newline="") as stream:
            stream.write(json_text)
        with md_target.open("w", encoding="utf-8", newline="") as stream:
            stream.write(md_text)
    return json_path, md_path
