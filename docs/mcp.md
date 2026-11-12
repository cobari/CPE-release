# Local MCP Server

Classic Pattern Explorer exposes its research corpus search, library management,
import pipelines and export engines to AI clients through a local stdio MCP
(Model Context Protocol) server. The GUI, CLI and MCP server share the same CPE
core version (`3.1`). MCP is an
additional headless interface, not a second matcher or a public web service.
This implementation uses the official
[Python SDK v2](https://py.sdk.modelcontextprotocol.io/).

All 20 tools remain registered, but result payloads are host-gated. The default
is **references and metadata only**: display excerpts, source positions,
canonical records and raw import-unit text are withheld unless the host starts
the server with a matching `--allow-source-text` grant. A grant is not a
provider licence or a legal clearance. See
[MCP data policy and source-text delivery](mcp-data-policy.md).

---

## 1. Installation & Quickstart

Install Python 3.11 or newer and [uv](https://docs.astral.sh/uv/), then run from
the project repository:

```sh
uv sync --extra mcp
uv run --extra mcp cpe mcp path/to/texts
```

The command above is the **breaking default**: searches work, but rows contain
references and metadata rather than source text or canonical records. To show
text from the local `local` slot, add its grant:

```sh
uv run --extra mcp cpe mcp path/to/texts --allow-source-text local
```

Use `local2`, `local3` or `local4` when the folder belongs to one of those slots.
`--allow-source-text` is repeatable and has no `all` value. It accepts the 22
local/online source identifiers, `docusky:PROFILE`, and
`import:gutenberg|ndl|rise|cbeta|nikh-partial`. Online access and import access
are separate decisions; see the examples below.

`cpe-mcp` (when installed or packaged) and `python -m cpe.mcp` accept the same
arguments. The server listens on `stdin` for JSON-RPC protocol messages and writes
protocol responses exclusively to `stdout`. Human-readable diagnostics and logs
go to `stderr`. The process exits cleanly when the client closes `stdin`.

### Client Configuration Template

Below is a **references-only** stdio client configuration (e.g. for Claude Desktop,
Claude Code, or compatible MCP hosts). Replace `/absolute/path/to/classic` and
`/absolute/path/to/texts` with absolute paths on your system:

```json
{
  "mcpServers": {
    "cpe": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/classic",
        "run", "--extra", "mcp", "cpe", "mcp",
        "/absolute/path/to/texts",
        "--enable-online", "itkc",
        "--itkc-log", "/absolute/path/to/itkc-requests.jsonl",
        "--read-root", "/absolute/path/to",
        "--write-root", "/absolute/path/to/output"
      ]
    }
  }
}
```

That configuration returns no local or ITKC source text. Add source grants as
separate arguments; they do not follow automatically from `--enable-online`,
`--enable-import`, `--read-root` or `--write-root`:

```text
--allow-source-text local       # local files in the local slot
--allow-source-text itkc        # ITKC excerpts and canonical records
--allow-source-text import:ndl  # direct NDL import-unit text
```

For a locally built standalone Windows executable (`cpe-mcp.exe`), use its absolute
path as `command` and pass startup flags in `args`. It does not require an active
Python runtime environment. Do not use `cpe-gui.exe` as the MCP command.

---

## 2. Host Authorization & Security Model

To prevent unauthorized file access, remote network abuse, or credential leaks,
the MCP server operates under a strict host authorization model configured at
startup:

| Startup Argument | Default | Description |
|---|---|---|
| `inputs` (positional) | None | Initial UTF-8 text files, folders, or CPE `.texts.jsonl` bundles loaded into `local`. |
| `--library SLOT FOLDER` | None | Assigns specific folders to slots (`local`, `local2`, `local3`, `local4`). |
| `--read-root PATH` | None | Authorizes tool reads under `PATH`. Can be repeated. This is a path capability, not source-text disclosure. |
| `--write-root PATH` | None | Authorizes tool writes/reads under an existing folder `PATH`. Tool writes are disabled unless granted; explicitly configured state and ITKC log files are separate host destinations. This is a path capability, not source-text disclosure. |
| `--enable-online SOURCE` | None | Allows access to an online repository (`all`, `docusky:PROFILE`, or any of the 18 online source identifiers: `itkc`, `ndl`, `cbeta`, `cyberseodang`, `docusky`, `historydb`, `sillok`, `nrich`, `ith`, `nahf`, `vietdien`, `sat`, `aksarchive`, `ships`, `thdl`, `csid`, `cand`, `kyudb`). Repeat as needed. Access does not return source text; add a separate `--allow-source-text` grant. |
| `--allow-source-text SOURCE` | None (references only) | Host permission to return display text, provider excerpts and canonical records for one source or import. Repeat; no `all`. Accepts `local`–`local4`, the 18 online source IDs, `docusky:PROFILE`, or `import:gutenberg|ndl|rise|cbeta|nikh-partial`. This is not a provider licence. |
| `--itkc-log PATH` | None | Required local request log path whenever `itkc` is enabled. |
| `--enable-import PROVIDER`| None | Allows import providers (`portal`, `ctp`, `gutenberg`, `ndl`, `rise`, `cbeta`, `nikh-partial`, or `all`); network ones need Internet access, while CBETA and NIKH need host-readable XML files. Previews and progress metadata do not require a source-text grant; direct unit text does. |
| `--workspace-state PATH` | Memory | Dedicated, locked MCP JSON state file outside corpus folders and source sidecars. Desktop GUI preferences are never used automatically. |
| `--ctp-key-env VAR` | `CPE_CTP_API_KEY` | Environment variable name holding the CTP API key. Keys are never passed via tool parameters. |
| `--language CODE` | System | Saved interface language setting; MCP descriptions and error codes remain English (`ko`, `ja`, `zh-Hans`, `zh-Hant`, `vi`, `en`). |
| `--sort ORDER` | `source` | Default sort order (`source`, `era`, `era-desc`). |
| `--variants TOML` | None | Variant mapping table for normalization. |
| `--variation-selectors CHOICE` | `keep` | Normalization handling for Unicode variation selectors (`keep` or `delete`). |
| `--whitespace CHOICE` | `keep` | Normalization handling for horizontal whitespace (`keep` or `delete`). |
| `--line-breaks CHOICE` | `delete` | Normalization handling for line breaks (`delete` or `keep`). |
| `--notes CHOICE` | `barrier` | Interlinear note treatment during pattern matching (`barrier` or `join`). |
| `--note-markers OPEN CLOSE` | None | Interlinear note delimiter pair. |
| `--annotation-markers OPEN CLOSE` | None | Attached annotation delimiter pair. |

### Source-text disclosure grant

`--allow-source-text SOURCE` is an immutable startup grant, separate from every
path, network and import capability. Without a matching grant, retained rows,
snapshot pages, staged ITKC payloads and snapshot exports are projected to
references and metadata. They add `text_withheld: true` and a
`record_withheld` boolean; `record_withheld` distinguishes a suppressed canonical
record from a reference-only row that never had one. `text_views` is changed to
the withheld form, and a `disclosure` object reports `references_only`,
`legal_permission: not_certified` and `model_location: not_verified`. Its
`rights_review` entry marks review as required for the actual work, provider
conditions, host processing and downstream use, and may carry a primary
`reference_url` plus `reviewed_on` date; that URL and date are not a blanket
authorization.

- Local file text is source text. A loaded folder needs its own `local`,
  `local2`, `local3` or `local4` grant even when `--read-root` already allows the
  path.
- Online text needs both provider access and its own grant. `--enable-online` is
  not a disclosure permission; a `docusky` grant covers its profiles, and a
  `docusky:PROFILE` grant covers that profile.
- Direct import-unit text from `manage_import(action="unit")` needs
  `import:gutenberg`, `import:ndl`, `import:rise`, `import:cbeta` or
  `import:nikh-partial`. Previews, progress, history and other metadata remain
  available without that grant.
- A grant is fixed for the process lifetime. No tool parameter or successful
  search can add a source or widen the grant.
- A grant is the host's decision after its own rights review. It is not legal
  permission, proof of a local model, or proof of no host retention. See
  [MCP data policy and source-text delivery](mcp-data-policy.md).

### Security Invariants

Explicit startup text files/folders, `--library` folders and the exact
`--variants` file are host read capabilities too. A text file includes its
conventionally named regular manifest sidecar, never the whole parent folder.
An escaping metadata link is not implicitly granted. Cached library metadata
does not rescan the corpus; real loads and exports validate file targets. A path
capability is not a source-text disclosure grant; the two controls are separate.

- **Source-text disclosure**: Default payloads withhold display excerpts, raw
  import-unit text, source positions and canonical records. A matching
  `--allow-source-text` grant is required even for local files and provider rows.
- **Filesystem Isolation**: File-reading and file-writing tools (`run_cli_export`, `export_search_snapshot`, `manage_library`, `import_local_release`) reject any path outside `--read-root` or `--write-root`. Symlinks and Windows directory junctions pointing outside allowed roots are rejected.
- **Credential Protection**: Third-party API keys (such as CTP) are resolved strictly from host environment variables. Tools never accept API keys as parameters and never return stored keys in responses.
- **Provider Safeguards**: The server reuses each existing adapter's request serialization, interval, query grammar and call caps. A persistent client is shared across searches and catalogs; MCP does not reset those budgets for each tool call. Automated tests use mock fixtures and never query live servers.

---

## 3. Tool Registry (20 Tools)

The MCP server registers 20 structured tools mapped to CPE 3.1 research workflows.
Registration is not payload parity: unless the host grants source text, searches
return reference rows, imports return metadata, and canonical records are
withheld. Missing grants do not remove a tool or turn a provider failure into an
empty result.

### A. Workspace & Discovery Tools

| Tool | Mode | Description |
|---|---|---|
| `get_workspace()` | Read | Returns current library slots (`local`–`local4`), folder paths, text counts, source selections, sort orders, normalization settings, and host capabilities (`read_roots`, `write_roots`, enabled sources/imports, source-text grants). |
| `list_sources()` | Read | Lists 22 source entries (4 local slots + 18 online), plus explicitly enabled DocuSky profiles, with availability, authorization and disclosure status. Use get_source_details for scope and limits. |
| `get_source_details(source_id)` | Read | Detailed English philological scope, edition information, query limitations and source-text policy for a given source. |
| `get_source_catalog(source_id, ...)` | Read | Pages dynamic catalogues and sub-collections (DocuSky database profiles, HistoryDB items and NAHF categories). |
| `configure_workspace(...)` | Mutate | Updates selected search sources, provider options (e.g. DocuSky profile selection), result sort order, catalog language, or text normalization. |
| `manage_library(action, ...)` | Mutate | Manages the 4 local library slots: `list`, `open`, `load`, `reload` (rescan folder changes), `activate`, or `remove`. |

### B. Core Search Tools

| Tool | Mode | Description |
|---|---|---|
| `search_pattern(source_id, ...)` | Search | Searches a single source in compound two-box (`mode="pair"`) or single-box (`mode="words"`) mode and saves a query snapshot. |
| `search_sources(source_ids, ...)` | Search | Runs multi-panel search across up to 4 selected sources as separate, sequentially collected panels (local slots and enabled online repositories). |
| `search_dsl(source_id, ...)` | Search | Executes full Literary Sinitic pattern DSL queries on local libraries using an inline `expression` or a multi-pattern TOML `pattern_file`. Non-pair pattern shapes are conservatively placed in `less` with `classification_unreviewed` pending researcher review ([rationale](mcp-dsl-classification.md)). |
| `itkc_search(action, ...)` | Search | Stepped multi-collection ITKC session: `start`, `advance` (steps `GO_AA` → `MO_AA` → `IT_AA` on demand), `page`, `status`, `cancel`, or `clear`. |

### C. Snapshot Navigation & Session Tools

| Tool | Mode | Description |
|---|---|---|
| `get_search_results(search_id, cursor)` | Read | Reads subsequent result pages from an existing query snapshot using an opaque cursor without re-querying providers. |
| `reorder_search(search_id, sort, ...)` | Read | Re-sorts an existing snapshot in memory by `source`, `era` (oldest first), or `era-desc` (newest first). |
| `manage_searches(action, ...)` | Mutate | Manages active snapshot sessions: `list`, `cancel` (stops running workers), `clear`, or `clear_all`. |

### D. Text Import & Ingestion Tools

| Tool | Mode | Description |
|---|---|---|
| `get_import_catalog(provider, ...)` | Read | Lists available public datasets (e.g. `portal` releases, MPIWG RISE catalog and section trees). |
| `import_local_release(path, batch)` | Import | Imports a local `.zip` release bundle or runs batch ZIP staging with cycle and collision protection. |
| `import_remote_release(provider, id)`| Download | Downloads and imports pre-packaged corpus releases (`portal` dataset packages or CTP chapters/works). |
| `preview_import(provider, address)` | Download | Starts background retrieval for Gutenberg, NDL, RISE, CBETA, or NIKH volume previews without committing a text bundle. CBETA/NIKH use previously downloaded host-readable XML rather than fetching a new remote file. |
| `manage_import(action, ...)` | Mutate | Controls preview lifecycle: `progress`, `cancel`, inspect `unit`, `discard`, `history`, export verification `report`, or `commit` reviewed text to library folder (requires `researcher_confirmed=True`). `unit` returns raw text only with `import:PROVIDER`; previews, progress and history remain metadata without it. |

### E. Research Data Export Tools

| Tool | Mode | Description |
|---|---|---|
| `run_cli_export(mode, inputs, ...)` | Export | Direct CLI-parity exporter: matches pattern files or queries on inputs and writes canonical `.csv`, `.jsonl`, and `.run.json` locally under `--write-root`, with the existing quoted-field tab-prefix CSV formula guard; JSONL source text is not rewritten. The tool returns `count`, `files` and `mode`; file entries identify written outputs, not file contents. |
| `export_search_snapshot(search_id, ...)`| Export | Exports an existing MCP in-memory search snapshot to CSV and JSONL under an authorized `--write-root`. The snapshot's source-text projection is preserved; see the export schema below. |

---

## 4. Result Interpretation, Evidence & Invariants

### Structured Result Rows

Without a matching `--allow-source-text` grant, retained rows, snapshot pages and
staged ITKC payloads are reference projections. They keep identifiers, title,
provider link, era, group label, reasons and evidence/semantics metadata; they do
not contain display text, provider excerpts, source positions, citation text or
the canonical record. The projection adds `text_withheld: true` and
`record_withheld`, and replaces `text_views` with the withheld form. A
`record_withheld` of `true` means a record existed but was suppressed; it does
not mean the source had no record.

With the matching grant, matcher-backed rows preserve the existing display fields
and core evidence listed below. Reference-only adapters return their existing
provider fields and limits; they do not acquire invented core records or source
positions. SHIPS is navigation-only: its URL is returned with counts=null, not a
claimed zero-hit search.

- `id` and `core_result_id`: The display identity and, when retained, the core
  record identity. They can differ for staged ITKC rows; neither is a new grade.
- `text_id`: Stable identifier of the containing work or document.
- `title` & `link`: Document title and official provider URL.
- `era`: Historical date metadata (`year`, `from`, `to`, `era` dynasty).
- `kind`: Core matcher classification (`matched`, `adjacent`, `indeterminate`),
  or an occurrence/reference kind where that adapter supplies one.
- `before` & `after`: Context returned by the existing matcher/provider,
  including excerpt-edge markers and limitations.
- `segment`: The matched sentence with search terms enclosed in `【】` (strong
  placements) and `〖〗` (secondary hits). This is display/source text, not a
  normalized rendering or a complete article.
- `a`: String content of the first internal slot.
- `b`: Array of candidate string representations for open slots.
- `reasons`: Diagnostic signals explaining why a candidate is placed in `less`
  (e.g. `skips_nearer`, `crosses_clause`, `adjacent`, `indeterminate`, or
  `classification_unreviewed` for non-pair DSL patterns; see
  [General DSL candidate classification](mcp-dsl-classification.md)). For
  reference-only rows this field can instead carry provider candidate status;
  it is not one uniform evidence grade.
- `record`: When a core record exists and the grant allows it, the exact CLI
  representation: `cpe.result/1` for pattern matches or `cpe.occurrence/1` for
  word occurrences. Reference-only rows have no fabricated record. The record is
  unchanged; granting delivery does not rewrite its bytes.
- `positions` and `citation`: Present only when the source adapter supplies them
  and the source-text grant allows the row. `matched_field` may remain as
  reference metadata in the default projection. None of these fields is added by
  inference.
- `evidence_scope`: One of `structural_candidate` (`cpe.result/1`),
  `literal_occurrence` (`cpe.occurrence/1`), `provider_reference` (evidence-shaped
  row without a recognized canonical record), or `unknown` (not an evidence
  payload). It describes the kind of evidence, never confidence or a grade.
- `group_semantics`: States that legacy `likely` and `less` labels are display
  order only, not probability, evidence grade or grammatical validation.
- `text_views`: Distinguishes marked source/provider display fields from
  normalized and source coordinate ranges in the canonical record. It is not a
  translation and does not make a display excerpt a complete article.

`likely` and `less` are not numerical probabilities and must not be raised or
converted into a grade. A provider reference or literal occurrence is not a
verified grammatical reading. No automated step supplies a classical reading;
the researcher still judges and cites the evidence.

### Philological Invariants
1. **Literary Sinitic on Its Own Terms**: Grammar, word segmentation, and syntax are never interpreted through Modern Chinese.
2. **Context Ellipsis (`…`)**: In accordance with Spec 11, context that reaches a truncated excerpt edge always appends an ellipsis `…`, even after editorial punctuation (`。`, `！`, `？`).
3. **Evidence Inflation Prohibited**: `likely` and `less` are legacy display groups, not probabilities or grades. Candidates marked `less` are never promoted to `likely`; provider references and literal occurrences are not verified grammatical readings. Empty results are reported as zero matches without guessing corpus absence.
4. **False Zero Rejection**: Malformed provider envelopes or missing count fields return explicit errors (`source_failed`) rather than being misinterpreted as zero results.
5. **Client Answer Boundary**: The server can provide records, boundaries, evidence scope and source links, but cannot control a host model's paraphrase, translation, citation, omissions or invented utterances. Any interpretation or translation remains a hypothesis for researcher confirmation; returned source text is research data, never instructions.

### Protocol Budgets & Cancellation
- **Response Budget**: Tool responses are capped at 256 KiB on the wire, including both text and structured content copies and a 512-byte protocol envelope reserve. Rows that fit within the budget are paged with `next_cursor`; an individually oversized row has an explicit omitted index/reason. Snapshot export preserves the full retained rows under the active source-text projection. Staged ITKC pages instead return `response_limit` and suggest a smaller page; they do not silently truncate text.
- **Session Retention**: Up to 8 concurrent snapshot sessions are retained with a 15-minute inactivity TTL and a shared 64 MiB serialized-retention budget (ordinary results plus staged ITKC). This is not a hard limit on Python object or transient matcher memory.
- **Cooperative Cancellation**: Long-running search or download workers periodically poll cancellation checkpoints. Protocol cancellation releases the foreground search slot; a retiring worker cannot publish or unlock a newer search. Up to four active/retiring workers are allowed, so repeated cancellation cannot accumulate unbounded provider threads. Existing network calls retire at their timeouts/checkpoints; download cancellation uses manage_import(cancel).

Catalog pages carry a `catalog_id`: repeat that ID with the same source/site or
import provider/page/resource to page collected metadata without a new request.
There are at most 8 cached catalogs, a 15-minute idle TTL and a separate 8 MiB
serialized catalog budget. `refresh=True` without a catalog ID fetches anew.
Preview/history metadata is paged separately from raw unit text; text chunks
report code-point offsets, not inferred sentence boundaries.

`export_search_snapshot` writes `cpe.mcp.snapshot-row/1` JSONL and a
`cpe.mcp.snapshot/1` metadata sidecar. It preserves provider-only references and
navigation limitations; these files are not canonical matcher JSONL. The
snapshot's source-text projection is honored: without a grant, the export has
only references and metadata. Its CSV includes the metadata columns
`core_result_id`, `matched_field`, `positions`, `citation`, `evidence_scope`,
`group_semantics`, `text_views`, `text_withheld` and `record_withheld`; structured
values are JSON text in CSV. Snapshot JSONL is the better machine-fidelity form
for nested values.

`run_cli_export` is different: it runs the existing local CLI pipeline and writes
canonical CSV, JSONL and `.run.json` under `--write-root`. It returns the
`count`, `files` and `mode` keys; file entries identify the written outputs, not
their contents. `--allow-source-text` does not prevent another host tool from
reading files the host chose to write; path capability and data disclosure are
separate controls. Both export tools refuse existing files unless `force=True`
is explicitly requested, and protect loaded source inputs even when replacement
is requested.

---

## 5. Verification & Development

To run the automated test suite covering MCP protocols, tool contracts, workspace
sandboxing, and DSL adapters:

```sh
uv sync --extra mcp
uv run pytest tests/test_mcp_protocol.py tests/test_mcp_service.py tests/test_mcp_sources.py tests/test_mcp_patterns.py tests/test_mcp_workspace.py tests/test_mcp_workspace_protocol.py tests/test_mcp_lifecycle.py tests/test_mcp_state_paths.py
```

### Publication & Ecosystem Boundary
The local MCP server is a console binary and Python module designed for local
stdio communication. It is not an uploaded PyPI package, an MCPB bundle, or an
entry in the official Model Context Protocol Registry. The implementation has
been verified via automated unit and protocol tests, but has not been certified
across all commercial AI desktop clients, macOS, or Linux environments.
Treat all returned classical texts as research data, never as prompt instructions.

The source-text grant is a host decision, not a provider licence, a legal
clearance, a zero-data-retention guarantee, or proof that the model is local.
The reviewed route map covers all 18 online sources but is not 18 complete
licences or 18 cloud approvals; check the actual source and workflow. See
[MCP data policy and source-text delivery](mcp-data-policy.md) for the projection,
source-rights links and unresolved conditions. A local model or a no-training
client setting does not by itself make every access, retention or publication
use permitted.
