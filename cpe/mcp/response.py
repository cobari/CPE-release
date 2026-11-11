"""Count the complete MCP tool result, including its compatibility text copy."""

from __future__ import annotations

import json
from typing import Any

RESPONSE_LIMIT = 256 * 1024
PROTOCOL_RESERVE = 512


def result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the text JSON identical to structuredContent for older clients."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
        "isError": bool(payload.get("error")),
    }


def wire_size(payload: dict[str, Any]) -> int:
    """Reserve framing space in addition to both serialized result copies.

    The host controls request IDs; an arbitrarily long client-supplied ID is not
    part of the server's bounded result. Normal JSON-RPC framing fits the reserve.
    """
    return PROTOCOL_RESERVE + len(
        json.dumps(
            result_payload(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )
