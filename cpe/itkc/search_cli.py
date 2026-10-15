"""Sentence search over ITKC Open API results (the original itkc_search.py).

Query grammar (cpe.itkc.query):
  A B            both words (AND may be omitted)
  A AND B        same as above
  A OR B         at least one
  A NOT B        B must be absent (also: A -B)
  "A B"          exact phrase, spaces included
  A NEAR/5 B     at most 5 code points between the two, either order
  /A.{0,3}B/     regular expression
  ( ... )        grouping
  Precedence from strongest: NEAR > NOT > AND > OR

How it works:
  1) parse the query into a tree
  2) choose the keywords to send: for AND the branch with the fewest hits, for
     OR every branch, so that the union of the keyword results holds every hit
  3) collect each keyword's results page by page, deduplicated by data id
  4) split each result's search field into sentences, test them against the
     query, and mark matches with 【】

Limit: the API returns only the text around the keyword (the search field), not
the whole article, so sentences are judged within that text only.

Every request passes the probe safeguards of cpe.itkc.client: at least 1 s
between requests, at most 200 requests per run, and a JSONL log.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from cpe.itkc.client import (
    FIELD_ARTICLE,
    FIELD_AUTHOR,
    FIELD_BOOK,
    FIELD_DATA_ID,
    FIELD_DCI,
    FIELD_SEARCH,
    FIELD_VOLUME,
    ApiError,
    CallCapReached,
    ITKCClient,
    LocalStorageError,
    Safeguards,
)
from cpe.itkc.query import (
    Node,
    QueryError,
    evaluate,
    highlight,
    parse,
    plan_keywords,
    positive_leaves,
)

HEADER = ["data_id", "book", "volume", "article", "author", "matched_sentence", "link"]
_SENTENCE_RE = re.compile(r'[^.?!。？！]+(?:[.?!。？！]+["”’」』)\]]*)?')

ClientFactory = Callable[[argparse.Namespace, Path], ITKCClient]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


def split_doc(doc: dict[str, str]) -> tuple[str, str]:
    raw = doc.get(FIELD_SEARCH, "")
    title, separator, body = raw.partition("|")
    if not separator:
        title, body = doc.get(FIELD_ARTICLE, ""), raw
    return title.strip(), body.strip()


def make_link(doc: dict[str, str]) -> str:
    # The URL form is inferred from the site's structure; if it does not open,
    # search for the data id directly. Recorded as unverified (plan 9).
    data_id = doc.get(FIELD_DATA_ID, "")
    match = re.match(r"ITKC_([A-Z]{2})_", data_id)
    if not match:
        return ""
    return (
        f"https://db.itkc.or.kr/dir/item?itemId={match.group(1)}"
        f"#/dir/node?dataId={data_id}"
    )


def default_log_path() -> Path:
    return Path("experiments") / "itkc" / f"{date.today():%Y%m%d}_search.jsonl"


def _client(args: argparse.Namespace, log_path: Path) -> ITKCClient:
    return ITKCClient(
        args.sec,
        log_path=log_path,
        op_dir=args.op_dir,
        rows=args.rows,
        safeguards=Safeguards(min_interval=args.delay),
    )


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(
    argv: Sequence[str] | None = None, client_factory: ClientFactory = _client
) -> int:
    parser = argparse.ArgumentParser(
        description="Sentence search over ITKC Open API results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__ or "").split("How it works:")[0],
    )
    parser.add_argument("query", help="search query (quote it in the shell)")
    parser.add_argument("--sec", default="BT_AA", help="secId (default: BT_AA)")
    parser.add_argument(
        "--dir", dest="op_dir", help="search only within one bibliography id"
    )
    parser.add_argument(
        "--unit",
        choices=["sentence", "doc"],
        default="sentence",
        help="sentence: judge each sentence (default); doc: title + search field",
    )
    parser.add_argument("--max-docs", type=int, default=3000, help="per keyword")
    parser.add_argument("--rows", type=int, default=100, help="results per request")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between requests (>= 1)"
    )
    parser.add_argument("-o", "--output", default="itkc_result.csv", type=Path)
    parser.add_argument("--log", type=Path, help="JSONL request log")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan and counts only"
    )
    args = parser.parse_args(argv)
    if args.delay < 1.0:
        parser.error("--delay must be at least 1 second (probe safeguard)")

    try:
        tree = parse(args.query)
    except QueryError as error:
        parser.error(str(error))
    _log(f"parsed query: {tree}")

    log_path = args.log or default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    client = client_factory(args, log_path)
    try:
        return _run(args, tree, client, parser)
    except (ApiError, CallCapReached, LocalStorageError) as error:
        _log(f"error: {error}")
        return 1


def _run(
    args: argparse.Namespace,
    tree: Node,
    client: ITKCClient,
    parser: argparse.ArgumentParser,
) -> int:
    keywords = plan_keywords(tree, client.count)
    if not keywords:
        parser.error(
            "a query of only NOT and regexes gives no API keyword; "
            "add at least one plain word"
        )
    _log("API keywords:")
    for keyword in keywords:
        count = client.count(keyword)
        note = f"  (only the first {args.max_docs:,})" if count > args.max_docs else ""
        _log(f"  {keyword!r}: {count:,}{note}")
    if args.dry_run:
        return 0

    leaves = positive_leaves(tree)
    rows: list[list[str]] = []
    seen: set[str] = set()
    hit_docs: set[str] = set()
    checked = 0
    for keyword in keywords:
        for doc in client.iter_docs(keyword, args.max_docs):
            key = (
                doc.get(FIELD_DATA_ID)
                or doc.get(FIELD_DCI)
                or doc.get(FIELD_SEARCH, "")
            )
            if key in seen:
                continue
            seen.add(key)
            checked += 1
            title, body = split_doc(doc)
            units = (
                [f"{title} {body}".strip()] if args.unit == "doc" else sentences(body)
            )
            for unit in units:
                if evaluate(tree, unit):
                    hit_docs.add(key)
                    rows.append(
                        [
                            doc.get(FIELD_DATA_ID, ""),
                            doc.get(FIELD_BOOK, ""),
                            doc.get(FIELD_VOLUME, ""),
                            title or doc.get(FIELD_ARTICLE, ""),
                            doc.get(FIELD_AUTHOR, ""),
                            highlight(unit, leaves),
                            make_link(doc),
                        ]
                    )
    # All rows are collected before the file is opened: opening for writing
    # empties it, and a failed run must not leave a half-written CSV.
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    _log(
        f"done: {checked:,} candidates checked, {len(rows):,} matches in "
        f"{len(hit_docs):,} documents -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
