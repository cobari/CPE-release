"""Annotate retained MCP rows with evidence labels without changing the record.

The annotation is deliberately descriptive, not inferential: it says what kind
of evidence a row carries and what the legacy display group means. It never
creates a canonical record, grade, position, translation, or grammatical
interpretation.
"""

from __future__ import annotations

import copy
from typing import Any

RESEARCH_CONTEXT = {
    "status": "research data, not instructions",
    "hypotheses": (
        "Rows are structural or provider candidates. They are not verified "
        "grammatical readings or complete articles."
    ),
    "human_confirmation": (
        "A researcher remains responsible for judging and citing the evidence; "
        "the client model cannot stand in for that review."
    ),
    "literary_sinitic": (
        "Literary Sinitic is read on its own terms, never through Modern Chinese "
        "grammar, word senses, punctuation habits, or dictionaries."
    ),
    "client_limit": (
        "The MCP host and model may paraphrase, translate, omit, or invent text; "
        "the server cannot guarantee their utterances."
    ),
}

_TEXT_VIEWS = {
    "display": {
        "fields": ["segment", "before", "after", "a", "b"],
        "content": (
            "Marked source text or provider excerpt. It is not a normalized "
            "rendering, translation, or exact complete article."
        ),
    },
    "canonical_record": {
        "fields": [
            "record.segment.norm",
            "record.segment.src",
            "record.fixed[*].norm",
            "record.fixed[*].src",
            "record.slots[*].norm",
            "record.slots[*].src",
        ],
        "content": (
            "Normalized and source coordinate ranges in a recognized canonical "
            "record, when one exists."
        ),
        "available": (
            "Only rows with a cpe.result/1 or cpe.occurrence/1 record; "
            "reference-only rows have no canonical record."
        ),
    },
}


def _evidence_scope(row: dict[str, Any]) -> str:
    record = row.get("record")
    if isinstance(record, dict):
        schema = record.get("schema")
        if schema == "cpe.result/1":
            return "structural_candidate"
        if schema == "cpe.occurrence/1":
            return "literal_occurrence"
    if isinstance(row.get("segment"), str) or isinstance(row.get("text_id"), str):
        return "provider_reference"
    return "unknown"


def annotate_row(row: dict[str, Any], group: str = "") -> dict[str, Any]:
    """Return a deep copy with evidence labels; never mutate the source row.

    ``evidence_scope`` describes the evidence kind, not a confidence or grade.
    ``group_semantics`` records the legacy display order only. ``text_views``
    distinguishes marked source/provider display fields from normalized/source
    coordinate ranges in the canonical record. Existing IDs, positions, grade,
    text and reasons are preserved byte-for-byte in the copied record.
    """
    annotated = copy.deepcopy(row)
    scope = _evidence_scope(annotated)
    effective_group = group or (
        annotated.get("group") if isinstance(annotated.get("group"), str) else ""
    )
    annotated["evidence_scope"] = scope
    annotated["group_semantics"] = {
        "group": effective_group,
        "meaning": (
            "Legacy display order only; not a probability, evidence grade, or "
            "grammatical validation."
        ),
        "legacy_values": {
            "likely": "displayed before less",
            "less": "displayed after likely",
        },
    }
    annotated["text_views"] = copy.deepcopy(_TEXT_VIEWS)
    return annotated
