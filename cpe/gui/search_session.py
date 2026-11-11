"""Bounded ITKC search sessions for the GUI bridge.

The session owns the frozen query and the three first-page collection batches.
Each explicit advance adds one collection; the bridge receives metadata only,
and page reads transfer at most one bounded group slice from an immutable
revision. No later offset is requested and no provider-wide end is claimed.
"""

from __future__ import annotations

import copy
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from cpe.gui import online
from cpe.itkc import adapter
from cpe.match.engine import Settings

TOTAL_COLLECTIONS = len(adapter.COLLECTIONS)
PAGE_ROWS = 100
GROUPS = ("likely", "less")
SORTS = ("source", "era", "era-desc")


@dataclass(frozen=True)
class Snapshot:
    revision: int
    completed: int
    likely: tuple[dict[str, Any], ...]
    less: tuple[dict[str, Any], ...]
    source: tuple[dict[str, Any], ...]
    state: str


def _decorate_rows(
    answer: dict[str, Any], collection: str, batch: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    likely = [copy.deepcopy(row) for row in answer.get("likely", [])]
    less = [copy.deepcopy(row) for row in answer.get("less", [])]
    position = 0
    for group in (likely, less):
        for row in group:
            position += 1
            row["result_id"] = f"itkc:{collection}:{batch}:{position:06d}"
    return likely, less


def _era_year(row: dict[str, Any]) -> int | float | None:
    era = row.get("era")
    if not isinstance(era, dict):
        return None
    year = era.get("year")
    if isinstance(year, bool) or not isinstance(year, (int, float)):
        return None
    return year if math.isfinite(year) else None


def sorted_rows(rows: Sequence[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    """Source order is stable; era orders put unknown years last in both directions."""
    if order == "source":
        return list(rows)
    direction = -1 if order == "era-desc" else 1

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, row = item
        year = _era_year(row)
        if year is None:
            return 1, 0.0, index
        return 0, direction * year, index

    return [row for _, row in sorted(enumerate(rows), key=key)]


class ItkcSearchSession:
    """One active ITKC session with immutable batch revisions."""

    def __init__(
        self,
        job_id: str,
        request: dict[str, Any],
        settings: Settings,
        client: Any,
        generation: int,
        current_generation: Callable[[int], bool],
        *,
        analyzer: Callable[..., dict[str, Any]] = online.search_itkc,
    ) -> None:
        self.id = job_id
        self._request = copy.deepcopy(request)
        self._settings = copy.deepcopy(settings)
        self._client = client
        self._generation = generation
        self._current_generation = current_generation
        self._analyzer = analyzer
        self._lock = threading.RLock()
        self._snapshot_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._state = "paused"
        self._error = ""
        self._revision = 0
        self._completed = 0
        self._snapshots: dict[int, Snapshot] = {0: Snapshot(0, 0, (), (), (), "paused")}

    def _checkpoint(self) -> None:
        if self._cancelled.is_set() or not self._current_generation(self._generation):
            self._cancelled.set()
            raise InterruptedError("superseded ITKC session")

    def cancel(self) -> None:
        with self._snapshot_lock:
            self._cancelled.set()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def completed(self) -> int:
        return self._completed

    @property
    def generation(self) -> int:
        return self._generation

    def _current_snapshot(self) -> Snapshot:
        return self._snapshots[self._revision]

    def _state_now(self) -> str:
        if self._cancelled.is_set():
            return "cancelled"
        if self._completed >= TOTAL_COLLECTIONS:
            return "complete"
        return self._state

    def _next_collection(self, completed: int) -> str | None:
        return adapter.COLLECTIONS[completed] if completed < TOTAL_COLLECTIONS else None

    def _metadata_for(self, snapshot: Snapshot, *, current: bool) -> dict[str, Any]:
        state = self._state_now() if current else snapshot.state
        collections = [copy.deepcopy(item) for item in snapshot.source]
        answer: dict[str, Any] = {
            "id": self.id,
            "state": state,
            "revision": snapshot.revision,
            "completed": snapshot.completed,
            "total": TOTAL_COLLECTIONS,
            "next_collection": self._next_collection(snapshot.completed),
            "source": {"name": "itkc", "collections": collections},
            "counts": {"likely": len(snapshot.likely), "less": len(snapshot.less)},
            "warnings": [],
        }
        if current and self._error and state == "failed":
            answer["error"] = self._error
        return answer

    def metadata(self) -> dict[str, Any]:
        with self._snapshot_lock:
            return self._metadata_for(self._current_snapshot(), current=True)

    def _cancel_metadata_locked(self) -> dict[str, Any]:
        with self._snapshot_lock:
            self._state = "cancelled"
            self._error = "search_cancelled"
            return self._metadata_for(self._current_snapshot(), current=True)

    def _step_locked(self) -> dict[str, Any]:
        try:
            self._checkpoint()
        except InterruptedError:
            return self._cancel_metadata_locked()
        if self._completed >= TOTAL_COLLECTIONS:
            with self._snapshot_lock:
                self._state = "complete"
                return self._metadata_for(self._current_snapshot(), current=True)
        if self._cancelled.is_set():
            return self._cancel_metadata_locked()
        collection = adapter.COLLECTIONS[self._completed]
        try:
            answer = self._analyzer(
                self._request,
                self._client,
                self._settings,
                checkpoint=self._checkpoint,
                collections=(collection,),
            )
        except InterruptedError:
            return self._cancel_metadata_locked()
        if "error" in answer:
            with self._snapshot_lock:
                self._state = "failed"
                self._error = str(answer["error"])
                return self._metadata_for(self._current_snapshot(), current=True)
        try:
            self._checkpoint()
        except InterruptedError:
            return self._cancel_metadata_locked()
        source = answer.get("source", {}).get("collections", [])
        if not isinstance(source, list) or len(source) != 1:
            with self._snapshot_lock:
                self._state = "failed"
                self._error = "source_internal"
                return self._metadata_for(self._current_snapshot(), current=True)
        likely, less = _decorate_rows(answer, collection, self._completed)
        with self._snapshot_lock:
            # Cancellation and publishing share this boundary so prepared rows
            # cannot become a new revision after the caller has stopped the job.
            try:
                self._checkpoint()
            except InterruptedError:
                self._state = "cancelled"
                self._error = "search_cancelled"
                return self._metadata_for(self._current_snapshot(), current=True)
            previous = self._current_snapshot()
            snapshot = Snapshot(
                revision=previous.revision + 1,
                completed=previous.completed + 1,
                likely=previous.likely + tuple(likely),
                less=previous.less + tuple(less),
                source=tuple([*previous.source, copy.deepcopy(source[0])]),
                state="complete"
                if previous.completed + 1 >= TOTAL_COLLECTIONS
                else "paused",
            )
            self._revision = snapshot.revision
            self._completed = snapshot.completed
            self._snapshots[snapshot.revision] = snapshot
            self._state = snapshot.state
            self._error = ""
            return self._metadata_for(snapshot, current=True)

    def start(self) -> dict[str, Any]:
        with self._lock:
            return self._step_locked()

    def advance(self, generation: int) -> dict[str, Any]:
        if isinstance(generation, bool) or not isinstance(generation, int):
            return {"error": "bad_request"}
        if generation != self._generation:
            return {"error": "bad_request"}
        if not self._lock.acquire(blocking=False):
            return {"error": "source_internal"}
        try:
            if not self._current_generation(generation):
                self._cancelled.set()
                return self._metadata_for(self._current_snapshot(), current=True)
            if self._cancelled.is_set():
                return self._metadata_for(self._current_snapshot(), current=True)
            if self._completed >= TOTAL_COLLECTIONS:
                return self._metadata_for(self._current_snapshot(), current=True)
            return self._step_locked()
        finally:
            self._lock.release()

    def page(
        self,
        revision: int,
        group: str,
        start: int = 0,
        sort: str = "source",
    ) -> dict[str, Any]:
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or group not in GROUPS
            or isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or sort not in SORTS
        ):
            return {"error": "bad_request"}
        with self._snapshot_lock:
            snapshot = self._snapshots.get(revision)
            if snapshot is None:
                return {"error": "bad_request"}
            current = snapshot.revision == self._revision
            source = snapshot.likely if group == "likely" else snapshot.less
            ordered = sorted_rows(source, sort)
            answer = self._metadata_for(snapshot, current=current)
        rows = copy.deepcopy(ordered[start : start + PAGE_ROWS])
        answer.update({"group": group, "start": start, "rows": rows})
        return answer
