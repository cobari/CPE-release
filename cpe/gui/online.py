"""Searches over online sources, shown in the window like local results.

Each source returns text the matcher then searches; the results carry the source's
link and the evidence of what was returned (docs/decisions.md "Source databases are
connected one at a time"). Errors are codes; the page words them from its catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from cpe.aks import archive as aks_archive
from cpe.cancellation import Checkpoint
from cpe.cbeta import adapter as cbeta
from cpe.cyberseodang import adapter as cyberseodang
from cpe.docusky import adapter as docusky
from cpe.export import csv_out
from cpe.gui.search_support import recorded_year, single_term
from cpe.gui.service import Library, search_pair, search_words, text_from
from cpe.historydb import search as historydb_search
from cpe.ith import search as ith_search
from cpe.itkc import adapter
from cpe.itkc.client import ApiError, CallCapReached, ITKCClient, LocalStorageError
from cpe.itkc.query import (
    And,
    Near,
    Node,
    Not,
    Or,
    QueryError,
    Regex,
    Term,
    evaluate,
    parse,
    positive_leaves,
)
from cpe.local.search import normalize_query
from cpe.match.dsl import DslError
from cpe.match.engine import Settings
from cpe.match.pair import pair_spec
from cpe.nahf import search as nahf_search
from cpe.ndl import adapter as ndl
from cpe.nikh import search as sillok_search
from cpe.normalize.options import normalize
from cpe.normalize.text import NormalizedText
from cpe.nrich import adapter as nrich
from cpe.sat import search as sat_search
from cpe.vietdien import adapter as vietdien


def _unsupported(node: Node) -> bool:
    """OR or a regular expression anywhere in the query."""
    if isinstance(node, Or | Regex):
        return True
    if isinstance(node, Not):
        return _unsupported(node.child)
    if isinstance(node, Near):
        return _unsupported(node.left) or _unsupported(node.right)
    if isinstance(node, And):
        return any(_unsupported(child) for child in node.children)
    return False


def mandatory_terms(node: Node) -> list[str]:
    """Terms that must be present in every document satisfying the query.

    A term inside a NOT expression cannot guarantee inclusion, even when
    nested inside another NOT (e.g. `NOT (乙 AND NOT 丙)` does not require 丙).
    """
    if isinstance(node, Term):
        return [node.text]
    if isinstance(node, Not):
        return []
    if isinstance(node, Near):
        return mandatory_terms(node.left) + mandatory_terms(node.right)
    if isinstance(node, And):
        return [term for child in node.children for term in mandatory_terms(child)]
    return []


def mandatory_query_words(tree: Node) -> list[str] | None:
    """Return mandatory words for server querying.

    Returns None if the query contains unsupported nodes (Or, Regex) or
    if no mandatory term exists (e.g. `NOT 甲`), so the caller rejects
    the query with the appropriate *_query error.
    """
    if _unsupported(tree):
        return None
    words = mandatory_terms(tree)
    return words if words else None


def _pair_boxes_valid(first: str, second: str) -> bool:
    """Whether the pair boxes are syntactically valid (spec 14.1).

    Online routes run the same validation as the local pair search before
    spending a provider request. The call sites return a literal bad_box
    error so the existing catalog-key check can inspect it statically.
    """
    try:
        pair_spec(first, second)
    except DslError:
        return False
    return True


def _itkc_keyword_for_query(query: str) -> str | None:
    """ITKC keyword for a single-search query: its positive words, which ITKC ANDs.

    OR and regular expressions have no ITKC equivalent, and an AND of OR branches
    would drop documents, so such queries are refused instead of narrowed.
    """
    tree = parse(query)
    words = mandatory_query_words(tree)
    return " ".join(words) if words is not None else None


def itkc_plan(request: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    """Validate one ITKC request and return its fixed search fields.

    The plan is shared by the existing one-shot route and the session route so
    the copy's alternatives, query, and error codes cannot drift.
    """
    if not isinstance(request, dict):
        return None, "bad_request"
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return None, "empty"
        if "|" in first + second:
            return None, "itkc_alternatives"
        if not _pair_boxes_valid(first, second):
            return None, "bad_box"
        return {
            "mode": "pair",
            "first": first,
            "second": second,
            "keyword": f"{first} {second}",
        }, None
    if request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return None, "empty"
        try:
            found = _itkc_keyword_for_query(query)
        except QueryError:
            return None, "bad_query"
        if found is None:
            return None, "itkc_query"
        return {"mode": "words", "query": query, "keyword": found}, None
    return None, "bad_request"


def search_itkc(
    request: dict[str, Any],
    client: ITKCClient,
    settings: Settings,
    checkpoint: Checkpoint = None,
    collections: Sequence[str] = adapter.COLLECTIONS,
    *,
    include_records: bool = False,
) -> dict[str, Any]:
    plan, error = itkc_plan(request)
    if error is not None:
        return {"error": error}
    if not collections or any(
        collection not in adapter.COLLECTIONS for collection in collections
    ):
        return {"error": "bad_request"}
    assert plan is not None
    keyword = plan["keyword"]
    try:
        if collections is adapter.COLLECTIONS and checkpoint is None:
            fetched = adapter.fetch(keyword, client)
        else:
            fetched = adapter.fetch(
                keyword,
                client,
                collections=collections,
                checkpoint=checkpoint,
            )
    except CallCapReached:
        return {"error": "source_cap"}
    except LocalStorageError:
        return {"error": "local_log_failed"}
    except InterruptedError:
        raise
    except (ApiError, OSError):
        return {"error": "source_failed"}
    library = Library(
        [
            text_from(local, settings, item.eras.get(local.text_id))
            for item in fetched
            for local in item.texts
        ],
        settings,
    )
    if plan["mode"] == "pair":
        answer = search_pair(
            library,
            plan["first"],
            plan["second"],
            checkpoint=checkpoint,
            include_records=include_records,
        )
    else:
        answer = search_words(
            library,
            plan["query"],
            checkpoint=checkpoint,
            include_records=include_records,
        )
    answer["source"] = {
        "name": "itkc",
        "collections": [
            {
                "code": item.collection,
                "total": item.total,
                "records": len({text.text_id.split("#")[0] for text in item.texts}),
            }
            for item in fetched
        ],
    }
    return answer


def _sendable(first: str, second: str) -> list[str]:
    """The boxes that can be sent as one phrase: those without | alternatives.

    NDL and CBETA are asked for one box only and the matcher checks the other, so the
    other box may hold alternatives such as 爲|為 (CBETA writes 為 where many texts
    write 爲); only a box that would be sent must be a single word.
    """
    return [box for box in (first, second) if "|" not in box]


def _ndl_phrase(words: list[str]) -> str | None:
    """The phrase NDL is asked for: the longest word, if it has two characters or more.

    NDL finds nothing for a single character and does not AND space-separated words,
    so one phrase discovers the books and the matcher checks the rest locally.
    """
    longest = max(words, key=len, default="")
    return longest if len(longest) >= 2 else None


def search_ndl(
    request: dict[str, Any],
    client: ndl.NdlClient,
    settings: Settings,
    checkpoint: Checkpoint = None,
    *,
    include_records: bool = False,
) -> dict[str, Any]:
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "ndl_alternatives"}
        if not _pair_boxes_valid(first, second):
            return {"error": "bad_box"}
        phrase = _ndl_phrase(boxes)
    elif request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return {"error": "empty"}
        try:
            tree = parse(query)
        except QueryError:
            return {"error": "bad_query"}
        words = mandatory_query_words(tree)
        if words is None:
            return {"error": "ndl_query"}
        phrase = _ndl_phrase(words)
    else:
        return {"error": "bad_request"}
    if phrase is None:
        return {"error": "ndl_short"}
    try:
        fetched = ndl.fetch(phrase, client, checkpoint=checkpoint)
    except ndl.NdlCapReached:
        return {"error": "source_cap"}
    except ndl.NdlError:
        return {"error": "source_failed"}
    library = Library(
        [
            text_from(local, settings, fetched.eras.get(local.text_id))
            for local in fetched.texts
        ],
        settings,
    )
    if request.get("mode") == "pair":
        answer = search_pair(
            library,
            first,
            second,
            checkpoint=checkpoint,
            include_records=include_records,
        )
    else:
        answer = search_words(
            library, query, checkpoint=checkpoint, include_records=include_records
        )
    answer["source"] = {
        "name": "ndl",
        "phrase": phrase,
        "total": fetched.total,
        "books": fetched.books,
    }
    return answer


def search_cbeta(
    request: dict[str, Any],
    client: cbeta.CbetaClient,
    settings: Settings,
    checkpoint: Checkpoint = None,
    *,
    include_records: bool = False,
) -> dict[str, Any]:
    """CBETA: the longer element (or longest word) as the phrase, excerpts matched here.

    A single character is a valid CBETA query; NEAR between two of them was too slow
    to use, so one phrase is sent and the matcher checks the other element.
    """
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "cbeta_alternatives"}
        if not _pair_boxes_valid(first, second):
            return {"error": "bad_box"}
        phrase = max(boxes, key=len)
    elif request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return {"error": "empty"}
        try:
            tree = parse(query)
        except QueryError:
            return {"error": "bad_query"}
        words = mandatory_query_words(tree)
        if words is None:
            return {"error": "cbeta_query"}
        phrase = max(words, key=len)
    else:
        return {"error": "bad_request"}
    try:
        fetched = cbeta.fetch(phrase, client, checkpoint=checkpoint)
    except cbeta.CbetaCapReached:
        return {"error": "source_cap"}
    except cbeta.CbetaError:
        return {"error": "source_failed"}
    library = Library(
        [
            text_from(local, settings, fetched.eras.get(local.text_id))
            for local in fetched.texts
        ],
        settings,
    )
    if request.get("mode") == "pair":
        answer = search_pair(
            library,
            first,
            second,
            checkpoint=checkpoint,
            include_records=include_records,
        )
    else:
        answer = search_words(
            library, query, checkpoint=checkpoint, include_records=include_records
        )
    answer["source"] = {
        "name": "cbeta",
        "phrase": phrase,
        "total": fetched.total,
        "juans": fetched.juans,
    }
    return answer


def search_docusky(
    request: dict[str, Any],
    client: docusky.DocuskyClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
    *,
    include_records: bool = False,
) -> dict[str, Any]:
    """Search a reviewed profile or a public catalogue database."""
    try:
        profile = docusky.profile_for_source(str(request.get("source") or "docusky"))
    except docusky.DocuskyError:
        return {"error": "bad_request"}
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "docusky_alternatives"}
        if not _pair_boxes_valid(first, second):
            return {"error": "bad_box"}
        words = boxes
    elif request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return {"error": "empty"}
        try:
            tree = parse(query)
        except QueryError:
            return {"error": "bad_query"}
        mandatory = mandatory_query_words(tree)
        if mandatory is None:
            return {"error": "docusky_query"}
        words = mandatory
    else:
        return {"error": "bad_request"}
    sendable = [word for word in words if docusky.literal_phrase(word)]
    if not sendable:
        return {"error": "docusky_query"}
    phrase = max(sendable, key=len)
    try:
        fetched = docusky.fetch(phrase, client, profile, checkpoint=checkpoint)
    except docusky.DocuskyCapReached:
        return {"error": "source_cap"}
    except docusky.DocuskyError:
        return {"error": "source_failed"}
    library = Library([text_from(local, settings) for local in fetched.texts], settings)
    if request.get("mode") == "pair":
        answer = search_pair(
            library,
            first,
            second,
            checkpoint=checkpoint,
            include_records=include_records,
        )
    else:
        answer = search_words(
            library, query, checkpoint=checkpoint, include_records=include_records
        )
    answer["source"] = {
        "name": "docusky",
        "phrase": phrase,
        "total": fetched.total,
        "records": fetched.records,
        "accepted": fetched.accepted,
        "excluded": fetched.excluded,
        "database": profile.database,
        "reviewed": profile.reviewed,
    }
    return answer


def docusky_catalog(client: docusky.DocuskyClient) -> dict[str, Any]:
    """The provider's public databases, offered as profile choices."""
    try:
        entries = client.catalog()
    except docusky.DocuskyCapReached:
        return {"error": "source_cap"}
    except docusky.DocuskyError:
        return {"error": "source_failed"}
    return {
        "default": docusky.TEMPLE.database,
        "databases": [
            {
                "source": entry.source,
                "database": entry.database,
                "corpora": list(entry.corpora),
                "reviewed": entry.reviewed,
            }
            for entry in entries
        ],
    }


def _allowed_field(field: NormalizedText, start: int, end: int) -> bool:
    return not any(start < barrier < end for barrier in field.barriers)


def _pair_matches_text(
    field: NormalizedText, first_options: Sequence[str], second_options: Sequence[str]
) -> bool:
    """Ordered elements within one returned field, respecting note barriers."""
    for first in first_options:
        start = 0
        while first and (left := field.text.find(first, start)) != -1:
            after = left + len(first)
            for second in second_options:
                right = field.text.find(second, after) if second else -1
                if right != -1 and _allowed_field(field, left, right + len(second)):
                    return True
            start = left + 1
    return False


def _query_in_field(
    node: Node, field: NormalizedText, fields: Sequence[NormalizedText]
) -> bool:
    """Positive conditions share a field; NOT inspects every returned field."""
    if isinstance(node, Not):
        offsets = []
        current = 0
        for candidate in fields:
            offsets.append((current, current + len(candidate.text), candidate))
            current += len(candidate.text) + 1
        return not evaluate(
            node.child,
            "\0".join(item.text for item in fields),
            lambda start, end: any(
                left <= start
                and end <= right
                and _allowed_field(item, start - left, end - left)
                for left, right, item in offsets
            ),
        )
    if isinstance(node, And):
        return all(_query_in_field(child, field, fields) for child in node.children)
    if isinstance(node, Or):
        return any(_query_in_field(child, field, fields) for child in node.children)
    return evaluate(
        node, field.text, lambda start, end: _allowed_field(field, start, end)
    )


def _marked_field(field: NormalizedText, terms: Sequence[str]) -> str:
    """Map normalized occurrences back to the returned source characters."""
    strong = set()
    for term in terms:
        start = 0
        while term and (position := field.text.find(term, start)) != -1:
            end = position + len(term)
            if _allowed_field(field, position, end):
                span = field.src_span(position, end)
                strong.add((span.start, span.end))
            start = position + 1
    return csv_out.mark_terms(
        field.reference,
        0,
        len(field.reference),
        strong,
        [],
        csv_out.hidden_spans(field),
    )


def search_cyberseodang(
    request: dict[str, Any],
    client: cyberseodang.CyberseodangClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Check normalized conditions only on fields the provider actually returns."""
    options = settings.normalization
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "cyberseodang_alternatives"}
        if not _pair_boxes_valid(first, second):
            return {"error": "bad_box"}
        phrase = max(boxes, key=len)
        first_options = [options.literal(part.strip()) for part in first.split("|")]
        second_options = [options.literal(part.strip()) for part in second.split("|")]
        if (
            not all(first_options + second_options)
            or len(set(first_options)) != len(first_options)
            or len(set(second_options)) != len(second_options)
        ):
            return {"error": "bad_box"}
        condition_terms = first_options + second_options
    elif request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return {"error": "empty"}
        try:
            raw_tree = parse(query)
            tree = normalize_query(raw_tree, options)
        except QueryError:
            return {"error": "bad_query"}
        words = mandatory_query_words(raw_tree)
        if words is None:
            return {"error": "cyberseodang_query"}
        # Provider retrieval keeps the user's supplied phrase. Normalization
        # verifies only returned fields and does not broaden remote coverage.
        phrase = max(words, key=len)
        condition_terms = [
            leaf.text for leaf in positive_leaves(tree) if isinstance(leaf, Term)
        ]
    else:
        return {"error": "bad_request"}
    try:
        fetched = cyberseodang.fetch(phrase, client, checkpoint=checkpoint)
    except cyberseodang.CyberseodangCapReached:
        return {"error": "source_cap"}
    except cyberseodang.CyberseodangError:
        return {"error": "source_failed"}
    likely_rows: list[dict[str, Any]] = []
    less_rows: list[dict[str, Any]] = []
    for index, item in enumerate(fetched.items, 1):
        if checkpoint:
            checkpoint()
        title = item.title or ""
        fields = [normalize(item.content or "", options), normalize(title, options)]
        matches = [
            _pair_matches_text(field, first_options, second_options)
            if request.get("mode") == "pair"
            else _query_in_field(tree, field, fields)
            for field in fields
        ]
        chosen = 0 if matches[0] else 1 if matches[1] else 0 if item.content else 1
        verified = any(matches)
        row = {
            "id": f"cyberseodang:{item.item_id}#{index}",
            "text_id": f"cyberseodang:{item.item_id}",
            "title": title or item.item_id,
            "link": item.url,
            "era": None,
            "kind": "occurrence",
            "before": "",
            "segment": _marked_field(
                fields[chosen],
                condition_terms if verified else [options.literal(phrase)],
            ),
            "after": "",
            "a": "",
            "b": [],
            "reasons": ["found_in_text" if verified else "unverified_body"],
            "matched_field": ("body" if chosen == 0 else "title") if verified else None,
        }
        (likely_rows if verified else less_rows).append(row)
    return {
        "mode": request.get("mode"),
        "likely": likely_rows,
        "less": less_rows,
        "source": {
            "name": "cyberseodang",
            "phrase": phrase,
            "total": fetched.total,
            "items": len(fetched.items),
        },
    }


def _nrich_hits(text: str, term: str) -> list[tuple[int, int]]:
    """Exact, non-overlapping literal positions in the raw transcription."""
    hits: list[tuple[int, int]] = []
    start = 0
    while True:
        position = text.find(term, start)
        if position < 0:
            return hits
        end = position + len(term)
        hits.append((position, end))
        start = end


def _nrich_marked(text: str, hits: list[tuple[int, int]]) -> str:
    """Wrap exact hits for the existing card display; no other text changes."""
    parts: list[str] = []
    last = 0
    for start, end in hits:
        parts.append(cyberseodang.escape_markup(text[last:start]))
        parts.append("【" + cyberseodang.escape_markup(text[start:end]) + "】")
        last = end
    parts.append(cyberseodang.escape_markup(text[last:]))
    return "".join(parts)


def search_nrich(
    request: dict[str, Any],
    client: nrich.NrichClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the bounded NRICH snapshot for one literal transcription term.

    The server has no documented text query, so this scans the cached raw
    transcriptions only. Every hit is an unverified candidate: mixed scripts
    and Idu are not classified, and no hit is promoted to grammatical usage.
    """
    del settings
    term, error = single_term(request, "nrich_query", "|{}()@")
    if error:
        return {"error": error}
    try:
        snapshot = client.snapshot(checkpoint)
    except nrich.NrichCapReached:
        return {"error": "source_cap"}
    except nrich.NrichError:
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    less_rows: list[dict[str, Any]] = []
    for row in snapshot.rows:
        raw = row.transcription
        if not raw:
            continue
        hits = _nrich_hits(raw, term)
        if not hits:
            continue
        title = (
            " · ".join(part for part in (row.item_number, row.site) if part)
            or row.stable_id
        )
        less_rows.append(
            {
                "id": f"nrich:{row.stable_id}",
                "text_id": f"nrich:{row.stable_id}",
                "title": title,
                "link": row.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": _nrich_marked(raw, hits),
                "after": "",
                "a": "",
                "b": [],
                "reasons": ["unverified_literal_candidate"],
                "matched_field": "body",
                "positions": [{"start": start, "end": end} for start, end in hits],
                "citation": {
                    "item_number": row.item_number or "",
                    "report": row.report or "",
                    "institution": row.institution or "",
                    "rights": row.rights_text or "",
                    "opaque_field22": row.opaque_field22 or "",
                },
            }
        )
    rights = sorted({row.rights_text for row in snapshot.rows if row.rights_text})
    institutions = sorted({row.institution for row in snapshot.rows if row.institution})
    return {
        "mode": request.get("mode"),
        "likely": [],
        "less": less_rows,
        "source": {
            "name": "nrich",
            "phrase": term,
            "total": len(snapshot.rows),
            "rows": len(snapshot.rows),
            "items": len(less_rows),
            "coverage": {
                "mode": "bounded_snapshot",
                "pages": snapshot.pages,
                "rows": len(snapshot.rows),
                "row_cap": nrich.MAX_ROWS,
                "capped": snapshot.capped,
                "stopped_short": snapshot.stopped_short,
                "full_database": False,
                "no_transcription": snapshot.no_transcription,
            },
            "rights": rights,
            "institutions": institutions,
        },
    }


def _reference_evidence(term: str, title: str, snippet: str) -> tuple[str, str]:
    """Whether the provider's returned fields actually contain the query term.

    A search-window result is a candidate reference unless the returned text
    verifies the condition. The excerpt may be truncated or use a different
    spacing, so an absent term must not be promoted to found_in_text.
    """
    if term and term in snippet:
        return "body", "found_in_text"
    if term and term in title:
        return "title", "found_in_text"
    return "body", "candidate_reference"


def search_sillok(
    request: dict[str, Any],
    client: sillok_search.SillokClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the NIKH Sillok public search window for one term.

    Only the first page of the original-text tab is read. Results are candidate
    references with the provider's excerpt and article link; the excerpt is not
    promoted to a full-text or grammatical match. No pagination or article-page
    crawl is performed.
    """
    del settings
    term, error = single_term(request, "sillok_query", "|{}()@")
    if error:
        return {"error": error}
    try:
        fetched = client.search(term, checkpoint)
    except sillok_search.SillokCapReached:
        return {"error": "source_cap"}
    except sillok_search.SillokError:
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for idx, item in enumerate(fetched.items, start=1):
        matched_field, reason = _reference_evidence(term, item.title, item.snippet)
        likely.append(
            {
                "id": f"sillok:{item.article_id}#{idx}",
                "text_id": f"sillok:{item.article_id}",
                "title": item.title or item.article_id,
                "link": sillok_search.article_url(item.article_id),
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": [reason],
                "matched_field": matched_field,
            }
        )
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "sillok",
            "phrase": term,
            "total": fetched.total,
            "items": len(fetched.items),
        },
    }


def search_ith(
    request: dict[str, Any],
    client: ith_search.IthClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the ITH Taiwan Full-text Database first result page for one term.

    Results are candidate references with the provider's title, excerpt and
    article link. The adapter sends one query, reads the first page and does not
    paginate, fetch article pages or store source text.
    """
    del settings
    term, error = single_term(request, "ith_query", "|{}()@")
    if error:
        return {"error": error}
    try:
        fetched = client.search(term, checkpoint)
    except ith_search.IthCapReached:
        return {"error": "source_cap"}
    except ith_search.IthError:
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for idx, item in enumerate(fetched.items, start=1):
        likely.append(
            {
                "id": f"ith:{item.url}#{idx}",
                "text_id": f"ith:{item.url}",
                "title": item.title or item.url,
                "link": item.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": ["candidate_reference"],
                "matched_field": "body",
            }
        )
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "ith",
            "phrase": term,
            "items": len(fetched.items),
        },
    }


def search_nahf(
    request: dict[str, Any],
    client: nahf_search.NahfClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search one NAHF collection's first original-text result page.

    The provider's main search page returns only counts, so the researcher first
    chooses one source-library item. This route sends that item and one query, reads
    at most the first 20 rows, and returns candidate titles, excerpts and links.
    It does not paginate, fetch article pages or store source text.
    """
    del settings
    term, error = single_term(request, "nahf_query", "|{}()@")
    if error:
        return {"error": error}
    item = request.get("item")
    if not isinstance(item, str) or item not in nahf_search.ITEM_IDS:
        return {"error": "nahf_item"}
    try:
        fetched = client.search(item, term, checkpoint)
    except nahf_search.NahfCapReached:
        return {"error": "source_cap"}
    except nahf_search.NahfError as error:
        code = str(error)
        if code == "search_layout":
            return {"error": "nahf_layout"}
        if code == "search_query":
            return {"error": "nahf_query"}
        if code == "search_item":
            return {"error": "nahf_item"}
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for idx, item_row in enumerate(fetched.items, start=1):
        likely.append(
            {
                "id": f"nahf:{item_row.level_id}#{idx}",
                "text_id": f"nahf:{item_row.level_id}",
                "title": item_row.title or item_row.level_id,
                "link": item_row.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item_row.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": ["candidate_reference"],
                "matched_field": "body",
            }
        )
    item_name = dict(nahf_search.ITEMS).get(item, "")
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "nahf",
            "phrase": term,
            "total": fetched.total,
            "items": len(fetched.items),
            "item": item,
            "item_name": item_name,
            "types": "d",
            "first_page_only": True,
        },
    }


def search_aksarchive(
    request: dict[str, Any],
    client: aks_archive.AksArchiveClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the AKS manuscript archive's first original-text page.

    The archive returns server-rendered rows with a title, a highlighted
    transcription excerpt, a date and a record UCI. Only the first page is
    read. Results are candidate references; the excerpt verifies the term only
    when it contains the exact characters sent.
    """
    del settings
    term, error = single_term(request, "aksarchive_query", '|{}()@"?*†$')
    if error:
        return {"error": error}
    try:
        fetched = client.search(term, checkpoint)
    except aks_archive.AksArchiveCapReached:
        return {"error": "source_cap"}
    except aks_archive.AksArchiveError as error:
        if str(error) == "search_query":
            return {"error": "aksarchive_query"}
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for index, item in enumerate(fetched.items, start=1):
        if term in item.snippet:
            matched_field, reason = "body", "found_in_text"
        elif term in item.title:
            matched_field, reason = "title", "found_in_text"
        else:
            matched_field, reason = "body", "candidate_reference"
        likely.append(
            {
                "id": f"aksarchive:{item.uci}#{index}",
                "text_id": f"aksarchive:{item.uci}",
                "title": item.title,
                "link": item.url,
                "era": recorded_year(item.date),
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": [reason],
                "matched_field": matched_field,
                "citation": {"uci": item.uci, "date": item.date},
            }
        )
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "aksarchive",
            "phrase": term,
            "total": fetched.total,
            "items": len(likely),
            "first_page_only": True,
            "page_items": aks_archive.PAGE_ITEMS,
        },
    }


def search_sat(
    request: dict[str, Any],
    client: sat_search.SatClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the SAT Daizōkyō first result group for one term.

    SAT returns line-level keyword-in-context rows with Taishō page/line ranges
    and detail links. Only the first group is read. Results are candidate
    references; the returned snippet verifies the term only when it contains
    the exact characters sent. SAT's Taishō text overlaps the connected CBETA
    canon, so this route adds line references, not a new holding.
    """
    del settings
    term, error = single_term(request, "sat_query", '|{}()@"?*')
    if error:
        return {"error": error}
    try:
        fetched = client.search(term, checkpoint)
    except sat_search.SatCapReached:
        return {"error": "source_cap"}
    except sat_search.SatError as error:
        if str(error) == "search_query":
            return {"error": "sat_query"}
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for item in fetched.items:
        reason = "found_in_text" if term in item.snippet else "candidate_reference"
        likely.append(
            {
                "id": f"sat:{item.url}",
                "text_id": f"sat:{item.number}:{item.pages}",
                "title": f"{item.work} (No. {item.number})",
                "link": item.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": [reason],
                "matched_field": "body",
                "citation": {
                    "number": item.number,
                    "pages": item.pages,
                    "hits": str(item.hits),
                },
            }
        )
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "sat",
            "phrase": term,
            "total": fetched.total,
            "items": len(likely),
            "first_page_only": True,
            "page_items": sat_search.PAGE_ITEMS,
            "glyphs": sum(item.glyphs for item in fetched.items),
        },
    }


def search_vietdien(
    request: dict[str, Any],
    client: vietdien.VietDienClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search the Việt Điện Hán-Nôm OCR corpus and show first-page candidates.

    The provider returns page-level OCR snippets for one query. Only the first
    page is read; titles, snippets and viewer links are shown. OCR is
    machine-generated and uncorrected, and the server also matches other glyph
    forms and simplified/traditional variants, so a returned row is an exact
    literal candidate only when the snippet contains the sent characters.
    """
    del settings
    term, error = single_term(request, "vietdien_query", '|{}()@"?*')
    if error:
        return {"error": error}
    try:
        fetched = client.search(term, checkpoint)
    except vietdien.VietDienCapReached:
        return {"error": "source_cap"}
    except vietdien.VietDienError as error:
        if str(error) == "search_query":
            return {"error": "vietdien_query"}
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for item in fetched.items:
        reason = "found_in_text" if term in item.snippet else "candidate_reference"
        title = (
            " · ".join(part for part in (item.title_han, item.title) if part)
            or item.item_id
        )
        likely.append(
            {
                "id": f"vietdien:{item.text_id}:{item.page_number}",
                "text_id": f"vietdien:{item.text_id}",
                "title": title,
                "link": item.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": [reason],
                "matched_field": "body",
                "citation": {
                    "item_id": item.item_id,
                    "title_han": item.title_han,
                    "page": str(item.page_number),
                },
            }
        )
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "vietdien",
            "phrase": term,
            "returned": fetched.count,
            "items": len(likely),
            "first_page_only": True,
            "ocr": True,
        },
    }


def search_historydb(
    request: dict[str, Any],
    client: historydb_search.HistoryDbClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Search one NIKH Korean History Database era and item.

    The integrated page only counts items; the rows come from the era subsite and
    need a selected item in five of six eras. Only the first original-text page is
    read. Results are candidate references with provider excerpts and links; no
    pagination, article-page fetch or storage is performed.
    """
    del settings
    term, error = single_term(request, "historydb_query", "|{}()@")
    if request.get("mode") != "words" or not isinstance(request.get("query"), str):
        return {"error": error}
    site = request.get("site")
    item = request.get("item", "")
    if not isinstance(site, str) or site not in historydb_search.SITES:
        return {"error": "historydb_site"}
    if not isinstance(item, str):
        return {"error": "historydb_item"}
    if error:
        return {"error": error}
    try:
        catalog = client.catalog(site, checkpoint)
        if any(entry.item_id == item for entry in catalog):
            pass
        elif not catalog and not item:
            pass
        else:
            return {"error": "historydb_item"}
        fetched = client.search(term, site, item, "o", checkpoint)
    except historydb_search.HistoryDbCapReached:
        return {"error": "source_cap"}
    except historydb_search.HistoryDbError as error:
        code = str(error)
        if code in ("catalog_layout", "search_result_layout"):
            return {"error": "historydb_layout"}
        if code == "search_query":
            return {"error": "historydb_query"}
        if code == "search_site":
            return {"error": "historydb_site"}
        if code == "search_item":
            return {"error": "historydb_item"}
        if code == "search_types":
            return {"error": "historydb_types"}
        return {"error": "source_failed"}
    if checkpoint:
        checkpoint()

    likely: list[dict[str, Any]] = []
    for idx, item_row in enumerate(fetched.items, start=1):
        matched_field, reason = _reference_evidence(
            term, item_row.title, item_row.snippet
        )
        likely.append(
            {
                "id": f"historydb:{item_row.level_id}#{idx}",
                "text_id": f"historydb:{item_row.level_id}",
                "title": item_row.title or item_row.level_id,
                "link": historydb_search.article_url(item_row.level_id),
                "era": site,
                "kind": "occurrence",
                "before": "",
                "segment": cyberseodang.mark_occurrences(item_row.snippet, [term]),
                "after": "",
                "a": "",
                "b": [],
                "reasons": [reason],
                "matched_field": matched_field,
            }
        )
    item_label = next((entry.label for entry in catalog if entry.item_id == item), "")
    return {
        "mode": "words",
        "likely": likely,
        "less": [],
        "source": {
            "name": "historydb",
            "phrase": term,
            "total": fetched.total,
            "items": len(fetched.items),
            "site": site,
            "item": item,
            "item_label": item_label,
            "types": "o",
            "first_page_only": True,
        },
    }
