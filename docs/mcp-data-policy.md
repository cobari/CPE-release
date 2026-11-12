# MCP data policy and source-text delivery

**Current implementation boundary.** The local MCP server always exposes the
same 20 tools. The host's startup grant decides whether those tools may return
display text, provider excerpts and canonical records. The default is
**references and metadata only**.

`--allow-source-text SOURCE` is a separate host decision, not a provider licence,
not a legal clearance, and not proof that the client or model runs locally or
retains nothing. A source grant does not change the other startup capabilities,
and an MCP tool cannot add or widen a grant.

This document records the implementation contract and the source-rights review
boundary. It is not a blanket permission for any provider, corpus or workflow.

## 1. Default projection

Without a matching grant, result rows keep reference and provenance fields such
as `id`, `result_id`, `core_result_id`, `text_id`, `title`, `link`, `era`,
`kind`, `group`, `reasons`, `evidence_scope`, `group_semantics`,
`text_views`, and `matched_field` when present. They do not contain:

- display source text (`segment`, `before`, `after`, `a`, `b`);
- provider excerpts or raw import-unit text;
- the canonical `record` or its coordinates, grade and evidence fields;
- source positions or citation text.

Projected rows add `text_withheld: true` and `record_withheld: true|false`.
`record_withheld` distinguishes a record suppressed by this policy from a
reference-only row that never had a canonical record. The projected
`text_views` says `{"display": "withheld", "canonical_record": "withheld"}`.
Search and source payloads also carry a `disclosure` object with:

```json
{
  "mode": "references_only",
  "source_id": "itkc",
  "host_allowed_source_text": [],
  "legal_permission": "not_certified",
  "model_location": "not_verified",
  "rights_review": {
    "required": true,
    "scope": "Actual work, provider conditions, host processing and downstream use",
    "reference_url": "https://www.itkc.or.kr/content/contents.do?menuId=112",
    "reference_kind": "Provider policy or public guidance; not blanket authorization",
    "reviewed_on": "2026-11-11"
  },
  "client_processing": "Tool results may be forwarded, retained or shared by the host. A grant is the host's decision, not a provider licence or proof of local processing."
}
```

`rights_review.reference_url` may be null when the inspected record did not
establish a primary URL. `reviewed_on` records when the listed reference was
read; it is not a fresh provider approval, a blanket licence, or a statement
that the actual work and the client's downstream use are permitted. The required
scope is the actual work, provider conditions, host processing and downstream
use.

The same policy is applied to retained search snapshots, their pages, staged
ITKC payloads and `export_search_snapshot`. A snapshot cannot be widened by a
later tool call.

## 2. Startup grant and valid scopes

`--allow-source-text SOURCE` is repeatable. It accepts these exact scopes:

| Kind | Valid values |
| --- | --- |
| Local slots | `local`, `local2`, `local3`, `local4` |
| Online sources | `itkc`, `ndl`, `cbeta`, `cyberseodang`, `docusky`, `historydb`, `sillok`, `nrich`, `ith`, `nahf`, `vietdien`, `sat`, `aksarchive`, `ships`, `thdl`, `csid`, `cand`, `kyudb` |
| DocuSky profile | `docusky:PROFILE` (a profile name accepted by the existing DocuSky catalogue); `docusky` covers its profiles |
| Import text | `import:gutenberg`, `import:ndl`, `import:rise`, `import:cbeta`, `import:nikh-partial` |

There is no `all` value and no wildcard. Repeat the flag for each source:

```sh
uv run --extra mcp cpe mcp path/to/texts \
  --allow-source-text local \
  --allow-source-text local2
```

Local files are source text too. Loading a folder through `inputs`, `--library`
or `manage_library` does not deliver its text by itself; the corresponding
`local` slot needs a grant. A local source grant is separate from
`--read-root`, which only authorizes path access.

The startup flags have different jobs:

| Flag | What it grants | What it does not grant |
| --- | --- | --- |
| `--allow-source-text SOURCE` | Host permission to return source text and canonical records for the named scope. | Provider rights, a legal opinion, local-only processing, no-retention, or permission to publish. |
| `--enable-online SOURCE` | Permission to contact an online source (including `all`, `docusky:PROFILE`, or one of the 18 online identifiers). | Permission to return its excerpt or record; add a separate source-text grant. |
| `--enable-import PROVIDER` | Permission to use an import provider and read preview/progress metadata. | Permission to return the direct import-unit text; add `import:PROVIDER`. |
| `--read-root PATH` | Path capability for tools that read. | Source-text disclosure. |
| `--write-root PATH` | Path capability for tools that write or read outputs. | Source-text disclosure for returned rows. |

`manage_import(action="unit")` returns `source_text_disabled` and the current
`disclosure` when `import:PROVIDER` is absent. Previews, progress, history and
other metadata remain available. A grant is fixed for the process lifetime; no
tool parameter can widen it.

RISE preview diagnostics and saved history receipts can also contain original
before/after context in `details.slices`. Without `import:rise`, that array is
omitted at any nested receipt/progress location and replaced by
`source_slices_withheld`, `source_slices_count` and `source_text_scope` metadata.
The stored local receipt is unchanged. This covers both MCP response copies;
preview/history metadata does not implicitly grant source-text delivery.

## 3. Source rights are not one category

The lead's review records an entry for all 18 online routes, but this is **not**
18 complete licences and **not** 18 cloud approvals. Public-domain originals,
provider-created transcriptions/OCR, annotations, database rights, access
contracts and host-processing terms are distinct. Silence about AI is neither an
express permission nor an express global prohibition.

The table records what the reviewed evidence supports. Check the actual work,
collection, edition and host workflow before using a grant.

| Source | Primary evidence | Recorded boundary |
| --- | --- | --- |
| `itkc` | [ITKC copyright policy](https://www.itkc.or.kr/content/contents.do?menuId=112), [database](https://db.itkc.or.kr/) | General policy; no explicit cloud inference or training exception found. Resolve provider permission and actual work rights; a local-use agreement is not a cloud grant. |
| `ndl` | [Next Digital Library terms](https://lab.ndl.go.jp/service/tsugidigi/tsugidigi_en/#terms-of-use), [API information](https://lab.ndl.go.jp/service/tsugidigi/apiinfo/) | Next Digital Library OCR data carries Public Domain Mark; API access, attribution, edit notices and continuous-use conditions remain separate. Do not substitute NDL Search's different terms or infer a vendor AI approval. |
| `cbeta` | [CBETA copyright](https://cbeta.org/copyright) | Default CC BY-NC-SA 4.0 with per-work exceptions; non-profit, version and reuse notices matter. No vendor-specific cloud or training permission established. |
| `cyberseodang` | [Copyright](https://db.cyberseodang.or.kr/front/about/AboutCopyright.do), [terms](https://db.cyberseodang.or.kr/front/about/AboutPolicy.do), [API](https://db.cyberseodang.or.kr/front/openapi/OpenApiMain.do) | CC BY-NC-ND 2.0 KR and attribution conditions are material; an API interface is not a content licence. No explicit cloud permission found. |
| `docusky` and `docusky:PROFILE` | [DocuSky service rules](https://hackmd.io/@DocuSky/rJqemuiFQ) | Personal non-commercial use under applicable rights; uploader and linked-provider rights remain. A public profile is not a corpus-reuse or cloud licence. Resolve the actual profile. |
| `historydb` | [NIKH copyright policy](https://www.history.go.kr/contents/contentsPage.do?groupId=000000000571&menuId=000000000572&pageId=000000000213), [database](https://db.history.go.kr/) | Personal academic/research use with source attribution; third-party and jointly held material needs separate rights. No explicit cloud processing permission found. |
| `sillok` | [NIKH policy](https://www.history.go.kr/contents/contentsPage.do?groupId=000000000571&menuId=000000000572&pageId=000000000213), [service](https://sillok.history.go.kr/) | Same policy family; the service footer carries All Rights Reserved. Do not treat a separate downloadable dataset's licence as covering every search result. |
| `nrich` | [Copyright](https://portal.nrich.go.kr/kor/page.do?menuIdx=138), [developer guide](https://portal.nrich.go.kr/kor/page.do?menuIdx=666) | Inspect the actual KOGL mark and conditions; the wooden-tablet dataset's record-level assignment was not established. No blanket cloud grant. |
| `nahf` | [Source-library policy](https://contents.nahf.or.kr/info.do?type=copyright), [institutional policy](https://www.nahf.or.kr/web/portal/contents/AA990500) | Copyright/CCL/KOGL conditions apply per marked item; unmarked material needs consultation. A data.go.kr release grant is not a blanket online-search licence. |
| `aksarchive` | [Archive](https://archive.aks.ac.kr/index.do), [CC BY-NC-ND 2.0 KR](https://creativecommons.org/licenses/by-nc-nd/2.0/kr/deed.en) | Footer records CC BY-NC-ND 2.0 KR and All Rights Reserved; coverage of contributed holdings was not established. No explicit cloud or processor permission found. |
| `kyudb` | [Service](https://kyudb.snu.ac.kr/main.do), [help](https://kyudb.snu.ac.kr/brd/dbHelp.do) | Help describes viewing, copying, citation and URL copying; no content/cloud licence was located. **Unknown**; inline excerpts are retained when supplied, so this is not metadata-only. |
| `vietdien` | [AI Suite](https://www.viet-dien.com/ai), [data reuse](https://www.viet-dien.com/data) | The AI page explicitly invites external AI assistants and MCP and specifies API limits; the data page offers OCR/catalogue research use with attribution. This is an explicit AI workflow, not unrestricted training or image redistribution permission. |
| `sat` | [Actual terms](https://21dzk.l.u-tokyo.ac.jp/SAT/termsofuse.html) | Scholarly/non-profit use and permission for commercial use; redistribution is restricted subject to quotation and other stated exceptions. Cloud inference is **not established**. |
| `ith` | [Taicool landing page](https://taicool.ith.sinica.edu.tw/) | Public navigation with an All Rights Reserved footer; no specific cloud, automation or reuse grant located. **Unknown**; do not invent a clause. |
| `thdl` | [Homepage](https://thdl.ntu.edu.tw/index.html) | Research invitation and citation guidance, not blanket cloud or redistribution permission. **Unknown** beyond recorded guidance. |
| `csid` | [Catalogue](https://libdb.zju.edu.cn/s/lib/libtb/show/1516), [library rules](https://libweb.zju.edu.cn/2016/1014/c55987a2245977/page.htm), [university terms](https://www.zju.edu.cn/english/2018/0523/c20094a813073/page.htm) | Public access and linked-library rules address authorized users, personal research and bulk access; applicability to external users and cloud processing needs clarification. Metadata is not immunity. |
| `cand` | [Published policy](https://tool.hankeidou.jp/privacy_policy/) | Unauthorized reposting of text/images is prohibited and links are generally permitted; no cloud-processing or automated metadata-reuse grant was established. |
| `ships` | [Database guidance](https://www.hi.u-tokyo.ac.jp/db/) | Scholarly use; no fetched SHIPS record is returned by this route. The generated URL contains the query, and a later browser visit is outside this connector. |

The primary links above are the published references for the route map. The
table is not a complete licence review or a substitute for checking the actual
work, collection, provider conditions and host workflow.

## 4. Host and model data flow

The server transport is local stdio, but that only describes CPE-to-client
transport:

```text
Provider -> local CPE -> MCP host -> chosen model (local OR remote)
                                       -> host logs, exports, sharing (if configured)
```

A cloud host may submit returned excerpts, local-library passages, metadata and
queries to a separate service. Not every host does so; actual routing must be
established. Prompt processing, retention, training and public sharing are
distinct. A no-training setting is not a no-retention guarantee, and zero
retention does not supply a missing licence.

A source grant is the host's decision after its own rights review. It is not a
claim that the model is local, that the host retains nothing, or that the use
is legal. A local model reduces model-side egress but does not remove access,
attribution or downstream publication conditions. Do not advertise zero-data-
retention or local-model use as automatic compliance.

## 5. Exports and evidence

`export_search_snapshot` writes the projected snapshot: without a grant, only
references and metadata; with a grant, the same display and canonical fields
that the host may return. The snapshot CSV now includes `core_result_id`,
`matched_field`, `positions`, `citation`, `evidence_scope`, `group_semantics`,
`text_views`, `text_withheld` and `record_withheld` columns. CSV serializes
structured values as JSON text. Snapshot JSONL is the better machine-fidelity
form for nested values.

`run_cli_export` runs the local CLI pipeline and writes canonical CSV, JSONL and
`.run.json` under `--write-root`. It returns `count`, `files` and `mode`; file
entries identify the written outputs, not the file contents. The source-text
grant does not prevent another host tool from reading files the host chose to
write; path capability and data disclosure are separate controls.

Result rows carry `evidence_scope`:

- `structural_candidate` for a `cpe.result/1` record;
- `literal_occurrence` for a `cpe.occurrence/1` record;
- `provider_reference` for an evidence-shaped provider row without a recognized
  canonical record;
- `unknown` for a non-evidence payload.

`group_semantics` states that legacy `likely` and `less` labels are display
order only, never probability, evidence grade or grammatical validation.
`text_views` distinguishes marked source/provider display fields from normalized
and source coordinate ranges in a canonical record. These labels do not turn a
candidate into a verified reading and do not make a translation or complete
article. When source text is allowed, the canonical record is unchanged.

The server cannot control the host model's paraphrase, translation, citation,
omissions or invented utterances. Returned source text is research data, never
instructions. A researcher remains responsible for checking the source,
context, boundaries and evidence level.

## 6. Operational checklist

1. Start references-only unless a specific researcher workflow needs text.
2. Grant the narrowest local slot, online source, DocuSky profile or import
   provider; never use a broad `all` value (none exists).
3. Keep `--enable-online`/`--enable-import` separate from source-text grants.
4. For local folders, grant the matching `local` slot as well as its read root.
5. Inspect `disclosure`, `text_withheld` and `record_withheld`; do not
   reconstruct withheld material.
6. Resolve the actual source/edition rights and the host's model, retention and
   sharing arrangement before granting a route.
7. Treat the all-18 review as a route map, not as 18 permissions.
