"""Show THDL search excerpts as attributed references without match grades."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpe.cyberseodang.adapter import mark_occurrences
from cpe.gui.search_support import single_term
from cpe.thdl.search import CORPUS, PAGE_ITEMS, ThdlCapReached, ThdlClient, ThdlError


def search_thdl(
    request: dict[str, Any],
    client: ThdlClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    query, error = single_term(request, "thdl_query")
    if error:
        return {"error": error}
    try:
        fetched = client.search(query, checkpoint)
    except ThdlCapReached:
        return {"error": "source_cap"}
    except ThdlError as error:
        return {
            "error": "thdl_query" if str(error) == "search_query" else "source_failed"
        }
    if checkpoint:
        checkpoint()
    rows = [
        {
            "id": f"thdl:{CORPUS}:{item.file_id}",
            "text_id": f"thdl:{CORPUS}:{item.file_id}",
            "title": item.title,
            "link": item.url,
            "era": None,
            "kind": "occurrence",
            "before": "",
            "after": "",
            "segment": mark_occurrences(item.snippet, [query]),
            "a": "",
            "b": [],
            "reasons": ["candidate_reference"],
            "citation": {"thdl_reference": item.reference, "thdl_file": item.file_id},
        }
        for item in fetched.items
    ]
    return {
        "mode": "words",
        "likely": rows,
        "less": [],
        "source": {
            "name": "thdl",
            "corpus": CORPUS,
            "phrase": query,
            "total": fetched.total,
            "items": len(rows),
            "first_page_only": True,
            "page_items": PAGE_ITEMS,
        },
    }
