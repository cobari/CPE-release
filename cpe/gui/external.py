"""Official search-window links without fetching or claiming provider results."""

from __future__ import annotations

import urllib.parse
from typing import Any

from cpe.gui.search_support import single_term

SHIPS_SEARCH = "https://wwwap.hi.u-tokyo.ac.jp/ships/w16/search"


def search_ships(request: dict[str, Any]) -> dict[str, Any]:
    """Prepare one literal keyword for the public kanbun-diary search window.

    SHIPS treats spaces as Boolean separators. Refuse them and operator syntax
    so a local query cannot silently acquire different meaning on the source.
    This returns a navigation link, never a hit or a claimed zero-result count.
    """
    query, error = single_term(request, "ships_query")
    if error:
        return {"error": error}
    if (
        len(query) > 80
        or any(
            char.isspace() or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
            for char in query
        )
        or any(char in query for char in '\x7f|{}()@"?*/\\[]<>=!~^:$&')
    ):
        return {"error": "ships_query"}
    params = urllib.parse.urlencode(
        {
            "keyword": query,
            "page": "1",
            "itemsperpage": "20",
            "sortby": "jc_date",
            "sortdesc": "false",
            "sortitem": "和暦年月日：昇順",
        }
    )
    return {
        "mode": "words",
        "likely": [],
        "less": [],
        "source": {"name": "ships", "phrase": query, "external_only": True},
        "external_search_url": SHIPS_SEARCH + "?" + params,
    }
