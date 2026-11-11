"""Expose staged row identities without rewriting canonical matcher records."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any


def staged_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the session's collection/batch identity in the MCP display row.

    Core record IDs are collection-local and must stay equal to CLI records.
    Keeping them separately avoids making a display identity look like evidence
    returned by the matcher or provider.
    """
    output = copy.deepcopy(list(rows))
    for row in output:
        if isinstance(row.get("result_id"), str):
            row["core_result_id"] = row.get("id")
            row["id"] = row["result_id"]
    return output
