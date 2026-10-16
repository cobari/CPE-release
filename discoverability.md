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
  `docs/ja/`, `docs/zh-hans/`, `docs/zh-hant/` (one `index.html` each),
  `docs/assets/style.css`, `docs/assets/icon-192.png` (a 192-pixel copy of
  `cpe/gui/assets/icon.png`, same license), `docs/sitemap.xml`.
- **Base URL**: `https://cobari.github.io/CPE-release/`. The canonical links, the
  `hreflang` links (each page lists all five and `x-default`) and the sitemap use it.
  If the repository is renamed or a custom domain is added, every page and the
  sitemap must be changed together, or search engines are pointed at missing pages.
- **What the pages say**: release v1.0 only, as its READMEs describe it. A feature
  that is not in a published release is not described; the pages are updated with
  each release.
- **No external requests**: no analytics, tracker, web font or script from another
  host. Links go to this repository, its v1.0 release and Microsoft's WebView2 page.
- **robots.txt**: a project site cannot serve `robots.txt` at the host root
  (`cobari.github.io`), so none is added; the sitemap is submitted in Search Console.
- **Check after publishing**: each of the five URLs answers 200; the language
  switch reaches every page; `sitemap.xml` answers 200 with five URLs.

## Google Search Console

- **Property**: URL prefix `https://cobari.github.io/CPE-release/`. A Domain property
  is not possible, because `github.io` is not the owner's domain.
- **Verification**: the owner adds the property with their own Google account and
  uses either method:
  - HTML file: add the file Google provides (`google….html`) to `docs/` unchanged;
  - HTML tag: add the `<meta name="google-site-verification" …>` tag Google provides
    to the head of `docs/index.html`.
  The token belongs to the owner's account. None is in the repository, and a
  placeholder must not be committed: a wrong token only fails verification and
  publishes a meaningless file.
- **After verification**: submit `sitemap.xml`, then request indexing of the five
  pages with URL Inspection.

## Limits

- Publishing and submitting do not guarantee indexing or any ranking; search engines
  decide.
- The pages and READMEs describe the tool as a research aid that collects candidates.
  They do not claim grammatical analysis, complete coverage of a corpus, or results
  online sources cannot give (online results are excerpts with per-source limits).
- The pages quote no source text beyond pattern notation such as 以…爲.
- Nothing is announced to institutions, databases, mailing lists or forums unless the
  owner asks for it.
