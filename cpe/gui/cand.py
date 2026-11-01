"""Present CanD search headings without redisplaying edited poetry."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any

from cpe.cand.search import DISPLAY_ITEMS, CandCapReached, CandClient, CandError
from cpe.gui.search_support import single_term


def search_cand(
    request: dict[str, Any],
    client: CandClient,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    query, error = single_term(request, "cand_query")
    if error:
        return {"error": error}
    try:
        fetched = client.search(query, checkpoint)
    except CandCapReached:
        return {"error": "source_cap"}
    except CandError as error:
        return {
            "error": "cand_query" if str(error) == "search_query" else "source_failed"
        }
    if checkpoint:
        checkpoint()
    rows = [
        {
            "id": f"cand:{sha256(item.reference.encode('utf-8')).hexdigest()}:{index}",
            "text_id": f"cand:{sha256(item.reference.encode('utf-8')).hexdigest()}",
            "title": item.reference,
            "link": item.url,
            "era": None,
            "kind": "occurrence",
            "before": "",
            "segment": "",
            "after": "",
            "a": "",
            "b": [],
            "reasons": ["candidate_reference"],
            "citation": {"cand_collection": item.collection},
        }
        for index, item in enumerate(fetched.items, 1)
    ]
    return {
        "mode": "words",
        "likely": [],
        "less": rows,
        "source": {
            "name": "cand",
            "phrase": query,
            "items": len(rows),
            "total": fetched.total,
            "display_items": DISPLAY_ITEMS,
            "metadata_only": True,
        },
    }
