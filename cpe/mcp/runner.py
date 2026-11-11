"""Host configuration for the optional local stdio MCP server."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="host-selected UTF-8 text files, folders or CPE bundles",
    )
    parser.add_argument(
        "--enable-online",
        action="append",
        default=[],
        help="allow a 3.1 online source, docusky:PROFILE, or all; repeat as needed",
    )
    parser.add_argument(
        "--itkc-log", type=Path, help="required request log path when ITKC is enabled"
    )
    parser.add_argument("--note-markers", nargs=2, metavar=("OPEN", "CLOSE"))
    parser.add_argument("--annotation-markers", nargs=2, metavar=("OPEN", "CLOSE"))
    parser.add_argument(
        "--read-root",
        type=Path,
        action="append",
        default=[],
        help="allow tool reads under this path; repeat as needed",
    )
    parser.add_argument(
        "--write-root",
        type=Path,
        action="append",
        default=[],
        help="allow tool writes/reads under this folder; no writes by default",
    )
    parser.add_argument(
        "--library",
        nargs=2,
        action="append",
        default=[],
        metavar=("SLOT", "FOLDER"),
        help="assign local/local2/local3/local4 to a host-selected folder",
    )
    parser.add_argument(
        "--enable-import",
        action="append",
        default=[],
        choices=(
            "portal",
            "ctp",
            "gutenberg",
            "ndl",
            "rise",
            "cbeta",
            "nikh-partial",
            "all",
        ),
        help="allow a network import provider or all; no network imports by default",
    )
    parser.add_argument(
        "--workspace-state",
        type=Path,
        help="dedicated MCP state file; GUI preferences are not used automatically",
    )
    parser.add_argument(
        "--ctp-key-env",
        default="CPE_CTP_API_KEY",
        help="host environment variable containing the optional CTP key",
    )
    parser.add_argument(
        "--language", choices=("ko", "ja", "zh-Hans", "zh-Hant", "vi", "en")
    )
    parser.add_argument("--sort", choices=("source", "era", "era-desc"))
    parser.add_argument(
        "--variants", type=Path, help="researcher-selected variant table TOML"
    )
    for option in ("variation-selectors", "whitespace", "line-breaks"):
        parser.add_argument("--" + option, choices=("keep", "delete"))
    parser.add_argument("--notes", choices=("barrier", "join"))


def run(args: argparse.Namespace) -> int:
    if (
        any(value in args.enable_online for value in ("itkc", "all"))
        and args.itkc_log is None
    ):
        print("error: --enable-online itkc requires --itkc-log PATH", file=sys.stderr)
        return 2
    try:
        from cpe.mcp.server import create_server
    except ModuleNotFoundError as error:
        if error.name not in {"mcp", "anyio", "mcp_types"}:
            raise
        print(
            "error: MCP support needs the optional extra: uv sync --extra mcp",
            file=sys.stderr,
        )
        return 2
    from cpe.gui.service import Library
    from cpe.local.file_lock import exclusive
    from cpe.local.txt import InputError
    from cpe.match.engine import Settings
    from cpe.mcp.policy import PathPolicy, PolicyError, host_path
    from cpe.mcp.service import SearchService
    from cpe.mcp.sources import LOCAL_SOURCE_IDS, SOURCE_IDS, SourceRegistry
    from cpe.mcp.workspace import IMPORT_PROVIDERS, Workspace
    from cpe.normalize.options import NormalizationError, Options, load_variant_table

    notes = args.note_markers or (None, None)
    annotations = args.annotation_markers or (None, None)
    workspace = None
    try:
        inputs = [host_path(path) for path in args.inputs]
        slots: dict[str, Path] = {}
        for code, path in args.library:
            if code not in LOCAL_SOURCE_IDS or code in slots:
                raise ValueError(
                    "--library needs a unique local/local2/local3/local4 slot"
                )
            slots[code] = host_path(path)
        variants = host_path(args.variants) if args.variants else None
        if args.itkc_log is not None:
            args.itkc_log = host_path(args.itkc_log)
        startup_policy = PathPolicy(
            (
                *inputs,
                *args.read_root,
                *slots.values(),
                *((variants,) if variants else ()),
            ),
            args.write_root,
        )
        try:
            for path in inputs:
                startup_policy.check_library(path)
            if variants is not None:
                startup_policy.check(variants)
        except PolicyError as error:
            raise ValueError(error.code) from error
        settings = Settings(
            normalization=Options(
                note_open=notes[0],
                note_close=notes[1],
                annotation_open=annotations[0],
                annotation_close=annotations[1],
                variation_selectors=args.variation_selectors or "keep",
                whitespace=args.whitespace or "keep",
                line_breaks=args.line_breaks or "delete",
                notes=args.notes or "barrier",
                variants=load_variant_table(variants) if variants else None,
            )
        )
        library = Library.load(inputs, settings)
        online = tuple(
            dict.fromkeys(
                code
                for value in args.enable_online
                for code in (SOURCE_IDS if value == "all" else (value,))
            )
        )
        imports = tuple(
            dict.fromkeys(
                code
                for value in args.enable_import
                for code in (IMPORT_PROVIDERS if value == "all" else (value,))
            )
        )
        with ExitStack() as stack:
            state = None
            if args.workspace_state is not None:
                state = host_path(args.workspace_state)
                if state.suffix.lower() in {".txt", ".md", ".toml", ".jsonl"}:
                    raise ValueError("MCP state must not use a source-text file suffix")
                from cpe.gui.preferences import config_dir

                if (
                    state == (config_dir() / "gui.json").resolve()
                    or any(state == path for path in inputs if path.is_file())
                    or any(
                        state.is_relative_to(path)
                        for path in (*inputs, *slots.values())
                        if path.is_dir()
                    )
                ):
                    raise ValueError(
                        "MCP state must not overlap GUI preferences or source libraries"
                    )
                state.parent.mkdir(parents=True, exist_ok=True)
                stack.enter_context(
                    exclusive(state.with_name(state.name + ".mcp.lock"))
                )
            explicit_normalization = any(
                getattr(args, key) is not None
                for key in (
                    "note_markers",
                    "annotation_markers",
                    "variants",
                    "variation_selectors",
                    "whitespace",
                    "line_breaks",
                    "notes",
                )
            )
            workspace = Workspace(
                library,
                input_paths=inputs,
                read_roots=(
                    *args.read_root,
                    *slots.values(),
                    *((variants,) if variants else ()),
                ),
                write_roots=args.write_root,
                enabled_imports=imports,
                state_path=state,
                ctp_key=os.environ.get(args.ctp_key_env, ""),
                restore_normalization=not explicit_normalization,
            )
            registry = SourceRegistry(
                workspace.settings, enabled_online=online, itkc_log=args.itkc_log
            )
            workspace.attach_registry(registry)
            service = SearchService(
                workspace.api._library,
                registry=registry,
                enabled_online=online,
                itkc_log=args.itkc_log,
                library_provider=workspace.library,
                local_status=workspace.library_status,
                external_retained=lambda: workspace._itkc_bytes,
            )
            workspace.attach_service(service)
            for code, path in slots.items():
                result = workspace.manage_library("open", code, str(path))
                if "error" in result:
                    raise ValueError(f"cannot open library {code}: {result['error']}")
            if args.language is not None or args.sort is not None:
                result = workspace.configure(language=args.language, sort=args.sort)
                if "error" in result:
                    raise ValueError(str(result["error"]))
            try:
                create_server(service, workspace).run(transport="stdio")
            finally:
                workspace.close()
    except (InputError, NormalizationError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if workspace is not None:
            workspace.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        prog="cpe-mcp",
        description=(
            "Classic Pattern Explorer local MCP server (stdio only). "
            "No online source is enabled by default."
        ),
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))
