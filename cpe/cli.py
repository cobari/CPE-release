"""Provide command-line entry points for matching, probing, and evaluation."""

import argparse
import multiprocessing
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

OUTPUT_HELP = "output prefix; writes PREFIX.csv, PREFIX.jsonl and PREFIX.run.json"
DEFAULT_PATTERNS = Path("patterns") / "mvp.toml"


def _emit(text: str, *, error: bool = False) -> None:
    """Write a line to stdout or stderr without crashing on legacy code pages."""
    stream = sys.stderr if error else sys.stdout
    try:
        stream.write(text + "\n")
        stream.flush()
    except UnicodeEncodeError:
        stream.flush()
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write((text + "\n").encode("utf-8", errors="replace"))
            buffer.flush()
        else:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            stream.write(
                (text + "\n").encode(encoding, errors="replace").decode(encoding)
            )
            stream.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command and return its exit status."""
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="cpe", description="Classic Pattern Explorer")
    subcommands = parser.add_subparsers(dest="command", required=True)
    match = subcommands.add_parser(
        "match",
        help="Search source text for pattern candidates.",
        description="Search source text for pattern candidates.",
    )
    match.add_argument("patterns", type=Path, help="pattern file (TOML)")
    _add_inputs(match)
    pair = subcommands.add_parser(
        "pair",
        help="Two-box structure search: FIRST {A} SECOND {B}.",
        description=(
            "Two-box structure search (spec 14.1): finds FIRST, then SECOND later in "
            "the same sentence, with the text between them as A and the text after "
            "SECOND as B. For the reverse order, swap the two inputs. Each input is a "
            "word, or words separated by |."
        ),
    )
    pair.add_argument("first", help="first box, for example 非")
    pair.add_argument("second", help="second box, for example 不")
    pair.add_argument("--a", default="1..20", help="length range of A (default 1..20)")
    pair.add_argument("--b", default="1..20", help="length range of B (default 1..20)")
    pair.add_argument(
        "--scope", choices=("sentence", "clause"), default="sentence", help="segment"
    )
    _add_inputs(pair)
    search = subcommands.add_parser(
        "search",
        help="One-box word search in local texts.",
        description=(
            "One-box word search (spec 14.2): reports every sentence that "
            "satisfies the query. Query grammar: words, AND, OR, NOT or -, "
            '"phrase", NEAR/n, /regex/, parentheses. No pattern DSL.'
        ),
    )
    search.add_argument("query", help="search query (quote it in the shell)")
    search.add_argument(
        "--scope", choices=("sentence", "clause"), default="sentence", help="segment"
    )
    _add_inputs(search)
    probe = subcommands.add_parser(
        "probe", help="Plan or run safeguarded ITKC experiments (default: dry-run)."
    )
    probe.add_argument(
        "config", type=Path, help="researcher-selected experiment config (TOML)"
    )
    probe.add_argument(
        "--experiment",
        action="append",
        choices=[f"E{i}" for i in range(8)],
        help="select an experiment; repeat to select several (E2 requires this option)",
    )
    probe.add_argument(
        "--live",
        action="store_true",
        help="send real requests; otherwise only write plans",
    )
    probe.add_argument(
        "--sample",
        action="store_true",
        help="include configured sample pages (at most five per experiment)",
    )
    evaluation = subcommands.add_parser(
        "eval",
        help="Evaluate pattern candidates against gold data.",
        description=(
            "Evaluate the matcher against gold JSONL files (plan 8.3): structural "
            "agreement, recall with Wilson intervals and missed usages, precision "
            "range, slot accuracy. Writes PREFIX.json and PREFIX.md."
        ),
    )
    evaluation.add_argument("gold", type=Path, nargs="+", help="gold JSONL files")
    evaluation.add_argument(
        "--patterns",
        type=Path,
        default=DEFAULT_PATTERNS,
        help="pattern file (default: patterns/mvp.toml)",
    )
    evaluation.add_argument("--out", type=Path, required=True, help="output prefix")
    evaluation.add_argument(
        "--force", action="store_true", help="replace existing output files"
    )
    evaluation.add_argument(
        "--confirmed-only",
        action="store_true",
        help="count only entries the researcher has confirmed (gate 1-A)",
    )
    gold = subcommands.add_parser(
        "gold", help="Prepare and check local G2 annotations."
    )
    gold_commands = gold.add_subparsers(dest="gold_command", required=True)
    sentences = gold_commands.add_parser(
        "sentences", help="List a source passage in reading order with source offsets."
    )
    sentences.add_argument("passage", type=Path, help="local UTF-8 passage file")
    sentences.add_argument(
        "--span",
        type=int,
        nargs=2,
        metavar=("START", "END"),
        help="zero-based source code-point range [START, END)",
    )
    validation = gold_commands.add_parser(
        "validate",
        help="Check G2 JSONL and its source files before evaluation.",
        description="Check evaluation-ready G2 JSONL against its source files.",
    )
    validation.add_argument("gold", type=Path, nargs="+", help="G2 JSONL files")
    validation.add_argument(
        "--patterns",
        type=Path,
        default=DEFAULT_PATTERNS,
        help="pattern file (default: patterns/mvp.toml)",
    )
    build = gold_commands.add_parser(
        "build", help="Convert a passage and annotation CSV into evaluation JSONL."
    )
    build.add_argument("passage", type=Path, help="local UTF-8 passage file")
    build.add_argument("annotations", type=Path, help="human annotation CSV")
    for field, help_text in (
        ("passage-id", "unique ID for this source passage"),
        ("pattern-id", "ID from the pattern file"),
        ("work", "recorded work title"),
        ("section", "recorded chapter or section"),
        ("edition", "recorded edition or source identifier"),
    ):
        build.add_argument(f"--{field}", required=True, help=help_text)
    build.add_argument("--out", required=True, type=Path, help="output gold JSONL file")
    build.add_argument(
        "--patterns", type=Path, default=DEFAULT_PATTERNS, help="pattern file"
    )
    build.add_argument(
        "--span",
        type=int,
        nargs=2,
        metavar=("START", "END"),
        help="the same source span used with gold sentences",
    )
    build.add_argument(
        "--source-sha256", help="expected source hash from gold sentences"
    )
    build.add_argument("--force", action="store_true", help="replace existing output")
    from cpe.gui.__main__ import add_gui_arguments, run_gui
    from cpe.mcp.runner import add_arguments

    gui = subcommands.add_parser(
        "gui",
        help="Open the search window.",
        description=(
            "Open the search window. The given texts (files, or folders whose .txt, "
            ".md, and .texts.jsonl files are read with their subfolders) are read once "
            "at start; without them the window opens the folder "
            "used last time, and another folder can be opened from the window."
        ),
    )
    add_gui_arguments(gui)
    mcp = subcommands.add_parser(
        "mcp", help="Serve host-selected texts through local MCP (optional extra)."
    )
    add_arguments(mcp)
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from cpe.mcp.runner import run

        return run(args)
    if args.command == "gui":
        return run_gui(args)
    if args.command == "match":
        return _run(args, _match)
    if args.command == "pair":
        return _run(args, _pair)
    if args.command == "search":
        return _run(args, _search)
    if args.command == "probe":
        return _probe(args)
    if args.command == "gold":
        if args.gold_command == "sentences":
            return _gold_sentences(args)
        if args.gold_command == "build":
            return _build_gold(args)
        return _validate_gold(args)
    return _evaluate(args)


def _add_inputs(command: argparse.ArgumentParser) -> None:
    command.add_argument("inputs", type=Path, nargs="+", help="UTF-8 text files")
    command.add_argument("--out", type=Path, required=True, help=OUTPUT_HELP)
    command.add_argument(
        "--force", action="store_true", help="replace existing output files"
    )
    command.add_argument("--run-id", help="run identifier (default: local time)")
    normalization = command.add_argument_group("normalization (default: identity)")
    normalization.add_argument("--variants", type=Path, help="variant table (TOML)")
    for flag in ("--variation-selectors", "--whitespace"):
        normalization.add_argument(flag, choices=("keep", "delete"), default="keep")
    normalization.add_argument(
        "--line-breaks",
        choices=("keep", "delete"),
        default="delete",
        help="line breaks are layout and deleted by default",
    )
    normalization.add_argument(
        "--note-markers",
        nargs=2,
        metavar=("OPEN", "CLOSE"),
        help="markers enclosing interlinear notes in TXT input",
    )
    normalization.add_argument(
        "--annotation-markers",
        nargs=2,
        metavar=("OPEN", "CLOSE"),
        help="markers enclosing attached annotations (never a barrier)",
    )
    normalization.add_argument(
        "--notes",
        choices=("barrier", "join"),
        default="barrier",
        help="barrier: no match crosses a note (default); join: record joined notes",
    )


def _evaluate(args: argparse.Namespace) -> int:
    import json

    from cpe.eval.report import build, write

    try:
        data = build(args.gold, args.patterns, args.confirmed_only)
        json_path, md_path = write(args.out, data, force=args.force)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        _emit(f"error: {error}", error=True)
        return 1
    _emit(f"wrote {json_path}, {md_path}")
    return 0


def _gold_sentences(args: argparse.Namespace) -> int:
    from cpe.eval.gold import reading_list
    from cpe.mcp.onboarding import emit

    try:
        emit(reading_list(args.passage, args.span))
    except (OSError, ValueError) as error:
        _emit(f"error: {error}", error=True)
        return 1
    return 0


def _validate_gold(args: argparse.Namespace) -> int:
    from cpe.eval.gold import validate

    try:
        entries, sources = validate(args.gold, args.patterns)
    except (OSError, ValueError) as error:
        _emit(f"error: {error}", error=True)
        return 1
    _emit(f"Validated {entries} gold entries against {sources} source files.")
    return 0


def _build_gold(args: argparse.Namespace) -> int:
    from cpe.eval.gold_build import Citation, build

    try:
        entries = build(
            args.passage,
            args.annotations,
            args.out,
            passage_id=args.passage_id,
            citation=Citation(args.work, args.section, args.edition),
            pattern_id=args.pattern_id,
            patterns_path=args.patterns,
            span=args.span,
            source_sha256=args.source_sha256,
            force=args.force,
        )
    except (OSError, ValueError) as error:
        _emit(f"error: {error}", error=True)
        return 1
    _emit(f"wrote {args.out} ({entries} gold entries)")
    return 0


def _probe(args: argparse.Namespace) -> int:
    import json

    from cpe.itkc.probe import ProbeConfigError, run

    try:
        summary = run(
            args.config, live=args.live, sample=args.sample, experiments=args.experiment
        )
    except (ProbeConfigError, OSError) as error:
        _emit(f"error: {error}", error=True)
        return 1
    _emit(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["stopped"] else 0


def _run(args: argparse.Namespace, command: Callable[..., tuple[int, Any]]) -> int:
    # Imported here so that `cpe --help` works even if a matcher module is broken.
    from cpe.export.safe import check_input_collisions
    from cpe.itkc.query import QueryError
    from cpe.local.txt import InputError
    from cpe.match.dsl import DslError
    from cpe.match.engine import Settings
    from cpe.match.pattern_file import PatternFileError
    from cpe.match.run import outputs_for
    from cpe.normalize.options import NormalizationError, Options, load_variant_table

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if args.variants:
            outputs = outputs_for(args.out)
            check_input_collisions(
                [outputs.csv, outputs.jsonl, outputs.run], [args.variants]
            )
        markers = args.note_markers or (None, None)
        annotations = args.annotation_markers or (None, None)
        options = Options(
            variation_selectors=args.variation_selectors,
            whitespace=args.whitespace,
            line_breaks=args.line_breaks,
            notes=args.notes,
            note_open=markers[0],
            note_close=markers[1],
            annotation_open=annotations[0],
            annotation_close=annotations[1],
            variants=load_variant_table(args.variants) if args.variants else None,
        )
        count, outputs = command(args, run_id, Settings(normalization=options))
    except (
        InputError,
        DslError,
        PatternFileError,
        NormalizationError,
        QueryError,
        UnicodeEncodeError,
        OSError,
    ) as error:
        _emit(f"error: {error}", error=True)
        return 1
    _emit(f"{count} results; wrote {outputs.csv}, {outputs.jsonl}, {outputs.run}")
    return 0


def _match(args: argparse.Namespace, run_id: str, settings: Any) -> tuple[int, Any]:
    from cpe.match.run import match_files

    results, outputs = match_files(
        args.patterns,
        args.inputs,
        args.out,
        run_id,
        settings,
        force=args.force,
        on_warning=lambda msg: _emit(msg, error=True),
    )
    return len(results), outputs


def _pair(args: argparse.Namespace, run_id: str, settings: Any) -> tuple[int, Any]:
    from cpe.match.dsl import DslError
    from cpe.match.pair import pair_spec
    from cpe.match.run import match_specs

    ranges = []
    for label, text in (("--a", args.a), ("--b", args.b)):
        low, separator, high = text.partition("..")
        # str.isdigit() also accepts digits such as superscripts that int()
        # rejects; only ASCII 0-9 reach int() here.
        ascii_digits = all(
            part and len(part) <= 6 and all("0" <= c <= "9" for c in part)
            for part in (low, high)
        )
        if not separator or not ascii_digits:
            raise DslError("syntax", f"{label} must be MIN..MAX, for example 1..20")
        ranges.append((int(low), int(high)))
    spec = pair_spec(args.first, args.second, ranges[0], ranges[1], args.scope)
    results, outputs = match_specs(
        [spec],
        Path("pair"),
        spec.pattern.canonical().encode("utf-8"),
        args.inputs,
        args.out,
        run_id,
        settings,
        force=args.force,
        on_warning=lambda msg: _emit(msg, error=True),
    )
    return len(results), outputs


def _search(args: argparse.Namespace, run_id: str, settings: Any) -> tuple[int, Any]:
    from cpe.local.search import search_files

    records, outputs = search_files(
        args.query,
        args.inputs,
        args.out,
        run_id,
        settings,
        args.scope,
        force=args.force,
    )
    return len(records), outputs


if __name__ == "__main__":
    raise SystemExit(main())
