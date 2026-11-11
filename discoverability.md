# Discoverability and project pages

This file records how the repository is meant to be found: the GitHub About box, the
project pages under `docs/`, and Google Search Console. Hosting and Search Console
are configured separately from the static files in this repository.

The publication target is the `main` branch's `/docs` folder at
`https://cobari.github.io/CPE-release/`. Check the Pages build and the published URLs
to confirm deployment. Search Console ownership verification is a separate step.

## GitHub About box (applied)

As shown by `gh repo view cobari/CPE-release`:

- **Description**: `Classic Pattern Explorer (CPE): Desktop and CLI pattern search for Literary Sinitic (Classical Chinese), with ITKC, CBETA and NDL excerpt search and local XML import.`
- **Topics** (10): `literary-sinitic`, `classical-chinese`, `digital-humanities`,
  `itkc`, `cbeta`, `korean-studies`, `sinology`, `corpus-linguistics`, `hanja`,
  `python`.
- **Website**: `https://cobari.github.io/CPE-release/`. Set this repository field
  after the pages answer at that address.

## Project pages (GitHub Pages)

- **Publishing**: Settings → Pages → Build and deployment → Deploy from a branch →
  `main`, folder `/docs`. `docs/.nojekyll` makes Pages serve the files as written,
  with no Jekyll build.
- **Files**: `docs/index.html` (English, also the `x-default` page), `docs/ko/`,
  `docs/ja/`, `docs/zh-hans/`, `docs/zh-hant/`, `docs/vi/` (one `index.html` each),
  `docs/assets/style.css`, `docs/assets/icon-192.png` (a 192-pixel copy of
  `cpe/gui/assets/icon.png`, same license), `docs/sitemap.xml`.
- **Base URL**: `https://cobari.github.io/CPE-release/`. The canonical links, the
  `hreflang` links (each page lists all six and `x-default`) and the sitemap use it.
  If the repository is renamed or a custom domain is added, every page and the
  sitemap must be changed together, or search engines are pointed at missing pages.
- **What the local candidate pages say**: v3.1 supports six interface languages and 18 online
  sources. Local full texts, provider-returned excerpts and source-linked
  references remain distinct; a source connection is not complete corpus coverage.
  Downloads, README guides and screenshots describe the same local 3.1 candidate.
  This does not publish it; the public 3.0.0 baseline remains until an authorized
  release and Pages deployment are completed.
  Indexing of a page is requested only after that page answers 200.
- **No external requests**: no analytics, tracker, remote font or remote script.
  The UI font and its license are served locally. Links go to the repository,
  the intended v3.1 release and Microsoft's WebView2 page.
- **robots.txt**: the project-path file is a reference containing the sitemap
  location, not a host-root robots policy. Crawlers use the host-root file;
  the project sitemap can be submitted directly in Search Console. No account
  indexing submission is implied by publication.
- **Check after publishing**: each of the six URLs answers 200; the language
  switch reaches every page; `sitemap.xml` answers 200 with six URLs.

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
- **After each publication**: resubmit the sitemap only if it changed, run URL
  Inspection on each changed page, and confirm the sitemap status shows all six
  URLs discovered. The `vi/` page is inspected only after it answers 200.
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
