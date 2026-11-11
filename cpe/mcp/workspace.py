"""Headless access to the existing 3.1 library and import application services."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import Any

from cpe.gui.app import LANGUAGES, LOCAL_SOURCES, Api
from cpe.gui.preferences import Preferences
from cpe.gui.search_session import ItkcSearchSession
from cpe.gui.service import Library, text_from
from cpe.local.search import SearchOutputs
from cpe.local.txt import InputError
from cpe.match.engine import Settings
from cpe.match.run import Outputs
from cpe.mcp.identity import staged_rows
from cpe.mcp.policy import PathPolicy, PolicyError
from cpe.mcp.response import wire_size
from cpe.normalize.options import NormalizationError, options_from_mapping

IMPORT_PROVIDERS = (
    "portal",
    "ctp",
    "gutenberg",
    "ndl",
    "rise",
    "cbeta",
    "nikh-partial",
)


class WorkspacePreferences(Preferences):
    """MCP-owned preferences; never consult or write the desktop GUI profile."""

    def __init__(self, path: Path | None = None) -> None:
        self._state_path = path
        self._state_lock = threading.RLock()
        self._data: dict[str, Any] = {}
        if path is not None and path.exists():
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("MCP workspace state must be an object")
            self._data = {
                k: v
                for k, v in value.items()
                if k
                in {
                    "local_folders",
                    "active_local",
                    "folder",
                    "sources",
                    "source_options",
                    "language",
                    "result_sort",
                    "normalization",
                }
            }

    def load(self) -> dict[str, Any]:
        with self._state_lock:
            return copy.deepcopy(self._data)

    def get(self, key: str) -> str:
        value = self.load().get(key, "")
        return value if isinstance(value, str) else ""

    def set(self, key: str, value: str) -> None:
        self.update({key: value})

    def update(self, values: dict[str, Any]) -> None:
        with self._state_lock:
            candidate = {**self._data, **copy.deepcopy(values)}
            if self._state_path is not None:
                path = self._state_path
                content = (
                    json.dumps(
                        {k: v for k, v in candidate.items() if k != "ctp_key"},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                handle, name = tempfile.mkstemp(prefix=".mcp-state-", dir=path.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                        stream.write(content)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            self._data = candidate

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._state_lock:
            before = copy.deepcopy(self._data)
            path = self._state_path
            self._state_path = None
            try:
                yield
                self._state_path = path
                self.update({})
            except BaseException:
                self._data = before
                raise
            finally:
                self._state_path = path


class Workspace:
    """Reuse headless GUI operations behind an explicit host path/network policy."""

    def __init__(
        self,
        library: Library,
        *,
        input_paths: Iterable[Path] = (),
        read_roots: Iterable[Path] = (),
        write_roots: Iterable[Path] = (),
        enabled_imports: Iterable[str] = (),
        state_path: Path | None = None,
        ctp_key: str = "",
        clients: Mapping[str, Any] | None = None,
        restore_normalization: bool = True,
    ) -> None:
        paths = tuple(input_paths)
        self.policy = PathPolicy((*paths, *read_roots), write_roots)
        for path in paths:
            self.policy.check_library(path)
        self.preferences = WorkspacePreferences(state_path)
        self._enabled_imports = frozenset(enabled_imports)
        if self._enabled_imports - set(IMPORT_PROVIDERS):
            raise ValueError("unknown import provider")
        self._lock = threading.RLock()
        self._blocked_local: dict[str, tuple[str, str]] = {}
        saved = self.preferences.load()
        folders = saved.get("local_folders", {})
        if isinstance(folders, dict):
            clean_folders: dict[str, str] = {}
            for code, path in folders.items():
                if code not in LOCAL_SOURCES or not isinstance(path, str):
                    raise ValueError("invalid saved local library")
                try:
                    self.policy.check_library(path)
                except PolicyError as error:
                    self._blocked_local[code] = (path, error.code)
                else:
                    clean_folders[code] = path
            if self._blocked_local:
                # Do not let Api restore a saved slot that the current host
                # roots do not open. Keep the blocked path only as status.
                data = self.preferences.load()
                data["local_folders"] = clean_folders
                active = data.get("active_local")
                if active not in clean_folders:
                    active = next(
                        (code for code in LOCAL_SOURCES if code in clean_folders), ""
                    )
                    data["active_local"] = active
                data["folder"] = clean_folders.get(active, "")
                self.preferences._data = data
        normalization = saved.get("normalization")
        if normalization is not None and restore_normalization:
            settings = replace(
                library.settings, normalization=options_from_mapping(normalization)
            )
            library = Library(
                [text_from(text.local, settings, text.era) for text in library.texts],
                settings,
                library.skipped,
            )
        # A single startup directory is a writable-import target only when the
        # separate write policy permits it; explicit file inputs remain a library.
        folder = (
            str(paths[0].resolve()) if len(paths) == 1 and paths[0].is_dir() else ""
        )
        if ctp_key:
            self.preferences._data["ctp_key"] = ctp_key
        self.api = Api(library, self.preferences, folder)
        for code, (blocked_path, error_code) in self._blocked_local.items():
            if code in self.api._local_libraries:
                continue
            with self.api._local_lock:
                self.api._local_paths.setdefault(code, blocked_path)
                self.api._local_errors[code] = error_code
        for name, client in (clients or {}).items():
            if name in {"portal", "ctp", "rise"}:
                setattr(self.api, "_" + name, client)
        self._epoch = 0
        self._itkc: ItkcSearchSession | None = None
        self._itkc_bytes = 0
        self._itkc_generation = 0
        self._clear_itkc_requested = ""
        self._clear_worker: threading.Thread | None = None
        self._clear_worker_lock = threading.Lock()
        self._closed = threading.Event()
        self._catalogs: dict[str, dict[str, Any]] = {}
        self._search_service: Any = None

    def attach_service(self, service: Any) -> None:
        self._search_service = service

    def cached_catalog(
        self,
        kind: str,
        source: str,
        site: str = "",
        provider_page: int = 1,
        resource_id: str = "",
        catalog_id: str = "",
        refresh: bool = False,
    ) -> dict[str, Any]:
        from cpe.mcp.service import _json_size

        key = json.dumps(
            [kind, source, site, provider_page, resource_id], ensure_ascii=False
        )
        now = time.monotonic()
        with self._lock:
            self._catalogs = {
                id_: item
                for id_, item in self._catalogs.items()
                if now - item["accessed"] <= 900
            }
            if catalog_id:
                item = self._catalogs.get(catalog_id)
                if item is None or item["key"] != key:
                    return {"error": "catalog_expired"}
                item["accessed"] = now
                return {**copy.deepcopy(item["data"]), "catalog_id": catalog_id}
            if not refresh:
                for id_, item in self._catalogs.items():
                    if item["key"] == key:
                        item["accessed"] = now
                        return {**copy.deepcopy(item["data"]), "catalog_id": id_}
            data = (
                self.registry.catalog(source, site)
                if kind == "source"
                else self.import_catalog(source, provider_page, resource_id)
            )
            if "error" in data:
                return data
            size = _json_size(data)
            if size > 8 * 1024 * 1024:
                return {"error": "catalog_storage_limit"}
            while self._catalogs and (
                len(self._catalogs) >= 8
                or sum(item["bytes"] for item in self._catalogs.values()) + size
                > 8 * 1024 * 1024
            ):
                oldest = min(
                    self._catalogs, key=lambda id_: self._catalogs[id_]["accessed"]
                )
                del self._catalogs[oldest]
            id_ = "catalog_" + secrets.token_urlsafe(18)
            self._catalogs[id_] = {
                "key": key,
                "data": copy.deepcopy(data),
                "bytes": size,
                "accessed": now,
            }
            return {**data, "catalog_id": id_}

    def attach_registry(self, registry: Any) -> None:
        from cpe.mcp.sources import SOURCE_IDS

        self.registry = registry
        for source in SOURCE_IDS:
            if source != "ships" and registry.enabled(source):
                name = "sillok" if source == "sillok" else source
                if hasattr(self.api, name + "_client"):
                    setattr(
                        self.api, name + "_client", partial(registry.client, source)
                    )

    @property
    def settings(self) -> Settings:
        return self.api._library.settings

    def library_status(self, source_id: str) -> dict[str, Any]:
        with self.api._local_lock:
            status = {
                "configured": self.api._local_configured(source_id),
                "load_error": self.api._local_errors.get(source_id, ""),
            }
            path = self.api._local_paths.get(source_id)
            if path:
                try:
                    self.policy.check(path)
                except PolicyError as error:
                    status["load_error"] = error.code
            return status

    def library(self, source_id: str) -> Library | None:
        if source_id not in LOCAL_SOURCES:
            return None
        with self._lock:
            with self.api._local_lock:
                path = self.api._local_paths.get(source_id)
                library = self.api._local_libraries.get(source_id)
            if path:
                try:
                    # A cached immutable library is safe, but the root itself
                    # must still be authorized. A real load also scans its tree.
                    self.policy.check(path)
                except PolicyError:
                    return None
            if library is None:
                if path:
                    try:
                        self.policy.check_library(path)
                    except PolicyError:
                        return None
                self.api.load_local(source_id)
                with self.api._local_lock:
                    library = self.api._local_libraries.get(source_id)
            return library

    def describe(self) -> dict[str, Any]:
        return {
            **self.api.local_libraries(),
            "language": self.api.saved_language(),
            "sort": self.api.saved_sort(),
            "source_options": self.api.source_options(),
            "normalization": self.normalization(),
            "epoch": self._epoch,
            "capabilities": {
                **self.policy.describe(),
                "enabled_imports": sorted(self._enabled_imports),
            },
            "ctp_key_set": self.api.ctp_key_set(),
        }

    def normalization(self) -> dict[str, Any]:
        from cpe.export.run_file import normalization_section

        return normalization_section(self.settings.normalization)

    def manage_library(
        self, action: str, source_id: str = "local", path: str = ""
    ) -> dict[str, Any]:
        if action == "list":
            return self.api.local_libraries()
        if source_id not in LOCAL_SOURCES:
            return {"error": "bad_request"}
        with self._lock:
            try:
                if action == "open":
                    target = self.policy.check_library(path)
                    answer = self.api.open_folder(str(target), source_id)
                elif action in {"load", "reload", "activate", "remove"}:
                    stored = self.api._local_paths.get(source_id)
                    if stored and action != "remove":
                        self.policy.check_library(stored)
                    answer = getattr(self.api, action + "_local")(source_id)
                else:
                    return {"error": "bad_request"}
            except PolicyError as error:
                return {"error": error.code}
            if "error" not in answer and action in {"open", "reload", "remove"}:
                self._changed()
            return answer

    def configure(
        self,
        *,
        sources: list[str] | None = None,
        source_options: dict[str, str] | None = None,
        sort: str | None = None,
        language: str | None = None,
        normalization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if normalization is not None and self._search_service is not None:
                if (
                    self._search_service.manage_searches("list")["active_operation"]
                    is not None
                ):
                    return {"error": "search_busy"}
            if language is not None and language not in LANGUAGES:
                return {"error": "bad_language"}
            if sort is not None and sort not in {"source", "era", "era-desc"}:
                return {"error": "bad_sort"}
            if sources is not None and any(
                code not in LOCAL_SOURCES and not self.registry.enabled(code)
                for code in sources
            ):
                return {"error": "source_disabled"}
            rebuilt: dict[str, Library] = {}
            try:
                if normalization is not None:
                    settings = replace(
                        self.settings, normalization=options_from_mapping(normalization)
                    )
                    for code, library in self.api._local_libraries.items():
                        rebuilt[code] = Library(
                            [
                                text_from(text.local, settings, text.era)
                                for text in library.texts
                            ],
                            settings,
                            library.skipped,
                        )
            except (NormalizationError, InputError):
                return {"error": "bad_normalization"}
            prior_sources = self.api._selected_sources.copy()
            try:
                with self.preferences.transaction():
                    if sources is not None or source_options is not None:
                        answer = self.api.set_sources(
                            sources if sources is not None else prior_sources,
                            source_options,
                        )
                        if "error" in answer:
                            raise PolicyError(str(answer["error"]))
                    values: dict[str, Any] = {}
                    if sort is not None:
                        values["result_sort"] = sort
                    if language is not None:
                        values["language"] = language
                    if normalization is not None:
                        values["normalization"] = normalization
                    self.preferences.update(values)
            except PolicyError as error:
                self.api._selected_sources = prior_sources
                return {"error": error.code}
            except OSError:
                self.api._selected_sources = prior_sources
                return {"error": "preferences_write_failed"}
            if normalization is not None:
                with self.api._local_lock:
                    self.api._local_libraries.update(rebuilt)
                    active = self.api._active_local
                    self.api._library = rebuilt.get(active, Library([], settings))
                    self.registry.settings = settings
                self._changed()
            return self.describe()

    def _destination(self) -> Path:
        folder = self.api._folder
        if not folder:
            raise PolicyError("release_no_folder")
        return self.policy.check(folder, write=True)

    def _protected_inputs(self) -> list[Path]:
        with self.api._local_lock:
            paths = [
                text.local.path
                for library in self.api._local_libraries.values()
                for text in library.texts
            ]
        if self.preferences._state_path is not None:
            paths.append(self.preferences._state_path)
        registry = getattr(self, "registry", None)
        itkc_log = getattr(registry, "_itkc_log", None)
        if itkc_log is not None:
            paths.append(Path(itkc_log))
        return paths

    def _changed(self) -> None:
        self._epoch += 1
        if self._search_service is not None:
            self._search_service.cancel_active()
        if self._itkc is not None:
            self._itkc.cancel()

    def _schedule_clear_worker(self) -> None:
        """Clear whichever ITKC session has an explicit pending clear."""
        with self._clear_worker_lock:
            worker = self._clear_worker
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=self._clear_when_free,
                name="cpe-mcp-itkc-clear",
                daemon=True,
            )
            self._clear_worker = worker
            worker.start()

    def _clear_when_free(self) -> None:
        try:
            while not self._closed.is_set():
                if not self._lock.acquire(timeout=0.1):
                    continue
                try:
                    with self._clear_worker_lock:
                        pending = self._clear_itkc_requested
                        session = self._itkc
                        if session is not None and session.id == pending:
                            session.cancel()
                            self._itkc = None
                            self._itkc_bytes = 0
                            self._itkc_generation += 1
                            self._clear_itkc_requested = ""
                            cleared = True
                        else:
                            cleared = False
                    if cleared:
                        return
                    if pending and (session is None or session.id != pending):
                        # A superseded clear must not clear a replacement
                        # session without a new explicit pending request.
                        return
                    return
                finally:
                    self._lock.release()
        finally:
            with self._clear_worker_lock:
                if self._clear_worker is threading.current_thread():
                    self._clear_worker = None
                pending = self._clear_itkc_requested
                session = self._itkc
            if (
                not self._closed.is_set()
                and pending
                and session is not None
                and session.id == pending
            ):
                self._schedule_clear_worker()

    def import_local(self, path: str, *, batch: bool = False) -> dict[str, Any]:
        with self._lock:
            try:
                target = self.policy.check(path)
                self._destination()
            except PolicyError as error:
                return {"error": error.code}
            answer = (
                self.api.import_release_directory(str(target))
                if batch
                else self.api.import_release_file(str(target))
            )
            if "error" not in answer:
                self._changed()
            return answer

    def import_remote(self, provider: str, identifier: str) -> dict[str, Any]:
        if provider not in {"portal", "ctp"}:
            return {"error": "bad_request"}
        if provider not in self._enabled_imports:
            return {"error": "import_disabled"}
        with self._lock:
            try:
                target = self._destination()
            except PolicyError as error:
                return {"error": error.code}
            answer = (
                self.api.import_dataset(identifier, str(target))
                if provider == "portal"
                else self.api.import_ctp(identifier)
            )
            if "error" not in answer:
                self._changed()
            return answer

    def preview_import(
        self, provider: str, address: str, revision: str = ""
    ) -> dict[str, Any]:
        if provider not in {"gutenberg", "ndl", "rise", "cbeta", "nikh-partial"}:
            return {"error": "bad_request"}
        if provider not in self._enabled_imports:
            return {"error": "import_disabled"}
        if provider in {"cbeta", "nikh-partial"}:
            try:
                address = str(self.policy.check_library(address))
            except PolicyError as error:
                return {"error": error.code}
        return self.api.start_external_preview(provider, address, revision)

    def import_action(
        self,
        action: str,
        *,
        token: str = "",
        unit_id: str = "",
        job_id: str = "",
        selected: list[str] | None = None,
        edition: str = "",
        researcher_confirmed: bool = False,
    ) -> dict[str, Any]:
        if action == "progress":
            return self.api.external_progress(job_id)
        if action == "cancel":
            return {"cancelled": self.api.cancel_external(job_id)}
        if action == "unit":
            return self.api.external_unit(token, unit_id)
        if action == "discard":
            return {"discarded": self.api.discard_external(token)}
        with self._lock:
            try:
                if action in {"commit", "report"}:
                    self._destination()
                elif action == "history" and self.api._folder:
                    self.policy.check(self.api._folder)
            except PolicyError as error:
                return {"error": error.code}
            if action == "history":
                return self.api.external_history()
            if action == "report":
                return self.api.save_external_report(token)
            if action == "commit":
                answer = self.api.import_external(
                    token, selected or [], edition, researcher_confirmed is True
                )
                if "error" not in answer:
                    self._changed()
                return answer
            return {"error": "bad_request"}

    def import_catalog(
        self, provider: str, page: int = 1, resource_id: str = ""
    ) -> dict[str, Any]:
        if provider == "portal":
            return {"datasets": self.api.datasets()}
        if provider == "rise":
            if provider not in self._enabled_imports:
                return {"error": "import_disabled"}
            return (
                self.api.rise_sections(resource_id)
                if resource_id
                else self.api.rise_books(page)
            )
        return {"error": "bad_request"}

    def run_export(
        self,
        mode: str,
        inputs: list[str],
        output_prefix: str,
        *,
        first: str = "",
        second: str = "",
        query: str = "",
        pattern_file: str = "",
        expression: str = "",
        a_min: int = 1,
        a_max: int = 20,
        b_min: int = 1,
        b_max: int = 20,
        scope: str = "sentence",
        run_id: str = "mcp",
        force: bool = False,
    ) -> dict[str, Any]:
        from cpe.export.safe import check_input_collisions
        from cpe.itkc.query import QueryError
        from cpe.local.search import search_files
        from cpe.match.dsl import DslError
        from cpe.match.pair import pair_spec
        from cpe.match.pattern_file import PatternFileError, from_mapping
        from cpe.match.run import match_files, match_specs, outputs_for

        with self._lock:
            try:
                paths = [self.policy.check_library(path) for path in inputs]
                if not paths:
                    return {"error": "inputs_required"}
                prefix = self.policy.check(output_prefix, write=True)
                outputs: Outputs | SearchOutputs
                outputs = outputs_for(prefix)
                for path in (outputs.csv, outputs.jsonl, outputs.run):
                    self.policy.check(path, write=True)
                check_input_collisions(
                    (outputs.csv, outputs.jsonl, outputs.run), self._protected_inputs()
                )
                if scope not in {"sentence", "clause"}:
                    return {"error": "bad_request"}
                if mode == "words":
                    records, outputs = search_files(
                        query, paths, prefix, run_id, self.settings, scope, force=force
                    )
                    count = len(records)
                elif mode == "dsl" and pattern_file:
                    source = self.policy.check(pattern_file)
                    results, outputs = match_files(
                        source, paths, prefix, run_id, self.settings, force=force
                    )
                    count = len(results)
                else:
                    if mode == "pair":
                        spec = pair_spec(
                            first, second, (a_min, a_max), (b_min, b_max), scope
                        )
                    elif mode == "dsl" and expression:
                        spec = from_mapping(
                            {"id": "mcp-pattern", "expr": expression}, "MCP expression"
                        )
                    else:
                        return {"error": "bad_request"}
                    results, outputs = match_specs(
                        [spec],
                        Path("mcp-pattern"),
                        spec.pattern.canonical().encode("utf-8"),
                        paths,
                        prefix,
                        run_id,
                        self.settings,
                        force=force,
                    )
                    count = len(results)
            except PolicyError as error:
                return {"error": error.code}
            except (
                InputError,
                NormalizationError,
                QueryError,
                DslError,
                PatternFileError,
            ):
                return {"error": "bad_query_or_input"}
            except OSError:
                return {"error": "export_failed"}
            files = [
                {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in (outputs.csv, outputs.jsonl, outputs.run)
            ]
            return {"count": count, "files": files, "mode": mode}

    def export_snapshot(
        self, service: Any, search_id: str, output_prefix: str, *, force: bool = False
    ) -> dict[str, Any]:
        from cpe.export.csv_out import excel_safe
        from cpe.export.safe import check_input_collisions, staged
        from cpe.match.run import outputs_for

        snapshot: dict[str, Any] = service.snapshot(search_id)
        if "error" in snapshot:
            return snapshot
        try:
            outputs = outputs_for(self.policy.check(output_prefix, write=True))
            paths = (outputs.csv, outputs.jsonl, outputs.run)
            for path in paths:
                self.policy.check(path, write=True)
            protected = self._protected_inputs()
            pattern_file = snapshot["query"].get("pattern_file")
            if pattern_file:
                protected.append(Path(pattern_file))
            check_input_collisions(paths, protected)
            columns = [
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
            ]
            with staged(paths, force=force) as temporary:
                with temporary[0].open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(
                        stream,
                        fieldnames=columns,
                        quoting=csv.QUOTE_ALL,
                        lineterminator="\n",
                    )
                    writer.writeheader()
                    for row in snapshot["rows"]:
                        values = {key: row.get(key, "") for key in columns}
                        writer.writerow(
                            {
                                key: excel_safe(
                                    value
                                    if isinstance(value, str)
                                    else json.dumps(
                                        value, ensure_ascii=False, separators=(",", ":")
                                    )
                                )
                                for key, value in values.items()
                            }
                        )
                with temporary[1].open("w", encoding="utf-8", newline="") as stream:
                    for row in snapshot["rows"]:
                        stream.write(
                            json.dumps(
                                {
                                    "schema": "cpe.mcp.snapshot-row/1",
                                    "source": snapshot["source"],
                                    "result": row,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                metadata = {
                    key: value for key, value in snapshot.items() if key != "rows"
                }
                metadata.update(
                    schema="cpe.mcp.snapshot/1", row_count=len(snapshot["rows"])
                )
                with temporary[2].open("w", encoding="utf-8", newline="") as stream:
                    json.dump(metadata, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
        except PolicyError as error:
            return {"error": error.code}
        except OSError:
            return {"error": "export_failed"}
        return {
            "files": [str(path) for path in paths],
            "rows": len(snapshot["rows"]),
            "schema": "cpe.mcp.snapshot/1",
        }

    def search_dsl(
        self,
        *,
        service: Any,
        source_id: str = "local",
        expression: str = "",
        pattern_file: str = "",
        sort: str = "source",
        page_size: int = 20,
        checkpoint: Any = None,
    ) -> dict[str, Any]:
        from cpe.gui.search_session import sorted_rows
        from cpe.mcp.patterns import search

        if source_id not in LOCAL_SOURCES or bool(expression) == bool(pattern_file):
            return {"error": "bad_request"}
        if sort not in {"source", "era", "era-desc"}:
            return {"error": "bad_sort"}
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            return {"error": "bad_page_size"}
        check = checkpoint or (lambda: None)
        operation = service.begin_operation(check, source_id, "dsl")
        if operation is None:
            return {"error": "search_busy"}
        check = operation.check
        try:
            with self._lock:
                check()
                library = self.library(source_id)
                if library is None:
                    return {
                        "error": self.library_status(source_id).get("load_error")
                        or "local_unconfigured"
                    }
                content = (
                    self.policy.check(pattern_file).read_bytes()
                    if pattern_file
                    else None
                )
                answer = search(
                    library,
                    expression=expression,
                    pattern_content=content,
                    checkpoint=check,
                )
                if "error" in answer:
                    return answer
                for group in ("likely", "less"):
                    answer[group] = sorted_rows(answer[group], sort)
                query = {
                    "source_id": source_id,
                    "normalization": self.normalization(),
                    "expression": expression,
                    "pattern_file": pattern_file,
                    "pattern_sha256": hashlib.sha256(content).hexdigest()
                    if content
                    else None,
                    "sort": sort,
                }
                check()
                result: dict[str, Any] = service._publish(
                    source_id, "dsl", query, page_size, answer, check
                )
                return result
        except PolicyError as error:
            return {"error": error.code}
        except InterruptedError:
            return {"error": "search_cancelled"}
        except OSError:
            return {"error": "pattern_read_failed"}
        finally:
            service.finish_operation(operation)

    def itkc_search(
        self,
        action: str,
        *,
        request: dict[str, Any] | None = None,
        job_id: str = "",
        revision: int = 0,
        group: str = "likely",
        start: int = 0,
        sort: str = "source",
        page_size: int = 20,
        checkpoint: Any = None,
        service: Any = None,
    ) -> dict[str, Any]:
        from cpe.gui import online
        from cpe.mcp.service import _json_size

        if not self.registry.enabled("itkc"):
            return {"error": "source_disabled"}
        if action == "clear":
            session = self._itkc
            if session is None or session.id != job_id:
                return {"error": "bad_request"}
            session.cancel()
            with self._clear_worker_lock:
                self._clear_itkc_requested = session.id
            if not self._lock.acquire(blocking=False):
                self._schedule_clear_worker()
                return {"id": job_id, "state": "cancelling", "clear_pending": True}
            try:
                if self._itkc is session:
                    self._itkc = None
                    self._itkc_bytes = 0
                    self._itkc_generation += 1
                self._clear_itkc_requested = ""
                return {"id": job_id, "state": "cleared", "cleared": True}
            finally:
                self._lock.release()
        if action in {"cancel", "status", "page"}:
            session = self._itkc
            if session is None or session.id != job_id:
                return {"error": "bad_request"}
            if action == "cancel":
                session.cancel()
            if action in {"cancel", "status"}:
                return session.metadata()
            if (
                isinstance(page_size, bool)
                or not isinstance(page_size, int)
                or not 1 <= page_size <= 100
            ):
                return {"error": "bad_page_size"}
            answer = session.page(revision, group, start, sort)
            if "rows" in answer:
                rows = staged_rows(answer["rows"][:page_size])
                answer["rows"] = rows
                answer["next_start"] = (
                    start + len(rows)
                    if start + len(rows) < answer["counts"][group]
                    else None
                )
                if wire_size(answer) > 256 * 1024:
                    return {
                        "error": "response_limit",
                        "id": job_id,
                        "revision": revision,
                        "try_smaller_page": True,
                    }
            return answer
        if action not in {"start", "advance"}:
            return {"error": "bad_request"}
        if not self._lock.acquire(blocking=False):
            return {"error": "search_busy"}
        try:
            if checkpoint is not None:
                checkpoint()
            if action == "start":
                query = copy.deepcopy(request or {})
                _, error = online.itkc_plan(query)
                if error is not None:
                    return {"error": error}
                settings_data = asdict(self.settings)
                for key in ("sentence_delimiters", "clause_delimiters"):
                    settings_data[key] = sorted(settings_data[key])
                limit = (
                    service._max_retained if service is not None else 64 * 1024 * 1024
                )
                if (
                    _json_size({"request": query, "settings": settings_data}) + 512
                    > limit
                ):
                    return {"error": "storage_limit"}
                try:
                    client = self.registry.client("itkc")
                except ValueError as error:
                    return {"error": str(error)}
                if self._itkc is not None:
                    self._itkc.cancel()
                self._itkc_generation += 1
                generation, epoch = self._itkc_generation, self._epoch

                def analyze(
                    request: dict[str, Any],
                    client: Any,
                    settings: Settings,
                    *,
                    checkpoint: Any = None,
                    collections: Sequence[str] = (),
                ) -> dict[str, Any]:
                    def check() -> None:
                        if checkpoint is not None:
                            checkpoint()
                        if outer_checkpoint is not None:
                            outer_checkpoint()

                    return online.search_itkc(
                        request,
                        client,
                        settings,
                        checkpoint=check,
                        include_records=True,
                        collections=collections,
                    )

                outer_checkpoint = checkpoint
                self._itkc = ItkcSearchSession(
                    "itkc_" + secrets.token_urlsafe(18),
                    query,
                    self.settings,
                    client,
                    generation,
                    lambda value: (
                        value == self._itkc_generation and epoch == self._epoch
                    ),
                    analyzer=analyze,
                )
                answer = self._itkc.start()
            else:
                if self._itkc is None or self._itkc.id != job_id:
                    return {"error": "bad_request"}
                # The analyzer uses this action's cancellation checkpoint,
                # rather than the completed start request's event.
                session = self._itkc

                def analyze_next(
                    request: dict[str, Any],
                    client: Any,
                    settings: Settings,
                    *,
                    checkpoint: Any = None,
                    collections: Sequence[str] = (),
                ) -> dict[str, Any]:
                    def check() -> None:
                        if checkpoint is not None:
                            checkpoint()
                        if outer_checkpoint is not None:
                            outer_checkpoint()

                    return online.search_itkc(
                        request,
                        client,
                        settings,
                        checkpoint=check,
                        include_records=True,
                        collections=collections,
                    )

                outer_checkpoint = checkpoint
                session._analyzer = analyze_next
                answer = session.advance(session.generation)
            assert self._itkc is not None
            if self._clear_itkc_requested == self._itkc.id:
                id_ = self._itkc.id
                self._itkc = None
                self._itkc_bytes = 0
                self._itkc_generation += 1
                self._clear_itkc_requested = ""
                return {"id": id_, "state": "cleared", "cleared": True}
            with self._itkc._snapshot_lock:
                settings_data = asdict(self._itkc._settings)
                for key in ("sentence_delimiters", "clause_delimiters"):
                    settings_data[key] = sorted(settings_data[key])
                retained = (
                    _json_size(
                        {
                            "request": self._itkc._request,
                            "settings": settings_data,
                            "id": self._itkc.id,
                            "generation": self._itkc.generation,
                            "error": self._itkc._error,
                        }
                    )
                    + 512
                    + sum(
                        _json_size(asdict(snap))
                        for snap in self._itkc._snapshots.values()
                    )
                )
            limit = service._max_retained if service is not None else 64 * 1024 * 1024
            if retained > limit:
                self._itkc.cancel()
                self._itkc = None
                self._itkc_bytes = 0
                return {"error": "storage_limit"}
            self._itkc_bytes = retained
            if service is not None:
                with service._lock:
                    while (
                        service._sessions
                        and service._retained_locked() + retained > limit
                    ):
                        oldest = min(
                            service._sessions,
                            key=lambda key: service._sessions[key].last_access,
                        )
                        del service._sessions[oldest]
                        service._retire_locked(oldest)
            return answer
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closed.set()
        worker = self._clear_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1)
        if self._itkc is not None:
            self._itkc.cancel()
        with self.api._external_job_lock:
            job = self.api._external_job
            if job and job["result"] is None:
                job["control"].cancel.set()
        if job and job.get("thread"):
            job["thread"].join(timeout=1)
