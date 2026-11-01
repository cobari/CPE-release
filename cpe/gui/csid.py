"""Present CSID's transcription-search references without invented excerpts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpe.csid.search import PAGE_ITEMS, CsidCapReached, CsidClient, CsidError
from cpe.gui.search_support import recorded_year, single_term


def search_csid(
    request: dict[str, Any],
    client: CsidClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    query, error = single_term(request, "csid_query")
    if error:
        return {"error": error}
    try:
        fetched = client.search(query, checkpoint)
    except CsidCapReached:
        return {"error": "source_cap"}
    except CsidError as error:
        return {
            "error": "csid_query" if str(error) == "search_query" else "source_failed"
        }
    if checkpoint:
        checkpoint()
    rows = [
        {
            "id": f"csid:{item.record_id}:{item.rubbing_id}:{index}",
            "text_id": f"csid:{item.record_id}:{item.rubbing_id}",
            "title": item.title,
            "link": item.url,
            "era": recorded_year(item.date),
            "kind": "occurrence",
            "before": "",
            "segment": "",
            "after": "",
            "a": "",
            "b": [],
            "reasons": ["candidate_reference"],
            "citation": {
                "csid_dynasty": item.dynasty,
                "csid_date": item.date,
                "csid_kind": item.kind,
                "csid_number": item.number,
            },
        }
        for index, item in enumerate(fetched.items, 1)
    ]
    return {
        "mode": "words",
        "likely": [],
        "less": rows,
        "source": {
            "name": "csid",
            "phrase": query,
            "items": len(rows),
            "pages": fetched.pages,
            "first_page_only": True,
            "page_items": PAGE_ITEMS,
            "metadata_only": True,
        },
    }
