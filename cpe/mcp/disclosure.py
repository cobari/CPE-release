"""Host-selected source-text disclosure, separate from provider access rights."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from cpe.mcp.sources import LOCAL_SOURCE_IDS, valid_source

IMPORT_SCOPES = frozenset(
    f"import:{provider}"
    for provider in ("gutenberg", "ndl", "rise", "cbeta", "nikh-partial")
)
_REFERENCE_FIELDS = frozenset(
    {
        "id",
        "result_id",
        "core_result_id",
        "text_id",
        "title",
        "link",
        "era",
        "kind",
        "group",
        "reasons",
        "evidence_scope",
        "group_semantics",
        "text_views",
        "matched_field",
    }
)
_TERMS = {
    "itkc": "https://www.itkc.or.kr/content/contents.do?menuId=112",
    "ndl": "https://lab.ndl.go.jp/service/tsugidigi/tsugidigi_en/",
    "cbeta": "https://cbeta.org/copyright",
    "cyberseodang": "https://db.cyberseodang.or.kr/front/about/AboutCopyright.do",
    "docusky": "https://hackmd.io/@DocuSky/rJqemuiFQ",
    "historydb": "https://www.history.go.kr/contents/contentsPage.do?groupId=000000000571&menuId=000000000572&pageId=000000000213",
    "sillok": "https://www.history.go.kr/contents/contentsPage.do?groupId=000000000571&menuId=000000000572&pageId=000000000213",
    "nrich": "https://portal.nrich.go.kr/kor/page.do?menuIdx=138",
    "ith": "https://taicool.ith.sinica.edu.tw/",
    "nahf": "https://contents.nahf.or.kr/info.do?type=copyright",
    "vietdien": "https://www.viet-dien.com/ai",
    "sat": "https://21dzk.l.u-tokyo.ac.jp/SAT/termsofuse.html",
    "aksarchive": "https://archive.aks.ac.kr/index.do",
    "ships": "https://www.hi.u-tokyo.ac.jp/db/",
    "thdl": "https://thdl.ntu.edu.tw/index.html",
    "csid": "https://libweb.zju.edu.cn/2016/1014/c55987a2245977/page.htm",
    "cand": "https://tool.hankeidou.jp/privacy_policy/",
    "kyudb": "https://kyudb.snu.ac.kr/brd/dbHelp.do",
}


class DisclosurePolicy:
    """An immutable startup grant; tools cannot widen it or certify a licence."""

    def __init__(self, allowed: Iterable[str] = ()) -> None:
        values = frozenset(allowed)
        if any(not isinstance(value, str) or not self.valid(value) for value in values):
            raise ValueError("unknown --allow-source-text scope")
        self._allowed = values

    @staticmethod
    def valid(scope: str) -> bool:
        return (
            scope in LOCAL_SOURCE_IDS or scope in IMPORT_SCOPES or valid_source(scope)
        )

    def allows(self, scope: str) -> bool:
        if not isinstance(scope, str):
            return False
        return scope in self._allowed or (
            scope.startswith("docusky:") and "docusky" in self._allowed
        )

    def describe(self, scope: str = "") -> dict[str, Any]:
        base = scope.split(":", 1)[0]
        return {
            "mode": "source_text" if self.allows(scope) else "references_only",
            "source_id": scope,
            "host_allowed_source_text": sorted(self._allowed),
            "legal_permission": "not_certified",
            "model_location": "not_verified",
            "rights_review": {
                "required": True,
                "scope": (
                    "Actual work, provider conditions, host processing "
                    "and downstream use"
                ),
                "reference_url": _TERMS.get(base),
                "reference_kind": (
                    "Provider policy or public guidance; not blanket authorization"
                ),
                "reviewed_on": "2026-11-11" if base in _TERMS else None,
            },
            "client_processing": (
                "Tool results may be forwarded, retained or shared by the host. "
                "A grant is the host's decision, not a provider licence or proof "
                "of local processing."
            ),
        }

    def row(self, row: dict[str, Any], scope: str) -> dict[str, Any]:
        if self.allows(scope):
            return copy.deepcopy(row)
        result = {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key in _REFERENCE_FIELDS
        }
        result["text_withheld"] = True
        result["record_withheld"] = "record" in row or bool(row.get("record_withheld"))
        result["text_views"] = {"display": "withheld", "canonical_record": "withheld"}
        return result

    def search(self, payload: dict[str, Any], scope: str = "") -> dict[str, Any]:
        """Project collected rows, including nested panels, without new I/O."""
        result = copy.deepcopy(payload)
        query = result.get("query")
        source = result.get("source")
        if not scope and isinstance(query, dict):
            scope = query.get("source_id", "")
        if not scope and isinstance(source, dict):
            candidate = source.get("name", "")
            scope = (
                candidate
                if isinstance(candidate, str) and self.valid(candidate)
                else ""
            )
        if not scope and isinstance(result.get("disclosure"), dict):
            scope = result["disclosure"].get("source_id", "")
        for field in ("rows", "likely", "less"):
            if isinstance(result.get(field), (list, tuple)):
                result[field] = [self.row(row, scope) for row in result[field]]
        if isinstance(result.get("results"), list):
            result["results"] = [
                self.search(panel, panel.get("source_id", ""))
                for panel in result["results"]
            ]
        if any(field in result for field in ("rows", "likely", "less", "search_id")):
            result["disclosure"] = self.describe(scope)
        return result

    def source(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(payload)
        if isinstance(result.get("sources"), list):
            result["sources"] = [self.source(source) for source in result["sources"]]
        if isinstance(result.get("id"), str) and self.valid(result["id"]):
            result["disclosure"] = self.describe(result["id"])
        return result
