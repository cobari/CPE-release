# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer searches Literary Sinitic source texts for pattern candidates (such as 以…爲 or 雖…而) and displays each candidate sentence alongside its surrounding context in a desktop graphical window or from the command line. The interface supports Korean, Japanese, Simplified Chinese, Traditional Chinese, and English. Changing the interface language never modifies or converts source text.

This application is not an automated grammar oracle or syntactic parser. It is a research aid designed to extract candidate sentences from large corpora and organize them using structural cues so scholars can directly read and evaluate the primary context.

## Getting Started

### Windows (Executable)

1. Go to the [v1.0 Release Page](https://github.com/cobari/CPE-release/releases/tag/v1.0) and download the Windows x64 executable [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe).
2. Double-click `cpe-gui.exe` to open the search window. Neither Python nor uv is required.
3. The executable uses the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). If the application does not start, install the WebView2 Runtime from the link.

> **Note**: The standalone `cpe-gui.exe` download contains only the graphical search window. It does not include command-line tools or pattern files. If you need the command-line interface (CLI), please use the full distribution package (source distribution) below.

### macOS and Linux (Distribution Launchers)

The Windows executable does not run on macOS or Linux. Please use the full distribution package:

1. Download the source package (`Source code .zip` or `.tar.gz`) from the [v1.0 Release Page](https://github.com/cobari/CPE-release/releases/tag/v1.0) and unpack it.
2. Keep the entire unzipped folder intact and run the appropriate launcher:
   - **macOS**: Double-click `cpe-gui.command`. (Finder opens it in a Terminal window. On the first launch, if prompted with a security warning, right-click the file and choose "Open".)
   - **Linux**: Run `cpe-gui.sh`. (Select "Run in terminal" in your file manager or run it from a shell.)
   - If the launcher does not run from your file manager, open a terminal in the unzipped distribution folder and run `sh cpe-gui.sh`.
3. If `uv` is not found on your system, the launcher offers to install it automatically via its official installer (type `y` to accept).
4. The first run automatically downloads the required Python environment and runtime dependencies (an Internet connection is required).

---

## Quick First Search (Online Database)

Even if you do not have local text files ready, you can immediately test the application using an online database if an Internet connection is available:

1. Start the program and click the **Settings** button in the upper-right corner.
2. Under **Search in**, check **Korean Classics DB (ITKC)** first, then uncheck **My text folder**, and click the **Close** button at the bottom of the Settings dialog.
3. In the default **Compound search** mode on the main screen:
   - **First element**: enter `以`
   - **Second element**: enter `爲`
4. Click **Search**. Returned candidate sentences from the online database will appear on screen. (Click the ⇄ button to swap the first and second elements.)

---

## Preparing Texts (Local Folder)

To search your own corpus, open Settings and ensure **My text folder** is checked under **Search in**.

### 1. Opening Local Text Files

- Click **Open text folder…** to select a folder containing `.txt` or `.md` files. Only UTF-8 plain text is read. Subfolders are scanned recursively. Non-text formats such as Excel are skipped.
- The window remembers the chosen folder for your next session.

### 2. Downloading Institute Public Releases

If you do not have Literary Sinitic files prepared, you can download public open-access classical texts directly from within the program:

1. In Settings, click **Download institute originals…**.
2. **1. Institute**: Select the National Institute of Korean History (NIKH; Samguk sagi, Goryeosa, Bibyeonsa deungrok, etc.) or the Institute for the Translation of Korean Classics (ITKC; literary collections, miscellaneous records).
3. **2. Release**: Select a dataset, choose a destination in **3. Folder to save in**, and click **Download**.
4. The download and text conversion run in the background. Once finished, the destination folder opens automatically as your active text library.

If a government portal connection fails or presents a CAPTCHA, click **View on the portal** to download the ZIP file manually, then click **Import a ZIP I already downloaded…**.

### 3. Importing from CTP (Chinese Text Project)

- In Settings, click **Import from CTP…** and enter the address of a work or chapter on `ctext.org`. Each chapter will be saved as a `.txt` file for searching.
- You must open a local text folder first. Because CTP restricts most full texts to accounts with an API key, obtain your personal API key from `ctext.org` and save it in the dialog.

---

## Search Features and Practical Guidelines

### Search Modes

- **Compound search (default)**: Finds sentences where the first element appears before the second element, with optional text between them. Use ⇄ to swap elements.
- **Single search**: Finds sentences containing specified terms. Supports Boolean `AND`, `OR`, `NOT`, exact `"phrase"`, and character distance limit `NEAR/n`. (Note: online searches do not support the `OR` operator.)

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

A. Ensure that the intended source (My text folder or an online database) is selected in Settings and that your text folder is loaded. When querying NDL, at least one element must be two or more characters long. Try simplifying your query. A lack of results does not prove that the pattern never occurs in the corpus.

**Q3. An error occurs during online searches.**

A. Check your Internet connection. The limit of 200 requests for online sources (ITKC, NDL, CBETA) is not the policy of the remote sources themselves, but a session safeguard limit enforced by this program; if reached, restarting the window resets the counter. Note also that online sources do not support the `OR` operator.

**Q4. The Windows executable (cpe-gui.exe) fails to open.**

A. The distributed `cpe-gui.exe` is built for 64-bit Windows (x64). If double-clicking produces no response or an error, ensure that the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) is installed on your system.

---

## Distribution and License

- This repository contains the runtime package, translation catalogs, web assets, launchers, runtime dependency lock, and license.
- Package version: `1.0.0`; Git tag: `v1.0`. Exact source snapshot recorded in `release.json`.
- Released under the MIT License (`LICENSE`).
- An independent personal project unaffiliated with ITKC. To respect copyright and terms of use, no proprietary database corpus, modern translation, or raw API response is bundled.
