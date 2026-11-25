"""Write retained result snapshots without rerunning a search or widening disclosure."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any, Literal

from cpe.export.csv_out import excel_safe
from cpe.export.safe import (
    check_input_collisions,
    check_outputs,
    check_prefix,
    staged,
)
from cpe.gui.search_session import SORTS, sorted_rows
from cpe.local.paths import is_directory_link, is_within

MCP_COLUMNS = (
    "id",
    "text_id",
    "title",
    "link",
    "group",
    "kind",
    "before",
    "segment",
    "after",
    "a",
    "b",
    "reasons",
    "record",
    "core_result_id",
    "matched_field",
    "positions",
    "citation",
    "evidence_scope",
    "group_semantics",
    "text_views",
    "text_withheld",
    "record_withheld",
)
GUI_COLUMNS = (
    "panel_id",
    "source_id",
    "snapshot_id",
    "era",
    "pattern_id",
    "pattern",
    "slots",
    "itkc_result_id",
    "warnings",
    *MCP_COLUMNS,
)


def output_paths(
    target: Path, *, profile: str = "gui", format: str = "csv"
) -> tuple[Path, ...]:
    """The GUI target is an exact filename; MCP retains its prefix convention."""
    check_prefix(target)
    if profile == "mcp":
        return tuple(
            target.with_name(target.name + suffix)
            for suffix in (".csv", ".jsonl", ".run.json")
        )
    if profile != "gui" or format not in {"csv", "jsonl"}:
        raise ValueError("invalid snapshot export profile or format")
    return target, target.with_name(target.name + ".meta.json")


def _check_paths(
    paths: Sequence[Path], protected: Sequence[Path], root: Path | None
) -> None:
    for path in paths:
        # Resolve only after checking lexical entries: resolving first hides a
        # junction or dangling symlink that must not become an output grant.
        if ".." in path.parts:
            raise OSError("snapshot output contains a parent reference")
        if any(is_directory_link(part) for part in (path, *path.parents)):
            raise OSError("snapshot output traverses a directory link")
        if root is not None and not is_within(root.resolve(), path):
            raise OSError("snapshot output is outside the selected root")
    check_input_collisions(paths, protected)


def _write_csv(
    stream: IO[str], rows: Sequence[dict[str, Any]], columns: Sequence[str]
) -> None:
    writer = csv.DictWriter(
        stream, fieldnames=columns, quoting=csv.QUOTE_ALL, lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        values = {key: row.get(key, "") for key in columns}
        writer.writerow(
            {
                key: excel_safe(value if isinstance(value, str) else _json(value))
                for key, value in values.items()
            }
        )


def _write_jsonl(
    stream: IO[str],
    rows: Sequence[dict[str, Any]],
    *,
    schema: str,
    source: Any = None,
) -> None:
    for row in rows:
        record: dict[str, Any] = {"schema": schema}
        if source is not None:
            record["source"] = source
        record["result"] = row
        stream.write(_json(record) + "\n")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _tagged_gui_row(
    row: dict[str, Any],
    group: str,
    panel_id: str,
    source_id: str,
    snapshot_id: str,
    default_warnings: Any,
) -> dict[str, Any]:
    item = dict(row)
    item["group"] = group
    item.update(
        panel_id=panel_id,
        source_id=source_id,
        snapshot_id=snapshot_id,
    )
    if "result_id" in row:
        item["itkc_result_id"] = row["result_id"]
    item.setdefault("warnings", default_warnings)
    return item


def _gui_payload(
    snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    order = snapshot.get("sort", "source")
    if order not in SORTS:
        raise ValueError("invalid snapshot sort")
    panels = snapshot.get("panels", [snapshot])
    if not isinstance(panels, (list, tuple)):
        raise ValueError("snapshot panels must be a sequence")
    rows: list[dict[str, Any]] = []
    metadata = {
        key: value
        for key, value in snapshot.items()
        if key not in {"rows", "likely", "less", "panels"}
    }
    panel_metadata = []
    for panel in panels:
        if not isinstance(panel, Mapping) or ("error" in panel and "rows" not in panel):
            raise ValueError("invalid snapshot panel")
        source = panel.get("source", {})
        source_id = panel.get(
            "source_id", source.get("name", "") if isinstance(source, dict) else ""
        )
        panel_id = panel.get("panel_id", source_id)
        snapshot_id = panel.get("snapshot_id", panel.get("id", ""))
        default_warnings = panel.get("warnings", [])
        raw_rows = panel.get("rows")
        tagged: list[dict[str, Any]] = []
        seen_less = False
        already_grouped = True
        if raw_rows is None:
            for group in ("likely", "less"):
                group_rows = panel.get(group, [])
                if not isinstance(group_rows, (list, tuple)) or not all(
                    isinstance(row, dict) for row in group_rows
                ):
                    raise ValueError("snapshot rows must be objects")
                for row in group_rows:
                    tagged.append(
                        _tagged_gui_row(
                            row,
                            group,
                            panel_id,
                            source_id,
                            snapshot_id,
                            default_warnings,
                        )
                    )
        else:
            if not isinstance(raw_rows, (list, tuple)) or not all(
                isinstance(row, dict) for row in raw_rows
            ):
                raise ValueError("snapshot rows must be objects")
            for row in raw_rows:
                group = row.get("group", "likely")
                if group not in {"likely", "less"}:
                    raise ValueError("invalid snapshot group")
                if group == "less":
                    seen_less = True
                elif seen_less:
                    already_grouped = False
                tagged.append(
                    _tagged_gui_row(
                        row, group, panel_id, source_id, snapshot_id, default_warnings
                    )
                )
        # Preserve the two display groups and stable source order within ties.
        # Panels returned by ResultSnapshotStore.get(id, sort) carry sort == order
        # and are already grouped and sorted.
        if raw_rows is not None and panel.get("sort") == order and already_grouped:
            rows.extend(tagged)
        else:
            for group in ("likely", "less"):
                rows.extend(
                    sorted_rows(
                        [row for row in tagged if row["group"] == group],
                        order,
                    )
                )
        panel_metadata.append(
            {
                key: value
                for key, value in panel.items()
                if key not in {"rows", "likely", "less"}
            }
        )
    metadata.update(
        schema="cpe.gui.snapshot/1",
        panels=panel_metadata,
        sort=order,
        row_count=len(rows),
    )
    return rows, metadata


def write(
    snapshot: Mapping[str, Any],
    target: Path,
    *,
    profile: Literal["mcp", "gui"] = "gui",
    format: Literal["csv", "jsonl"] = "csv",
    force: bool = False,
    protected: Sequence[Path] = (),
    root: Path | None = None,
) -> dict[str, Any]:
    """Write an already-projected snapshot; callers retain authority over disclosure."""
    paths = output_paths(target, profile=profile, format=format)
    _check_paths(paths, protected, root)
    check_outputs(paths, force=force)
    if profile == "mcp":
        rows = snapshot["rows"]
        metadata = {key: value for key, value in snapshot.items() if key != "rows"}
        metadata.update(schema="cpe.mcp.snapshot/1", row_count=len(rows))
        mcp_source = snapshot["source"]
        writers: list[tuple[str, Callable[[IO[str]], Any]]] = [
            ("utf-8-sig", lambda stream: _write_csv(stream, rows, MCP_COLUMNS)),
            (
                "utf-8",
                lambda stream: _write_jsonl(
                    stream,
                    rows,
                    schema="cpe.mcp.snapshot-row/1",
                    source=mcp_source,
                ),
            ),
        ]
    else:
        rows, metadata = _gui_payload(snapshot)
        writers = [
            (
                "utf-8-sig" if format == "csv" else "utf-8",
                (lambda stream: _write_csv(stream, rows, GUI_COLUMNS))
                if format == "csv"
                else (
                    lambda stream: _write_jsonl(
                        stream, rows, schema="cpe.gui.snapshot-row/1"
                    )
                ),
            )
        ]
    writers.append(
        (
            "utf-8",
            lambda stream: stream.write(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
            ),
        )
    )
    with staged(paths, force=force) as temporary:
        _check_paths(paths, protected, root)
        for path, (encoding, writer) in zip(temporary, writers, strict=True):
            with path.open("w", encoding=encoding, newline="") as stream:
                writer(stream)
    return {
        "files": [str(path) for path in paths],
        "rows": len(rows),
        "schema": metadata["schema"],
    }
