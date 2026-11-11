# Classic Pattern Explorer 3.1

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [Tiếng Việt](README.vi.md)

Classic Pattern Explorer is a desktop application and command-line tool for finding sentence-pattern candidates, such as 以…爲 or 雖…而, in Literary Sinitic source texts (漢文, often called Classical Chinese). Each candidate is shown with the context its text or the source's excerpt provides and, where the source gives one, a link back to it, so you can read and judge it yourself. The window is available in Korean, Japanese, Simplified Chinese, Traditional Chinese, Vietnamese, and English; changing the interface language never converts or alters the source text.

It is a digital humanities research aid, not a grammar oracle or syntactic parser. It collects candidates, and in compound search it sorts them by structural cues into likely and less likely results; the reading stays with the researcher.

## What It Searches

Version 3.1 organizes online connections into five browsing regions covering 18 online sources, alongside local text folders. Online sources return bounded excerpts or catalogue references according to provider query limits.

| | Where the text is | What you get |
|---|---|---|
| **Your own texts** | A folder on your computer: UTF-8 `.txt` and `.md` files, works imported from the Chinese Text Project (CTP/CText, ctext.org) with your own API key (saved as `.txt`), and the `.texts.jsonl` bundles the program writes when it converts institute originals downloaded from Korea's public data portal (National Institute of Korean History, Institute for the Translation of Korean Classics, Academy of Korean Studies, etc.) | The text of each file is searched in full; the search covers what the files contain |
| **Online excerpt and catalogue search** | 18 online sources across five browsing regions (Korea 8, Japan 4, China 1, Taiwan 4, Vietnam 1); an Internet connection is needed | Passages or catalogue records returned by each source around a hit, often cut at both ends, within provider query limits |

Online results are passages or records returned by provider endpoints within query limits. An empty result does not show that a pattern never occurs in that historical corpus. Before citing an example, open its source link and check the full passage.

## Getting Started

### Windows (Executable)

1. Go to the [v3.1 Release Page](https://github.com/cobari/CPE-release/releases/tag/v3.1) and download the Windows x64 executable [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v3.1/cpe-gui.exe).
2. Double-click `cpe-gui.exe` to open the search window. Neither Python nor uv is required.
3. The executable uses the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). If the application does not start, install the WebView2 Runtime from the link.

> **Note**: The standalone `cpe-gui.exe` download contains only the graphical search window. It does not include command-line tools or pattern files. If you need the command-line interface (CLI), please use the full distribution package (source distribution) below.

### macOS and Linux (Distribution Launchers)

The Windows executable does not run on macOS or Linux. Please use the full distribution package:

1. Download the source package (`Source code .zip` or `.tar.gz`) from the [v3.1 Release Page](https://github.com/cobari/CPE-release/releases/tag/v3.1) and unpack it.
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

You can select up to four sources at once from your local text folders and 18 online sources grouped into five browsing regions:

- **Text folders**: Reads the .txt and .md files (UTF-8 text) and .texts.jsonl text bundles in the chosen folder and all its subfolders. A file NAME.txt.manifest.toml next to a text can give its title, punctuation conventions and date (era = "唐", year = 618, year_end = 907); otherwise the file name is the title. Other formats, such as Excel, are not read.
- **Korea (8)**:
  - **Korean Classics DB (ITKC)**: Searches ITKC's classical originals (Classical Texts, the Korean Literary Collections, and Ilseongnok) online. ITKC returns an excerpt of about 150 characters around the match, cut at both ends, so results show only what the excerpt contains; the title opens the record on ITKC. The first 100 records per collection are checked. Nothing is stored.
  - **Cyber Seodang (Dongyang Classics DB)**: Searches the Dongyang Classics DB (Cyber Seodang) of the Institute of Traditional Culture via its Open API. The server returns records with titles, descriptions, and article links. Results display the returned text with links to the original articles on Cyber Seodang; full body text and context are not guaranteed, and the returned records do not establish whether a field is original text or translation, so unverified records remain candidates. Up to 50 records are checked. Nothing is stored.
  - **Korean History Database integrated search**: Searches the NIKH Korean History Database public search window by era and database item. Sends one word or phrase and reads only the first page, up to 20 rows, of the original-text tab. Titles, excerpts, and provider links are shown; there is no pagination, article-page request, or storage. Candidate references, not a full-text pattern match. Personal non-commercial research.
  - **Annals of the Joseon Dynasty, NIKH search**: Searches the National Institute of Korean History's public Sillok search window with one word or phrase. The first page of original-text results, up to 50, is read; titles, excerpts, and links to the article pages are shown. This is a candidate search, not a full-text pattern match; the excerpt is truncated and results come from the provider's original-text tab. One query is sent at a time; later result pages are not requested, and nothing is stored. Personal non-commercial research.
  - **NAHF Northeast Asian History Net search**: Searches one collection of the Northeast Asian History Foundation's public source library with one word or phrase. The first page of original-text results, up to 20 rows, is read; titles, excerpts, and provider links are shown. The main search page gives counts only, so choose a collection first. This is a candidate search, not a full-text pattern match. No pagination, article-page requests, or storage. Personal non-commercial research.
  - **NRICH · Wooden-tablet transcriptions**: Loads up to 600 public wooden-tablet records and searches their supplied transcriptions, including Idu. This loaded set is not the full database and is reused in later searches. Results are candidate references whose characters match your input; transcription notation is kept as supplied. Confirm pattern suitability from the original material; it is not judged automatically. The first load may take a little while. Personal non-commercial research only. Loaded material is not automatically saved to files.
  - **AKS Korean Historical Manuscript Archive**: Searches the Academy of Korean Studies manuscript archive at archive.aks.ac.kr, one word or phrase at a time. Only the first page of the original-text group, up to 20 rows, is read; each result shows the manuscript title, a highlighted transcription excerpt, a date if supplied, and a link to the archive's record page. The excerpt is cut at both ends and keeps the source notation; image viewers and record bodies are not fetched. The sample inspected overlaps zero titles with the 561 local Wikisource manifests, but overlap with the connected institute holdings is unmeasured. Nothing is stored. Content rights: the archive footer carries a CC BY-NC-ND 2.0 KR badge and an All Rights Reserved notice; use is personal non-commercial and no excerpt should be redistributed. Record pages offer a citation-copy control.
  - **Kyujanggak historical documents · originals**: Searches Seoul National University's Kyujanggak historical-document database (GMS), one keyword without spaces, up to 80 characters. Reads only the first 20 references from one native response. Shows titles, document numbers, parties, source dates, types and available inline original-text excerpts, at most 600 characters. Some results supply metadata only; lazy content is not requested. Transcriptions can contain Idu, uncertainty and editorial notation; language and sentence boundaries are not determined. No grammatical match or grade is assigned. Titles open the official original-text viewer. Old books, chronicle collections, royal protocols and modern-government groups are outside this route. Overlap with other institutional document holdings is unmeasured. No next page, additional article text, image or translation is fetched or stored. Personal non-commercial research only; retain Kyujanggak and document attribution, with no redistribution.
- **Japan (4)**:
  - **National Diet Library, Japan (NDL)**: Searches OCR text from NDL's classical books (NDL Lab). NDL requires a phrase of at least two characters. CPE sends the longer element or longest term and checks the remaining conditions in the supplied excerpts. OCR may contain recognition errors; excerpts have no punctuation and may be cut at both ends. Check the page image through the title link before citing. Only the first 50 books are checked. Nothing is stored.
  - **SAT Daizōkyō Text Database**: Searches the University of Tokyo SAT Daizōkyō Text Database with one word or phrase. Only the first group of up to 30 line hits is read; each result shows the Taishō page and line range, a keyword-in-context excerpt and a detail link. The excerpt is cut at both ends, and image-only glyphs are kept as □. SAT's Taishō text overlaps the connected CBETA canon, so this route adds line references rather than new holdings. Terms distinguish scholarly or nonprofit text use; image collections are separate. No pagination, detail-page fetch or storage.
  - **University of Tokyo Old Japanese Diaries · official search**: Opens the University of Tokyo Historiographical Institute's public 古記録フルテキストデータベース (full-text database of historical diaries) in your browser. It searches diary texts in kanbun, including Teishinkoki and Kyureki, across the database. Use one keyword without spaces. The official page provides excerpts, source dates, volume and page references, and available image links. Editorial notes and uncertain characters remain as supplied. This connection does not fetch, store or verify results inside CPE. Scholarly use only; retain attribution and follow the Institute's restrictions on redistribution.
  - **CanD Chinese and Japanese poetry · references**: Searches the body field of Hankeidou's privately edited CanD poetry database across its collections, including Japanese anthologies. Uses one keyword of up to 10 characters without spaces or wildcards, with provider variant expansion OFF. Reads one native response and displays its first 20 bibliographic headings, retaining titles, authors and native source locators. The response also contains poems and Japanese readings; CPE discards those bodies and commentary. No sentence, literal occurrence or grammatical match is verified. Titles open the provider's edition overview, not an individual poem. Returned characters remain unchanged; the provider edits glyphs and punctuation. Chinese works partly overlap existing sources; institution-wide overlap is unmeasured. No pagination, separate text request or storage. Personal non-commercial research only; retain CanD and edition attribution and respect the provider's copying restrictions.
- **China (1)**:
  - **Zhejiang University Chinese Stone Inscription Database · transcription search**: Searches the transcription field of Zhejiang University's Chinese Stone Inscription Database across its seven default collections. Uses the provider's public HTTP search, one keyword without spaces, and reads only the first 15 references. The list supplies titles, dynasties, source dates, object types and catalog numbers, without transcription excerpts. CPE does not verify a character, sentence or grammatical match. Open a title to inspect the official record. Returned duplicate references remain visible. The site requests traditional characters; CPE sends your characters unchanged. Holdings include later material and are not all established as Literary Sinitic. No next page, article body or image is fetched or stored. Personal non-commercial research only; retain Zhejiang University and record attribution.
- **Taiwan (4)**:
  - **CBETA Chinese Buddhist Canon**: Searches the CBETA Buddhist canon online (CBData API). CPE sends the longer element or longest term. CBETA supplies about 40 characters on each side of each hit; CPE checks the remaining conditions in those excerpts. Interlinear notes are excluded. Only the first 50 juan are checked; titles open the corresponding juan in CBETA Online. Texts: CBETA Chinese Electronic Tripitaka Collection, CC BY-NC-SA 4.0 (non-commercial use, attribution, share alike). Nothing is stored.
  - **DocuSky · 佛寺志靈應故事**: Searches the 佛寺志靈應故事 public database on DocuSky: selected stories from 普陀洛迦新志 and 清涼山志, not the complete books. One element or word is sent; the first 50 returned records are checked here. Only isolated source paragraphs are searched; research tags and unsupported body text are excluded. Titles open the project page. Compiler: 曹德啓; data: 中華佛學研究所; platform: 臺灣大學數位人文研究中心. Personal, non-commercial research use under the provider's terms. Nothing is stored. The catalog also lists duplicate entries, metadata databases, and modern-language or translated databases. Only the reviewed database's body text has been inspected; every other entry is marked unreviewed.
  - **Academia Sinica ITH Taiwan Full-text Database**: Searches the Institute of Taiwan History's public Taiwan Full-text Database with one word or phrase. The first result page is read; candidate titles, excerpts, and provider links are shown. This is a candidate search, not a full-text pattern match. No pagination, article-page requests, or storage; cite the provider.
  - **Taiwan History Digital Library · Old Deeds**: Searches THDL's Old Deeds collection with one keyword without spaces, across document text and metadata. Reads only the first ten results, keeping returned characters, excerpts and source references. Excerpts can contain separated contexts, uncertain characters and editorial material; these are candidate references, not verified sentences or grammatical matches. A single-result page may supply more text; CPE displays at most 600 characters. Source titles open the official document. No pagination, separate article request or text storage. Ming/Qing administrative archives and Tan-Hsin files are outside this connection. Cite THDL and each document's original source; personal non-commercial research only, with no redistribution.
- **Vietnam (1)**:
  - **Việt Điện Hán-Nôm OCR corpus**: Searches the public Việt Điện OCR search of the Digitizing Vietnam Hán-Nôm corpus, one word or phrase at a time. Only the first page, up to 20 page-level hits, is read; each result shows the OCR excerpt and opens the page viewer on Việt Điện. The OCR is machine-generated and uncorrected, and the provider also matches other glyph forms and simplified/traditional variants, so a returned hit is a candidate reference unless the excerpt contains the exact characters you entered. Page and item metadata may be missing or uneven; image and record rights are separate from the OCR research-use statement. Nothing is stored.

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

### Local MCP Server (AI Integration)

The unpublished 3.1 candidate includes an optional local stdio MCP (Model Context Protocol) server based on the official Python SDK v2. It exposes 20 structured tools covering the 4 local library slots, all 18 online adapters, provider catalogues, staged ITKC collection sessions, full Literary Sinitic pattern DSL search, safe corpus imports, and direct CSV/JSONL exports.

```sh
# Start the MCP server on stdio and grant local source text
uv run --extra mcp cpe mcp path/to/texts --allow-source-text local
```

The default is references and metadata only. To return local source text, the host must add `--allow-source-text local` after its own rights review. Online access and imports are separate: `--enable-online` adds provider access and `--enable-import` adds import access, but neither returns source text unless the matching `--allow-source-text SOURCE` grant is also present. There is no `all` grant. A grant is not a provider licence and cannot guarantee that cloud processing, model answers or retention are lawful. See [MCP installation, options, and tools](docs/mcp.md) and the [feature equivalence matrix](docs/mcp-parity.md).

Standalone Windows packages also include cpe-mcp.exe, which accepts the same startup arguments. The server runs over standard input/output (stdin/stdout) with no GUI window or HTTP server. This candidate implementation does not claim certification on live proprietary AI clients (e.g. Claude Desktop, Claude Code) or live third-party services.

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
- Package version: `3.1`; Git tag: `v3.1`. Exact source snapshot recorded in `release.json`.
- Released under the MIT License (`LICENSE`).
- An independent personal project unaffiliated with ITKC. To respect copyright and terms of use, no proprietary database corpus, modern translation, or raw API response is bundled.

## Feedback and requests

[Feedback and requests (Google Forms)](https://docs.google.com/forms/d/e/1FAIpQLSfcg9NjJil-pxO_e833Xjzo-sVar1YDY8hRIkunBhEZmtPn2A/viewform)
