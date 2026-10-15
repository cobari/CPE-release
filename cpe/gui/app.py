"""Desktop search window: the page in cpe/gui/static inside a native web view.

The page talks to Python through the in-process bridge (window.pywebview.api),
not over a local HTTP server: on the researcher's Windows machine a Python process
sending more than about 16 KB over localhost TCP lost data or stalled, while the bridge
moved 4 MB intact (docs/decisions.md "The GUI is built before gate 1-A is complete").
`webview` is imported only when a window opens, so the API is testable without it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cpe.gui.online import search_cbeta, search_itkc, search_ndl
from cpe.gui.preferences import Preferences, config_dir
from cpe.gui.service import Library, search_pair, search_words
from cpe.local.txt import InputError
from cpe.match.engine import Settings

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
I18N = HERE / "i18n"
# English is the source catalog (AGENTS.md "Language"); this order is the menu order.
LANGUAGES = ("ko", "ja", "zh-Hans", "zh-Hant", "en")


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
        self._window: Any = None
        # Online clients live for the session, so their call caps count every search.
        self._itkc: Any = None
        self._ndl: Any = None
        self._cbeta: Any = None
        # Tests replace CTP's transport here; None means the real network.
        self._ctp_transport: Any = None
        # Tests replace the portal's transport with recorded answers.
        self._portal: Any = None
        self._portal_transport: Any = None

    def saved_language(self) -> str:
        """The language chosen last time, or "" so the page starts in the OS one."""
        code = self._preferences.get("language")
        return code if code in LANGUAGES else ""

    def save_language(self, code: str) -> bool:
        if code not in LANGUAGES:
            return False
        self._preferences.set("language", code)
        return True

    def open_folder(self, folder: str) -> dict[str, Any]:
        """Replace the library with the .txt files of a folder and remember it."""
        try:
            library = Library.load([Path(folder)], self._library.settings)
        except (InputError, OSError):
            # The path is data, not text: the page words the error from its catalog.
            return {"error": "load_failed", "path": folder}
        self._library = library
        self._folder = folder
        self._preferences.set("folder", folder)
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
        from cpe.local.portal import PortalClient, download
        from cpe.local.releases import ReleaseError

        if not folder or not Path(folder).is_dir():
            return {"error": "release_no_folder"}
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
        from cpe.local.releases import ReleaseError, import_release

        folder = folder or self._folder
        if not folder or not Path(folder).is_dir():
            return {"error": "release_no_folder"}
        try:
            imported = import_release(Path(path), Path(folder))
        except ReleaseError as error:
            return {"error": error.code}
        except OSError:
            return {"error": "release_write_failed"}
        library = self.open_folder(folder)
        return {"imported": imported, "library": library}

    def ctp_key_set(self) -> bool:
        """Whether a CTP API key is stored; the key itself never goes to the page."""
        return bool(self._preferences.get("ctp_key"))

    def save_ctp_key(self, key: str) -> bool:
        self._preferences.set("ctp_key", str(key).strip())
        return True

    def import_ctp(self, address: str) -> dict[str, Any]:
        """Save a CTP work into the open text folder, then read the folder again."""
        if not self._folder:
            return {"error": "ctp_no_folder"}
        from cpe.ctp.adapter import CtpClient, CtpError, import_work

        client = CtpClient(apikey=self._preferences.get("ctp_key"))
        if self._ctp_transport is not None:
            client.transport = self._ctp_transport
        try:
            imported = import_work(str(address), client, Path(self._folder))
        except CtpError as error:
            return {"error": error.code}
        except OSError:
            return {"error": "ctp_write_failed"}
        library = self.open_folder(self._folder)
        return {"imported": imported, "library": library}

    def choose_folder(self) -> dict[str, Any]:
        """Ask for a folder with the system dialog, then open it."""
        if self._window is None:
            return {"cancelled": True}
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {"cancelled": True}
        return self.open_folder(str(chosen[0]))

    def set_title(self, text: str) -> None:
        """The page names the window in the chosen language."""
        if self._window is not None:
            self._window.set_title(str(text))

    def languages(self) -> list[dict[str, str]]:
        return [
            {"code": code, "name": self.catalog(code)["language.name"]}
            for code in LANGUAGES
        ]

    def catalog(self, code: str) -> dict[str, str]:
        if code not in LANGUAGES:
            # Only the listed files: a code from the page never becomes a path.
            return {}
        data: dict[str, str] = json.loads(
            (I18N / f"{code}.json").read_text(encoding="utf-8")
        )
        return data

    def library(self) -> dict[str, Any]:
        return {**self._library.describe(), "folder": self._folder}

    def open_link(self, url: str) -> bool:
        """Open a result's source page in the system browser (web addresses only)."""
        if not str(url).startswith(("https://", "http://")):
            return False
        import webbrowser

        return webbrowser.open(str(url))

    def itkc_client(self) -> Any:
        if self._itkc is None:
            from cpe.itkc.adapter import make_client

            # The request log of plan 7.1, kept with the window's preferences.
            config_dir().mkdir(parents=True, exist_ok=True)
            self._itkc = make_client(config_dir() / "itkc-requests.jsonl")
        return self._itkc

    def ndl_client(self) -> Any:
        if self._ndl is None:
            from cpe.ndl.adapter import NdlClient

            self._ndl = NdlClient()
        return self._ndl

    def cbeta_client(self) -> Any:
        if self._cbeta is None:
            from cpe.cbeta.adapter import CbetaClient

            self._cbeta = CbetaClient()
        return self._cbeta

    def search(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"error": "bad_request"}
        if request.get("source") == "itkc":
            return search_itkc(request, self.itkc_client(), self._library.settings)
        if request.get("source") == "cbeta":
            return search_cbeta(request, self.cbeta_client(), self._library.settings)
        if request.get("source") == "ndl":
            return search_ndl(request, self.ndl_client(), self._library.settings)
        if request.get("mode") == "pair":
            return search_pair(
                self._library,
                str(request.get("first", "")),
                str(request.get("second", "")),
            )
        if request.get("mode") == "words":
            return search_words(self._library, str(request.get("query", "")))
        return {"error": "bad_request"}


def page() -> str:
    """index.html with its stylesheet and script inlined: no file or server URL."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="style.css">', f"<style>\n{style}</style>"
    )
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
        return Library.load(paths, settings), ""
    folder = preferences.get("folder")
    if folder:
        try:
            return Library.load([Path(folder)], settings), folder
        except (InputError, OSError):
            pass
    return Library([], settings), ""


def open_window(library: Library, folder: str = "", debug: bool = False) -> None:
    import webview

    api = Api(library, folder=folder)
    # The page replaces this placeholder title with the catalog's once it loads.
    api._window = webview.create_window(
        "cpe", html=page(), js_api=api, width=1180, height=860, min_size=(360, 480)
    )
    # Only Linux (Qt) takes the PNG as window icon. On Windows pywebview builds a
    # System.Drawing.Icon from it, which accepts only .ico and crashed the window at
    # start; Windows and macOS show the executable's icon (scripts/build_exe.py).
    icon = HERE / "assets" / "icon.png"
    use_icon = sys.platform.startswith("linux") and icon.exists()
    webview.start(debug=debug, icon=str(icon) if use_icon else None)
