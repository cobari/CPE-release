# Variant Comparison & Research-Note Workflow Recipe

This guide documents a reproducible MCP and desktop workflow for comparing parallel editions and compiling structured research notes across **ITKC vs DocuSky** and **CBETA vs SAT**. It describes existing implemented tool sequences and evidence boundaries; it does not authorize provider content redistribution, assert edition equivalence, or supply automated classical readings.

See [Local MCP Server](mcp.md), [MCP data policy and source-text delivery](mcp-data-policy.md), and the connected source inventory (`docs/sources-connected.md` in the development repository) for full policy and source limits.

---

## 1. Host Startup & Grant Prerequisites

By default, the MCP server returns **references and metadata only** (`text_withheld: true`). Before collecting excerpts into local research notes:

1. Enable only the online sources you need (`--enable-online`).
2. Supply `--itkc-log PATH` whenever `itkc` is enabled (required provenance log).
3. Add explicit `--allow-source-text` grants only after reviewing provider terms and your host/model retention boundary; there is no `all` grant.
4. Set `--write-root PATH` if exporting snapshot files (`export_search_snapshot`).

Example startup flags (placeholders only):

```sh
cpe mcp path/to/local-corpus \
  --enable-online itkc --enable-online docusky --enable-online cbeta --enable-online sat \
  --itkc-log path/to/itkc-requests.jsonl \
  --write-root path/to/output \
  --allow-source-text itkc \
  --allow-source-text 'docusky:承政院日記' \
  --allow-source-text cbeta \
  --allow-source-text sat
```

Without `--allow-source-text`, `search_pattern`, `itkc_search`, and `export_search_snapshot` retain identifiers, titles, links, eras, `evidence_scope`, `group_semantics`, and `record_withheld` while withholding display text (`before`, `segment`, `after`, `a`, `b`) and canonical `record` payloads.

---

## 2. Recipe A: ITKC vs DocuSky Cross-Check

Use this workflow when checking overlapping Korean classical collections (such as *Seungjeongwon Ilgi* or *Joseon Wangjo Sillok* across different providers and collections).

### Source Scope Reminders
- **ITKC (`itkc`)**: Queries `GO_AA` (고전원문), `MO_AA` (한국문집총간) and `KP_AA` (한국고전총간). `IT_AA` is a Korean translation layer and is excluded. It does **not** query ITKC's separate *Seungjeongwon Ilgi* collection, and API results do not establish website-wide coverage.
- **DocuSky (`docusky` / `docusky:DATABASE`)**: Queries one public database per request. Twenty-nine profiles are reviewed, including `承政院日記` for *Seungjeongwon Ilgi* and `朝鮮王朝實錄` for *Joseon Wangjo Sillok*. Use the catalogue's exact `source` value for search and source-text grants; internal profile codes such as `chengzheng` are not database identifiers. Bare `docusky` selects the default temple-stories database. The desktop GUI supports manual continuation up to 5 pages for reviewed profiles; MCP currently fetches page 1 only. MCP result cursors page already collected candidates, not later provider pages. Unreviewed catalogue databases remain locked to page 1 and carry `"unreviewed body layers"` labels.

### Step-by-Step Tool Sequence
1. **Inspect available DocuSky profiles**:
   - Call `get_source_catalog(source_id="docusky")` to inspect exact `source` values and reviewed vs unreviewed status. Select a returned source with `configure_workspace(sources=["docusky:承政院日記"])`; `source_options` accepts only HistoryDB, NAHF, and SHIPS filters (`historydb_site`, `historydb_item`, `nahf_item`, `ships_database`), not a DocuSky profile.
2. **Run stepped ITKC retrieval**:
   - Call `itkc_search(action="start", mode="words", query="甲乙")` (or `mode="pair"`, `first="甲"`, `second="乙"`).
   - Advance one collection at a time (`itkc_search(action="advance", job_id=...)`) and page results (`itkc_search(action="page", job_id=..., start=0, page_size=20)`).
3. **Run DocuSky retrieval**:
   - Call `search_pattern(source_id="docusky:承政院日記", mode="words", query="甲乙")` (or `search_sources(source_ids=["itkc", "docusky:承政院日記"], ...)` for side-by-side one-shot panels). A direct search uses its explicit source ID, independently of the saved source selection.
   - Keep track of DocuSky `source` coverage counts (`records`, `accepted`, `excluded`) to note records dropped for unknown XML layers.
4. **Compare citations & snapshot export**:
   - Record per-row provenance: `id`, `title`, `link`, `era`, `matched_field`, `evidence_scope`, and `disclosure.rights_review`.
   - Export retained rows without re-querying providers via `export_search_snapshot(search_id=..., output_prefix=...)` (`cpe.mcp.snapshot-row/1` JSONL preserves full nested metadata; CSV serializes structured fields as JSON).

---

## 3. Recipe B: CBETA vs SAT Cross-Check

Use this workflow when comparing Buddhist canon excerpts against Taishō line references.

### Source Scope Reminders
- **CBETA (`cbeta`)**: Online API excerpts (2 MiB response limit) plus optional selected local P5 XML partial import (`preview_import(provider="cbeta", ...)`). Neither mode performs witness reconstruction.
- **SAT (`sat`)**: Reads the **first page only** (up to 30 line hits) of SAT Daizōkyō search (`mode="words"` only; pair/regex/boolean expressions are refused).
- **Overlap & Evidence Caveat**: SAT's Taishō text belongs to the same canonical family already queried via CBETA (`docs/research/sat-search-window.md`). SAT adds Taishō page/line citations (`citation.number`, `citation.pages`, `citation.hits`) rather than independent holdings.
  - `<p name="totalHits">` (`citation.hits`) counts keyword occurrences, **not** distinct works or result rows.
  - Image-only glyphs in SAT snippets appear as `□`.
  - SAT may return related-character variants: exact-character hits carry `found_in_text`, while variant-only hits remain `candidate_reference`.

### Step-by-Step Tool Sequence
1. **Query CBETA**:
   - Call `search_pattern(source_id="cbeta", mode="words", query="甲乙")` (or `mode="pair"`).
   - Note the `search_id` and paginate with `get_search_results(search_id=..., cursor=...)`.
2. **Query SAT**:
   - Call `search_pattern(source_id="sat", mode="words", query="甲乙")` with a literal single word or phrase.
   - Inspect returned `citation` fields (work number, Taishō page/line range) and `evidence_scope`.
3. **Cross-reference in Research Notes**:
   - Pair CBETA and SAT entries by work identifier / Taishō number and line range while keeping each provider's excerpt, character-glyph placeholders (`□`), and `evidence_scope` distinct.
   - If you also imported local CBETA P5 XML (`preview_import(provider="cbeta", ...)`), record the import exclusion report (`manage_import(action="report", ...)`), since excluded editorial structures are omitted from local search.

---

## 4. Citation & Research-Note Checklist (Mandatory Safeguards)

When drafting research notes or AI-assisted summaries from tool outputs:

- **Distinguish `evidence_scope` and `group`**: `likely` and `less` are legacy display ordering only (`group_semantics`), never grammatical grades or probabilities. `candidate_reference` or `literal_occurrence` rows are not verified readings.
- **Check `text_withheld` / `record_withheld`**: Never paraphrase or reconstruct withheld text when `text_withheld: true`; cite only the returned identifier, title, and provider link.
- **Keep Literary Sinitic independent**: Never segment, punctuate, or gloss classical excerpts using modern Chinese grammar or dictionaries (`AGENTS.md`).
- **Keep editions separate**: Different provider names or query results never prove textual identity or edition equivalence; verify the underlying edition and witness before asserting variants.
- **Do not redistribute protected bodies**: Snapshot exports are for host-authorized local research use in accordance with each provider's terms (`docs/mcp-data-policy.md`).
