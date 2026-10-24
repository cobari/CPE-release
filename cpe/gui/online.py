"""Searches over online sources, shown in the window like local results.

Each source returns text the matcher then searches; the results carry the source's
link and the evidence of what was returned (docs/decisions.md "Source databases are
connected one at a time"). Errors are codes; the page words them from its catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from cpe.cbeta import adapter as cbeta
from cpe.cyberseodang import adapter as cyberseodang
from cpe.docusky import adapter as docusky
from cpe.gui.service import Library, search_pair, search_words, text_from
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
from cpe.match.engine import Settings
from cpe.ndl import adapter as ndl


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


def _itkc_keyword_for_query(query: str) -> str | None:
    """ITKC keyword for a single-search query: its positive words, which ITKC ANDs.

    OR and regular expressions have no ITKC equivalent, and an AND of OR branches
    would drop documents, so such queries are refused instead of narrowed.
    """
    tree = parse(query)
    words = mandatory_query_words(tree)
    return " ".join(words) if words is not None else None


def search_itkc(
    request: dict[str, Any], client: ITKCClient, settings: Settings
) -> dict[str, Any]:
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        if "|" in first + second:
            return {"error": "itkc_alternatives"}
        keyword = f"{first} {second}"
    elif request.get("mode") == "words":
        query = str(request.get("query", "")).strip()
        if not query:
            return {"error": "empty"}
        try:
            found = _itkc_keyword_for_query(query)
        except QueryError:
            return {"error": "bad_query"}
        if found is None:
            return {"error": "itkc_query"}
        keyword = found
    else:
        return {"error": "bad_request"}
    try:
        fetched = adapter.fetch(keyword, client)
    except CallCapReached:
        return {"error": "source_cap"}
    except LocalStorageError:
        return {"error": "local_log_failed"}
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
    if request.get("mode") == "pair":
        answer = search_pair(library, first, second)
    else:
        answer = search_words(library, query)
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
    request: dict[str, Any], client: ndl.NdlClient, settings: Settings
) -> dict[str, Any]:
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "ndl_alternatives"}
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
        fetched = ndl.fetch(phrase, client)
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
        answer = search_pair(library, first, second)
    else:
        answer = search_words(library, query)
    answer["source"] = {
        "name": "ndl",
        "phrase": phrase,
        "total": fetched.total,
        "books": fetched.books,
    }
    return answer


def search_cbeta(
    request: dict[str, Any], client: cbeta.CbetaClient, settings: Settings
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
        fetched = cbeta.fetch(phrase, client)
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
        answer = search_pair(library, first, second)
    else:
        answer = search_words(library, query)
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
) -> dict[str, Any]:
    """Search a reviewed public DB; match only its isolated source paragraphs."""
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "docusky_alternatives"}
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
        fetched = docusky.fetch(phrase, client, checkpoint=checkpoint)
    except docusky.DocuskyCapReached:
        return {"error": "source_cap"}
    except docusky.DocuskyError:
        return {"error": "source_failed"}
    library = Library([text_from(local, settings) for local in fetched.texts], settings)
    if request.get("mode") == "pair":
        answer = search_pair(library, first, second)
    else:
        answer = search_words(library, query)
    answer["source"] = {
        "name": "docusky",
        "phrase": phrase,
        "total": fetched.total,
        "records": fetched.records,
        "accepted": fetched.accepted,
        "excluded": fetched.excluded,
    }
    return answer


def _pair_matches_text(
    text: str, first_options: Sequence[str], second_options: Sequence[str]
) -> bool:
    """Whether `first` appears before `second` in `text` (no cross-field matching)."""
    if not text:
        return False
    for f in first_options:
        if not f:
            continue
        start = 0
        while (pos_f := text.find(f, start)) != -1:
            after_f = pos_f + len(f)
            for s in second_options:
                if not s:
                    continue
                if text.find(s, after_f) != -1:
                    return True
            start = pos_f + 1
    return False


def _query_in_field(
    node: Node, field: str, title: str, content: str, description: str = ""
) -> bool:
    """Positive conditions share a field; exclusions inspect all returned fields."""
    if isinstance(node, Not):
        fields = [f for f in (title, description, content) if f]
        if not fields:
            return True
        boundaries: list[tuple[int, int]] = []
        cur = 0
        parts: list[str] = []
        for f in fields:
            parts.append(f)
            boundaries.append((cur, cur + len(f)))
            cur += len(f) + 1
        combined = "\0".join(parts)
        return not evaluate(
            node.child,
            combined,
            lambda start, end: any(bs <= start and end <= be for bs, be in boundaries),
        )
    if isinstance(node, And):
        return all(
            _query_in_field(child, field, title, content, description)
            for child in node.children
        )
    if isinstance(node, Or):
        return any(
            _query_in_field(child, field, title, content, description)
            for child in node.children
        )
    return evaluate(node, field)


def search_cyberseodang(
    request: dict[str, Any],
    client: cyberseodang.CyberseodangClient,
    settings: Settings,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Cyber Seodang: query Open API and verify conditions on returned text."""
    del settings  # Reserved for future matcher expansion over verified excerpts
    if request.get("mode") == "pair":
        first = str(request.get("first", "")).strip()
        second = str(request.get("second", "")).strip()
        if not first or not second:
            return {"error": "empty"}
        boxes = _sendable(first, second)
        if not boxes:
            return {"error": "cyberseodang_alternatives"}
        phrase = max(boxes, key=len)
        first_options = [opt.strip() for opt in first.split("|") if opt.strip()]
        second_options = [opt.strip() for opt in second.split("|") if opt.strip()]
        condition_terms = first_options + second_options
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
            return {"error": "cyberseodang_query"}
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

    for idx, item in enumerate(fetched.items, start=1):
        title = item.title or ""
        content = item.content or ""
        description = getattr(item, "description", "") or ""

        if request.get("mode") == "pair":
            title_matches = (
                _pair_matches_text(title, first_options, second_options)
                if title
                else False
            )
            body_matches = (
                _pair_matches_text(content, first_options, second_options)
                if content
                else False
            )
            desc_matches = (
                _pair_matches_text(description, first_options, second_options)
                if description
                else False
            )
        else:
            title_matches = (
                _query_in_field(tree, title, title, content, description)
                if title
                else False
            )
            body_matches = (
                _query_in_field(tree, content, title, content, description)
                if content
                else False
            )
            desc_matches = (
                _query_in_field(tree, description, title, content, description)
                if description
                else False
            )

        if body_matches:
            matched_field: str | None = "body"
            display_text = content
            condition_met = True
        elif desc_matches:
            matched_field = "description"
            display_text = description
            condition_met = True
        elif title_matches:
            matched_field = "title"
            display_text = title
            condition_met = True
        else:
            matched_field = None
            display_text = content or description or title
            condition_met = False

        if condition_met:
            marked_segment = cyberseodang.mark_occurrences(
                display_text, condition_terms
            )
            row = {
                "id": f"cyberseodang:{item.item_id}#{idx}",
                "text_id": f"cyberseodang:{item.item_id}",
                "title": title or item.item_id,
                "link": item.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": marked_segment,
                "after": "",
                "a": "",
                "b": [],
                "reasons": ["found_in_text"],
                "matched_field": matched_field,
            }
            likely_rows.append(row)
        else:
            marked_segment = cyberseodang.mark_occurrences(display_text, [phrase])
            row = {
                "id": f"cyberseodang:{item.item_id}#{idx}",
                "text_id": f"cyberseodang:{item.item_id}",
                "title": title or item.item_id,
                "link": item.url,
                "era": None,
                "kind": "occurrence",
                "before": "",
                "segment": marked_segment,
                "after": "",
                "a": "",
                "b": [],
                "reasons": ["unverified_body"],
                "matched_field": matched_field,
            }
            less_rows.append(row)

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
