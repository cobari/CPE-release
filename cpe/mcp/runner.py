"""Host configuration for the optional local stdio MCP server."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

from cpe.mcp.onboarding import DEFAULT_CTP_KEY_ENV


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
        help=(
            "allow a 3.3 online source, docusky:DATABASE with the catalogue's "
            "exact source value, or all; repeat as needed"
        ),
    )
    parser.add_argument(
        "--itkc-log", type=Path, help="required request log path when ITKC is enabled"
    )
    parser.add_argument(
        "--allow-source-text",
        action="append",
        default=[],
        metavar="SOURCE",
        help=(
            "host permits source text in client results for this source or "
            "import:PROVIDER; repeat; references only by default; "
            "not a provider licence"
        ),
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
        default=DEFAULT_CTP_KEY_ENV,
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
    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "check this configuration locally and exit; starts no stdio "
            "transport and makes no network request"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="print the --doctor report as JSON"
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="validate this configuration and print an mcpServers JSON entry",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the CPE version and optional MCP package status and exit",
    )


def _emit_stderr(text: str) -> None:
    if sys.stderr is None:
        return
    from cpe.mcp.onboarding import emit

    try:
        emit(text + "\n", sys.stderr)
    except UnicodeEncodeError:
        encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
        sys.stderr.write(
            (text + "\n").encode(encoding, errors="replace").decode(encoding)
        )
        sys.stderr.flush()


def run(args: argparse.Namespace) -> int:
    if getattr(args, "version", False):
        from cpe.mcp.onboarding import emit, version_text

        emit(version_text())
        return 0
    if args.doctor and args.print_config:
        _emit_stderr("error: --doctor and --print-config cannot be combined")
        return 2
    if args.json and not (args.doctor or args.print_config):
        _emit_stderr("error: --json needs --doctor")
        return 2
    if args.doctor:
        return _doctor(args)
    if args.print_config:
        return _print_config(args)
    return _serve(args)


def _doctor(args: argparse.Namespace) -> int:
    from cpe.mcp.onboarding import diagnose, emit, to_json

    try:
        report = diagnose(args)
    except KeyboardInterrupt:
        return 130
    emit(to_json(report.as_dict()) if args.json else report.as_text())
    return 0 if report.ok else 2


def _print_config(args: argparse.Namespace) -> int:
    from cpe.mcp.onboarding import diagnose, emit, server_config, to_json

    try:
        report = diagnose(args)
    except KeyboardInterrupt:
        return 130
    if not report.ok:
        # Print nothing on stdout: a host must not paste a half-valid entry.
        _emit_stderr("error: configuration is invalid; see --doctor")
        for check in report.checks:
            if check.status == "fail":
                _emit_stderr(f"error: {check.id}: {check.message}")
        return 2
    emit(to_json(server_config(args, report.config)))
    return 0


def _serve(args: argparse.Namespace) -> int:
    from cpe.local.txt import InputError
    from cpe.mcp.onboarding import StartupError, prepare
    from cpe.normalize.options import NormalizationError

    workspace = None
    try:
        with ExitStack() as stack:
            try:
                config = prepare(args, stack)
            except StartupError as error:
                _emit_stderr(f"error: {error.check.message}")
                return error.exit_code
            # prepare() has imported the server module, so the optional
            # extra is present; import here so tests can replace the factory.
            from cpe.mcp.server import create_server
            from cpe.mcp.service import SearchService
            from cpe.mcp.sources import SourceRegistry
            from cpe.mcp.workspace import Workspace

            assert config.library is not None
            variants = (config.variants,) if config.variants else ()
            workspace = Workspace(
                config.library,
                input_paths=config.inputs,
                read_roots=(*config.read_roots, *config.slots.values(), *variants),
                write_roots=config.write_roots,
                protected_paths=variants,
                enabled_imports=config.imports,
                state_path=config.state,
                ctp_key=os.environ.get(args.ctp_key_env, ""),
                restore_normalization=not config.explicit_normalization,
                disclosure=config.disclosure,
            )
            registry = SourceRegistry(
                workspace.settings,
                enabled_online=config.online,
                itkc_log=config.itkc_log,
            )
            workspace.attach_registry(registry)
            service = SearchService(
                workspace.api._library,
                registry=registry,
                enabled_online=config.online,
                itkc_log=config.itkc_log,
                library_provider=workspace.library,
                local_status=workspace.library_status,
                external_retained=lambda: workspace._itkc_bytes,
                disclosure=config.disclosure,
            )
            workspace.attach_service(service)
            for code, path in config.slots.items():
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
        _emit_stderr(f"error: {error}")
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
