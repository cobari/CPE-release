"""Backward-compatible MCP entry point for shared local pattern searches."""

from __future__ import annotations

from typing import Any

from cpe.cancellation import Checkpoint
from cpe.gui.patterns import search as shared_search
from cpe.gui.service import Library


def search(
    library: Library,
    *,
    expression: str = "",
    pattern_content: bytes | None = None,
    checkpoint: Checkpoint = None,
) -> dict[str, Any]:
    answer = shared_search(
        library,
        expression=expression,
        pattern_content=pattern_content,
        checkpoint=checkpoint,
        inline_id="mcp-pattern",
    )
    answer.pop("patterns", None)
    # MCP presentation predates these GUI labels; its existing row shape and
    # canonical pattern IDs remain stable for retained snapshots and exports.
    for group in ("likely", "less"):
        for row in answer.get(group, []):
            row.pop("pattern_id", None)
            row.pop("pattern", None)
    return answer
