"""Desktop search window: the page in cpe/gui/static inside a native web view.

The page talks to Python through the in-process bridge (window.pywebview.api),
not over a local HTTP server: on the researcher's Windows machine a Python process
sending more than about 16 KB over localhost TCP lost data or stalled, while the bridge
moved 4 MB intact (docs/decisions.md "The GUI is built before gate 1-A is complete").
`webview` is imported only when a window opens, so the API is testable without it.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import uuid
from base64 import b64encode
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from cpe.docusky.adapter import valid_database_name
from cpe.gui.cand import search_cand
from cpe.gui.csid import search_csid
from cpe.gui.external import search_ships
from cpe.gui.kyudb import search_kyudb
from cpe.gui.online import (
    docusky_catalog as docusky_database_list,
)
from cpe.gui.online import (
    itkc_plan,
    search_aksarchive,
    search_cbeta,
    search_cyberseodang,
    search_docusky,
    search_historydb,
    search_ith,
    search_itkc,
    search_nahf,
    search_ndl,
    search_nrich,
    search_sat,
    search_sillok,
    search_vietdien,
)
from cpe.gui.preferences import Preferences, config_dir
from cpe.gui.search_session import ItkcSearchSession
from cpe.gui.service import Library, search_pair, search_words
from cpe.gui.thdl import search_thdl
from cpe.local.txt import InputError
from cpe.match.engine import Settings
from cpe.normalize.options import NormalizationError

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
I18N = HERE / "i18n"
# English is the source catalog (AGENTS.md "Language"); this order is the menu order.
LANGUAGES = ("ko", "ja", "zh-Hans", "zh-Hant", "vi", "en")
SORT_ORDERS = ("source", "era", "era-desc")
LOCAL_SOURCES = ("local", "local2", "local3", "local4")
ONLINE_SOURCES = (
    "itkc",
    "ndl",
    "cbeta",
    "cyberseodang",
    "docusky",
    "historydb",
    "sillok",
    "nrich",
    "ith",
    "nahf",
    "vietdien",
    "sat",
    "aksarchive",
    "ships",
    "thdl",
    "csid",
    "cand",
    "kyudb",
)


def online_source(code: Any) -> bool:
    """A built-in online source, or a DocuSky catalogue database `docusky:<name>`."""
    if not isinstance(code, str):
        return False
    if code in ONLINE_SOURCES:
        return True
    if not code.startswith("docusky:"):
        return False
    return valid_database_name(code[len("docusky:") :])


def _same_folder(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


@lru_cache(maxsize=len(LANGUAGES))
def _catalog(code: str) -> dict[str, str]:
    data: dict[str, str] = json.loads(
        (I18N / f"{code}.json").read_text(encoding="utf-8")
    )
    return data


@lru_cache(maxsize=1)
def _home_icon(path: Path) -> str:
    icon = b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{icon}"


def _load_folder(path: str, settings: Settings) -> Library:
    """One folder as a library; a root that cannot be listed is a failed read.

    Library.load records an unlistable subfolder in `skipped` and keeps going,
    which suits a folder with one unreadable part. When the root itself cannot
    be listed (`skipped` holds "."), nothing was read, and opening, loading and
    rescanning must all report load_failed rather than an empty library.
    """
    library = Library.load([Path(path)], settings)
    if "." in library.skipped:
        raise OSError(f"{path}: folder cannot be listed")
    return library


class Api:
    """Methods the page calls; each returns plain JSON-compatible values."""

    def __init__(
        self,
        library: Library,
        preferences: Preferences | None = None,
        folder: str = "",
    ) -> None:
        self._library = library
        self._preferences = preferences or Preferences()
        self._folder = folder
        self._local_lock = threading.RLock()
        self._client_lock = threading.RLock()
        self._itkc_session_lock = threading.RLock()
        self._itkc_start_lock = threading.Lock()
        self._itkc_session: ItkcSearchSession | None = None
        self._search_generation = 0
        self._ctp_operation_lock = threading.Lock()
        self._local_busy = 0
        self._active_local = "local"
        self._local_libraries = {"local": library}
        self._local_paths = {"local": folder}
        self._local_errors: dict[str, str] = {}
        self._local_revisions: dict[str, int] = {}
        self._restore_local_libraries(library, folder)
        saved_sources = self._preferences.load().get("sources")
        self._selected_sources = (
            list(dict.fromkeys(saved_sources))
            if isinstance(saved_sources, list)
            and 0 <= len(saved_sources) <= 4
            and all(
                isinstance(code, str)
                and (self._local_configured(code) or online_source(code))
                for code in saved_sources
            )
            else [self._active_local]
            if self._active_local
            else []
        )
        self._window: Any = None
        # Online clients live for the session, so their call caps count every search.
        self._itkc: Any = None
        self._ndl: Any = None
        self._cbeta: Any = None
        self._cyberseodang: Any = None
        self._docusky: Any = None
        self._nrich: Any = None
        self._sillok: Any = None
        self._historydb: Any = None
        self._ith: Any = None
        self._nahf: Any = None
        self._vietdien: Any = None
        self._sat: Any = None
        self._thdl: Any = None
        self._csid: Any = None
        self._cand: Any = None
        self._kyudb: Any = None
        self._aksarchive: Any = None
        # Tests replace CTP's transport here; None means the real network.
        self._ctp_transport: Any = None
        self._ctp: Any = None
        # Tests replace the portal's transport with recorded answers.
        self._portal: Any = None
        self._portal_transport: Any = None
        self._external: Any = None
        self._external_pending: Any = None
        self._external_token = ""
        self._external_lock = threading.Lock()
        self._external_job_lock = threading.Lock()
        self._external_job: dict[str, Any] | None = None
        self._rise: Any = None
        self._rise_lock = threading.Lock()

    def _rise_client(self) -> Any:
        from cpe.local.rise import RiseClient

        with self._rise_lock:
            if self._rise is None:
                self._rise = RiseClient()
            return self._rise

    def rise_books(self, page: Any = 1) -> dict[str, Any]:
        """One page of the verified MPIWG catalog for the book picker."""
        from cpe.local.external import ExternalError
        from cpe.local.rise import books

        try:
            return books(self._rise_client(), page)
        except ExternalError as error:
            return {"error": error.code}

    def rise_sections(self, resource_uuid: Any) -> dict[str, Any]:
        """The selected book's sections; no caller-supplied section ids."""
        from cpe.local.external import ExternalError
        from cpe.local.rise import sections

        try:
            return sections(self._rise_client(), resource_uuid)
        except ExternalError as error:
            return {"error": error.code}

    def preview_external(
        self, provider: str, address: str, revision: str = ""
    ) -> dict[str, Any]:
        return self._preview_external(provider, address, revision=revision)

    def _preview_external(
        self, provider: str, address: str, control: Any = None, revision: str = ""
    ) -> dict[str, Any]:
        """Fetch one supplied book; keep preview bytes only until import/replacement."""
        from cpe.local.external import NDL, ExternalClient, ExternalError, fetch
        from cpe.local.external import transport as external_transport
        from cpe.ndl.adapter import NdlCapReached, NdlError
        from cpe.ndl.adapter import urllib_transport as ndl_transport

        def download_transport(url: str, timeout: float) -> bytes:
            if url.startswith(NDL):
                try:
                    data: bytes = self.ndl_client().request(url, {})
                    return data
                except NdlCapReached:
                    raise ExternalError("external_cap") from None
                except NdlError:
                    raise ExternalError("external_network") from None
            return external_transport(url, timeout)

        def download_stream(url: str, timeout: float, task: Any) -> bytes:
            if not url.startswith(NDL):
                return external_transport(url, timeout, task)
            source = self.ndl_client()
            try:
                data: bytes = source.request(
                    url,
                    {},
                    (lambda u, p, t: external_transport(u, t, task))
                    if source.transport is ndl_transport
                    else None,
                    checkpoint=task.checkpoint,
                    wait=lambda seconds: task.wait(seconds, source.sleep),
                )
                return data
            except NdlCapReached:
                raise ExternalError("external_cap") from None
            except NdlError:
                raise ExternalError("external_network") from None

        with self._external_lock:
            self._external_pending = None
            self._external_token = ""
            if self._external is None:
                self._external = ExternalClient(
                    transport=download_transport, streaming=download_stream
                )
            try:
                if provider == "rise":
                    from cpe.local.rise import preview as rise_preview

                    download = rise_preview(self._rise_client(), address, control)
                elif provider in {"cbeta", "nikh-partial"}:
                    from cpe.local.offline import fetch_file

                    download = fetch_file(provider, address, revision, control)
                else:
                    download = fetch(
                        str(provider), str(address), self._external, control
                    )
                if control:
                    control.checkpoint()
            except ExternalError as error:
                return {"error": error.code}
            self._external_pending = download
            self._external_token = uuid.uuid4().hex
            edition = download.edition
            if self._folder:
                from cpe.local.external_store import history

                saved_book = Path(self._folder) / f"{download.provider}-{download.id}"
                if saved_book.is_dir():
                    books = history(saved_book)
                    if books and not books[0].get("error"):
                        edition = books[0]["edition"]
            return {
                "token": self._external_token,
                "title": download.title,
                "edition": edition,
                "details": download.details,
                "units": [
                    {
                        "id": unit.id,
                        "characters": len(unit.text),
                        "page": int(unit.id.rsplit("_", 1)[1])
                        if download.provider == "ndl"
                        else None,
                    }
                    for unit in download.units
                ],
            }

    def external_unit(self, token: str, unit_id: str) -> dict[str, Any]:
        """Return only a unit actually supplied by the pending download."""
        with self._external_lock:
            if (
                not token
                or token != self._external_token
                or self._external_pending is None
            ):
                return {"error": "external_stale"}
            for unit in self._external_pending.units:
                if unit.id == unit_id:
                    return {"text": unit.text, "source": unit.source}
            return {"error": "external_selection"}

    def import_external(
        self, token: str, selected: Any, edition: str, reviewed: bool
    ) -> dict[str, Any]:
        """Save explicitly reviewed original-text units and reload the open folder."""
        with self._local_operation():
            from cpe.local.external import ExternalError, save

            with self._external_lock:
                if (
                    not token
                    or token != self._external_token
                    or self._external_pending is None
                ):
                    return {"error": "external_stale"}
                if not isinstance(selected, list) or not all(
                    isinstance(item, str) for item in selected
                ):
                    return {"error": "external_selection"}
                if not self._folder or not Path(self._folder).is_dir():
                    return {"error": "release_no_folder"}
                if destination_error := self._destination_error(self._folder):
                    return destination_error
                try:
                    imported = save(
                        self._external_pending,
                        selected,
                        Path(self._folder),
                        str(edition),
                        reviewed is True,
                    )
                except ExternalError as error:
                    return {"error": error.code}
                except OSError:
                    return {"error": "release_write_failed"}
                self._external_pending = None
                self._external_token = ""
                return {
                    "imported": imported,
                    "library": self._reload_local_folder(self._folder),
                }

    def start_external_preview(
        self, provider: str, address: str, revision: str = ""
    ) -> dict[str, Any]:
        """Start one bounded background download; progress/cancel never wait on I/O."""
        from cpe.local.external import DownloadControl

        with self._external_job_lock:
            if self._external_job and self._external_job["result"] is None:
                return {"error": "external_busy"}
            job_id = uuid.uuid4().hex
            task = DownloadControl()
            job: dict[str, Any] = {"id": job_id, "control": task, "result": None}
            self._external_job = job

            def work() -> None:
                try:
                    answer = self._preview_external(provider, address, task, revision)
                except Exception:
                    # An unexpected parser failure must retire the job instead of
                    # leaving the window polling a permanently running task.
                    answer = {"error": "external_format"}
                # A cancellation accepted after the last download checkpoint must
                # still win before the result and its preview token are published.
                with self._external_lock:
                    with self._external_job_lock:
                        if task.cancel.is_set():
                            if answer.get("token") == self._external_token:
                                self._external_pending = None
                                self._external_token = ""
                            answer = {"error": "external_cancelled"}
                        job["result"] = answer

            thread = threading.Thread(target=work, daemon=True)
            job["thread"] = thread
            thread.start()
            return {"job": job_id}

    def save_external_report(self, token: str) -> dict[str, Any]:
        with self._local_operation():
            from cpe.local.external import ExternalError
            from cpe.local.external_store import save_report

            with self._external_lock:
                if (
                    not token
                    or token != self._external_token
                    or self._external_pending is None
                ):
                    return {"error": "external_stale"}
                if not self._folder:
                    return {"error": "release_no_folder"}
                try:
                    report = save_report(self._external_pending, Path(self._folder))
                    return {"report": str(report)}
                except ExternalError as error:
                    return {"error": error.code}
                except OSError:
                    return {"error": "release_write_failed"}

    def external_progress(self, job_id: str) -> dict[str, Any]:
        with self._external_job_lock:
            job = self._external_job
            if not job or job["id"] != job_id:
                return {"error": "external_stale"}
            return {
                **job["control"].snapshot(),
                "done": job["result"] is not None,
                "answer": job["result"],
            }

    def cancel_external(self, job_id: str) -> bool:
        with self._external_job_lock:
            job = self._external_job
            if not job or job["id"] != job_id or job["result"] is not None:
                return False
            job["control"].cancel.set()
            return True

    def external_history(self) -> dict[str, Any]:
        from cpe.local.external import ExternalError
        from cpe.local.external_store import history

        try:
            return (
                {"books": history(Path(self._folder))}
                if self._folder
                else {"books": []}
            )
        except ExternalError as error:
            return {"error": error.code}
        except OSError:
            return {"error": "external_history_conflict"}

    def pick_external_xml(self, file_label: str) -> dict[str, Any]:
        if self._window is None:
            return {"cancelled": True}
        import webview

        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=(f"{file_label} (*.xml)",)
        )
        return {"path": str(chosen[0])} if chosen else {"cancelled": True}

    def discard_external(self, token: str = "") -> bool:
        with self._external_lock:
            if token and token != self._external_token:
                return False
            self._external_pending = None
            self._external_token = ""
        return True

    def saved_language(self) -> str:
        """The language chosen last time, or "" so the page starts in the OS one."""
        code = self._preferences.get("language")
        return code if code in LANGUAGES else ""

    def save_language(self, code: str) -> bool:
        if code not in LANGUAGES:
            return False
        try:
            self._preferences.set("language", code)
        except OSError:
            return False
        return True

    def saved_sort(self) -> str:
        """Restore supported orders; old or corrupt values use source order."""
        order = self._preferences.get("result_sort")
        return order if order in SORT_ORDERS else "source"

    def save_sort(self, order: Any) -> bool:
        if not isinstance(order, str) or order not in SORT_ORDERS:
            return False
        try:
            self._preferences.set("result_sort", order)
        except OSError:
            return False
        return True

    def _restore_local_libraries(self, library: Library, folder: str) -> None:
        # Texts or a folder given on the command line are this session's input;
        # remembering the last session's folders must not replace them.
        if library.texts and not folder:
            return
        data = self._preferences.load()
        saved = data.get("local_folders")
        if not isinstance(saved, dict):
            if folder or library.texts:
                return
            saved = {}
        if not saved and not folder and not library.texts:
            # No remembered folder and no command-line texts: the window starts
            # with no configured slot instead of an empty "local" row.
            # Removing the last folder leaves an explicit empty state; do not
            # revive the constructor's empty "local" slot on the next launch.
            self._local_libraries = {}
            self._local_paths = {}
            self._local_errors = {}
            self._active_local = ""
            self._library = Library([], library.settings)
            self._folder = ""
            return
        paths = {
            code: path
            for code, path in saved.items()
            if code in LOCAL_SOURCES and isinstance(path, str) and path
        }
        active = data.get("active_local")
        active = active if isinstance(active, str) and active in paths else "local"
        if not folder and paths:
            self._local_libraries = {}
            self._local_paths = {}
            self._active_local = (
                active if active in paths else next(iter(paths), "local")
            )
        if folder and paths.get(active) == folder:
            self._active_local = active
            self._local_libraries = {active: library}
            self._local_paths = {active: folder}
        # Saved paths are checked for existence but not read: each slot loads when
        # it is searched, activated, or chosen for import. Only the active slot is
        # read before the window opens; the fallback path may read further slots
        # when the remembered active folder is unusable.
        for code, path in paths.items():
            # A folder given on the command line keeps its slot; the check is of
            # the path the slot holds, not of the stale saved one it replaced.
            held = self._local_paths.setdefault(code, path)
            try:
                is_directory = Path(held).is_dir()
            except OSError:
                is_directory = False
            if not is_directory:
                self._local_errors[code] = "load_failed"
        if self._active_local not in self._local_libraries:
            for code in (
                self._active_local,
                *(code for code in LOCAL_SOURCES if code != self._active_local),
            ):
                if code not in self._local_paths:
                    continue
                if self._load_local_slot(code, library.settings) is not None:
                    self._active_local = code
                    break
        self._library = self._local_libraries.get(self._active_local, library)
        self._folder = self._local_paths.get(self._active_local, "")
        if self._active_local in self._local_errors:
            self._folder = ""

    def _local_configured(self, code: str) -> bool:
        """A slot is configured when it is loaded or has a saved folder."""
        return code in self._local_libraries or bool(self._local_paths.get(code))

    def _load_local_slot(
        self, code: str, settings: Settings, *, retry: bool = False
    ) -> Library | None:
        """Read one saved slot without holding the lock across its disk work.

        A search or activation can start this while the settings screen asks
        `local_libraries()`; holding the lock for `Library.load` would make the
        screen look frozen. The install is conditional on the folder not having
        been replaced and on no import having started meanwhile.

        `retry` ignores the recorded error and the empty library that a failed
        read left behind while it reads, so an explicit retry can recover a
        folder that became readable again. Both are replaced only by a successful
        install; a failed or stale retry leaves the failed state as it was.
        """
        with self._local_lock:
            if code in self._local_errors and not retry:
                return None
            existing = self._local_libraries.get(code)
            if existing is not None and not (retry and code in self._local_errors):
                return existing
            path = self._local_paths.get(code, "")
            if not path or self._local_busy:
                return None
            # A retry may replace only the failed snapshot it started from. The
            # identity and revision catch a concurrent retry, a reload, or a
            # same-path replacement that installed a newer library while this
            # read was out of the lock.
            failed = existing
            revision = self._local_revisions.get(code, 0)
        try:
            library = _load_folder(path, settings)
        except (InputError, OSError, NormalizationError):
            # A variant table that touches this folder's delimiters fails the read
            # like a bad manifest (cpe.match.engine check_variants); escaping as an
            # exception would leave the slot with no recorded error to retry from.
            with self._local_lock:
                if self._local_paths.get(code) == path and not self._local_busy:
                    if code not in self._local_libraries:
                        self._local_libraries[code] = Library([], settings)
                        self._local_errors[code] = "load_failed"
            return None
        with self._local_lock:
            if self._local_busy or self._local_paths.get(code) != path:
                return None
            current = self._local_libraries.get(code)
            if retry:
                if (
                    current is not failed
                    or self._local_revisions.get(code, 0) != revision
                ):
                    return None
            elif code in self._local_errors:
                return None
            elif current is not None:
                return current
            self._local_libraries[code] = library
            self._local_errors.pop(code, None)
            if code == self._active_local:
                self._library = library
            return library

    def _destination_error(self, folder: str) -> dict[str, Any] | None:
        with self._local_lock:
            if any(
                code in self._local_errors and _same_folder(Path(path), Path(folder))
                for code, path in self._local_paths.items()
                if path
            ):
                return {"error": "load_failed", "path": folder}
        return None

    def _remember_local(
        self, paths: dict[str, str], active: str, sources: list[str] | None = None
    ) -> bool:
        values: dict[str, Any] = {
            "local_folders": paths,
            "active_local": active,
            "folder": paths[active],
        }
        if sources is not None:
            values["sources"] = sources
        try:
            self._preferences.update(values)
        except OSError:
            return False
        return True

    def local_libraries(self) -> dict[str, Any]:
        """Four independent source slots; only the active one is an import target."""
        with self._local_lock:
            libraries = []
            for code in LOCAL_SOURCES:
                path = self._local_paths.get(code, "")
                loaded = (
                    code in self._local_libraries and code not in self._local_errors
                )
                summary = (
                    self._local_libraries[code].summary()
                    if loaded
                    else {"text_count": 0, "skipped": [], "skipped_count": 0}
                )
                libraries.append(
                    {
                        "id": code,
                        "folder": path,
                        "name": Path(path).name,
                        "configured": loaded or bool(path),
                        "loaded": loaded,
                        "load_error": self._local_errors.get(code, ""),
                        **summary,
                    }
                )
            return {
                "active": self._active_local,
                "busy": bool(self._local_busy),
                "selected": self._selected_sources.copy(),
                "libraries": libraries,
            }

    def activate_local(self, source: str) -> dict[str, Any]:
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
            if source not in LOCAL_SOURCES or not self._local_configured(source):
                return {"error": "bad_request"}
            if source in self._local_errors:
                return {"error": "load_failed", "path": self._local_paths[source]}
            path = self._local_paths.get(source, "")
            library = self._local_libraries.get(source)
            settings = self._library.settings
        if library is None:
            library = self._load_local_slot(source, settings)
            if library is None:
                with self._local_lock:
                    if source in self._local_errors:
                        return {
                            "error": "load_failed",
                            "path": self._local_paths.get(source, ""),
                        }
                    if self._local_busy:
                        return {"error": "local_busy"}
                    return {"error": "bad_request"}
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
            if (
                self._local_paths.get(source) != path
                or source not in self._local_libraries
            ):
                return {"error": "bad_request"}
            library = self._local_libraries[source]
            if not self._remember_local(self._local_paths, source):
                return {"error": "preferences_write_failed"}
            self._active_local = source
            self._library = library
            self._folder = path
            return self.library()

    def load_local(self, source: str) -> dict[str, Any]:
        """Read one saved slot without making it the active import target.

        This is also the explicit retry: a slot whose earlier read failed is read
        again, so the page's Retry button can recover from a folder that became
        readable. The error stays recorded until a read succeeds, and the active
        target, the selection and the saved preferences are left alone.
        """
        with self._local_lock:
            if source not in LOCAL_SOURCES or not self._local_configured(source):
                return {"error": "bad_request"}
            if self._local_busy:
                return {"error": "local_busy"}
            retry = source in self._local_errors
            settings = self._library.settings
        library = self._load_local_slot(source, settings, retry=retry)
        if library is None:
            with self._local_lock:
                if source in self._local_errors:
                    return {
                        "error": "load_failed",
                        "path": self._local_paths.get(source, ""),
                    }
                if self._local_busy:
                    return {"error": "local_busy"}
                if source in self._local_libraries:
                    # Another caller loaded or replaced the slot while the retry
                    # read; report that state instead of a stale failure.
                    return self.local_libraries()
            return {"error": "bad_request"}
        return self.local_libraries()

    def reload_local(self, source: str) -> dict[str, Any]:
        """Rescan a saved folder, retaining the current snapshot on failure.

        This replaces only one library. Selection, import target and preferences
        stay unchanged, and disk work never holds the status snapshot lock.
        """
        with self._local_lock:
            if source not in LOCAL_SOURCES or not self._local_paths.get(source):
                return {"error": "bad_request"}
            if self._local_busy:
                return {"error": "local_busy"}
            path = self._local_paths[source]
            previous = self._local_libraries.get(source)
            revision = self._local_revisions.get(source, 0)
            settings = self._library.settings
        try:
            if not Path(path).is_dir():
                return {"error": "load_failed", "path": path}
            library = _load_folder(path, settings)
        except (InputError, OSError, NormalizationError):
            return {"error": "load_failed", "path": path}
        with self._local_lock:
            # A slow rescan must not overwrite a newer open, retry or import,
            # including removal followed by re-adding the same folder path.
            if (
                self._local_busy
                or self._local_paths.get(source) != path
                or self._local_libraries.get(source) is not previous
                or self._local_revisions.get(source, 0) != revision
                or self._library.settings is not settings
            ):
                return {"error": "local_busy"}
            self._local_libraries[source] = library
            self._local_errors.pop(source, None)
            self._local_revisions[source] = revision + 1
            if source == self._active_local:
                self._library = library
            return self.local_libraries()

    def remove_local(self, source: str) -> dict[str, Any]:
        """Remove one saved slot from search and import without touching its files."""
        with self._local_lock:
            if source not in LOCAL_SOURCES or not self._local_configured(source):
                return {"error": "bad_request"}
            if self._local_busy:
                return {"error": "local_busy"}
            paths = {
                code: path for code, path in self._local_paths.items() if code != source
            }
            selected = [code for code in self._selected_sources if code != source]
            active = self._active_local
            if active == source:
                active = next(
                    (
                        code
                        for code in LOCAL_SOURCES
                        if code in paths and code not in self._local_errors
                    ),
                    next((code for code in LOCAL_SOURCES if code in paths), ""),
                )
            settings = self._library.settings
            try:
                self._preferences.update(
                    {
                        "local_folders": paths,
                        "active_local": active,
                        "folder": paths.get(active, ""),
                        "sources": selected,
                    }
                )
            except OSError:
                return {"error": "preferences_write_failed"}
            self._local_paths = paths
            self._local_libraries.pop(source, None)
            self._local_errors.pop(source, None)
            # Keep a monotonic per-slot epoch across removal so a read that
            # started before this removal cannot install after a later re-add
            # of the same path (the ABA case).
            self._local_revisions[source] = self._local_revisions.get(source, 0) + 1
            self._selected_sources = selected
            self._active_local = active
            self._folder = paths.get(active, "")
            self._library = self._local_libraries.get(active) or Library([], settings)
            return self.local_libraries()

    def set_sources(self, sources: Any, options: Any = None) -> dict[str, Any]:
        with self._local_lock:
            if (
                not isinstance(sources, list)
                or not 0 <= len(sources) <= 4
                or not all(isinstance(code, str) for code in sources)
                or len(set(sources)) != len(sources)
                or any(
                    not self._local_configured(code) and not online_source(code)
                    for code in sources
                )
                or sum(
                    1
                    for code in sources
                    if code == "docusky" or code.startswith("docusky:")
                )
                > 1
            ):
                return {"error": "bad_request"}
            values: dict[str, Any] = {"sources": sources}
            if options is not None:
                if not isinstance(options, dict):
                    return {"error": "bad_request"}
                clean: dict[str, str] = {}
                for key in ("historydb_site", "historydb_item", "nahf_item"):
                    value = options.get(key, "")
                    if not isinstance(value, str) or len(value) > 64:
                        return {"error": "bad_request"}
                    clean[key] = value
                values["source_options"] = clean
            try:
                self._preferences.update(values)
            except OSError:
                return {"error": "preferences_write_failed"}
            self._selected_sources = sources.copy()
            return {"sources": sources.copy()}

    def source_options(self) -> dict[str, str]:
        """The last selected history database and NAHF collection, if saved."""
        saved = self._preferences.load().get("source_options")
        if not isinstance(saved, dict):
            return {}
        return {
            key: value
            for key in ("historydb_site", "historydb_item", "nahf_item")
            if isinstance(value := saved.get(key), str) and 0 < len(value) <= 64
        }

    @contextmanager
    def _local_operation(self) -> Iterator[None]:
        # Folder changes cannot redirect an import while its network/disk work runs.
        # Searching the other immutable library snapshots remains available.
        with self._local_lock:
            self._local_busy += 1
        try:
            yield
        finally:
            with self._local_lock:
                self._local_busy -= 1

    def open_folder(self, folder: str, source: str = "") -> dict[str, Any]:
        """Replace one slot only after its folder and preferences both succeed."""
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
            chosen = source or self._active_local
            if not chosen:
                chosen = next(
                    (code for code in LOCAL_SOURCES if code not in self._local_paths),
                    "local",
                )
        return self._open_folder(folder, chosen)

    def _open_folder(self, folder: str, source: str) -> dict[str, Any]:
        if source not in LOCAL_SOURCES or not isinstance(folder, str) or not folder:
            return {"error": "bad_request"}
        stored = str(Path(folder).absolute())
        try:
            target = Path(stored).resolve()
            if not target.is_dir():
                return {"error": "load_failed", "path": folder}
        except OSError:
            return {"error": "load_failed", "path": folder}
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
            if any(
                code != source and path and _same_folder(Path(path), target)
                for code, path in self._local_paths.items()
            ):
                return {"error": "local_duplicate", "path": folder}
            prior_path = self._local_paths.get(source)
            revision = self._local_revisions.get(source, 0)
            settings = self._library.settings
        try:
            # Reading a large folder must not hold the lock used by status snapshots.
            library = _load_folder(stored, settings)
        except (InputError, OSError, NormalizationError):
            # The path is data, not text: the page words the error from its catalog.
            return {"error": "load_failed", "path": folder}
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
            if (
                self._local_paths.get(source) != prior_path
                or self._local_revisions.get(source, 0) != revision
                or self._library.settings is not settings
            ):
                # Another open changed this slot or the active library while the
                # folder was read; do not install a stale result.
                return {"error": "local_busy"}
            if any(
                code != source and path and _same_folder(Path(path), target)
                for code, path in self._local_paths.items()
            ):
                return {"error": "local_duplicate", "path": folder}
            # Re-read paths and selection at commit: another slot may have changed.
            paths = {**self._local_paths, source: stored}
            selected = self._selected_sources.copy()
            if not selected:
                selected = [source]
            if not self._remember_local(paths, source, selected):
                return {"error": "preferences_write_failed"}
            self._local_libraries[source] = library
            self._local_paths = paths
            self._local_errors.pop(source, None)
            self._local_revisions[source] = self._local_revisions.get(source, 0) + 1
            self._selected_sources = selected
            self._active_local = source
            self._library, self._folder = library, stored
            return self.library()

    def _reload_local_folder(self, folder: str) -> dict[str, Any]:
        """Refresh an import's destination slot without replacing another folder."""
        with self._local_lock:
            target = Path(folder).resolve()
            source = next(
                (
                    code
                    for code, path in self._local_paths.items()
                    if path and _same_folder(Path(path), target)
                ),
                None,
            )
            if source is None:
                return {"error": "import_not_open", "path": folder}
            revision = self._local_revisions.get(source, 0)
            settings = self._library.settings
        try:
            library = _load_folder(folder, settings)
        except (InputError, OSError, NormalizationError):
            # The same failure set as _load_local_slot: an import's own destination
            # that cannot be read back must answer the caller, not raise.
            with self._local_lock:
                if (
                    self._local_revisions.get(source, 0) == revision
                    and self._local_paths.get(source)
                    and _same_folder(Path(self._local_paths[source]), target)
                ):
                    self._local_errors[source] = "load_failed"
            return {"error": "load_failed", "path": folder}
        with self._local_lock:
            if self._local_revisions.get(source, 0) != revision or not _same_folder(
                Path(self._local_paths[source]), target
            ):
                return {"error": "local_busy"}
            if not self._remember_local(self._local_paths, source):
                self._local_errors[source] = "load_failed"
                return {"error": "preferences_write_failed"}
            self._local_libraries[source] = library
            self._local_errors.pop(source, None)
            self._local_revisions[source] = revision + 1
            self._active_local = source
            self._library, self._folder = library, self._local_paths[source]
            return self.library()

    def datasets(self) -> list[dict[str, str]]:
        """The institute releases the import dialog offers (cpe.local.portal); the
        page words their names and rights from its catalogs."""
        from cpe.local.portal import DATASETS, page_url

        return [
            {
                "pk": entry.pk,
                "institute": entry.institute,
                "rights": entry.rights,
                "page": page_url(entry.pk),
            }
            for entry in DATASETS
        ]

    def pick_folder(self) -> dict[str, Any]:
        """Ask for a folder with the system dialog without opening it yet."""
        if self._window is None:
            return {"cancelled": True}
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {"cancelled": True}
        return {"folder": str(chosen[0])}

    def import_dataset(self, pk: str, folder: str) -> dict[str, Any]:
        """Download a listed release into a temporary file, convert it into folder,
        and open that folder."""
        with self._local_operation():
            from cpe.local.portal import PortalClient, download
            from cpe.local.releases import ReleaseError

            if not folder or not Path(folder).is_dir():
                return {"error": "release_no_folder"}
            if destination_error := self._destination_error(folder):
                return destination_error
            with self._client_lock:
                if self._portal is None:
                    self._portal = PortalClient(transport=self._portal_transport)
            try:
                path = download(str(pk), self._portal)
            except ReleaseError as error:
                return {"error": error.code}
            try:
                return self.import_release_file(str(path), folder)
            finally:
                path.unlink(missing_ok=True)

    def import_release(self, file_label: str, folder: str = "") -> dict[str, Any]:
        """Convert a release file the researcher already downloaded.

        `file_label` is the file-type name in the page's language, for the dialog.
        """
        with self._local_operation():
            if self._window is None:
                return {"cancelled": True}
            import webview

            chosen = self._window.create_file_dialog(
                webview.FileDialog.OPEN, file_types=(f"{file_label} (*.zip;*.xml)",)
            )
            if not chosen:
                return {"cancelled": True}
            return self.import_release_file(str(chosen[0]), folder)

    def import_release_file(self, path: str, folder: str = "") -> dict[str, Any]:
        """Convert a release into folder (default: the open folder), then open it."""
        with self._local_operation():
            from cpe.local.releases import ReleaseError, import_release

            folder = folder or self._folder
            if not folder or not Path(folder).is_dir():
                return {"error": "release_no_folder"}
            if destination_error := self._destination_error(folder):
                return destination_error
            try:
                imported = import_release(Path(path), Path(folder))
            except ReleaseError as error:
                return {"error": error.code}
            except OSError:
                return {"error": "release_write_failed"}
            library = self._reload_local_folder(folder)
            return {"imported": imported, "library": library}

    def import_releases(self, folder: str = "") -> dict[str, Any]:
        """Ask for the folder holding downloaded releases, then import them all."""
        with self._local_operation():
            if self._window is None:
                return {"cancelled": True}
            import webview

            chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not chosen:
                return {"cancelled": True}
            return self.import_release_directory(str(chosen[0]), folder)

    def import_release_directory(self, source: str, folder: str = "") -> dict[str, Any]:
        """Convert every release file under source into folder (default: the open
        folder), each as one complete release (cpe.local.batch), then open folder.

        The per-file results come back even when the folder cannot be opened
        afterwards; the library then carries its own error.
        """
        with self._local_operation():
            from cpe.local.batch import import_directory
            from cpe.local.releases import ReleaseError

            folder = folder or self._folder
            if not folder or not Path(folder).is_dir():
                return {"error": "release_no_folder"}
            if destination_error := self._destination_error(folder):
                return destination_error
            if not source:
                return {"error": "batch_source_missing"}
            try:
                batch = import_directory(Path(source), Path(folder))
            except ReleaseError as error:
                return {"error": error.code}
            except OSError:
                return {"error": "release_write_failed"}
            library = self._reload_local_folder(folder)
            return {"batch": batch, "source": source, "library": library}

    def ctp_key_set(self) -> bool:
        """Whether a CTP API key is stored; the key itself never goes to the page."""
        return bool(self._preferences.get("ctp_key"))

    def save_ctp_key(self, key: str) -> bool:
        try:
            self._preferences.set("ctp_key", str(key).strip())
        except OSError:
            return False
        return True

    def import_ctp(self, address: str) -> dict[str, Any]:
        """Save a CTP work into the open text folder, then read the folder again."""
        with self._local_operation(), self._ctp_operation_lock:
            if not self._folder:
                return {"error": "ctp_no_folder"}
            if destination_error := self._destination_error(self._folder):
                return destination_error
            from cpe.ctp.adapter import CtpClient, CtpError, import_work

            if self._ctp is None:
                self._ctp = CtpClient()
            client = self._ctp
            client.apikey = self._preferences.get("ctp_key")
            # The cap belongs to an import; request spacing belongs to the session.
            client.calls = 0
            if self._ctp_transport is not None:
                client.transport = self._ctp_transport
            try:
                imported = import_work(str(address), client, Path(self._folder))
            except CtpError as error:
                return {"error": error.code}
            except OSError:
                return {"error": "ctp_write_failed"}
            library = self._reload_local_folder(self._folder)
            return {"imported": imported, "library": library}

    def choose_folder(self, source: str = "") -> dict[str, Any]:
        """Ask for a folder with the system dialog, then open it."""
        if source and source not in LOCAL_SOURCES:
            return {"error": "bad_request"}
        with self._local_lock:
            if self._local_busy:
                return {"error": "local_busy"}
        if self._window is None:
            return {"cancelled": True}
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {"cancelled": True}
        return self.open_folder(str(chosen[0]), source)

    def set_title(self, text: str) -> None:
        """The page names the window in the chosen language."""
        if self._window is not None:
            self._window.set_title(str(text))

    def home_icon(self) -> str:
        """Send the image separately from WebView2's size-limited initial HTML."""
        return _home_icon(HERE / "assets" / "home-icon.png")

    def languages(self) -> list[dict[str, str]]:
        return [
            {"code": code, "name": self.catalog(code)["language.name"]}
            for code in LANGUAGES
        ]

    def catalog(self, code: str) -> dict[str, str]:
        if code not in LANGUAGES:
            # Only the listed files: a code from the page never becomes a path.
            return {}
        return _catalog(code).copy()

    def library(self) -> dict[str, Any]:
        with self._local_lock:
            return {**self._library.summary(), "folder": self._folder}

    def open_link(self, url: str) -> bool:
        """Open a result's source page in the system browser (web addresses only)."""
        if not str(url).startswith(("https://", "http://")):
            return False
        import webbrowser

        return webbrowser.open(str(url))

    def itkc_client(self) -> Any:
        with self._client_lock:
            if self._itkc is None:
                from cpe.itkc.adapter import make_client

                config_dir().mkdir(parents=True, exist_ok=True)
                self._itkc = make_client(config_dir() / "itkc-requests.jsonl")
            return self._itkc

    def ndl_client(self) -> Any:
        with self._client_lock:
            if self._ndl is None:
                from cpe.ndl.adapter import NdlClient

                self._ndl = NdlClient()
            return self._ndl

    def cbeta_client(self) -> Any:
        with self._client_lock:
            if self._cbeta is None:
                from cpe.cbeta.adapter import CbetaClient

                self._cbeta = CbetaClient()
            return self._cbeta

    def cyberseodang_client(self) -> Any:
        with self._client_lock:
            if self._cyberseodang is None:
                from cpe.cyberseodang.adapter import CyberseodangClient

                self._cyberseodang = CyberseodangClient()
            return self._cyberseodang

    def docusky_client(self) -> Any:
        with self._client_lock:
            if self._docusky is None:
                from cpe.docusky.adapter import DocuskyClient

                self._docusky = DocuskyClient()
            return self._docusky

    def nrich_client(self) -> Any:
        with self._client_lock:
            if self._nrich is None:
                from cpe.nrich.adapter import NrichClient

                self._nrich = NrichClient()
            return self._nrich

    def sillok_client(self) -> Any:
        with self._client_lock:
            if self._sillok is None:
                from cpe.nikh.search import SillokClient

                self._sillok = SillokClient()
            return self._sillok

    def historydb_client(self) -> Any:
        with self._client_lock:
            if self._historydb is None:
                from cpe.historydb.search import HistoryDbClient

                self._historydb = HistoryDbClient()
            return self._historydb

    def ith_client(self) -> Any:
        with self._client_lock:
            if self._ith is None:
                from cpe.ith.search import IthClient

                self._ith = IthClient()
            return self._ith

    def nahf_client(self) -> Any:
        with self._client_lock:
            if self._nahf is None:
                from cpe.nahf.search import NahfClient

                self._nahf = NahfClient()
            return self._nahf

    def nahf_catalog(self) -> dict[str, Any]:
        """Source-library collections for the NAHF selector; no provider request."""
        from cpe.nahf.search import ITEMS

        return {"items": [{"id": item_id, "label": label} for item_id, label in ITEMS]}

    def vietdien_client(self) -> Any:
        with self._client_lock:
            if self._vietdien is None:
                from cpe.vietdien.adapter import VietDienClient

                self._vietdien = VietDienClient()
            return self._vietdien

    def sat_client(self) -> Any:
        with self._client_lock:
            if self._sat is None:
                from cpe.sat.search import SatClient

                self._sat = SatClient()
            return self._sat

    def kyudb_client(self) -> Any:
        with self._client_lock:
            if self._kyudb is None:
                from cpe.kyudb.search import KyudbClient

                self._kyudb = KyudbClient()
            return self._kyudb

    def cand_client(self) -> Any:
        with self._client_lock:
            if self._cand is None:
                from cpe.cand.search import CandClient

                self._cand = CandClient()
            return self._cand

    def csid_client(self) -> Any:
        with self._client_lock:
            if self._csid is None:
                from cpe.csid.search import CsidClient

                self._csid = CsidClient()
            return self._csid

    def thdl_client(self) -> Any:
        with self._client_lock:
            if self._thdl is None:
                from cpe.thdl.search import ThdlClient

                self._thdl = ThdlClient()
            return self._thdl

    def aksarchive_client(self) -> Any:
        with self._client_lock:
            if self._aksarchive is None:
                from cpe.aks.archive import AksArchiveClient

                self._aksarchive = AksArchiveClient()
            return self._aksarchive

    def historydb_catalog(self, site: Any) -> dict[str, Any]:
        """Era database items for the integrated-search item selector."""
        if not isinstance(site, str):
            return {"error": "historydb_site"}
        try:
            entries = self.historydb_client().catalog(site)
        except Exception as error:
            from cpe.historydb.search import HistoryDbCapReached

            if isinstance(error, HistoryDbCapReached):
                return {"error": "source_cap"}
            code = str(error)
            if code == "search_site":
                return {"error": "historydb_site"}
            return {"error": "source_failed"}
        return {
            "site": site,
            "items": [{"id": entry.item_id, "label": entry.label} for entry in entries],
        }

    def docusky_catalog(self) -> dict[str, Any]:
        """The public DocuSky database list the window offers as profile choices."""
        try:
            return docusky_database_list(self.docusky_client())
        except Exception:
            return {"error": "source_internal"}

    def start_itkc_search(self, request: Any) -> dict[str, Any]:
        """Start one bounded ITKC session and return metadata only."""
        try:
            return self._start_itkc_search(request)
        except InterruptedError:
            return {"error": "search_cancelled"}
        except Exception:
            return {"error": "source_internal"}

    def _start_itkc_search(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"error": "bad_request"}
        if request.get("source") not in (None, "itkc"):
            return {"error": "bad_request"}
        _, error = itkc_plan(request)
        if error is not None:
            return {"error": error}
        generation = request.get("generation")
        if generation is not None:
            if isinstance(generation, bool) or not isinstance(generation, int):
                return {"error": "bad_request"}
            self.invalidate_searches(generation)
            if generation != self.search_generation():
                return {"error": "search_cancelled"}
        else:
            generation = self.search_generation()
        with self._itkc_start_lock:
            if generation != self.search_generation():
                return {"error": "search_cancelled"}
            session = ItkcSearchSession(
                uuid.uuid4().hex,
                request,
                copy.deepcopy(self._library.settings),
                self.itkc_client(),
                generation,
                lambda value: self.search_generation() == value,
            )
            with self._itkc_session_lock:
                previous = self._itkc_session
                if previous is not None:
                    previous.cancel()
                self._itkc_session = session
            return session.start()

    def advance_itkc_search(self, job_id: Any, generation: Any) -> dict[str, Any]:
        """Fetch and analyze exactly the next collection on explicit advance."""
        try:
            if not isinstance(job_id, str) or not job_id:
                return {"error": "bad_request"}
            if isinstance(generation, bool) or not isinstance(generation, int):
                return {"error": "bad_request"}
            with self._itkc_session_lock:
                session = self._itkc_session
            if session is None or session.id != job_id:
                return {"error": "bad_request"}
            return session.advance(generation)
        except InterruptedError:
            return {"error": "search_cancelled"}
        except Exception:
            return {"error": "source_internal"}

    def itkc_search_page(
        self,
        job_id: Any,
        revision: Any,
        group: Any,
        start: Any = 0,
        sort: Any = "source",
    ) -> dict[str, Any]:
        """Read a bounded slice of one immutable session revision without network."""
        try:
            if not isinstance(job_id, str) or not job_id:
                return {"error": "bad_request"}
            with self._itkc_session_lock:
                session = self._itkc_session
            if session is None or session.id != job_id:
                return {"error": "bad_request"}
            return session.page(revision, group, start, sort)
        except Exception:
            return {"error": "source_internal"}

    def invalidate_searches(self, generation: int) -> bool:
        if isinstance(generation, bool) or not isinstance(generation, int):
            return False
        with self._client_lock:
            self._search_generation = max(self._search_generation, generation)
        with self._itkc_session_lock:
            session = self._itkc_session
            if session is not None and generation > session.generation:
                session.cancel()
        return True

    def search_generation(self) -> int:
        """Resume the current search generation when the window page reloads."""
        with self._client_lock:
            return self._search_generation

    def search(self, request: Any) -> dict[str, Any]:
        try:
            return self._search(request)
        except InterruptedError:
            return {"error": "search_cancelled"}
        except Exception:
            # The bridge must return an explicit failure instead of letting an
            # adapter exception masquerade as a disconnected desktop bridge.
            return {"error": "source_internal"}

    def _search(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"error": "bad_request"}
        generation = request.get("generation")
        if generation is not None:
            if not isinstance(generation, int) or isinstance(generation, bool):
                return {"error": "bad_request"}
            self.invalidate_searches(generation)

        def checkpoint() -> None:
            if generation is not None and generation != self.search_generation():
                raise InterruptedError("superseded search")

        checkpoint()
        answer = self._dispatch_search(request, checkpoint)
        checkpoint()
        return answer

    def _dispatch_search(
        self, request: dict[str, Any], checkpoint: Callable[[], None]
    ) -> dict[str, Any]:
        source = request.get("source")
        if source == "ships":
            return search_ships(request)
        routes: dict[
            str, tuple[Callable[..., dict[str, Any]], Callable[[], Any], bool]
        ] = {
            "itkc": (search_itkc, self.itkc_client, True),
            "cbeta": (search_cbeta, self.cbeta_client, True),
            "ndl": (search_ndl, self.ndl_client, True),
            "cyberseodang": (search_cyberseodang, self.cyberseodang_client, True),
            "docusky": (search_docusky, self.docusky_client, True),
            "nrich": (search_nrich, self.nrich_client, True),
            "sillok": (search_sillok, self.sillok_client, True),
            "historydb": (search_historydb, self.historydb_client, True),
            "ith": (search_ith, self.ith_client, True),
            "nahf": (search_nahf, self.nahf_client, True),
            "vietdien": (search_vietdien, self.vietdien_client, True),
            "sat": (search_sat, self.sat_client, True),
            "aksarchive": (search_aksarchive, self.aksarchive_client, True),
            "thdl": (search_thdl, self.thdl_client, False),
            "csid": (search_csid, self.csid_client, False),
            "cand": (search_cand, self.cand_client, False),
            "kyudb": (search_kyudb, self.kyudb_client, False),
        }
        route = (
            "docusky"
            if isinstance(source, str) and source.startswith("docusky:")
            else source
        )
        if isinstance(route, str) and route in routes:
            run, client, uses_settings = routes[route]
            checkpoint()
            if uses_settings:
                return run(request, client(), self._library.settings, checkpoint)
            return run(request, client(), checkpoint)
        source = request.get("source", self._active_local)
        if source not in LOCAL_SOURCES:
            return {"error": "bad_request"}
        with self._local_lock:
            if source in self._local_errors:
                return {"error": "load_failed", "path": self._local_paths[source]}
            library = self._local_libraries.get(source)
            if library is None:
                if not self._local_configured(source):
                    return {"error": "local_unconfigured"}
                if self._local_busy:
                    return {"error": "local_busy"}
            settings = self._library.settings
        if library is None:
            library = self._load_local_slot(source, settings)
            if library is None:
                with self._local_lock:
                    if source in self._local_errors:
                        return {
                            "error": "load_failed",
                            "path": self._local_paths.get(source, ""),
                        }
                    if self._local_busy:
                        return {"error": "local_busy"}
                    if not self._local_configured(source):
                        return {"error": "local_unconfigured"}
                return {
                    "error": "load_failed",
                    "path": self._local_paths.get(source, ""),
                }
        if request.get("mode") == "pair":
            return search_pair(
                library,
                str(request.get("first", "")),
                str(request.get("second", "")),
                checkpoint=checkpoint,
            )
        if request.get("mode") == "words":
            return search_words(
                library, str(request.get("query", "")), checkpoint=checkpoint
            )
        return {"error": "bad_request"}


def page() -> str:
    """Inline diagnostic snapshot; the runtime opens the local asset entrypoint."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="style.css">', f"<style>\n{style}</style>"
    )
    # NavigateToString rejects oversized HTML before the page or bridge starts.
    html = html.replace('src="../assets/home-icon.png"', "")
    return html.replace(
        '<script src="app.js"></script>', f"<script>\n{script}</script>"
    )


def start_library(
    paths: list[Path], settings: Settings, preferences: Preferences
) -> tuple[Library, str]:
    """The texts given on the command line, else the folder opened last time.

    A remembered folder that can no longer be read starts an empty library instead of
    failing: the window can still open another folder.
    """
    if paths:
        folder = ""
        if len(paths) == 1:
            try:
                folder = str(paths[0]) if paths[0].is_dir() else ""
            except OSError:
                folder = ""
        return Library.load(paths, settings), folder
    folder = preferences.get("folder")
    if folder:
        try:
            return _load_folder(folder, settings), folder
        except (InputError, NormalizationError, OSError):
            # A remembered folder that now conflicts with the settings starts empty,
            # like an unreadable one. Explicit paths above stay strict so cpe.cli can
            # report the invalid text and exit instead of opening an empty window.
            pass
    return Library([], settings), ""


def open_window(library: Library, folder: str = "", debug: bool = False) -> None:
    import webview

    api = Api(library, folder=folder)
    # A local asset URL keeps fonts, CSS and script outside NavigateToString.
    api._window = webview.create_window(
        api.catalog(api.saved_language() or "en")["app.title"],
        url=(STATIC / "index.html").as_uri(),
        js_api=api,
        width=1180,
        height=860,
        min_size=(360, 480),
    )
    # Only Linux (Qt) takes the PNG as window icon. On Windows pywebview builds a
    # System.Drawing.Icon from it, which accepts only .ico and crashed the window at
    # start; Windows and macOS show the executable's icon (scripts/build_exe.py).
    icon = HERE / "assets" / "icon.png"
    use_icon = sys.platform.startswith("linux") and icon.exists()
    webview.start(debug=debug, icon=str(icon) if use_icon else None)
