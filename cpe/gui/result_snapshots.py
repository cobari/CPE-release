"""Bounded, independently pinned panel results retained only in Python memory."""

from __future__ import annotations

import copy
import json
import secrets
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cpe.gui.search_session import SORTS, sorted_rows
from cpe.mcp.evidence import annotate_row


class SnapshotError(ValueError):
    """A panel cannot be retained without losing an existing pinned snapshot."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FrozenDict(dict[str, Any]):
    """A read-only mapping shared by panels from one library snapshot."""

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("shared provenance is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("shared provenance is immutable")

    def clear(self) -> None:
        raise TypeError("shared provenance is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("shared provenance is immutable")

    def pop(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("shared provenance is immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("shared provenance is immutable")

    def setdefault(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("shared provenance is immutable")


class SharedInputs(tuple[FrozenDict, ...]):
    """Captured input metadata shared by every panel from one library snapshot."""

    def __new__(cls, entries: Iterable[Mapping[str, Any]]) -> SharedInputs:
        return super().__new__(cls, (FrozenDict(entry) for entry in entries))

    def __deepcopy__(self, memo: dict[int, Any]) -> SharedInputs:
        return self


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


@dataclass
class _Entry:
    snapshot: dict[str, Any]
    size: int
    pins: int = 0
    retired: bool = False
    displayed: bool = False


class ResultSnapshotStore:
    """Copy at both boundaries so UI sorting and callers cannot change evidence."""

    def __init__(
        self, *, max_snapshots: int = 8, max_bytes: int = 64 * 1024 * 1024
    ) -> None:
        if (
            type(max_snapshots) is not int
            or max_snapshots < 1
            or type(max_bytes) is not int
            or max_bytes < 1
        ):
            raise ValueError("snapshot budgets must be positive integers")
        self._max_snapshots = max_snapshots
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.RLock()

    def _prepare(
        self,
        source_id: str,
        answer: Mapping[str, Any],
        *,
        request: Mapping[str, Any] | None = None,
        panel_id: str = "",
        generation: int | None = None,
    ) -> tuple[str, dict[str, Any], int]:
        """Build one isolated snapshot and its serialized size without retaining it."""
        if not isinstance(source_id, str) or not source_id or "error" in answer:
            raise SnapshotError("bad_request")
        if generation is not None and type(generation) is not int:
            raise SnapshotError("bad_request")
        raw_rows = answer.get("rows")
        if raw_rows is None:
            raw_items: list[tuple[str, dict[str, Any]]] = []
            for group in ("likely", "less"):
                group_rows = answer.get(group, [])
                if not isinstance(group_rows, (list, tuple)) or not all(
                    isinstance(row, dict) for row in group_rows
                ):
                    raise SnapshotError("bad_request")
                raw_items.extend((group, row) for row in group_rows)
        else:
            if not isinstance(raw_rows, (list, tuple)) or not all(
                isinstance(row, dict) for row in raw_rows
            ):
                raise SnapshotError("bad_request")
            raw_items = []
            for item in raw_rows:
                group = item.get("group", "likely")
                if group not in {"likely", "less"}:
                    raise SnapshotError("bad_request")
                raw_items.append((group, item))

        # Copy non-row metadata once; each row is deep-copied once below
        # (via copy.deepcopy or annotate_row) rather than copying answer whole.
        skip_keys = {"rows", "likely", "less"}
        if request is not None:
            skip_keys.add("query")
        snapshot = {
            key: copy.deepcopy(value)
            for key, value in answer.items()
            if key not in skip_keys
        }
        rows = []
        for group, item in raw_items:
            # Already-projected evidence must not be inferred again from an
            # absent canonical record, which would downgrade a known candidate.
            row = (
                copy.deepcopy(item)
                if "evidence_scope" in item
                else annotate_row(item, group)
            )
            row["group"] = group
            rows.append(row)
        source = snapshot.get("source", {"name": source_id})
        query = request if request is not None else snapshot.get("query", {})
        if not isinstance(source, dict) or not isinstance(query, Mapping):
            raise SnapshotError("bad_request")
        snapshot.update(
            source_id=source_id,
            panel_id=panel_id or source_id,
            query=copy.deepcopy(dict(request)) if request is not None else dict(query),
            generation=generation,
            source=source,
            rows=rows,
        )
        if "counts" not in snapshot and not snapshot.get("source", {}).get(
            "external_only"
        ):
            likely_count = sum(row["group"] == "likely" for row in rows)
            snapshot["counts"] = {
                "likely": likely_count,
                "less": len(rows) - likely_count,
                "total": len(rows),
            }
        snapshot_id = "gui_" + secrets.token_urlsafe(18)
        snapshot["snapshot_id"] = snapshot_id
        # Shared library provenance is one object referenced by many panels; it
        # is not panel-specific storage and must not be serialized per panel.
        size = _json_size(
            {
                key: value
                for key, value in snapshot.items()
                if not isinstance(value, SharedInputs)
            }
        )
        return snapshot_id, snapshot, size

    def put(
        self,
        source_id: str,
        answer: Mapping[str, Any],
        *,
        request: Mapping[str, Any] | None = None,
        panel_id: str = "",
        generation: int | None = None,
        displayed: bool = False,
        _replacing: str = "",
    ) -> str:
        snapshot_id, snapshot, size = self._prepare(
            source_id,
            answer,
            request=request,
            panel_id=panel_id,
            generation=generation,
        )
        with self._lock:
            if size > self._max_bytes:
                raise SnapshotError("storage_limit")
            removable = [
                key
                for key, entry in self._entries.items()
                if not entry.pins and (key == _replacing or not entry.displayed)
            ]
            if _replacing in removable:
                removable.remove(_replacing)
                removable.insert(0, _replacing)
            count = len(self._entries) + 1
            retained = sum(entry.size for entry in self._entries.values()) + size
            evict = []
            for key in removable:
                if count <= self._max_snapshots and retained <= self._max_bytes:
                    break
                evict.append(key)
                count -= 1
                retained -= self._entries[key].size
            if count > self._max_snapshots or retained > self._max_bytes:
                raise SnapshotError("storage_limit")
            for key in evict:
                del self._entries[key]
            self._entries[snapshot_id] = _Entry(snapshot, size, displayed=displayed)
        return snapshot_id

    def replace_generation(
        self,
        source_id: str,
        answer: Mapping[str, Any],
        *,
        request: Mapping[str, Any] | None = None,
        panel_id: str = "",
        generation: int | None = None,
        displayed: bool = True,
    ) -> str:
        """Retain this panel and retire the previous generation in one commit.

        The new snapshot is built and checked before any old entry is removed.
        A storage_limit therefore leaves the displayed panels (and their export
        pins) intact instead of trading them for a panel that cannot be stored.
        Pinned entries remain readable until their export releases them.
        """
        snapshot_id, snapshot, size = self._prepare(
            source_id,
            answer,
            request=request,
            panel_id=panel_id,
            generation=generation,
        )
        with self._lock:
            if size > self._max_bytes:
                raise SnapshotError("storage_limit")
            pinned = {key for key, entry in self._entries.items() if entry.pins}
            retained = sum(self._entries[key].size for key in pinned) + size
            if len(pinned) + 1 > self._max_snapshots or retained > self._max_bytes:
                raise SnapshotError("storage_limit")
            for key in list(self._entries):
                entry = self._entries[key]
                if key in pinned:
                    entry.retired = True
                    entry.displayed = False
                else:
                    del self._entries[key]
            self._entries[snapshot_id] = _Entry(snapshot, size, displayed=displayed)
        return snapshot_id

    def _view(
        self,
        snapshot_id: str,
        sort: str = "source",
        *,
        include_records: bool = True,
        deep_copy: bool = True,
    ) -> dict[str, Any]:
        if sort not in SORTS:
            return {"error": "bad_sort"}
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None:
                return {"error": "snapshot_not_found"}
            self._entries.move_to_end(snapshot_id)
            if deep_copy and include_records:
                snapshot = copy.deepcopy(entry.snapshot)
            elif deep_copy:
                snapshot = {
                    key: copy.deepcopy(value)
                    for key, value in entry.snapshot.items()
                    if key != "rows"
                }
                snapshot["rows"] = [
                    {k: copy.deepcopy(v) for k, v in row.items() if k != "record"}
                    for row in entry.snapshot["rows"]
                ]
            else:
                snapshot = {
                    key: value for key, value in entry.snapshot.items() if key != "rows"
                }
                snapshot["rows"] = list(entry.snapshot["rows"])
        snapshot["rows"] = [
            row
            for group in ("likely", "less")
            for row in sorted_rows(
                [row for row in snapshot["rows"] if row["group"] == group], sort
            )
        ]
        snapshot["sort"] = sort
        return snapshot

    def get(self, snapshot_id: str, sort: str = "source") -> dict[str, Any]:
        return self._view(snapshot_id, sort, include_records=True, deep_copy=True)

    def export_view(self, snapshot_id: str, sort: str = "source") -> dict[str, Any]:
        """Return a sorted view of a pinned snapshot without copying the payload."""
        return self._view(snapshot_id, sort, include_records=True, deep_copy=False)

    def continuation_identities(self, snapshot_id: str) -> tuple[str, ...]:
        """Return stored continuation identity strings without copying row payloads."""
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None:
                return ()
            self._entries.move_to_end(snapshot_id)
            continuation = entry.snapshot.get("continuation") or {}
            identities = continuation.get("identities") or ()
            return tuple(str(item) for item in identities)

    def present(self, snapshot_id: str, sort: str = "source") -> dict[str, Any]:
        snapshot = self._view(snapshot_id, sort, include_records=False, deep_copy=True)
        if "error" in snapshot:
            return snapshot
        rows = snapshot.pop("rows")
        for group in ("likely", "less"):
            snapshot[group] = [row for row in rows if row["group"] == group]
        return snapshot

    def select_row(self, snapshot_id: str, row_id: str) -> dict[str, Any]:
        """Copy one result with its submitted query and provenance."""
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None:
                return {"error": "snapshot_not_found"}
            self._entries.move_to_end(snapshot_id)
            row = next(
                (item for item in entry.snapshot["rows"] if item.get("id") == row_id),
                None,
            )
            if row is None:
                return {"error": "snapshot_not_found"}
            selected = {**entry.snapshot, "rows": [row]}
            return copy.deepcopy(selected)

    def pin(self, snapshot_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None:
                return False
            entry.pins += 1
            return True

    def unpin(self, snapshot_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(snapshot_id)
            if entry is None or not entry.pins:
                return False
            entry.pins -= 1
            if entry.retired and not entry.pins:
                del self._entries[snapshot_id]
            return True

    def clear(self, snapshot_id: str = "") -> int:
        """Retire displayed results while preserving any in-flight export pins."""
        with self._lock:
            keys = [snapshot_id] if snapshot_id else list(self._entries)
            count = 0
            for key in keys:
                entry = self._entries.get(key)
                if entry is None:
                    continue
                count += 1
                if entry.pins:
                    entry.retired = True
                    entry.displayed = False
                else:
                    del self._entries[key]
            return count

    def replace(
        self, snapshot_id: str, source_id: str, answer: Mapping[str, Any], **kwargs: Any
    ) -> str:
        with self._lock:
            new_id = self.put(source_id, answer, _replacing=snapshot_id, **kwargs)
            if snapshot_id:
                self.clear(snapshot_id)
            return new_id
