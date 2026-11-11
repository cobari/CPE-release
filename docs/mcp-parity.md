# CPE 3.1 & MCP Feature Equivalence Matrix

This document defines the functional equivalence between **Classic Pattern Explorer (CPE) 3.1**
(desktop GUI webview and CLI engine) and the **expanded local MCP server** (`cpe.mcp`).

The CPE 3.1 MCP server exposes 20 dedicated tools that achieve full functional parity with
CPE 3.1's core analytical, retrieval, ingestion, and export workflows while maintaining
host-selected path capabilities, existing provider safeguards, and philological invariants. Parity means exposing the existing research operations through structured tools; it does not remove their source-specific restrictions or certify live providers.

---

## 1. Feature Equivalence Matrix (Category A: Research Functions)

The numbering below preserves the exact baseline established in the independent parity inventory (`A.01`–`A.20`):

| ID | Research Function | CPE 3.1 GUI / CLI Origin | MCP 3.1 Tool Implementation | Parity Status | Evidence & Functional Invariants |
|---|---|---|---|---|---|
| **A.01** | **Two-Box Compound Search** | `cpe/gui/service.py:search_pair`<br>`cpe/cli.py:314` (`pair_spec`) | `search_pattern(mode="pair", ...)`<br>`search_sources(mode="pair", ...)` | **Full Parity** | The same GUI pair defaults; CLI range constraints and sentence/clause scope are available through run_cli_export(mode=pair). Groups matches into `likely` and `less`. |
| **A.02** | **Single-Box Word Search** | `cpe/gui/service.py:search_words`<br>`cpe/cli.py:53` | `search_pattern(mode="words", ...)`<br>`search_sources(mode="words", ...)` | **Full Parity** | Single-box boolean queries (`AND`, `OR`, `NOT`/`-`, exact `"phrase"`, proximity `NEAR/n`, and `/regex/`). Validates against provider query grammar limits. |
| **A.03** | **Multi-Pattern DSL Matching** | `cpe/cli.py:288` (`_match`)<br>`cpe/match/run.py:match_files` | `search_dsl(source_id, expression, pattern_file)` | **Full Parity** | Executes full Literary Sinitic pattern DSL queries using inline expressions or multi-pattern TOML files via `cpe.mcp.patterns.search`. Serializes exact `cpe.result/1` JSONL records. |
| **A.04** | **Text Normalization Engine** | `cpe/normalize/options.py`<br>`cpe/match/engine.py:Settings` | `configure_workspace(normalization=...)`<br>`cpe/mcp/runner.py` startup flags | **Full Parity** | Full runtime and startup control over variant character mappings (`variants.toml`), variation selectors (`keep`/`delete`), whitespace, line breaks, and interlinear notes (`barrier`/`join`). |
| **A.05** | **Local Corpus Ingestion** | `cpe/local/txt.py`<br>`cpe/gui/service.py:Library.load` | `inputs` argument<br>`manage_library(action="load", ...)` | **Full Parity** | Scans UTF-8 `.txt`, `.md`, and `.texts.jsonl`. Honors `NAME.txt.manifest.toml` metadata (title, era, year range, edition). Excludes dot-prefixed entries and directory symlinks/junctions; existing readable file-symlink behavior is preserved within allowed roots. |
| **A.06** | **Multi-Slot Local Workspace** | `cpe/gui/app.py:LOCAL_SOURCES`<br>`local_libraries()`, `activate_local()` | `manage_library(action="activate"/"load", source_id="local2", ...)` | **Full Parity** | Provides 4 isolated local library slots (`local`, `local2`, `local3`, `local4`) with independent root directories and error states managed by `Workspace`. |
| **A.07** | **Dynamic Library Reload** | `cpe/gui/app.py:reload_local` | `manage_library(action="reload", source_id=...)` | **Full Parity** | Re-scans open slot folders for file additions, modifications, or deletions without server restart, invalidating in-flight work while preserving already-published immutable search IDs and active slot configurations. |
| **A.08** | **Repository 1: ITKC One-Shot Search** | `cpe/itkc/adapter.py:fetch`<br>`cpe/gui/online.py:search_itkc` | `search_pattern(source_id="itkc", ...)`<br>`search_sources(source_ids=["itkc"], ...)` | **Full Parity** | One-shot search across classical originals (`GO_AA`, `MO_AA`, `IT_AA`). Enforces mandatory local logging (`--itkc-log`), rate limit (>= 1.0 s), and session call cap. |
| **A.09** | **ITKC Staged Collection Sessions** | `cpe/gui/app.py:start_itkc_search`<br>`advance_itkc_search`, `itkc_search_page` | `itkc_search(action="start"/"advance"/"page"/...)` | **Full Parity** | Multi-stage session manager advancing one collection at a time (`GO_AA` → `MO_AA` → `IT_AA`) on demand with immutable revisions and sorted page retrieval. |
| **A.10** | **Repository 2: NDL Search** | `cpe/ndl/adapter.py:fetch`<br>`cpe/gui/online.py:search_ndl` | `search_pattern(source_id="ndl", ...)` | **Full Parity** | National Diet Library search on longest term (>= 2 chars) with local excerpt matching. Enforces rate limits and schema validation. |
| **A.11** | **Repository 3: CBETA Search** | `cpe/cbeta/adapter.py:fetch`<br>`cpe/gui/online.py:search_cbeta` | `search_pattern(source_id="cbeta", ...)` | **Full Parity** | CBETA API search for longest term with local structure matching. Rejects invalid schemas/counts (`num_found`). |
| **A.12** | **Repositories 4–18: Online Sources** | `cpe/gui/online.py`<br>the shared provider adapters | `search_pattern(source_id=...)`<br>`search_sources(source_ids=[...])` | **Full Parity** | Exposes all 15 remaining online sources: Cyber Seodang, DocuSky, HistoryDB, Sillok, NRICH, ITH, NAHF, Viet Dien, SAT, AKS Archive, SHIPS, THDL, CSID, CAND, and Kyujanggak via `SourceRegistry`. |
| **A.13** | **Provider Catalogs & Profiles** | `cpe/gui/app.py:docusky_catalog`<br>`historydb_catalog`, `nahf_catalog` | `get_source_catalog(source_id=...)` | **Full Parity** | Pages dynamic catalogues and profiles: the existing reviewed/unreviewed DocuSky profiles and layer rules, HistoryDB items, and NAHF categories. Catalog IDs page a cached response without repeated provider calls (8 catalogs, 15-minute idle TTL, 8 MiB separate serialized budget). |
| **A.14** | **Result Sorting (Source vs Era)** | `cpe/gui/app.py:save_sort`<br>`cpe/gui/search_session.py:sorted_rows` | `reorder_search(search_id, sort=...)`<br>`search_pattern(sort=...)` | **Full Parity** | Sorts candidate rows by original `source` order, chronological date (`era` oldest first), or reverse chronological date (`era-desc` newest first). Undated rows remain last. |
| **A.15** | **Pagination & Result Snapshots** | `cpe/gui/static/app.js` (DOM paging)<br>`cpe/gui/search_session.py:page` | `get_search_results(search_id, cursor)` | **Full Parity** | Provides snapshot sessions (bounded to 8 sessions, 15 min TTL, 64 MiB shared serialized-retention budget, 256 KiB response budget) with opaque cursor navigation without re-querying providers. |
| **A.16** | **Batch ZIP Import & Staging** | `cpe/local/batch.py`<br>`cpe/gui/service.py` | `import_local_release(path, batch=True)` | **Full Parity** | Stages multi-work ZIP archives into validated bundle folders with cycle protection, collision detection, root isolation, per-work locks, collision preflight and recovery on partial publication failure. A multi-work import is not a filesystem-wide atomic transaction. |
| **A.17** | **CTP Import Pipeline** | `cpe/gui/app.py:import_ctp`<br>`save_ctp_key`, `ctp_key_set` | `import_remote_release(provider="ctp", ...)` | **Full Parity** | Retrieves chapters/works from ctext.org, formats structural manifests, and writes into active library slot. API key resolved securely via `--ctp-key-env`. |
| **A.18** | **External Preview & Ingestion** | `cpe/gui/app.py:start_external_preview`<br>`external_progress`, `import_external` | `preview_import(...)`<br>`manage_import(action="progress"/"unit"/"commit", ...)` | **Full Parity** | Asynchronous retrieval pipeline for Gutenberg, NDL, RISE, CBETA, and NIKH: progress polling, unit inspection, verification report generation, and researcher-confirmed import. |
| **A.19** | **Dataset & Release Installation** | `cpe/gui/app.py:datasets`<br>`import_dataset`, `import_releases` | `get_import_catalog(provider="portal")`<br>`import_remote_release(provider="portal", ...)` | **Full Parity** | Discovers and installs listed public corpus release archives directly into local library slots. |
| **A.20** | **Export Pipeline (CSV & JSONL)** | **CLI only**: `cpe/cli.py`<br>`cpe/export/csv_out.py`<br>`cpe/export/jsonl.py` | `run_cli_export(...)`<br>`export_search_snapshot(...)` | **Full Parity** | Direct CLI parity: exports `.csv`, `.jsonl`, and `.run.json` with the existing quoted CSV formula guard. Canonical JSONL preserves cpe.result/1 or cpe.occurrence/1. Snapshot export uses distinct cpe.mcp.snapshot-row/1 and cpe.mcp.snapshot/1 schemas and preserves reference-only rows without inventing evidence. Existing outputs require force=True to replace; loaded source inputs are protected. |

---

## 2. Category B: Presentation-Only Desktop Webview Features (Excluded from Parity)

These features belong strictly to the native graphical user interface (`cpe/gui/static/` and pywebview) and do not represent semantic research data or analytical capability:
1. **Desktop Window Management**: `cpe/gui/app.py:open_window`, `set_title`, `home_icon` base64 favicon delivery, pywebview bridge lifecycle (`window.pywebview.api`).
2. **GUI Internationalization Menus**: 6-language UI catalog selection (`ko`, `ja`, `zh-Hans`, `zh-Hant`, `vi`, `en`), `save_language`, `saved_language`, `languages()`, `catalog(code)`. (Note: Source text is never converted by UI language).
3. **Interactive UI Shell & Layout**: 2×2 source comparison grid, responsive CSS containers, search guide popover icons, input box focus transitions, keyboard shortcuts.
4. **Native OS File Pickers & System Links**: Native desktop directory dialogs (`pick_folder`, `choose_folder`, `pick_external_xml`) and default browser launcher (`open_link`).

---

## 3. Category C: Developer-Only & Experiment Tooling (Excluded from Parity)

These tools are developer/maintainer utilities and research probe experiments rather than end-user analytical services:
1. **Matcher Evaluation Harness (`cpe eval`)**: `cpe/cli.py:211`, `cpe/eval/report.py`. Benchmarks pattern candidates against gold JSONL datasets, calculating recall with Wilson score intervals and slot precision.
2. **ITKC Server Probe Suite (`cpe probe`)**: `cpe/cli.py:226`, `cpe/itkc/probe.py`. Plans and runs empirical experiments E0–E7 on live ITKC API endpoints with call budgets and dry-run reporting.
3. **Packaging & Build Scripts**: `scripts/build_exe.py`, `scripts/build_mcp.py`, `hatch_build.py`. Standalone PyInstaller console/GUI executables.

---

## 4. Architectural Invariants for MCP Parity

The MCP server adheres strictly to the project's core research invariants:

1. **Philological Primacy (Literary Sinitic on Its Own Terms)**:
   - Modern Chinese grammar, punctuation habits, or dictionaries never dictate Literary Sinitic segmentation or pattern matching.
   - Truncated excerpt boundaries always append an ellipsis `…`, even after editorial punctuation (Spec 11).
   - Candidate evidence levels (`likely` vs `less`) are never inflated.
2. **Research Data Safeguards**:
   - Zero-result responses are never inferred from malformed provider envelopes; unknown schemas return explicit error codes (`source_failed`).
   - Legitimate explicit zeroes (`count == 0` or empty results at valid offsets) are preserved.
   - Existing per-adapter intervals, query constraints and session call caps are preserved and shared across tool calls; no additional provider-wide coverage is implied.
3. **Security & System Safety**:
   - Directory scans and batch imports prevent symlink/junction path escapes and cyclic traversals.
   - CSV outputs use the existing excel_safe guard inside quoted fields for formula-like cells, including leading whitespace and fullwidth variants. This is not a universal spreadsheet safety claim; JSONL source strings remain unchanged.
   - Tool operations are strictly bounded by `--read-root` and `--write-root`.

---

## 5. Limitations & Current Verification Boundary

1. **Version Literal**: Version `3.1` candidate sharing the exact same working-tree core as the desktop GUI and CLI.
2. **Unpublished Candidate**: The server is a local stdio binary and module; it is not published on PyPI, packaged as an MCPB archive, or registered in the official Model Context Protocol Registry.
3. **Test Boundary**: Verified through automated protocol, unit, and regression test suites. Not certified on live proprietary AI clients (Claude Desktop, Claude Code) or non-Windows hosts.
4. **Third-Party Providers**: Online searches and remote imports require Internet access and agreement with third-party institutional terms. Tests never call live provider endpoints.
