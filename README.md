# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer searches Literary Sinitic source text in a desktop window or from the command line. The interface supports Korean, Japanese, Simplified Chinese, Traditional Chinese and English. The interface language never converts source text.

## Windows executable

Download [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe) and double-click it. Python and uv are not required. The Windows executable uses Microsoft Edge WebView2 Runtime.

## Run from source

Install Python 3.11 or newer and uv, then run from this folder:

```bash
uv sync --locked
uv run --locked cpe gui
```

Pass a UTF-8 text file or folder to open it directly:

```bash
uv run --locked cpe gui path/to/texts
```

On macOS, open `cpe-gui.command`. On Linux, run `cpe-gui.sh`. The first launch installs the runtime dependencies. Windows uses Edge WebView2; macOS uses WebKit; Linux uses Qt. The window remembers the chosen text folder.

The Settings dialog selects the interface language and search sources. Local text files use UTF-8; folders contribute `.txt` and `.md` files recursively. A text can have a `NAME.txt.manifest.toml` sidecar with its title, edition and date. Online source access requires a network connection.

## Command line

```bash
uv run --locked cpe --help
uv run --locked cpe match patterns/mvp.toml path/to/texts --out results
uv run --locked python itkc_search.py --help
```

Search commands export CSV, JSONL and run metadata. The bundled pattern definitions retain their source citations. The application package includes the existing CLI commands and their dependencies so all advertised commands remain available.

## Distribution

This repository contains the runtime package, translation catalogs, page resources, launchers, runtime dependency lock and licence. Tests, development dependencies, build tools, agent instructions, research documents, gold data, local corpora and the development repository's Git history are excluded.

The package version is `1.0.0`; the corresponding Git tag is `v1.0`. `release.json` records the exact source snapshot. Subsequent versions are exported as reviewed runtime snapshots; the private development history is not merged here.

Released under the MIT License (`LICENSE`). This is a personal project unaffiliated with ITKC. No source corpus, modern translation or database response is bundled.
