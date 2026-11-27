"""Match local files and write CSV, JSONL and run files (plan 9, spec 11)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from cpe.export import csv_out, jsonl, run_file
from cpe.export.safe import (
    check_input_collisions,
    check_outputs,
    check_prefix,
    staged,
)
from cpe.local.txt import InputError, LocalText, read_inputs, text_options
from cpe.match.engine import (
    Settings,
    TextInput,
    assign_ids,
    check_variants,
    clause_delimiters,
    long_segment_warning,
    match_text,
    normalized_pattern,
    prepare_pattern,
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
    check_prefix(prefix)
    return Outputs(
        prefix.with_name(prefix.name + ".csv"),
        prefix.with_name(prefix.name + ".jsonl"),
        prefix.with_name(prefix.name + ".run.json"),
    )


def delimiter_literal_warnings(
    specs: Sequence[PatternSpec],
    inputs: Sequence[TextInput],
    settings: Settings,
) -> list[str]:
    """Return CLI warnings for literals containing active delimiters (spec 4.9)."""
    warnings: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    options = settings.normalization
    if inputs:
        sentence_sets = {
            sentence_delimiters(settings, t.full_stop, t.halfwidth) for t in inputs
        }
        clause_sets = {
            clause_delimiters(settings, t.punctuation, t.halfwidth) for t in inputs
        }
    else:
        sentence_sets = {sentence_delimiters(settings, "sentence", False)}
        clause_sets = {clause_delimiters(settings, "modern", False)}
    for spec in specs:
        mode = spec.pattern.scope.mode
        if mode == "window":
            continue
        active_sets = sentence_sets if mode == "sentence" else clause_sets
        norm_pat = normalized_pattern(spec, options)
        for element in norm_pat.fixed:
            for literal in element.options:
                if all(
                    any(ch in delimiters for ch in literal)
                    for delimiters in active_sets
                ):
                    key = (spec.id, literal, mode)
                    if key not in seen:
                        seen.add(key)
                        warnings.append(
                            f"warning: pattern {spec.id!r} literal {literal!r} "
                            f"contains a {mode} delimiter and will never match "
                            "(spec 4.9)"
                        )
    return warnings


def match_files(
    pattern_file: Path,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
    *,
    force: bool = False,
    on_warning: Callable[[str], None] | None = None,
) -> tuple[list[Result], Outputs]:
    outputs = outputs_for(prefix)
    check_input_collisions([outputs.csv, outputs.jsonl, outputs.run], [pattern_file])
    pattern_bytes = pattern_file.read_bytes()
    specs = loads(pattern_bytes, str(pattern_file))
    return match_specs(
        specs,
        pattern_file,
        pattern_bytes,
        inputs,
        prefix,
        run_id,
        settings,
        force=force,
        on_warning=on_warning,
    )


def match_specs(
    specs: Sequence[PatternSpec],
    pattern_source: Path,
    pattern_bytes: bytes,
    inputs: Sequence[Path],
    prefix: Path,
    run_id: str,
    settings: Settings | None = None,
    *,
    force: bool = False,
    on_warning: Callable[[str], None] | None = None,
) -> tuple[list[Result], Outputs]:
    """Match already built pattern specs; `pattern_bytes` is what the run records."""
    settings = settings or Settings()
    outputs = outputs_for(prefix)
    check_input_collisions([outputs.csv, outputs.jsonl, outputs.run], inputs)
    check_outputs([outputs.csv, outputs.jsonl, outputs.run], force=force)
    texts = read_inputs(inputs)
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
    check_variants(
        settings,
        [
            (text.punctuation, text.full_stop, text.halfwidth_punctuation)
            for text in texts
        ],
    )
    prepared = [prepare_pattern(spec, options) for spec in specs] if inputs_ else []
    found = [
        (text_index, pattern_index, result)
        for text_index, text_input in enumerate(inputs_)
        for pattern_index, (spec, prepared_spec) in enumerate(
            zip(specs, prepared, strict=True)
        )
        for result in match_text(spec, text_input, settings, prepared=prepared_spec)
    ]
    results = assign_ids(found)
    if on_warning is not None:
        for message in delimiter_literal_warnings(specs, inputs_, settings):
            on_warning(message)
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
            prepared=text_input,
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
    views = {
        text.text_id: csv_out.text_view(
            text.reference,
            text_input.normalized,
            sentence_delimiters(settings, text.full_stop, text.halfwidth_punctuation),
            text.boundary_start,
            text.boundary_end,
            sentences=text_input.segments(
                sentence_delimiters(
                    settings, text.full_stop, text.halfwidth_punctuation
                )
            ),
        )
        for text, text_input in zip(texts, inputs_, strict=True)
    }
    titles = {text.text_id: text.title for text in texts}
    normalizations = {text.text_id: text_options(text, options).name for text in texts}
    # Matching is finished before any output is opened, and each target is
    # replaced only after its writer returns.
    with staged([outputs.jsonl, outputs.csv, outputs.run], force=force) as (
        jsonl_path,
        csv_path,
        run_path,
    ):
        jsonl.write(jsonl_path, results)
        csv_out.write(
            csv_path,
            results,
            views,
            titles,
            run_id,
            options.name,
            normalizations,
        )
        run_file.write(run_path, info, results)
    return results, outputs


def _check(texts: Sequence[LocalText]) -> None:
    counts = Counter(text.text_id for text in texts)
    duplicates = sorted(text_id for text_id, count in counts.items() if count > 1)
    if duplicates:
        raise InputError(f"duplicate text_id: {', '.join(duplicates)}")
    for text in texts:
        if text.text_type != "original":
            # Spec 3.6: only original text is matched in MVP.
            raise InputError(f"{text.path}: text_type {text.text_type!r} not matched")
