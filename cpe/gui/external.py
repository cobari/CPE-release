"""Official search-window links without fetching or claiming provider results."""

from __future__ import annotations

import urllib.parse
from typing import Any

from cpe.gui.search_support import single_term

SHIPS_BASE = "https://wwwap.hi.u-tokyo.ac.jp/ships"
SHIPS_SEARCH = SHIPS_BASE + "/w16/search"
SHIPS_DAI_NIHON_SEARCH = SHIPS_BASE + "/w61/search"
# database -> (search URL, result option, native page size)
SHIPS_DATABASES: dict[str, tuple[str, str | None, str]] = {
    "diaries": (SHIPS_SEARCH, None, "20"),
    "dai_nihon": (SHIPS_DAI_NIHON_SEARCH, None, "200"),
    "komonjo": (SHIPS_BASE + "/w10/search", "コンコーダンス表示", "200"),
    "nara": (SHIPS_BASE + "/w08/search", "コンコーダンス表示", "200"),
    "heian": (SHIPS_BASE + "/w09/search", "コンコーダンス表示", "200"),
    "kamakura": (SHIPS_BASE + "/w11/search", "コンコーダンス表示", "200"),
    "nanbokucho": (SHIPS_BASE + "/w29/search", "代表表示", "200"),
}
ENGISHIKI_SEARCH = "https://khirin-t.rekihaku.ac.jp/engishiki/search"


def _literal_keyword(
    request: dict[str, Any], error_code: str
) -> tuple[str | None, str | None]:
    """One literal keyword, without source-operator or whitespace meaning."""
    query, error = single_term(request, error_code)
    if error:
        return None, error
    if (
        len(query) > 80
        or any(
            char.isspace() or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
            for char in query
        )
        or any(char in query for char in '\x7f|{}()@"?*/\\[]<>=!~^:$&')
    ):
        return None, error_code
    return query, None


def search_ships(request: dict[str, Any]) -> dict[str, Any]:
    """Prepare one literal keyword for one public SHIPS search database.

    SHIPS treats spaces as Boolean separators. Refuse them and operator syntax
    so a local query cannot silently acquire different meaning on the source.
    This returns a navigation link, never a hit or a claimed zero-result count.
    The two databases use different native query fields: the diary database
    searches one keyword, while Dai Nihon Shiryō also carries its target,
    compilation and classification defaults.
    """
    query, error = _literal_keyword(request, "ships_query")
    if error:
        return {"error": error}
    assert query is not None
    database = request.get("database", "diaries")
    if not isinstance(database, str) or database not in SHIPS_DATABASES:
        return {"error": "ships_database"}
    search_url, result_option, page_size = SHIPS_DATABASES[database]
    if database == "dai_nihon":
        params = urllib.parse.urlencode(
            {
                "keyword": query,
                "searchtarget": "綱文 書名 本文 索引",
                "compilationtype": "正編 補遺",
                "classification": (
                    "大日本史料既刊分 "
                    "大日本史料未刊分(～寛永16年) "
                    "大日本史料未刊分(寛永17年～)"
                ),
                "type": "1",
                "page": "1",
                "itemsperpage": page_size,
                "sortby": "jc_date_article combined_number",
                "sortdesc": "false",
                "sortitem": "和暦年月日条：昇順",
            }
        )
    else:
        fields: dict[str, str] = {"keyword": query}
        if result_option is not None:
            fields["resultoption"] = result_option
        fields.update(
            {
                "page": "1",
                "itemsperpage": page_size,
                "sortby": "jc_date",
                "sortdesc": "false",
                "sortitem": "和暦年月日：昇順",
            }
        )
        params = urllib.parse.urlencode(fields)
    return {
        "mode": "words",
        "likely": [],
        "less": [],
        "source": {
            "name": "ships",
            "phrase": query,
            "external_only": True,
            "database": database,
        },
        "external_search_url": search_url + "?" + params,
    }


def search_engishiki(request: dict[str, Any]) -> dict[str, Any]:
    """Prepare one literal keyword for the public Digital Engishiki search.

    The provider's Nuxt search filters its published full-text file in the
    browser, so CPE opens the official search page rather than downloading that
    file or claiming rows it did not read. The query must stay one literal term:
    no local Boolean or expression syntax is translated into provider meaning.
    """
    query, error = _literal_keyword(request, "engishiki_query")
    if error:
        return {"error": error}
    assert query is not None
    params = urllib.parse.urlencode({"keyword": query})
    return {
        "mode": "words",
        "likely": [],
        "less": [],
        "source": {"name": "engishiki", "phrase": query, "external_only": True},
        "external_search_url": ENGISHIKI_SEARCH + "?" + params,
    }
