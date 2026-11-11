"""A library-injected application service for MCP frontends.

This module contains no MCP transport, SDK import, GUI window, preference or
provider request by itself.  The host supplies a preloaded
:class:`cpe.gui.service.Library`; online access is possible only for source IDs
listed in ``enabled_online`` and uses the same adapters as the desktop window.
"""

from __future__ import annotations

import copy
import json
import math
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from cpe.docusky.adapter import valid_database_name
from cpe.gui.search_session import sorted_rows
from cpe.gui.service import Library
from cpe.gui.service import search_pair as _gui_search_pair
from cpe.gui.service import search_words as _gui_search_words
from cpe.mcp.disclosure import DisclosurePolicy
from cpe.mcp.evidence import annotate_row
from cpe.mcp.response import wire_size
from cpe.mcp.sources import LOCAL_SOURCE_IDS, SOURCE_IDS, SourceRegistry

ADVERTISED_SOURCES = (*LOCAL_SOURCE_IDS, *SOURCE_IDS)
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 20
_SESSION_OVERHEAD = 128
_CURSOR_RESERVE = "x" * 64
_CURSOR_RETENTION = 128


class ServiceConfigError(ValueError):
    """An invalid service configuration supplied by the host."""


@dataclass(eq=False)
class _Operation:
    id: str
    source_id: str
    mode: str
    checkpoint: Callable[[], None]
    cancelled: threading.Event = field(default_factory=threading.Event)
    owns_guard: bool = True

    def check(self) -> None:
        if self.cancelled.is_set():
            raise InterruptedError("MCP search cancelled")
        self.checkpoint()


@dataclass
class _Session:
    """One immutable search snapshot and its opaque cursor offsets."""

    search_id: str
    created: float
    last_access: float
    mode: str
    query: dict[str, Any]
    source: dict[str, Any]
    rows: tuple[dict[str, Any], ...]
    row_sizes: tuple[int, ...]
    counts: dict[str, int] | None
    page_size: int
    bytes: int
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[Any] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    cursor_at: dict[int, str] = field(default_factory=dict)


def _catalog() -> dict[str, str]:
    """The English source catalog values, without importing the GUI app."""
    path = Path(__file__).resolve().parents[1] / "gui" / "i18n" / "en.json"
    return cast(dict[str, str], json.loads(path.read_text(encoding="utf-8")))


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ServiceConfigError(f"{name} must be a positive integer")
    return value


class SearchService:
    """Application-level search service for an MCP host.

    The service never opens a local folder, accepts a path or URL, imports text,
    or starts an online request at construction.  ``library`` is the host's
    already-loaded collection.  Online source IDs are usable only when named in
    ``enabled_online``; ITKC additionally needs either ``itkc_log`` or an
    injected ``clients["itkc"]`` carrying its recorded request log.
    """

    def __init__(
        self,
        library: Library,
        *,
        enabled_online: Iterable[str] = (),
        itkc_log: Path | None = None,
        clients: Mapping[str, Any] | None = None,
        registry: SourceRegistry | None = None,
        library_provider: Callable[[str], Library | None] | None = None,
        local_status: Callable[[str], dict[str, Any]] | None = None,
        external_retained: Callable[[], int] | None = None,
        max_sessions: int = 8,
        ttl_seconds: float = 900.0,
        max_retained_bytes: int = 64 * 1024 * 1024,
        response_byte_budget: int = 256 * 1024,
        clock: Callable[[], float] = time.monotonic,
        disclosure: DisclosurePolicy | None = None,
    ) -> None:
        if not isinstance(library, Library):
            raise ServiceConfigError("library must be a loaded cpe.gui.service.Library")
        enabled = tuple(dict.fromkeys(enabled_online or ()))
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ServiceConfigError("ttl_seconds must be a positive number")
        if ttl_seconds <= 0 or not math.isfinite(float(ttl_seconds)):
            raise ServiceConfigError("ttl_seconds must be a finite positive number")
        if clients is not None and not isinstance(clients, Mapping):
            raise ServiceConfigError("clients must be a mapping")
        self._library = library
        self.disclosure = disclosure
        self._enabled = frozenset(enabled)
        self._itkc_log = itkc_log
        self._clients: dict[str, Any] = dict(clients or {})
        self.registry = registry or SourceRegistry(
            library.settings, enabled_online=enabled, itkc_log=itkc_log, clients=clients
        )
        self._library_provider = library_provider
        self._local_status = local_status
        self._external_retained = external_retained or (lambda: 0)
        self._max_sessions = _positive_int(max_sessions, "max_sessions")
        self._ttl = float(ttl_seconds)
        self._max_retained = _positive_int(max_retained_bytes, "max_retained_bytes")
        self._response_budget = _positive_int(
            response_byte_budget, "response_byte_budget"
        )
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        # A bounded list of IDs removed by TTL or session eviction, so a stale
        # cursor reaches cursor_expired rather than being mistaken for a typo.
        self._retired: dict[str, None] = {}
        self._lock = threading.RLock()
        self._search_lock = threading.Lock()
        self._active_operation: _Operation | None = None
        self._operations: set[_Operation] = set()

    # ------------------------------------------------------------------ source
    def list_sources(self) -> dict[str, Any]:
        """The four local slots, eighteen online sources and enabled profiles."""
        with self._lock:
            return {
                "sources": [
                    self._source_summary(source_id)
                    for source_id in (
                        *ADVERTISED_SOURCES,
                        *(
                            value
                            for value in self._enabled
                            if value.startswith("docusky:")
                        ),
                    )
                ]
            }

    def get_source_details(self, source_id: str) -> dict[str, Any]:
        """English scope/notes for one advertised source; no download action."""
        return self._source_details(source_id)

    def _source_summary(self, source_id: str) -> dict[str, Any]:
        enabled = source_id in LOCAL_SOURCE_IDS or self.registry.enabled(source_id)
        configured = self._configured(source_id)
        catalog = _catalog()
        status = (
            self._local_status(source_id)
            if (self._local_status is not None and source_id in LOCAL_SOURCE_IDS)
            else {}
        )
        return {
            "id": source_id,
            "name": catalog.get(f"source.{source_id}", source_id),
            "kind": "local" if source_id in LOCAL_SOURCE_IDS else "online",
            "enabled": enabled,
            "configured": configured,
            "available": enabled and configured and not status.get("load_error"),
            **(
                {"load_error": status["load_error"]} if status.get("load_error") else {}
            ),
        }

    def _configured(self, source_id: str) -> bool:
        if source_id in LOCAL_SOURCE_IDS:
            if self._local_status is not None:
                return bool(self._local_status(source_id).get("configured"))
            # An empty host library is not a corpus: say so instead of returning
            # a global zero that a caller could mistake for "no text contains it".
            library = self._get_library(source_id)
            return library is not None and bool(library.texts)
        if source_id == "itkc":
            return self.registry._has_itkc()
        return True

    def _source_details(self, source_id: Any) -> dict[str, Any]:
        if not isinstance(source_id, str) or not source_id:
            return {"error": "unknown_source"}
        if not self._known_source(source_id):
            return {"error": "unimplemented_source"}
        catalog = _catalog()
        details: dict[str, Any] = {
            **self._source_summary(source_id),
            "search_modes": ["words"]
            if source_id in {"ships", "thdl", "csid", "cand", "kyudb"}
            else ["pair", "words"],
        }
        if source_id in LOCAL_SOURCE_IDS:
            library = self._get_library(source_id)
            details.update(
                {
                    "note": catalog.get("library.help", ""),
                    "scope": catalog.get("library.guide.scope", ""),
                    "provided": catalog.get("library.guide.provided", ""),
                    "limits": catalog.get("library.guide.limits", ""),
                    "text_count": len(library.texts) if library else 0,
                    "skipped_count": len(library.skipped) if library else 0,
                }
            )
            return details
        prefix = f"source.{source_id.split(':', 1)[0]}"
        details["note"] = catalog.get(f"{prefix}.note", "")
        details["scope"] = catalog.get(f"{prefix}.guide.scope", "")
        details["provided"] = catalog.get(f"{prefix}.guide.provided", "")
        details["limits"] = catalog.get(f"{prefix}.guide.limits", "")
        usage = catalog.get(f"{prefix}.guide.usage")
        if usage is not None:
            details["usage"] = usage
        return details

    # ------------------------------------------------------------------ search
    def search_pattern(
        self,
        source_id: str,
        mode: str = "pair",
        first: str = "",
        second: str = "",
        query: str = "",
        page_size: int = _DEFAULT_PAGE_SIZE,
        *,
        checkpoint: Callable[[], None] | None = None,
        options: dict[str, str] | None = None,
        sort: str = "source",
    ) -> dict[str, Any]:
        """Run one GUI-equivalent local or enabled online search.

        ``mode`` is the GUI compound pair mode or the GUI one-box word mode;
        this service does not claim the full pattern DSL.  The returned search ID
        owns an immutable snapshot, and ``next_cursor`` reads the next bounded
        page without searching or fetching again.
        """
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            return {"error": "bad_page_size"}
        if not 1 <= page_size <= _MAX_PAGE_SIZE:
            return {"error": "bad_page_size"}
        source_id = source_id.strip() if isinstance(source_id, str) else ""
        if not self._known_source(source_id):
            if not source_id:
                return {"error": "unknown_source"}
            return {"error": "unimplemented_source"}
        if mode not in ("pair", "words"):
            return {"error": "bad_mode"}
        if sort not in {"source", "era", "era-desc"}:
            return {"error": "bad_sort"}
        if options is not None and (
            not isinstance(options, dict)
            or any(
                key
                not in {"historydb_site", "historydb_item", "nahf_item", "site", "item"}
                or not isinstance(value, str)
                or len(value) > 256
                for key, value in options.items()
            )
        ):
            return {"error": "bad_options"}
        request_query: dict[str, Any]
        if mode == "pair":
            if not isinstance(first, str) or not isinstance(second, str):
                return {"error": "bad_request"}
            request_query = {"first": first, "second": second}
        else:
            if not isinstance(query, str):
                return {"error": "bad_request"}
            request_query = {"query": query}
        if source_id in LOCAL_SOURCE_IDS and not self._configured(source_id):
            return {"error": "local_unconfigured"}
        if source_id in LOCAL_SOURCE_IDS and self._local_status is not None:
            status = self._local_status(source_id)
            if status.get("load_error"):
                return {"error": status["load_error"]}
        if source_id not in LOCAL_SOURCE_IDS and not self.registry.enabled(source_id):
            return {"error": "source_disabled"}
        if checkpoint is None:
            checkpoint = _noop
        elif not callable(checkpoint):
            return {"error": "bad_request"}

        operation = self.begin_operation(checkpoint, source_id, mode)
        if operation is None:
            return {"error": "search_busy"}
        try:
            check = operation.check
            check()
            from cpe.export.run_file import normalization_section

            settings = self.registry.settings
            local_library: Library | None = None
            if source_id in LOCAL_SOURCE_IDS:
                local_library = self._get_library(source_id)
                if local_library is None:
                    status = self._local_status(source_id) if self._local_status else {}
                    return {"error": status.get("load_error") or "local_unconfigured"}
                settings = local_library.settings
            request_query["normalization"] = normalization_section(
                settings.normalization
            )
            if source_id in LOCAL_SOURCE_IDS:
                # Use the same Library snapshot for the provenance metadata and
                # the match itself. A second lazy load could observe a settings
                # change between those two reads.
                answer = self._run(
                    source_id,
                    mode,
                    first,
                    second,
                    query,
                    check,
                    library=local_library,
                )
            elif options:
                answer = self._run_options(
                    source_id, mode, first, second, query, check, options
                )
            else:
                answer = self._run(source_id, mode, first, second, query, check)
            if isinstance(answer, dict) and all(
                isinstance(answer.get(group), list) for group in ("likely", "less")
            ):
                answer = {
                    **answer,
                    "likely": sorted_rows(answer.get("likely", []), sort),
                    "less": sorted_rows(answer.get("less", []), sort),
                }
            request_query.update(
                source_id=source_id, options=copy.deepcopy(options or {}), sort=sort
            )
            check()
            return self._publish(
                source_id,
                mode,
                request_query,
                page_size,
                answer,
                check,
            )
        except InterruptedError:
            return {"error": "search_cancelled"}
        except Exception:
            return {"error": "source_internal"}
        finally:
            self.finish_operation(operation)

    def begin_operation(
        self, checkpoint: Callable[[], None], source_id: str, mode: str
    ) -> _Operation | None:
        with self._lock:
            # Cancelled workers retire at bounded core/network checkpoints.
            # They cannot be allowed to accumulate indefinitely under repeated
            # cancel/restart calls while a provider request is still in flight.
            if len(self._operations) >= 4 or not self._search_lock.acquire(
                blocking=False
            ):
                return None
            operation = _Operation(
                "operation_" + secrets.token_urlsafe(12), source_id, mode, checkpoint
            )
            self._active_operation = operation
            self._operations.add(operation)
            return operation

    def finish_operation(self, operation: _Operation) -> None:
        with self._lock:
            if operation.owns_guard:
                operation.owns_guard = False
                self._search_lock.release()
            if self._active_operation is operation:
                self._active_operation = None
            self._operations.discard(operation)

    def cancel_active(
        self, checkpoint: Callable[[], None] | None = None, operation_id: str = ""
    ) -> bool:
        with self._lock:
            operation = self._active_operation
            if operation is None or (
                checkpoint is not None and operation.checkpoint is not checkpoint
            ):
                return False
            if operation_id and operation.id != operation_id:
                return False
            operation.cancelled.set()
            if operation.owns_guard:
                operation.owns_guard = False
                self._search_lock.release()
            self._active_operation = None
            return True

    def manage_searches(
        self, action: str, search_id: str = "", operation_id: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            if action == "cancel":
                return {"cancelled": self.cancel_active(operation_id=operation_id)}
            if action in {"clear", "clear_all"}:
                keys = list(self._sessions) if action == "clear_all" else [search_id]
                removed = 0
                for key in keys:
                    if key in self._sessions:
                        del self._sessions[key]
                        self._retire_locked(key)
                        removed += 1
                return {"cleared": removed}
            if action != "list":
                return {"error": "bad_request"}
            self._expire_locked()
            active = self._active_operation
            return {
                "active_operation": None
                if active is None
                else {
                    "id": active.id,
                    "source_id": active.source_id,
                    "mode": active.mode,
                },
                "retiring_operations": len(self._operations) - int(active is not None),
                "searches": [
                    {
                        "search_id": session.search_id,
                        "mode": session.mode,
                        "source": session.source.get("name"),
                        "counts": copy.deepcopy(session.counts),
                    }
                    for session in self._sessions.values()
                ],
            }

    def _publish(
        self,
        source_id: str,
        mode: str,
        request_query: dict[str, Any],
        page_size: int,
        answer: Any,
        checkpoint: Callable[[], None],
        *,
        annotated: bool = False,
    ) -> dict[str, Any]:
        """Build and store the snapshot while the active-search guard is held."""
        if not isinstance(answer, dict):
            return {"error": "source_failed"}
        if "error" in answer:
            return {"error": str(answer["error"])}
        counts: dict[str, int] | None
        rows, counts = self._partition(answer)
        if rows is None:
            return {"error": "source_failed"}
        # Reordered snapshots already carry server-derived evidence labels even
        # when their canonical record was withheld. Reclassifying them from the
        # projected row would turn structural candidates into provider references.
        if not annotated:
            rows = [annotate_row(row) for row in rows]
        if self.disclosure is not None:
            rows = [self.disclosure.row(row, source_id) for row in rows]
        session_rows = tuple(rows)
        sizes = tuple(_json_size(row) for row in session_rows)
        retained = sum(sizes)
        source = self._source_metadata(source_id, answer)
        extra = {
            key: copy.deepcopy(answer[key])
            for key in ("external_search_url",)
            if key in answer
        }
        if self.disclosure is not None:
            extra["disclosure"] = self.disclosure.describe(source_id)
        if source.get("external_only"):
            counts = None
            extra["navigation_only"] = True
        details = self._source_details(source_id)
        for key in ("scope", "limits", "note"):
            if key in details:
                source.setdefault(key, details[key])
        raw_warnings = answer.get("warnings", [])
        warnings = copy.deepcopy(raw_warnings) if isinstance(raw_warnings, list) else []
        # Bound the snapshot metadata too: the submitted query/mode/counts, the
        # source block, warnings, a fixed session overhead, and one reserved
        # cursor slot per page. This is serialized-retention accounting, not a
        # row-only cap.
        retained += _json_size(source) + _json_size(warnings)
        retained += _json_size({"mode": mode, "query": request_query, "counts": counts})
        retained += _json_size(extra)
        # A byte-limited page may hold fewer rows than page_size, so the actual
        # page count can exceed the arithmetic page count. Reserving one cursor
        # slot per row is the safe upper bound.
        retained += _SESSION_OVERHEAD + len(session_rows) * _CURSOR_RETENTION
        external_bytes = self._external_retained()
        if retained + external_bytes > self._max_retained:
            return {
                "error": "storage_limit",
                "bytes": retained + external_bytes,
                "limit": self._max_retained,
            }
        # This is the last cancellation window: after it, publication is a short
        # in-memory commit and cannot be interrupted by the transport thread.
        checkpoint()
        with self._lock:
            checkpoint()
            self._expire_locked()
            while self._sessions and (
                len(self._sessions) >= self._max_sessions
                or self._retained_locked() + retained + external_bytes
                > self._max_retained
            ):
                oldest = min(
                    self._sessions, key=lambda key: self._sessions[key].last_access
                )
                del self._sessions[oldest]
                self._retire_locked(oldest)
            search_id = "search_" + secrets.token_urlsafe(18)
            now = self._clock()
            session = _Session(
                search_id=search_id,
                created=now,
                last_access=now,
                mode=mode,
                query=dict(request_query),
                source=source,
                rows=session_rows,
                row_sizes=sizes,
                counts=counts,
                page_size=page_size,
                bytes=retained,
                extra=extra,
                warnings=warnings,
            )
            self._sessions[search_id] = session
            page = self._page_locked(session, 0)
            if "error" in page:
                # Metadata alone could not fit the advertised response budget;
                # do not leave a search ID that can never return a page.
                del self._sessions[search_id]
                return page
            return page

    def get_search_results(self, search_id: str, cursor: str) -> dict[str, Any]:
        """Read one page from a stored snapshot; never run or fetch the search."""
        if not isinstance(search_id, str) or not search_id:
            return {"error": "search_not_found"}
        if cursor == "":
            with self._lock:
                self._expire_locked()
                session = self._sessions.get(search_id)
                if session is None:
                    return {
                        "error": "cursor_expired"
                        if search_id in self._retired
                        else "search_not_found"
                    }
                session.last_access = self._clock()
                return self._page_locked(session, 0)
        if not isinstance(cursor, str) or ":" not in cursor:
            return {"error": "invalid_cursor"}
        prefix, token = cursor.split(":", 1)
        if prefix != search_id:
            return {"error": "invalid_cursor"}
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(search_id)
            if session is None:
                if search_id in self._retired:
                    return {"error": "cursor_expired"}
                return {"error": "search_not_found"}
            offset = session.cursors.get(token)
            if offset is None:
                return {"error": "cursor_expired"}
            session.last_access = self._clock()
            return self._page_locked(session, offset)

    def snapshot(self, search_id: str) -> dict[str, Any]:
        """A complete retained snapshot for host-authorized export, not a wire page."""
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(search_id)
            if session is None:
                return {"error": "search_not_found"}
            session.last_access = self._clock()
            return {
                "search_id": search_id,
                "mode": session.mode,
                "query": copy.deepcopy(session.query),
                "source": copy.deepcopy(session.source),
                "counts": copy.deepcopy(session.counts),
                "rows": copy.deepcopy(session.rows),
                "warnings": copy.deepcopy(session.warnings),
                **copy.deepcopy(session.extra),
            }

    def reorder(
        self, search_id: str, sort: str = "source", page_size: int = 20
    ) -> dict[str, Any]:
        if sort not in {"source", "era", "era-desc"}:
            return {"error": "bad_sort"}
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            return {"error": "bad_page_size"}
        snapshot = self.snapshot(search_id)
        if "error" in snapshot:
            return snapshot
        answer = {**snapshot, "likely": [], "less": []}
        for group in ("likely", "less"):
            answer[group] = sorted_rows(
                [
                    {key: value for key, value in row.items() if key != "group"}
                    for row in snapshot["rows"]
                    if row["group"] == group
                ],
                sort,
            )
        query = {**snapshot["query"], "sort": sort}
        return self._publish(
            snapshot["query"].get("source_id", snapshot["source"]["name"]),
            snapshot["mode"],
            query,
            page_size,
            answer,
            _noop,
            annotated=True,
        )

    # --------------------------------------------------------------- internals
    def _run(
        self,
        source_id: str,
        mode: str,
        first: str,
        second: str,
        query: str,
        checkpoint: Callable[[], None],
        *,
        library: Library | None = None,
    ) -> dict[str, Any]:
        if source_id in LOCAL_SOURCE_IDS:
            if library is None:
                library = self._get_library(source_id)
            if library is None:
                status = self._local_status(source_id) if self._local_status else {}
                return {"error": status.get("load_error") or "local_unconfigured"}
            if mode == "pair":
                answer = _gui_search_pair(
                    library,
                    first,
                    second,
                    checkpoint=checkpoint,
                    include_records=True,
                )
            else:
                answer = _gui_search_words(
                    library, query, checkpoint=checkpoint, include_records=True
                )
            return {
                **answer,
                "source": {
                    "name": source_id,
                    "text_count": len(library.texts),
                    "skipped_count": len(library.skipped),
                },
            }
        return self._run_options(source_id, mode, first, second, query, checkpoint, {})

    def _run_options(
        self,
        source_id: str,
        mode: str,
        first: str,
        second: str,
        query: str,
        checkpoint: Callable[[], None],
        options: dict[str, str],
    ) -> dict[str, Any]:
        if source_id in LOCAL_SOURCE_IDS:
            return self._run(source_id, mode, first, second, query, checkpoint)
        request: dict[str, Any] = {**options, "source": source_id, "mode": mode}
        if source_id == "historydb":
            request.setdefault("site", options.get("historydb_site", ""))
            request.setdefault("item", options.get("historydb_item", ""))
        elif source_id == "nahf":
            request.setdefault("item", options.get("nahf_item", ""))
        if mode == "pair":
            request.update(first=first, second=second)
        else:
            request["query"] = query
        return self.registry.search(request, checkpoint, include_records=True)

    def _client(self, source_id: str) -> Any | None:
        return self.registry.client(source_id)

    def _get_library(self, source_id: str) -> Library | None:
        if self._library_provider is not None:
            return self._library_provider(source_id)
        return self._library if source_id == "local" else None

    @staticmethod
    def _known_source(source_id: str) -> bool:
        return source_id in ADVERTISED_SOURCES or (
            source_id.startswith("docusky:") and valid_database_name(source_id[8:])
        )

    def _partition(
        self, answer: dict[str, Any]
    ) -> tuple[list[dict[str, Any]] | None, dict[str, int]]:
        if "likely" not in answer or "less" not in answer:
            return None, {}
        likely = answer["likely"]
        less = answer["less"]
        if not isinstance(likely, list) or not isinstance(less, list):
            return None, {}
        rows: list[dict[str, Any]] = []
        for group, group_rows in (("likely", likely), ("less", less)):
            for row in group_rows:
                if not isinstance(row, dict):
                    return None, {}
                tagged = copy.deepcopy(row)
                tagged["group"] = group
                rows.append(tagged)
        return rows, {
            "likely": len(likely),
            "less": len(less),
            "total": len(rows),
        }

    def _source_metadata(
        self, source_id: str, answer: dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(answer.get("source"), dict):
            return copy.deepcopy(answer["source"])
        if source_id in LOCAL_SOURCE_IDS:
            library = self._get_library(source_id)
            return {
                "name": source_id,
                "text_count": len(library.texts) if library else 0,
                "skipped_count": len(library.skipped) if library else 0,
            }
        source = answer.get("source")
        if not isinstance(source, dict):
            return {"name": source_id}
        return copy.deepcopy(source)

    def _expire_locked(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, session in self._sessions.items()
            if now - session.last_access > self._ttl
        ]
        for key in expired:
            del self._sessions[key]
            self._retire_locked(key)

    def _retained_locked(self) -> int:
        return sum(session.bytes for session in self._sessions.values())

    def _retire_locked(self, search_id: str) -> None:
        self._retired[search_id] = None
        while len(self._retired) > 256:
            self._retired.pop(next(iter(self._retired)))

    def _cursor(self, session: _Session, offset: int) -> str:
        existing = session.cursor_at.get(offset)
        if existing is not None:
            return existing
        token = secrets.token_urlsafe(18)
        session.cursors[token] = offset
        cursor = f"{session.search_id}:{token}"
        session.cursor_at[offset] = cursor
        return cursor

    def _page_payload(
        self,
        session: _Session,
        rows: list[dict[str, Any]],
        omitted: list[dict[str, Any]],
        index: int,
    ) -> dict[str, Any]:
        # A dummy cursor reserves its maximum serialized size. The real token is
        # shorter, so a page accepted against this envelope cannot exceed the
        # advertised budget once the actual next_cursor is written.
        more = index < len(session.rows)
        next_cursor = f"{session.search_id}:{_CURSOR_RESERVE}" if more else None
        return {
            **copy.deepcopy(session.extra),
            "search_id": session.search_id,
            "mode": session.mode,
            "query": copy.deepcopy(session.query),
            "source": copy.deepcopy(session.source),
            "counts": copy.deepcopy(session.counts),
            "warnings": copy.deepcopy(session.warnings),
            "rows": rows,
            "omitted": omitted,
            "returned": len(rows),
            "next_cursor": next_cursor,
        }

    def _response_limit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "error": "response_limit",
            "bytes": wire_size(payload),
            "limit": self._response_budget,
        }

    def _page_locked(self, session: _Session, offset: int) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        omitted: list[dict[str, Any]] = []
        index = offset
        base = self._page_payload(session, [], [], offset)
        if wire_size(base) > self._response_budget:
            return self._response_limit(base)
        while index < len(session.rows) and (
            len(rows) + len(omitted) < session.page_size
        ):
            row = session.rows[index]
            row_size = session.row_sizes[index]
            if row_size > self._response_budget:
                candidate_omitted = [
                    *omitted,
                    {
                        "result_index": index,
                        "reason": "row_too_large",
                        "bytes": row_size,
                    },
                ]
                payload = self._page_payload(
                    session, copy.deepcopy(rows), candidate_omitted, index + 1
                )
                if wire_size(payload) <= self._response_budget:
                    omitted = candidate_omitted
                    index += 1
                    continue
                if rows or omitted:
                    break
                return self._response_limit(payload)
            candidate_rows = [*rows, copy.deepcopy(row)]
            payload = self._page_payload(
                session, candidate_rows, copy.deepcopy(omitted), index + 1
            )
            if wire_size(payload) <= self._response_budget:
                rows = candidate_rows
                index += 1
                continue
            if rows or omitted:
                break
            # The envelope plus this row cannot fit even on an empty page. Keep
            # the cursor moving by recording an explicit omission, never by
            # truncating the source row or returning the same cursor forever.
            candidate_omitted = [
                *omitted,
                {
                    "result_index": index,
                    "reason": "page_budget",
                    "bytes": row_size,
                },
            ]
            payload = self._page_payload(
                session, copy.deepcopy(rows), candidate_omitted, index + 1
            )
            if wire_size(payload) <= self._response_budget:
                omitted = candidate_omitted
                index += 1
                continue
            return self._response_limit(payload)
        next_cursor = (
            self._cursor(session, index) if index < len(session.rows) else None
        )
        answer = self._page_payload(session, rows, omitted, index)
        answer["next_cursor"] = next_cursor
        return answer


def _noop() -> None:
    """A checkpoint that does nothing."""
