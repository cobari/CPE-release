"""Match local files and write CSV, JSONL and run files (plan 9, spec 11)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cpe.export import csv_out, jsonl, run_file
from cpe.local.txt import InputError, LocalText, read, text_options
from cpe.match.engine import (
    Settings,
    TextInput,
    assign_ids,
    check_variants,
    long_segment_warning,
    match_text,
    sentence_delimiters,
)
from cpe.match.pattern_file import PatternSpec, loads
from cpe.match.result import Result
from cpe.normalize.options import normalize


@dataclass(frozen=True)
class Outputs:
    csv: Path
    jsonl: Path
    run: Path


def outputs_for(prefix: Path) -> Outputs:
    return Outputs(
        prefix.with_name(prefix.name + ".csv"),
        prefix.with_name(prefix.name + ".jsonl"),
        prefix.with_name(prefix.name + ".run.json"),
    )


def match_files(
    pattern_file: Path,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
) -> tuple[list[Result], Outputs]:
    pattern_bytes = pattern_file.read_bytes()
    specs = loads(pattern_bytes, str(pattern_file))
    return match_specs(
        specs, pattern_file, pattern_bytes, inputs, prefix, run_id, settings
    )


def match_specs(
    specs: Sequence[PatternSpec],
    pattern_source: Path,
    pattern_bytes: bytes,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
) -> tuple[list[Result], Outputs]:
    """Match already built pattern specs; `pattern_bytes` is what the run records."""
    settings = settings or Settings()
    texts = [read(path) for path in inputs]
    _check(texts)
    options = settings.normalization
    inputs_ = [
        TextInput(
            text.text_id,
            normalize(text.reference, text_options(text, options)),
            text.range,
            text.boundary_start,
            text.boundary_end,
            text.punctuation,
            text.full_stop,
            text.halfwidth_punctuation,
        )
        for text in texts
    ]
    check_variants(settings, [text.punctuation for text in texts])
    found = [
        (text_index, pattern_index, result)
        for text_index, text_input in enumerate(inputs_)
        for pattern_index, spec in enumerate(specs)
        for result in match_text(spec, text_input, settings)
    ]
    results = assign_ids(found)
    outputs = outputs_for(prefix)
    # Matching is finished before any output is opened, and each writer builds its
    # whole content before opening its file: opening for writing empties the file.
    jsonl.write(outputs.jsonl, results)
    csv_out.write(
        outputs.csv,
        results,
        {
            text.text_id: csv_out.text_view(
                text.reference,
                text_input.normalized,
                sentence_delimiters(
                    settings, text.full_stop, text.halfwidth_punctuation
                ),
                text.boundary_start,
                text.boundary_end,
            )
            for text, text_input in zip(texts, inputs_, strict=True)
        },
        {text.text_id: text.title for text in texts},
        run_id,
        options.name,
    )
    warnings = [
        (text_input.text_id, spec.id)
        for text_input in inputs_
        for spec in specs
        if long_segment_warning(
            spec,
            text_input.normalized.text,
            settings,
            text_input.punctuation,
            text_input.full_stop,
            text_input.halfwidth,
        )
    ]
    info = run_file.RunInfo(
        run_id,
        pattern_source,
        pattern_bytes,
        texts,
        specs,
        settings,
        options.name,
        warnings,
    )
    run_file.write(outputs.run, info, results)
    return results, outputs


def _check(texts: Sequence[LocalText]) -> None:
    ids = [text.text_id for text in texts]
    duplicates = sorted({text_id for text_id in ids if ids.count(text_id) > 1})
    if duplicates:
        raise InputError(f"duplicate text_id: {', '.join(duplicates)}")
    for text in texts:
        if text.text_type != "original":
            # Spec 3.6: only original text is matched in MVP.
            raise InputError(f"{text.path}: text_type {text.text_type!r} not matched")
