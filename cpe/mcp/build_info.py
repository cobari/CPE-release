"""Source revision embedded in the standalone MCP executable at build time."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypedDict, TypeGuard

BUILD_INFO_NAME = "build-info.json"


class BuildInfo(TypedDict):
    commit: str | None
    dirty: bool | None


def is_commit(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value) is not None
    )


def embedded_build_info() -> BuildInfo:
    """Report an unknown build for ordinary Python or missing metadata."""
    try:
        raw = json.loads(
            Path(__file__).with_name(BUILD_INFO_NAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {"commit": None, "dirty": None}
    if isinstance(raw, dict):
        commit, dirty = raw.get("commit"), raw.get("dirty")
        if is_commit(commit) and isinstance(dirty, bool):
            return {"commit": commit, "dirty": dirty}
    return {"commit": None, "dirty": None}
