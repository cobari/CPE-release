"""Run patterns over texts from the wire input (spec section 12.5)."""

from __future__ import annotations

from typing import Any

from cpe.export.jsonl import to_record
from cpe.match.engine import (
    Settings,
    TextInput,
    assign_ids,
    check_variants,
    match_text,
)
from cpe.match.pattern_file import from_mapping
from cpe.match.result import Result
from cpe.normalize.options import Options, normalize, options_from_mapping

_RANGES = ("whole_work", "volume", "excerpt")
_TEXT_EDGES = ("verified", "truncated")


class WireError(ValueError):
    """A wire input that does not follow spec 12.5."""


def run(wire: dict[str, Any]) -> list[dict[str, Any]]:
    return [to_record(result) for result in run_results(wire)]


def run_results(wire: dict[str, Any]) -> list[Result]:
    settings = _settings(wire.get("settings", {}))
    texts = [_text(entry, settings.normalization) for entry in wire.get("texts", [])]
    check_variants(settings, [text.punctuation for text in texts])
    specs = [from_mapping(entry) for entry in wire.get("patterns", [])]
    _unique("text_id", [text.text_id for text in texts])
    _unique("pattern id", [spec.id for spec in specs])
    found = [
        (text_index, pattern_index, result)
        for text_index, text in enumerate(texts)
        for pattern_index, spec in enumerate(specs)
        for result in match_text(spec, text, settings)
    ]
    return assign_ids(found)


def _text(entry: dict[str, Any], options: Options) -> TextInput:
    range_ = entry.get("range", "excerpt")
    if range_ not in _RANGES:
        raise WireError(f"unknown range: {range_!r}")
    text_type = entry.get("text_type", "original")
    if text_type != "original":
        # Spec 3.6: only original text is matched in MVP.
        raise WireError(f"text_type {text_type!r} is not matched in MVP")
    edges = [entry.get(key, "truncated") for key in ("boundary_start", "boundary_end")]
    if any(edge not in _TEXT_EDGES for edge in edges):
        raise WireError(f"text edges must be verified or truncated: {edges}")
    punctuation = entry.get("punctuation", "modern")
    if punctuation not in ("modern", "pause"):
        raise WireError(f"punctuation must be modern or pause: {punctuation!r}")
    full_stop = entry.get("full_stop", "sentence")
    if full_stop not in ("sentence", "reading"):
        raise WireError(f"full_stop must be sentence or reading: {full_stop!r}")
    halfwidth = entry.get("halfwidth_punctuation", False)
    if not isinstance(halfwidth, bool):
        raise WireError(f"halfwidth_punctuation must be true or false: {halfwidth!r}")
    return TextInput(
        entry["text_id"],
        normalize(entry["reference"], options),
        range_,
        edges[0],
        edges[1],
        punctuation,
        full_stop,
        halfwidth,
    )


def _settings(raw: dict[str, Any]) -> Settings:
    defaults = Settings()
    return Settings(
        normalization=options_from_mapping(raw.get("normalization", {})),
        sentence_delimiters=frozenset(
            raw.get("sentence_delimiters", defaults.sentence_delimiters)
        ),
        clause_delimiters=frozenset(
            raw.get("clause_delimiters", defaults.clause_delimiters)
        ),
        state_limit=_count(raw, "state_limit", defaults.state_limit),
        result_limit=_count(raw, "result_limit", defaults.result_limit),
        long_segment_threshold=_count(
            raw, "long_segment_threshold", defaults.long_segment_threshold
        ),
    )


def _count(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WireError(f"{key} must be an integer >= 0: {value!r}")
    return value


def _unique(label: str, values: list[str]) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise WireError(f"duplicate {label}: {', '.join(duplicates)}")
