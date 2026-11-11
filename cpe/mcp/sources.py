"""SDK-free access to the online search sources used by the desktop window.

The registry is a host-side dispatcher, not another matcher.  It keeps the clients
for the eighteen online sources and delegates every search to the same function the
GUI calls, so source-specific restrictions, error codes and provenance stay in one
place.  MCP transport code is deliberately absent: callers may use this module from
a CLI, a test or an MCP application service without importing the SDK.

The registry has no local library.  ``LOCAL_SOURCE_IDS`` is exported for hosts that
combine a local service with this online registry; local searches remain the job of
``cpe.gui.service``/``cpe.mcp.service``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from cpe.gui import cand as gui_cand
from cpe.gui import csid as gui_csid
from cpe.gui import external as gui_external
from cpe.gui import kyudb as gui_kyudb
from cpe.gui import online
from cpe.gui import thdl as gui_thdl
from cpe.historydb.search import HistoryDbCapReached
from cpe.itkc.client import ITKCClient
from cpe.match.engine import Settings

# The GUI order is intentional: a host can expose this tuple without importing the
# window module (which owns preferences and the webview).
SOURCE_IDS = (
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

LOCAL_SOURCE_IDS = ("local", "local2", "local3", "local4")

_DOCUSKY_PREFIX = "docusky:"
# These routes analyze fetched texts with the shared core matcher and can attach a
# serialized Result.  Every other online route returns provider rows that are only
# references or excerpts; it must not receive a fabricated record.
_RECORD_SOURCES = frozenset(("itkc", "ndl", "cbeta", "docusky"))
_USES_SETTINGS = frozenset(
    (
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
    )
)
_ROUTES: dict[str, tuple[Any, str]] = {
    "itkc": (online, "search_itkc"),
    "ndl": (online, "search_ndl"),
    "cbeta": (online, "search_cbeta"),
    "cyberseodang": (online, "search_cyberseodang"),
    "docusky": (online, "search_docusky"),
    "historydb": (online, "search_historydb"),
    "sillok": (online, "search_sillok"),
    "nrich": (online, "search_nrich"),
    "ith": (online, "search_ith"),
    "nahf": (online, "search_nahf"),
    "vietdien": (online, "search_vietdien"),
    "sat": (online, "search_sat"),
    "aksarchive": (online, "search_aksarchive"),
    "thdl": (gui_thdl, "search_thdl"),
    "csid": (gui_csid, "search_csid"),
    "cand": (gui_cand, "search_cand"),
    "kyudb": (gui_kyudb, "search_kyudb"),
}


def _base_source(source: str) -> str:
    return "docusky" if source.startswith(_DOCUSKY_PREFIX) else source


def _source_code(value: Any) -> str | None:
    """Normalize one GUI source code, including the dynamic DocuSky profiles."""
    if not isinstance(value, str):
        return None
    if value in SOURCE_IDS:
        return value
    if value.startswith(_DOCUSKY_PREFIX):
        from cpe.docusky.adapter import valid_database_name

        database = value[len(_DOCUSKY_PREFIX) :]
        if valid_database_name(database):
            return value
    return None


def valid_source(source_id: Any) -> bool:
    """Whether a host value names one of the eighteen sources or a valid profile."""
    return _source_code(source_id) is not None


def source_code(source_id: Any) -> str | None:
    """The validated source code, or ``None`` for an unknown value."""
    return _source_code(source_id)


def _validate_enabled(entries: Iterable[str]) -> tuple[str, ...]:
    enabled: list[str] = []
    for source in entries or ():
        if _source_code(source) is None:
            raise ValueError("enabled_online contains an unknown source")
        if source not in enabled:
            enabled.append(source)
    return tuple(enabled)


class SourceRegistry:
    """Persistent clients and GUI-equivalent dispatch for the online sources.

    ``enabled_online`` is an explicit allowlist.  The base name ``docusky`` opens
    every DocuSky profile; an entry such as ``docusky:宋會要輯稿`` opens only that
    profile.  No client is constructed and no provider request is made by the
    constructor.  An injected ``clients`` mapping wins over the lazy default
    factory; for DocuSky, ``clients['docusky']`` is the shared client and an exact
    ``clients['docusky:<database>']`` entry, when present, is used for that profile.
    """

    source_ids = SOURCE_IDS

    def __init__(
        self,
        settings: Settings,
        *,
        enabled_online: Iterable[str] = (),
        itkc_log: Path | None = None,
        clients: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(settings, Settings):
            raise ValueError("settings must be a cpe.match.engine.Settings")
        if clients is not None and not isinstance(clients, Mapping):
            raise ValueError("clients must be a mapping")
        enabled = _validate_enabled(enabled_online)
        injected: dict[str, Any] = {}
        if clients is not None:
            for source, client in clients.items():
                if _source_code(source) is None:
                    raise ValueError("clients contains an unknown source")
                injected[source] = client
        self._settings = settings
        self._enabled = frozenset(enabled)
        self._itkc_log = itkc_log
        self._clients = injected
        self._client_lock = RLock()

    # ------------------------------------------------------------------ source
    @property
    def settings(self) -> Settings:
        """The matcher/normalization settings used by every delegated search."""
        return self._settings

    @settings.setter
    def settings(self, value: Settings) -> None:
        if not isinstance(value, Settings):
            raise ValueError("settings must be a cpe.match.engine.Settings")
        self._settings = value

    def valid_source(self, source_id: Any) -> bool:
        """Whether this value names an online source or a valid DocuSky profile."""
        return valid_source(source_id)

    def enabled(self, source_id: str) -> bool:
        """Whether the host allowlist opens this source or DocuSky profile."""
        source = _source_code(source_id)
        if source is None:
            return False
        if source in self._enabled:
            return True
        return source.startswith(_DOCUSKY_PREFIX) and "docusky" in self._enabled

    def client(self, source_id: str) -> Any:
        """Return the persistent client for one source, creating it once.

        ``ships`` is external-only and returns ``None``.  A disabled source, an
        unknown source and an ITKC request without a log or injected client raise
        ``ValueError``; this method never starts a provider request.
        """
        source = _source_code(source_id)
        if source is None:
            if source_id in LOCAL_SOURCE_IDS:
                raise ValueError("unimplemented_source")
            raise ValueError("unknown_source")
        if not self.enabled(source):
            raise ValueError("source_disabled")
        if source == "ships":
            return None
        if source == "itkc" and not self._has_itkc():
            raise ValueError("itkc_log_required")

        exact = (
            source
            if source in self._clients and self._clients[source] is not None
            else None
        )
        key = exact or _base_source(source)
        with self._client_lock:
            if key in self._clients and self._clients[key] is not None:
                return self._clients[key]
            client = self._make_client(key)
            self._clients[key] = client
            return client

    def _has_itkc(self) -> bool:
        return self._clients.get("itkc") is not None or self._itkc_log is not None

    def _make_client(self, source: str) -> Any:
        if source == "itkc":
            assert self._itkc_log is not None
            return ITKCClient("GO_AA", self._itkc_log, rows=100)
        if source == "ndl":
            from cpe.ndl.adapter import NdlClient

            return NdlClient()
        if source == "cbeta":
            from cpe.cbeta.adapter import CbetaClient

            return CbetaClient()
        if source == "cyberseodang":
            from cpe.cyberseodang.adapter import CyberseodangClient

            return CyberseodangClient()
        if source == "docusky":
            from cpe.docusky.adapter import DocuskyClient

            return DocuskyClient()
        if source == "historydb":
            from cpe.historydb.search import HistoryDbClient

            return HistoryDbClient()
        if source == "sillok":
            from cpe.nikh.search import SillokClient

            return SillokClient()
        if source == "nrich":
            from cpe.nrich.adapter import NrichClient

            return NrichClient()
        if source == "ith":
            from cpe.ith.search import IthClient

            return IthClient()
        if source == "nahf":
            from cpe.nahf.search import NahfClient

            return NahfClient()
        if source == "vietdien":
            from cpe.vietdien.adapter import VietDienClient

            return VietDienClient()
        if source == "sat":
            from cpe.sat.search import SatClient

            return SatClient()
        if source == "aksarchive":
            from cpe.aks.archive import AksArchiveClient

            return AksArchiveClient()
        if source == "thdl":
            from cpe.thdl.search import ThdlClient

            return ThdlClient()
        if source == "csid":
            from cpe.csid.search import CsidClient

            return CsidClient()
        if source == "cand":
            from cpe.cand.search import CandClient

            return CandClient()
        if source == "kyudb":
            from cpe.kyudb.search import KyudbClient

            return KyudbClient()
        raise ValueError("unknown_source")

    # ------------------------------------------------------------------ search
    def search(
        self,
        request: dict[str, Any],
        checkpoint: Callable[[], None] | None = None,
        include_records: bool = True,
    ) -> dict[str, Any]:
        """Run the GUI-equivalent search for one enabled source.

        The request dictionary is passed through unchanged so DocuSky profile
        names, HistoryDB ``site``/``item`` and NAHF ``item`` keep their GUI
        meaning.  Reference-only sources ignore ``include_records``; only routes
        backed by the shared matcher may add a ``record`` field.
        """
        if not isinstance(request, dict):
            return {"error": "bad_request"}
        requested = request.get("source")
        source = _source_code(requested)
        if source is None:
            return {
                "error": (
                    "unimplemented_source"
                    if requested in LOCAL_SOURCE_IDS
                    else "bad_request"
                )
            }
        if not self.enabled(source):
            return {"error": "source_disabled"}
        if checkpoint is not None and not callable(checkpoint):
            return {"error": "bad_request"}
        if source == "itkc" and not self._has_itkc():
            return {"error": "itkc_log_required"}

        try:
            if checkpoint is not None:
                checkpoint()
            if source == "ships":
                answer = gui_external.search_ships(request)
            else:
                base = _base_source(source)
                owner, name = _ROUTES[base]
                function = getattr(owner, name)
                client = self.client(source)
                if base in _USES_SETTINGS:
                    if base in _RECORD_SOURCES:
                        answer = function(
                            request,
                            client,
                            self._settings,
                            checkpoint,
                            include_records=include_records,
                        )
                    else:
                        answer = function(request, client, self._settings, checkpoint)
                else:
                    answer = function(request, client, checkpoint)
            if checkpoint is not None:
                checkpoint()
            return answer if isinstance(answer, dict) else {"error": "source_failed"}
        except InterruptedError:
            return {"error": "search_cancelled"}
        except Exception:
            # Match the window bridge: an adapter surprise is an explicit source
            # failure, never a half-result that could look like a zero-hit search.
            return {"error": "source_internal"}

    # ----------------------------------------------------------------- catalog
    def catalog(self, source_id: str, site: str = "") -> dict[str, Any]:
        """Return the source's selector catalog with the GUI's existing mapping.

        DocuSky and HistoryDB catalogs use the same client methods (and therefore
        the same network caps) as the window.  NAHF is a static tuple and makes no
        request.  An unenabled network catalog is refused before client creation.
        """
        source = _source_code(source_id)
        if source is None:
            if source_id in LOCAL_SOURCE_IDS:
                return {"error": "unimplemented_source"}
            return {"error": "unknown_source"}
        base = _base_source(source)
        if base == "nahf":
            from cpe.nahf.search import ITEMS

            return {
                "items": [{"id": item_id, "label": label} for item_id, label in ITEMS]
            }
        if base not in ("docusky", "historydb"):
            return {"error": "unimplemented_source"}
        if base == "docusky":
            if source != "docusky" and self.enabled(source):
                client_source = source
            elif "docusky" in self._enabled:
                client_source = "docusky"
            else:
                client_source = next(
                    (
                        item
                        for item in self._enabled
                        if item.startswith(_DOCUSKY_PREFIX)
                    ),
                    "",
                )
            if not client_source:
                return {"error": "source_disabled"}
            try:
                return online.docusky_catalog(self.client(client_source))
            except Exception:
                return {"error": "source_internal"}
        if not self.enabled("historydb"):
            return {"error": "source_disabled"}
        if not isinstance(site, str):
            return {"error": "historydb_site"}
        try:
            entries = self.client("historydb").catalog(site)
        except HistoryDbCapReached:
            return {"error": "source_cap"}
        except Exception as error:
            code = str(error)
            if code == "search_site":
                return {"error": "historydb_site"}
            return {"error": "source_failed"}
        return {
            "site": site,
            "items": [{"id": entry.item_id, "label": entry.label} for entry in entries],
        }
