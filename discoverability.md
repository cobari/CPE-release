# Discoverability and project pages

This file records how the repository is meant to be found: the GitHub About box, the
project pages under `docs/`, and Google Search Console. Hosting and Search Console
are configured separately from the static files in this repository.

The publication target is the `main` branch's `/docs` folder at
`https://cobari.github.io/CPE-release/`. Check the Pages build and the published URLs
to confirm deployment. Search Console ownership verification is a separate step.

## GitHub About box (pending v3.3 publication)

Target settings for `gh repo view cobari/CPE-release` (GitHub About update awaits explicit user publication instruction):

- **Description**: `Classic Pattern Explorer (CPE): Desktop, CLI, and MCP pattern search for Literary Sinitic (Classical Chinese), with 19 online source connections and local text imports.`
- **Topics** (10): `literary-sinitic`, `classical-chinese`, `digital-humanities`,
  `itkc`, `cbeta`, `korean-studies`, `sinology`, `corpus-linguistics`, `hanja`,
  `python`.
- **Website**: `https://cobari.github.io/CPE-release/`. Set this repository field
  after the pages answer at that address.

## Project pages (GitHub Pages)

- **Publishing**: Settings → Pages → Build and deployment → Deploy from a branch →
  `main`, folder `/docs`. `docs/.nojekyll` makes Pages serve the files as written,
  with no Jekyll build.
- **Files**: 24 indexable HTML pages across six languages (`en` at `/`, `ko/`, `ja/`, `zh-hans/`, `zh-hant/`, `vi/`) and four routes per language:
  - Landing page: `docs/index.html` (`x-default` for landing), `docs/{ko,ja,zh-hans,zh-hant,vi}/index.html` (`SoftwareApplication` JSON-LD)
  - Connected databases guide: `docs/databases/index.html`, `docs/{ko,ja,zh-hans,zh-hant,vi}/databases/index.html` (`TechArticle` + `BreadcrumbList` JSON-LD)
  - Pattern search & FAQ guide: `docs/patterns/index.html`, `docs/{ko,ja,zh-hans,zh-hant,vi}/patterns/index.html` (`TechArticle` + `BreadcrumbList` + `FAQPage` JSON-LD)
  - Local MCP server guide: `docs/mcp/index.html`, `docs/{ko,ja,zh-hans,zh-hant,vi}/mcp/index.html` (`TechArticle` + `BreadcrumbList` JSON-LD)
  - Shared assets: `docs/assets/style.css`, localized GUI previews and Open Graph images, local UI font and license, `docs/404.html` (`noindex, follow`), `docs/robots.txt`, and `docs/sitemap.xml` (24 URLs with per-section reciprocal `xhtml:link` alternates).
- **Base URL**: `https://cobari.github.io/CPE-release/`. The canonical links, the
  `hreflang` links (each page lists all six languages in its section plus
  `x-default` pointing to the English route of that section) and the sitemap use it.
  If the repository is renamed or a custom domain is added, every page and the
  sitemap must be changed together, or search engines are pointed at missing pages.
- **What the pages say**:
  - The 24 static pages, `SoftwareApplication` JSON-LD, and `CITATION.cff` describe `v3.3` (six interface languages, 19 online sources including Digital Engishiki, SHIPS 7-database selector, Pattern DSL visual builder, and Review Notebook). Package metadata (`pyproject.toml`, `release.json` with `v3.3` binary SHA-256 hashes), GitHub About update, Pages push, GitHub Release creation, and Zenodo DOI minting await user publication instruction.
  - The landing-page titles, meta descriptions, and topic guides highlight flagship database coverage (ITKC, Sillok, Kyujanggak, NDL OCR, SAT, CBETA, DocuSky, Việt Điện) and `v3.3` capabilities (Digital Engishiki as the 19th online source, SHIPS 7-database selector, Pattern DSL builder, and Review Notebook) while preserving provider limits and non-affiliation notices.
  - Local full texts, provider-returned excerpts and source-linked references remain distinct; a source connection is not complete corpus coverage. Indexing of a page is requested only after that page answers 200.
- **No external requests**: no analytics, tracker, remote font or remote script.
  The UI font and its license are served locally. Links go to the repository,
  the release, documentation pages, and Microsoft's WebView2 page.
- **robots.txt & host-root `cobari/cobari.github.io`**: crawlers read
  `https://cobari.github.io/robots.txt` at the domain root and ignore subdirectory
  `robots.txt` files such as `/CPE-release/robots.txt`. Keep `docs/robots.txt` as
  a local bundle reference, and configure `cobari/cobari.github.io` at its root
  with:
  ```txt
  User-agent: *
  Allow: /

  Sitemap: https://cobari.github.io/CPE-release/sitemap.xml
  ```
  If `cobari/cobari.github.io` already has a `robots.txt`, verify it does not
  `Disallow: /CPE-release/` and append the `Sitemap:` line. Linking to
  `https://cobari.github.io/CPE-release/` from `https://cobari.github.io/` also
  helps crawlers discover the project path.
- **Check after publishing**: all 24 URLs and `sitemap.xml` answer 200; the
  language and guide switchers reach every page; `sitemap.xml` lists 24 URLs
  with matching `xhtml:link` alternates.

## Google Search Console

- **Property**: URL prefix `https://cobari.github.io/CPE-release/`. A Domain property
  is not possible, because `github.io` is not the owner's domain.
- **Verification**: the owner adds the property with their own Google account and
  uses either method:
  - HTML file: add the file Google provides (`google….html`) to `docs/` unchanged;
  - HTML tag: add the `<meta name="google-site-verification" …>` tag Google provides
    to the head of `docs/index.html`.
  The token belongs to the owner's account. The existing meta tag in
  `docs/index.html` is present; keep it unchanged. Whether the property is
  verified for the owner's account must be checked in Search Console; a
  placeholder must not be committed: a wrong token only fails verification and
  publishes a meaningless file.
- **After verification**: submit `https://cobari.github.io/CPE-release/sitemap.xml`,
  then request indexing of the six pages with URL Inspection.

## Search Console maintenance

- **Property**: URL prefix `https://cobari.github.io/CPE-release/`. Keep using this
  property; a Domain property is not possible on `github.io`.
- **After each publication**: resubmit `https://cobari.github.io/CPE-release/sitemap.xml`,
  confirm Search Console discovers all **24 URLs**, and run URL Inspection on `/vi/`
  and each newly added or updated guide page after confirming HTTP 200.

## v3.3 release-bump & Zenodo DOI checklist

Static site copy (24 pages), `SoftwareApplication` JSON-LD, `CITATION.cff`, and this file are updated for `v3.3` (19 online sources). Remaining publication steps await explicit user publication instruction:
1. Update `pyproject.toml` and `release.json` in `CPE-release` from `3.2` / `v3.2` to `3.3` / `v3.3` with the actual `v3.3` binary SHA-256 hashes and source snapshot.
2. Confirm the six landing pages (`docs/index.html` and `docs/{ko,ja,zh-hans,zh-hant,vi}/index.html`) and 18 topic guide pages (`databases/`, `patterns/`, `mcp/`) match `v3.3`; update the 24 PNG preview/OG captures if the GUI screenshots change.
3. Update GitHub About box description to `19 online source connections` (and add `mcp` / `model-context-protocol` topics if desired).
4. Run `python scripts/check_site_bundle.py local-space/site-refresh/cpe-release/docs` after `pyproject.toml` and `release.json` are bumped to `3.3`, and confirm 24 URLs pass before pushing to GitHub Pages (`main` `/docs`).
5. **Zenodo DOI**: Enable `cobari/CPE-release` in Zenodo before creating the `v3.3` GitHub Release so Zenodo mints a DOI on release publication; then record the minted `doi:` field in `CITATION.cff`. All live actions (Pages push, GitHub About update, GitHub Release creation, and Zenodo DOI minting) await user publication instruction.
- **Performance review**: read the Performance report by query, country and page to
  see which real queries surface which language page. Treat the numbers as
  observation only; they do not promise traffic or ranking, and passing local
  checks never establishes indexing.
- **Per-language search-phrase drafts**: the phrases below describe actual page
  content for future title and description work. Search volume was not checked for
  any of them, and none implies a ranking claim.
  - English: "sentence pattern search", "Literary Sinitic".
  - Korean: "한문 문형 검색".
  - Japanese: "漢文 文型 検索".
  - Simplified Chinese: "汉文 句型 检索".
  - Traditional Chinese: "漢文 句型 檢索".
  - Vietnamese: "tìm mẫu câu Hán văn" (Hán văn for Literary Sinitic; chữ Nôm only
    as a disambiguation line so the two scripts are not confused. Term usage:
    "mẫu câu" is the standard Vietnamese word for a sentence pattern in grammar
    instruction; "Hán văn" is the Vietnamese scholarly term for Literary Sinitic
    writing; "tìm" matches software UI usage such as Microsoft vi-vn Support.
    No native-speaker review is claimed).

## Official references

- Title links: <https://developers.google.com/search/docs/appearance/title-link>
- Snippets and meta descriptions: <https://developers.google.com/search/docs/appearance/snippet>
- Canonical URLs: <https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls>
- Localized page versions: <https://developers.google.com/search/docs/specialty/international/localized-versions>
- Sitemap building and submission: <https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap>
- robots.txt introduction: <https://developers.google.com/search/docs/crawling-indexing/robots/intro>
- Structured data introduction: <https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data>
- Search Console start: <https://developers.google.com/search/docs/monitor-debug/search-console-start>

## Limits

- Publishing and submitting do not guarantee indexing or any ranking; search engines
  decide.
- The pages and READMEs describe the tool as a research aid that collects candidates.
  They do not claim grammatical analysis, complete coverage of a corpus, or results
  online sources cannot give (online results are excerpts with per-source limits).
- The pages quote no source text beyond pattern notation such as 以…爲.
- Nothing is announced to institutions, databases, mailing lists or forums unless the
  owner asks for it.
