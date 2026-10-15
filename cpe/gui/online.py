"""Searches over online sources, shown in the window like local results.

Each source returns text the matcher then searches; the results carry the source's
link and the evidence of what was returned (docs/decisions.md "Source databases are
connected one at a time"). Errors are codes; the page words them from its catalog.
"""

from __future__ import annotations

from typing import Any

from cpe.cbeta import adapter as cbeta
from cpe.gui.service import Library, search_pair, search_words, text_from
from cpe.itkc import adapter
from cpe.itkc.client import ApiError, CallCapReached, ITKCClient
from cpe.itkc.query import (
    And,
    Near,
    Node,
    Not,
    Or,
    QueryError,
    Regex,
    Term,
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


def _itkc_keyword_for_query(query: str) -> str | None:
    """ITKC keyword for a single-search query: its positive words, which ITKC ANDs.

    OR and regular expressions have no ITKC equivalent, and an AND of OR branches
    would drop documents, so such queries are refused instead of narrowed.
    """
    tree = parse(query)
    if _unsupported(tree):
        return None
    words = [leaf.text for leaf in positive_leaves(tree) if isinstance(leaf, Term)]
    return " ".join(words) if words else None


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
        if _unsupported(tree):
            return {"error": "ndl_query"}
        words = [leaf.text for leaf in positive_leaves(tree) if isinstance(leaf, Term)]
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
        if _unsupported(tree):
            return {"error": "cbeta_query"}
        words = [leaf.text for leaf in positive_leaves(tree) if isinstance(leaf, Term)]
        if not words:
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
