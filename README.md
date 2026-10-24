# Classic Pattern Explorer 2.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer is a desktop application and command-line tool for finding sentence-pattern candidates, such as 以…爲 or 雖…而, in Literary Sinitic source texts (漢文, often called Classical Chinese). Each candidate is shown with the context its text or the source's excerpt provides and, where the source gives one, a link back to it, so you can read and judge it yourself. The window is available in Korean, Japanese, Simplified Chinese, Traditional Chinese and English; changing the interface language never converts or alters the source text.

It is a digital humanities research aid, not a grammar oracle or syntactic parser. It collects candidates, and in compound search it sorts them by structural cues into likely and less likely results; the reading stays with the researcher.

## What It Searches

Version 2.0 adds Cyber Seodang and the selected DocuSky temple-story database alongside ITKC, NDL and CBETA. Cyber Seodang also returns source-linked candidates whose body text is unverified. MPIWG gazetteers use selected-section local import, not online full-collection search.

| | Where the text is | What you get |
|---|---|---|
| **Your own texts** | A folder on your computer: UTF-8 `.txt` and `.md` files, works imported from the Chinese Text Project (CTP/CText, ctext.org) with your own API key (saved as `.txt`), and the `.texts.jsonl` bundles the program writes when it converts institute originals downloaded from Korea's public data portal (National Institute of Korean History, Institute for the Translation of Korean Classics) | The text of each file is searched in full; the search covers what the files contain |
| **Online excerpt search** | Korean Classics DB (ITKC: its original-text collections Classical Texts and Korean Literary Collections; translated collections such as the Annals of the Joseon Dynasty, the Seungjeongwon ilgi and the Ilseongnok are not searched), National Diet Library of Japan (NDL, OCR text) and the CBETA Chinese Buddhist Canon; an Internet connection is needed | Passages each source returns around a hit, often cut at both ends: the first 50 to 100 records, books or juan per source or collection |

Online results are passages the sources return, with limits per source and collection, so an empty result does not show that a pattern never occurs. Before citing an example, open its source link and check the full passage.

## Getting Started

### Windows (Executable)

1. Go to the [v2.0 Release Page](https://github.com/cobari/CPE-release/releases/tag/v2.0) and download the Windows x64 executable [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v2.0/cpe-gui.exe).
2. Double-click `cpe-gui.exe` to open the search window. Neither Python nor uv is required.
3. The executable uses the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). If the application does not start, install the WebView2 Runtime from the link.

> **Note**: The standalone `cpe-gui.exe` download contains only the graphical search window. It does not include command-line tools or pattern files. If you need the command-line interface (CLI), please use the full distribution package (source distribution) below.

### macOS and Linux (Distribution Launchers)

The Windows executable does not run on macOS or Linux. Please use the full distribution package:

1. Download the source package (`Source code .zip` or `.tar.gz`) from the [v2.0 Release Page](https://github.com/cobari/CPE-release/releases/tag/v2.0) and unpack it.
2. Keep the entire unzipped folder intact and run the appropriate launcher:
   - **macOS**: Double-click `cpe-gui.command`. (Finder opens it in a Terminal window. On the first launch, if prompted with a security warning, right-click the file and choose "Open".)
   - **Linux**: Run `cpe-gui.sh`. (Select "Run in terminal" in your file manager or run it from a shell.)
   - If the launcher does not run from your file manager, open a terminal in the unzipped distribution folder and run `sh cpe-gui.sh`.
3. If `uv` is not found on your system, the launcher offers to install it automatically via its official installer (type `y` to accept).
4. The first run automatically downloads the required Python environment and runtime dependencies (an Internet connection is required).

---

## Quick First Search (Online Database)

1. Open **Sources** and check the folders or online sources to search. You may choose up to four in total.
2. Click the book icon and application title to return to the search screen.
3. Enter your search terms and click Search. Online sources need an Internet connection; results cover only what the provider returns.

---

## Preparing Texts (Local Folder)

- In **Sources**, add a folder of UTF-8 `.txt`, `.md` or imported `.texts.jsonl` texts. Four separate folder slots are available; removing a slot does not delete its files.

- Open **Import**, choose the folder to receive new texts, then **Import original texts**. The import destination is separate from the folders checked for searching. If needed, the program asks for a destination when you save.

- Choose an institute release, CTP, Project Gutenberg, NDL, CBETA/NIKH XML or MPIWG gazetteer. Address-entry forms link to the source website. CTP permits trying a chapter without a key; a whole work's chapter list requires your CTP key, and access remains the provider's decision.

- Where available, click **Preview**, inspect the source text, choose the parts to keep, record the edition and check the review box. **Download** saves online material; **Import** brings in a file already on your computer. Unsupported units are excluded with reasons. The header × closes the dialog and remains visible while its body scrolls.

---

## Search Features and Practical Guidelines

### Search Modes

- **Compound search (default)**: Finds sentences where the first element appears before the second element, with optional text between them. Use ⇄ to swap elements.
- **Single search**: Finds sentences containing specified terms. Supports Boolean `AND`, `OR`, `NOT`, exact `"phrase"`, and character distance limit `NEAR/n`.

### Search Sources (Up to 4 Concurrently)

- **My text folder**: Searches your local plain-text corpus.
- **Korean Classics DB (ITKC)**: Searches classical originals online. Returns excerpts of about 150 characters around hits (cut at both ends), checking up to the first 100 records per collection.
- **National Diet Library, Japan (NDL)**: Searches OCR texts of classical books (text recognized from book images). **Requires at least one element of two or more characters** (single characters like `以` and `爲` alone cannot be queried on NDL). Returns unpunctuated OCR excerpts (recognition errors may exist), checking up to the first 50 books.
- **CBETA Chinese Buddhist Canon**: Searches the Buddhist canon online. Returns excerpts of about 40 characters on each side, checking up to the first 50 juan.

Online sources return **excerpts**, not full book texts, and query limits apply per source and collection (50–100 records). Therefore, **"No results" does NOT prove that a pattern never occurs in that historical corpus.** Before citing any example in scholarship, always click the title link in the result card to verify the primary source image or full passage.

---

## Reading and Interpreting Results

Each candidate appears in a card displaying the matched sentence alongside context within the range present in the source text or excerpt.

- **Compound search slots**:
  - **Slot A**: The text intervening between the first and second elements.
  - **Slot B**: Candidate text following the second element (not a single grammatically fixed boundary; several candidate spans may be listed).
- **Likely results vs Less likely results**:
  - In Compound search, the tool groups results into two sections based on structural heuristics (e.g. elements are adjacent with no A, a nearer second element was skipped, or A crosses clause punctuation). In Single search, all matched items appear in a single **Results** group.
  - **This categorization is not a grammatical verdict.** Sentences classified as "less likely" may still contain linguistically significant historical usage. Direct critical reading of context by the researcher remains essential.
- **Note**: The GUI window does not provide a button to export CSV files. If tabular data files are needed for research analysis, use the command-line interface below.

---

## Advanced: Running from Source, Metadata, and Command Line (CLI)

Scholars and developers working from a shell can use the full package with `uv`. For manual installation of `uv`, refer to the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Running the GUI from Source

```bash
uv sync --locked
uv run --locked cpe gui
# Or open a text folder directly (replace path/to/texts with your folder path):
uv run --locked cpe gui path/to/texts
```

### Bibliographical Metadata Sidecar (Optional)

You may place a sidecar file next to any text file to record metadata (for example, for a text file `my-text.txt`, name the metadata file `my-text.txt.manifest.toml` by appending `.manifest.toml` to the actual text filename). Optional fields include `title`, `edition`, `era`, `year`, and `year_end`. If omitted, the filename serves as the title, and omitting date fields has no adverse effect on searching.

### Command-Line Interface (CLI)

CLI commands export CSV, JSONL, and run metadata files. Replace `my-text.txt` with your actual file path:

```bash
# View general options
uv run --locked cpe --help

# Run pattern matching on an individual UTF-8 text file
# (--out results is a file prefix generating results.csv, results.jsonl, and results.run.json)
uv run --locked cpe match patterns/mvp.toml my-text.txt --out results

# Search ITKC via command line
uv run --locked python itkc_search.py --help
```

---

## Frequently Asked Questions (FAQ)

**Q1. My text files do not open or cannot be read.**

A. This program reads only plain text files (`.txt`, `.md`) encoded in UTF-8. Renaming Word (`.docx`), HWP, or PDF files to `.txt` does not convert them into plain text. Please save your documents as UTF-8 plain text files from their respective applications.

**Q2. No results are returned from my search.**

A. Check the chosen sources and folder loading in **Sources**. NDL needs at least one term of two or more characters. Try a simpler query; no results do not prove historical absence.

**Q3. An error occurs during online searches.**

A. Check your connection and the source's error message. The application's per-session request limits protect providers; they are not statements of the provider's own policy. Complex online queries also depend on which required search terms can be sent to that provider. You can download permitted originals and search them locally.

**Q4. The Windows executable (cpe-gui.exe) fails to open.**

A. The distributed `cpe-gui.exe` is built for 64-bit Windows (x64). If double-clicking produces no response or an error, ensure that the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) is installed on your system.

---

## Distribution and License

- This repository contains the runtime package, translation catalogs, web assets, launchers, runtime dependency lock, and license.
- The `docs/` folder holds the project's web pages, one for each interface language, and `discoverability.md` records how the repository and its pages are described.
- Package version: `2.0.0`; Git tag: `v2.0`. Exact source snapshot recorded in `release.json`.
- Released under the MIT License (`LICENSE`).
- An independent personal project unaffiliated with ITKC. To respect copyright and terms of use, no proprietary database corpus, modern translation, or raw API response is bundled.
