"""Shared input and date contracts for reference-only source connections."""

import re
from typing import Any

from cpe.itkc.query import QueryError, Term, parse


def single_term(
    request: dict[str, Any], source_error: str, forbidden: str = ""
) -> tuple[str, str | None]:
    """Accept one unquoted literal without sending provider-dependent syntax."""
    if request.get("mode") != "words":
        return "", source_error
    query = request.get("query")
    if not isinstance(query, str):
        return "", "bad_query"
    query = query.strip()
    if not query:
        return "", "empty"
    if any(char.isspace() or char in forbidden for char in query):
        return "", source_error
    try:
        tree = parse(query)
    except QueryError:
        return "", "bad_query"
    if not isinstance(tree, Term) or tree.text != query:
        return "", source_error
    return query, None


def recorded_year(label: str) -> dict[str, Any] | None:
    """Use only an explicit leading four-digit year, retaining the source label.

    Regnal years, ranges, unknown dates and negative years are not converted.
    A parenthesized qualifier may accompany an explicit year. This parses a
    metadata format, not a classical reading or a calendar conversion.
    """
    match = re.fullmatch(r"([1-9][0-9]{3})(?:\s*\([^()]+\))?", label.strip())
    if not match:
        return None
    return {
        "kind": "source_date",
        "label": label,
        "year": int(match[1]),
        "from": None,
        "to": None,
    }
