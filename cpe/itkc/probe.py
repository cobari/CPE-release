"""Plan and record ITKC experiments; transport requires an explicit live run."""

from __future__ import annotations

import csv
import io
import json
import math
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from cpe.itkc.client import (
    FIELD_DATA_ID,
    ApiError,
    CallCapReached,
    ITKCClient,
    LocalStorageError,
    Safeguards,
    Transport,
    urllib_transport,
)


class ProbeConfigError(ValueError):
    """The experiment configuration cannot be executed as written."""


@dataclass(frozen=True)
class ExperimentDefinition:
    id: str
    question: str
    categories: tuple[str, ...]
    manual: bool = False
    optional: bool = False


DEFINITIONS = {
    item.id: item
    for item in (
        ExperimentDefinition(
            "E0",
            "How does the server combine search terms?",
            ("AND", "phrase", "OR", "ignored"),
        ),
        ExperimentDefinition(
            "E1",
            "Do range restrictions take effect?",
            ("working", "partial", "ineffective"),
        ),
        ExperimentDefinition(
            "E2",
            "How large are single-character result sets?",
            ("counts", "response limits"),
            optional=True,
        ),
        ExperimentDefinition(
            "E3",
            "Where and how much are snippets truncated?",
            ("length distribution", "position bias within an article"),
            manual=True,
        ),
        ExperimentDefinition(
            "E4",
            "Does a second anchor add decidable context?",
            ("additional sentences per extra call",),
        ),
        ExperimentDefinition(
            "E5",
            "What unit does a data ID identify?",
            ("article", "paragraph", "work"),
        ),
        ExperimentDefinition(
            "E6",
            "Is pagination complete for a small result set?",
            ("missing", "duplicate", "maximum rows"),
        ),
        ExperimentDefinition(
            "E7",
            "Do official source links identify the intended text?",
            ("exact", "nearby", "failed"),
            manual=True,
        ),
    )
}


@dataclass(frozen=True)
class _Request:
    id: str
    kind: str
    params: dict[str, Any]


@dataclass(frozen=True)
class _Experiment:
    definition: ExperimentDefinition
    requests: tuple[_Request, ...]
    targets: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class _Config:
    experiments: tuple[_Experiment, ...]
    interval: float
    cap: int


def _table(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - keys:
        raise ProbeConfigError(f"invalid {name} table or unknown keys")
    return dict(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProbeConfigError(f"{name} must be a non-empty string")
    return value


def _integer(value: Any, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ProbeConfigError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _requests(value: Any, definition: ExperimentDefinition) -> tuple[_Request, ...]:
    if not isinstance(value, list) or (definition.manual and value):
        raise ProbeConfigError(
            f"{definition.id}: invalid requests (manual experiments send none)"
        )
    requests = []
    ids: set[str] = set()
    for entry in value:
        request = _table(entry, "request", {"id", "kind", "params"})
        label = _text(request.get("id"), "request.id")
        if label in ids:
            raise ProbeConfigError(f"duplicate request ID: {label}")
        ids.add(label)
        kind = request.get("kind", "count")
        if kind not in ("count", "sample"):
            raise ProbeConfigError("request.kind must be count or sample")
        if definition.id == "E2" and kind != "count":
            raise ProbeConfigError("E2 permits rows=1 count requests only")
        params = _table(
            request.get("params"),
            "params",
            {"secId", "keyword", "q", "start", "rows", "opDir", "opJib"},
        )
        _text(params.get("secId"), "params.secId")
        if ("keyword" in params) == ("q" in params):
            raise ProbeConfigError("params must contain exactly one of keyword and q")
        for key in set(params) - {"start", "rows"}:
            _text(params[key], f"params.{key}")
        params["start"] = _integer(params.get("start", 0), "start", 0)
        params["rows"] = _integer(params.get("rows", 1), "rows", 1)
        if kind == "count" and (params["start"] != 0 or params["rows"] != 1):
            raise ProbeConfigError("count requests require start=0 and rows=1")
        requests.append(_Request(label, kind, params))
    if not definition.manual and not requests:
        raise ProbeConfigError(f"{definition.id} needs at least one planned request")
    if sum(request.kind == "sample" for request in requests) > 5:
        raise ProbeConfigError(
            f"{definition.id}: at most five sample pages are allowed"
        )
    return tuple(requests)


def _targets(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise ProbeConfigError("targets must be an array of tables")
    targets = []
    for entry in value:
        target = _table(entry, "target", {"data_id", "source_type", "url"})
        _text(target.get("data_id"), "target.data_id")
        if any(not isinstance(item, str) for item in target.values()):
            raise ProbeConfigError("target fields must be strings")
        targets.append(
            {key: str(target.get(key, "")) for key in ("data_id", "source_type", "url")}
        )
    return tuple(targets)


def _load(path: Path) -> _Config:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            data = tomllib.loads(stream.read())
    except (tomllib.TOMLDecodeError, UnicodeError) as error:
        raise ProbeConfigError(f"invalid UTF-8 TOML config: {error}") from None
    root = _table(data, "config", {"schema_version", "safeguards", "experiments"})
    if type(root.get("schema_version")) is not int or root["schema_version"] != 1:
        raise ProbeConfigError("schema_version must be 1")
    guard = _table(
        root.get("safeguards", {}), "safeguards", {"min_interval", "max_calls"}
    )
    interval = guard.get("min_interval", 1.0)
    if (
        type(interval) not in (int, float)
        or not math.isfinite(interval)
        or interval < 1
    ):
        raise ProbeConfigError("min_interval must be finite and at least 1 second")
    cap = _integer(guard.get("max_calls", 200), "max_calls", 1)
    if cap > 200:
        raise ProbeConfigError("max_calls cannot exceed 200")
    entries = _table(
        root.get("experiments"), "experiments", {key.lower() for key in DEFINITIONS}
    )
    if not entries:
        raise ProbeConfigError("at least one experiment must be configured")
    experiments = []
    for name, value in sorted(entries.items()):
        definition = DEFINITIONS[name.upper()]
        entry = _table(value, name, {"requests", "targets", "sample_file"})
        targets = _targets(entry.get("targets", []))
        if "sample_file" in entry:
            sample = path.parent / _text(entry["sample_file"], "sample_file")
            try:
                with sample.open(encoding="utf-8", newline="") as stream:
                    rows = [json.loads(line) for line in stream if line.strip()]
                targets += _targets(rows)
            except (json.JSONDecodeError, UnicodeError) as error:
                raise ProbeConfigError(
                    f"invalid target sample JSONL: {error}"
                ) from None
        experiments.append(
            _Experiment(
                definition, _requests(entry.get("requests", []), definition), targets
            )
        )
    return _Config(tuple(experiments), float(interval), cap)


def _write(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def _event(path: Path, entry: dict[str, Any]) -> None:
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(line)


def _form(experiment: _Experiment) -> str:
    columns = ["data_id", "source_type", "url"]
    if experiment.definition.id == "E3":
        columns += ["snippet_length", "source_length", "article_position"]
    else:
        columns += ["link_status"]
    columns += ["judgement", "notes"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(experiment.targets)
    return "\ufeff" + stream.getvalue()


def _report(experiment: _Experiment, observed: dict[str, Any], mode: str) -> str:
    definition = experiment.definition
    lines = [
        f"# {definition.id}: {definition.question}",
        "",
        f"Mode: {mode}",
        f"Execution status: {observed['status']}",
        "",
        "Gate 1-B judgement (researcher completes):",
        "",
        "- [ ] possible",
        "- [ ] impossible",
        "- [ ] limited",
        "",
        "Evidence / limitations / reason not performed: __________",
        "",
        f"Experiment categories: {', '.join(definition.categories)}",
        "",
        "Category judgement: __________",
        "",
        "## Observed counts",
        "",
    ]
    for key in (
        "calls",
        "responses",
        "documents",
        "unique_ids",
        "duplicate_ids",
        "missing_ids",
        "targets",
    ):
        lines.append(f"- {key}: {observed[key]}")
    lines += [
        "",
        "## Per-request counts",
        "",
        "| Request | totalCount | Documents |",
        "| --- | ---: | ---: |",
    ]
    for response in observed["results"]:
        label = (
            str(response["request"])
            .replace("|", "\\|")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        lines.append(f"| {label} | {response['total']} | {response['documents']} |")
    lines += [
        "",
        "No conclusion is inferred from counts.",
        "Unlisted requests have no observed response.",
        "ID counts include all returned documents, including count requests;",
        "repeated IDs across different requests do not prove a pagination defect.",
        "",
    ]
    return "\n".join(lines)


def run(
    config_path: Path,
    *,
    live: bool = False,
    sample: bool = False,
    experiments: Sequence[str] | None = None,
    workspace: Path | None = None,
    transport: Transport | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    today: date | None = None,
) -> dict[str, Any]:
    """Write plans, blank manual forms and reports; only live=True sends requests.

    Count requests use rows=1. Sample pages require sample=True, with five pages
    per experiment. One client keeps the call cap and interval across experiments
    and retries. E2 is selected only by an explicit experiment ID.
    """
    config = _load(config_path)
    selected = (
        {name.upper() for name in experiments}
        if experiments is not None
        else {
            item.definition.id
            for item in config.experiments
            if not item.definition.optional
        }
    )
    configured = {item.definition.id for item in config.experiments}
    if not selected or selected - configured:
        raise ProbeConfigError(
            "select at least one configured experiment (E2 requires explicit selection)"
        )
    active = [item for item in config.experiments if item.definition.id in selected]
    planned = {
        item.definition.id: [
            request for request in item.requests if sample or request.kind == "count"
        ]
        for item in active
    }
    if live and any(
        "CHOOSE_" in str(value)
        for requests in planned.values()
        for request in requests
        for value in request.params.values()
    ):
        raise ProbeConfigError("replace CHOOSE_ placeholders before a live run")
    day = (today or date.today()).strftime("%Y%m%d")
    root = (workspace or Path.cwd()) / "experiments" / "itkc"
    root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"run-{day}-", dir=root))
    mode = "live" if live else "dry-run"
    summary: dict[str, Any] = {
        "mode": mode,
        "sample": sample,
        "planned_calls": sum(map(len, planned.values())),
        "max_calls_including_retries": config.cap,
        "min_interval": config.interval,
        "calls": 0,
        "stopped": False,
        "cap_reached": False,
        "reason": None,
        "output": str(output),
        "experiments": [],
    }
    client: ITKCClient | None = None
    for item in active:
        name = item.definition.id
        requests = planned[name]
        observed: dict[str, Any] = {
            "id": name,
            "question": item.definition.question,
            "categories": list(item.definition.categories),
            "manual": item.definition.manual,
            "planned_requests": [
                {"id": request.id, "kind": request.kind, "params": request.params}
                for request in requests
            ],
            "deferred_sample_requests": [
                {"id": request.id, "kind": request.kind, "params": request.params}
                for request in item.requests
                if request not in requests
            ],
            "status": "manual" if item.definition.manual else "dry_run",
            "calls": 0,
            "responses": 0,
            "documents": 0,
            "unique_ids": 0,
            "duplicate_ids": 0,
            "missing_ids": 0,
            "targets": len(item.targets),
            "results": [],
        }
        prefix = output / name.lower()
        plan_path = prefix.with_suffix(".plan.json")
        _write(plan_path, json.dumps(observed, ensure_ascii=False, indent=2) + "\n")
        observed["plan"] = str(plan_path)
        if item.definition.manual:
            form_path = prefix.with_suffix(".csv")
            _write(form_path, _form(item))
            observed["form"] = str(form_path)
        ids: set[str] = set()
        if live and requests and not summary["stopped"]:
            log_path = root / f"{day}_{name.lower()}.jsonl"
            if client is None:
                raw_root = root / "raw"
                raw_root.mkdir(parents=True, exist_ok=True)
                raw_dir = Path(tempfile.mkdtemp(prefix=f"run-{day}-", dir=raw_root))
                summary["raw"] = str(raw_dir)
                client = ITKCClient(
                    sec_id=requests[0].params["secId"],
                    log_path=log_path,
                    safeguards=Safeguards(
                        min_interval=config.interval, max_calls=config.cap
                    ),
                    transport=transport if transport is not None else urllib_transport,
                    raw_dir=raw_dir,
                    clock=clock,
                    sleep=sleep,
                )
            client.log_path = log_path
            _event(
                log_path,
                {
                    "event": "probe_start",
                    "run": output.name,
                    "experiment": name,
                    "planned_calls": len(requests),
                },
            )
            before = client.calls
            try:
                for request in requests:
                    total, docs = client.fetch_params(request.params)
                    observed["responses"] += 1
                    observed["documents"] += len(docs)
                    observed["results"].append(
                        {"request": request.id, "total": total, "documents": len(docs)}
                    )
                    for doc in docs:
                        identifier = doc.get(FIELD_DATA_ID)
                        if not identifier:
                            observed["missing_ids"] += 1
                        elif identifier in ids:
                            observed["duplicate_ids"] += 1
                        else:
                            ids.add(identifier)
                    if client.stopped:
                        break
            except CallCapReached:
                summary["reason"] = "call_cap"
            except (ApiError, LocalStorageError, OSError, ValueError) as error:
                summary["stopped"] = True
                summary["reason"] = type(error).__name__
            observed["calls"] = client.calls - before
            observed["unique_ids"] = len(ids)
            summary["calls"] = client.calls
            if client.stopped:
                summary["stopped"] = True
                summary["cap_reached"] = True
                if summary["reason"] is None:
                    summary["reason"] = "call_cap"
            observed["log"] = str(log_path)
            observed["status"] = summary["reason"] or "requests_finished"
            _event(
                log_path,
                {
                    "event": "probe_end",
                    "run": output.name,
                    "experiment": name,
                    "calls": observed["calls"],
                    "responses": observed["responses"],
                    "documents": observed["documents"],
                    "status": observed["status"],
                },
            )
        elif live and not item.definition.manual:
            observed["status"] = summary["reason"] or "sampling_not_enabled"
        report_path = prefix.with_suffix(".md")
        _write(report_path, _report(item, observed, mode))
        observed["report"] = str(report_path)
        summary["experiments"].append(observed)
    _write(
        output / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary
