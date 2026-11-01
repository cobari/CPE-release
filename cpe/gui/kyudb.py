"""Present Kyujanggak historical-document references without evidence grades."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpe.cyberseodang.adapter import mark_occurrences
from cpe.gui.search_support import recorded_year, single_term
from cpe.kyudb.search import PAGE_ITEMS, KyudbCapReached, KyudbClient, KyudbError


def search_kyudb(
    request: dict[str, Any],
    client: KyudbClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    query, error = single_term(request, "kyudb_query")
    if error:
        return {"error": error}
    try:
        fetched = client.search(query, checkpoint)
    except KyudbCapReached:
        return {"error": "source_cap"}
    except KyudbError as error:
        return {
            "error": "kyudb_query" if str(error) == "search_query" else "source_failed"
        }
    if checkpoint:
        checkpoint()
    rows = [
        {
            "id": f"kyudb:{item.book_id}:{index}",
            "text_id": f"kyudb:{item.book_id}",
            "title": item.title,
            "link": item.url,
            "era": recorded_year(item.citation.get("date", "")),
            "kind": "occurrence",
            "before": "",
            "after": "",
            "segment": mark_occurrences(item.snippet, [query]),
            "a": "",
            "b": [],
            "reasons": ["candidate_reference"],
            "citation": {f"kyudb_{key}": value for key, value in item.citation.items()},
        }
        for index, item in enumerate(fetched.items, 1)
    ]
    return {
        "mode": "words",
        "likely": [],
        "less": rows,
        "source": {
            "name": "kyudb",
            "phrase": query,
            "items": len(rows),
            "total": fetched.total,
            "excerpt_items": sum(bool(item.snippet) for item in fetched.items),
            "first_page_only": True,
            "page_items": PAGE_ITEMS,
        },
    }
