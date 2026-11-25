"""Startup validation shared by stdio serving, ``--doctor`` and ``--print-config``.

Every host flag is checked by one ordered list of steps.  Serving stops at the
first failure and keeps the workspace-state lock; ``--doctor`` runs every step
it can, reports the rest as skipped, and leaves no new folder or lock file on
disk.  No step starts the stdio transport, contacts an online source or reads
the value of the CTP key variable beyond whether it is set.

Only the standard library is imported at module level: ``cpe.cli`` imports the
runner (and therefore this module) to build every subcommand's parser.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TextIO

if TYPE_CHECKING:
    from cpe.gui.service import Library
    from cpe.match.engine import Settings
    from cpe.mcp.disclosure import DisclosurePolicy

DEFAULT_CTP_KEY_ENV = "CPE_CTP_API_KEY"
_FALLBACK_VERSION = "3.3"
# Top-level modules of the optional [mcp] extra.  A missing module outside this
# set is a packaging bug and must surface as a traceback, not as advice to
# install the extra.
_MCP_MODULES = frozenset({"mcp", "anyio", "mcp_types"})
_NORMALIZATION_KEYS = (
    "note_markers",
    "annotation_markers",
    "variants",
    "variation_selectors",
    "whitespace",
    "line_breaks",
    "notes",
)
_STATE_SUFFIXES = frozenset({".txt", ".md", ".toml", ".jsonl"})
_ITKC_FORBIDDEN_SUFFIXES = (".txt", ".md", ".toml", ".texts.jsonl")

Status = Literal["pass", "fail", "skip"]


@dataclass(frozen=True)
class Check:
    """One preflight result; ``message`` never contains a secret value."""

    id: str
    status: Status
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "message": self.message}


class CheckFailed(Exception):
    """A step's host configuration cannot start the server."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class StartupError(Exception):
    """The first failing step when serving; carries the serving exit status."""

    def __init__(self, check: Check, exit_code: int) -> None:
        super().__init__(check.message)
        self.check = check
        self.exit_code = exit_code


@dataclass
class StartupConfig:
    """Validated startup values.  It deliberately holds no CTP key value."""

    disclosure: DisclosurePolicy | None = None
    online: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    itkc_log: Path | None = None
    inputs: list[Path] = field(default_factory=list)
    slots: dict[str, Path] = field(default_factory=dict)
    variants: Path | None = None
    read_roots: list[Path] = field(default_factory=list)
    write_roots: list[Path] = field(default_factory=list)
    settings: Settings | None = None
    library: Library | None = None
    state: Path | None = None
    explicit_normalization: bool = False
    ctp_key_present: bool = False


@dataclass(frozen=True)
class Report:
    """The outcome of a full preflight run."""

    checks: tuple[Check, ...]
    config: StartupConfig

    @property
    def ok(self) -> bool:
        return all(check.status == "pass" for check in self.checks)

    def summary(self) -> dict[str, Any]:
        config = self.config
        return {
            "passed": sum(check.status == "pass" for check in self.checks),
            "failed": sum(check.status == "fail" for check in self.checks),
            "skipped": sum(check.status == "skip" for check in self.checks),
            "online_sources": list(config.online),
            "network_imports": list(config.imports),
            "source_text": (
                sorted(config.disclosure.describe()["host_allowed_source_text"])
                if config.disclosure is not None
                else []
            ),
            "write_roots": [str(path) for path in config.write_roots],
            "ctp_key": "present" if config.ctp_key_present else "unset",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
            "summary": self.summary(),
        }

    def as_text(self) -> str:
        lines = [f"cpe-mcp doctor: {'OK' if self.ok else 'FAILED'}"]
        for check in self.checks:
            label = "FAIL" if check.status == "fail" else check.status
            lines.append(f"  [{label}] {check.id}: {check.message}")
        summary = self.summary()
        lines.append(
            f"{summary['passed']} passed, {summary['failed']} failed, "
            f"{summary['skipped']} skipped"
        )
        lines.append("No stdio transport was started and no network request was made.")
        return "\n".join(lines) + "\n"


class _Run:
    def __init__(
        self, args: argparse.Namespace, *, doctor: bool, hold: ExitStack | None
    ) -> None:
        self.args = args
        self.doctor = doctor
        # Serving keeps the state lock on the caller's stack for the server's
        # lifetime; ``None`` means probe and release without leaving a lock file.
        self.hold = hold
        self.config = StartupConfig()
        self.itkc_collision = False


def version_text() -> str:
    """Report the CPE version and optional MCP package status without serving."""
    from cpe.mcp.build_info import embedded_build_info

    try:
        cpe_version = metadata.version("classic-pattern-explorer")
    except metadata.PackageNotFoundError:
        cpe_version = _FALLBACK_VERSION
    try:
        importlib.import_module("mcp")
        importlib.import_module("anyio")
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] not in _MCP_MODULES:
            raise
        mcp_status = "mcp not installed"
    else:
        try:
            mcp_version = metadata.version("mcp")
        except metadata.PackageNotFoundError:
            mcp_status = "mcp installed"
        else:
            mcp_status = f"mcp {mcp_version} installed"
    build = embedded_build_info()
    tracked_changes = " (tracked working tree changes)" if build["dirty"] else ""
    return (
        f"cpe-mcp {cpe_version} "
        f"(classic-pattern-explorer {cpe_version}, {mcp_status})\n"
        f"Build commit {build['commit'] or 'unavailable'}{tracked_changes}\n"
    )


def _host(value: Path | str, flag: str) -> Path:
    from cpe.mcp.policy import PolicyError, host_path

    try:
        return host_path(value)
    except PolicyError as error:
        raise CheckFailed(f"{flag}: {error.code}: {value}") from None


def _norm_parts(path: Path) -> tuple[str, ...]:
    """Case-folded path components for cross-platform collision checks."""
    return tuple(part.casefold() for part in Path(os.path.normcase(str(path))).parts)


def _paths_overlap(left: Path, right: Path) -> bool:
    """Whether ``left`` and ``right`` are equal or one contains the other."""
    lp = _norm_parts(left)
    rp = _norm_parts(right)
    if not lp or not rp:
        return False
    limit = min(len(lp), len(rp))
    return lp[:limit] == rp[:limit]


def _candidate_paths(value: str | Path) -> tuple[Path, ...]:
    """Lexical, parent-resolved and fully resolved forms of ``value``."""
    candidates: list[Path] = []
    try:
        raw = Path(value).expanduser()
        absolute = raw.absolute()
        candidates.append(absolute)
        candidates.append(raw.resolve())
        if absolute.parent != absolute:
            candidates.append(absolute.parent.resolve() / absolute.name)
    except (OSError, RuntimeError, ValueError):
        pass
    return tuple(dict.fromkeys(candidates))


def _walk_library_files(root: Path) -> Iterator[Path]:
    from cpe.local.paths import is_directory_link, is_within

    try:
        for current, directories, files in os.walk(root, followlinks=False):
            parent = Path(current)
            directories[:] = [
                name
                for name in directories
                if not name.startswith(".")
                and not is_directory_link(parent / name)
                and is_within(root, parent / name)
            ]
            for name in files:
                if not name.startswith("."):
                    yield parent / name
    except OSError:
        return


def _protected_role_paths(
    args: argparse.Namespace,
    *,
    include_itkc_log: bool,
    include_workspace_state: bool,
) -> Iterator[tuple[str, Path]]:
    from cpe.gui.preferences import config_dir
    from cpe.local.txt import MANIFEST_SUFFIX

    for path in _candidate_paths(config_dir() / "gui.json"):
        yield ("GUI preferences (gui.json)", path)
    if args.variants is not None:
        for path in _candidate_paths(args.variants):
            yield ("--variants", path)
    if include_workspace_state and args.workspace_state is not None:
        for path in _candidate_paths(args.workspace_state):
            yield ("--workspace-state", path)
            yield (
                "--workspace-state",
                path.with_name(path.name + ".mcp.lock"),
            )
    if include_itkc_log and args.itkc_log is not None:
        for path in _candidate_paths(args.itkc_log):
            yield ("--itkc-log", path)
    sources: list[tuple[str, str | Path]] = [
        *(("input", item) for item in args.inputs),
        *(("--library", folder) for _, folder in args.library),
    ]
    for label, raw in sources:
        candidates = _candidate_paths(raw)
        for path in candidates:
            yield (label, path)
            for sidecar in _candidate_paths(
                path.with_name(path.name + MANIFEST_SUFFIX)
            ):
                yield (".manifest.toml sidecar", sidecar)
        for path in candidates:
            if not path.is_dir():
                continue
            for entry in _walk_library_files(path):
                entry_role = (
                    ".manifest.toml sidecar"
                    if entry.name.lower().endswith(MANIFEST_SUFFIX)
                    else label
                )
                for file_path in _candidate_paths(entry):
                    yield (entry_role, file_path)
                for sidecar in _candidate_paths(
                    entry.with_name(entry.name + MANIFEST_SUFFIX)
                ):
                    yield (".manifest.toml sidecar", sidecar)


def _nearest_existing_ancestor(path: Path) -> Path:
    current = path.parent
    while (
        not current.exists() and not current.is_symlink() and current.parent != current
    ):
        current = current.parent
    return current


def _validate_itkc_log_path(args: argparse.Namespace, itkc_log: Path) -> None:
    log_candidates = _candidate_paths(args.itkc_log)
    for role, target in _protected_role_paths(
        args, include_itkc_log=False, include_workspace_state=True
    ):
        if any(_paths_overlap(log_path, target) for log_path in log_candidates):
            raise CheckFailed(f"--itkc-log must not overlap {role}: {itkc_log}")
    if itkc_log.is_dir():
        raise CheckFailed(f"--itkc-log must not be a folder: {itkc_log}")
    lower_name = itkc_log.name.lower()
    if lower_name.endswith(".manifest.toml"):
        raise CheckFailed(
            f"--itkc-log must not overlap .manifest.toml sidecar: {itkc_log}"
        )
    if lower_name.endswith(_ITKC_FORBIDDEN_SUFFIXES):
        raise CheckFailed(
            f"--itkc-log must not use a source-text or config suffix: {itkc_log}"
        )
    ancestor = _nearest_existing_ancestor(itkc_log)
    if not ancestor.is_dir():
        raise CheckFailed(f"--itkc-log parent is not a folder: {ancestor}")


def _check_itkc_log(run: _Run) -> str:
    args = run.args
    enabled = any(value in args.enable_online for value in ("itkc", "all"))
    if enabled and args.itkc_log is None:
        raise CheckFailed("--enable-online itkc requires --itkc-log PATH", 2)
    if args.itkc_log is None:
        return "not required: ITKC is not enabled"
    try:
        itkc_log = _host(args.itkc_log, "--itkc-log")
        _validate_itkc_log_path(args, itkc_log)
    except CheckFailed:
        run.itkc_collision = True
        raise
    run.config.itkc_log = itkc_log
    return f"request log: {itkc_log}"


def _check_dependencies(run: _Run) -> str:
    try:
        importlib.import_module("cpe.mcp.server")
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] not in _MCP_MODULES:
            raise
        raise CheckFailed(
            "MCP support needs the optional extra: uv sync --extra mcp", 2
        ) from None
    return "optional [mcp] extra is installed"


def _check_disclosure(run: _Run) -> str:
    from cpe.mcp.disclosure import DisclosurePolicy

    values = list(dict.fromkeys(run.args.allow_source_text))
    unknown = [value for value in values if not DisclosurePolicy.valid(value)]
    if unknown:
        raise CheckFailed(f"unknown --allow-source-text scope: {', '.join(unknown)}")
    run.config.disclosure = DisclosurePolicy(values)
    if not values:
        return "references only (default)"
    return (
        f"source text allowed for: {', '.join(sorted(values))} "
        "(host grant, not a provider licence)"
    )


def _check_online(run: _Run) -> str:
    from cpe.mcp.sources import SOURCE_IDS, valid_source

    values = run.args.enable_online
    unknown = [value for value in values if value != "all" and not valid_source(value)]
    if unknown:
        raise CheckFailed(f"unknown --enable-online source: {', '.join(unknown)}")
    run.config.online = tuple(
        dict.fromkeys(
            code
            for value in values
            for code in (SOURCE_IDS if value == "all" else (value,))
        )
    )
    if not run.config.online:
        return "no online source enabled (default)"
    return f"enabled, not contacted: {', '.join(run.config.online)}"


def _check_imports(run: _Run) -> str:
    from cpe.mcp.workspace import IMPORT_PROVIDERS

    values = run.args.enable_import
    unknown = [
        value for value in values if value != "all" and value not in IMPORT_PROVIDERS
    ]
    if unknown:
        raise CheckFailed(f"unknown --enable-import provider: {', '.join(unknown)}")
    run.config.imports = tuple(
        dict.fromkeys(
            code
            for value in values
            for code in (IMPORT_PROVIDERS if value == "all" else (value,))
        )
    )
    if not run.config.imports:
        return "no network import enabled (default)"
    return f"enabled, not contacted: {', '.join(run.config.imports)}"


def _check_inputs(run: _Run) -> str:
    paths = [_host(value, "input") for value in run.args.inputs]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise CheckFailed(f"input not found: {', '.join(missing)}")
    run.config.inputs = paths
    if not paths:
        return "no startup inputs"
    return f"{len(paths)} input(s) found"


def _check_library_slots(run: _Run) -> str:
    from cpe.mcp.sources import LOCAL_SOURCE_IDS

    slots: dict[str, Path] = {}
    for code, value in run.args.library:
        if code not in LOCAL_SOURCE_IDS or code in slots:
            raise CheckFailed(
                "--library needs a unique local/local2/local3/local4 slot"
            )
        slots[code] = _host(value, "--library")
    # Serving would open each slot after the workspace is built and fail there;
    # checking the folder here reports the same refusal before any state write.
    missing = [f"{code} {path}" for code, path in slots.items() if not path.is_dir()]
    if missing:
        raise CheckFailed(f"--library folder not found: {', '.join(missing)}")
    run.config.slots = slots
    if not slots:
        return "no library slots assigned"
    return f"assigned: {', '.join(slots)}"


def _check_variants(run: _Run) -> str:
    if run.args.variants is None:
        return "no variant table (default)"
    variants = _host(run.args.variants, "--variants")
    if not variants.is_file():
        raise CheckFailed(f"--variants file not found: {variants}")
    run.config.variants = variants
    return f"variant table: {variants}"


def _check_path_policy(run: _Run) -> str:
    from cpe.mcp.policy import PathPolicy, PolicyError

    config = run.config
    read_roots = [_host(value, "--read-root") for value in run.args.read_root]
    write_roots = [_host(value, "--write-root") for value in run.args.write_root]
    not_folders = [str(path) for path in write_roots if not path.is_dir()]
    if not_folders:
        raise CheckFailed(f"--write-root is not an existing folder: {not_folders[0]}")
    variants = (config.variants,) if config.variants else ()
    policy = PathPolicy(
        (*config.inputs, *read_roots, *config.slots.values(), *variants),
        write_roots,
    )
    current: Path | None = None
    try:
        for current in (*config.inputs, *config.slots.values()):
            policy.check_library(current)
        for current in variants:
            policy.check(current)
    except PolicyError as error:
        raise CheckFailed(f"{error.code}: {current}") from None
    config.read_roots = read_roots
    config.write_roots = write_roots
    writes = (
        f"{len(write_roots)} write root(s)"
        if write_roots
        else "no write roots (writes disabled by default)"
    )
    return f"{len(policy.read_roots)} read root(s); {writes}"


def _read_state(state: Path) -> None:
    from cpe.mcp.workspace import WorkspacePreferences

    try:
        WorkspacePreferences(state)
    except (OSError, ValueError) as error:
        raise CheckFailed(f"cannot read MCP state {state}: {error}") from None


def _check_workspace_state(run: _Run) -> str:
    from cpe.local.file_lock import FileBusyError, exclusive

    config = run.config
    if run.args.workspace_state is None:
        return "not set: workspace state is kept in memory only"
    state = _host(run.args.workspace_state, "--workspace-state")
    if state.suffix.lower() in _STATE_SUFFIXES:
        raise CheckFailed("MCP state must not use a source-text file suffix")
    if state.is_dir():
        raise CheckFailed(f"MCP state must not be a folder: {state}")
    state_candidates = _candidate_paths(run.args.workspace_state)
    for role, target in _protected_role_paths(
        run.args, include_itkc_log=True, include_workspace_state=False
    ):
        if any(_paths_overlap(candidate, target) for candidate in state_candidates):
            raise CheckFailed(
                "MCP state must not overlap GUI preferences, --itkc-log or "
                f"source libraries ({role})"
            )
    ancestor = _nearest_existing_ancestor(state)
    if not ancestor.is_dir():
        raise CheckFailed(f"MCP state parent is not a folder: {ancestor}")
    if run.hold is None:
        # The doctor is strictly non-mutating: it never creates folders or
        # leaves a newly created .mcp.lock file on disk.
        if not state.parent.is_dir():
            config.state = state
            return f"{state} (folder created at startup; lock not tested)"
        lock = state.with_name(state.name + ".mcp.lock")
        lock_existed = lock.exists() or lock.is_symlink()
        try:
            try:
                with exclusive(lock):
                    _read_state(state)
            except FileBusyError:
                raise CheckFailed(
                    f"MCP state is in use by another process: {state}"
                ) from None
        finally:
            if not lock_existed:
                try:
                    lock.unlink(missing_ok=True)
                except OSError:
                    pass
        config.state = state
        return f"{state} (lock acquired and released)"
    if state.exists():
        _read_state(state)
    config.state = state
    return f"{state} (validated)"


def _check_normalization(run: _Run) -> str:
    from cpe.match.engine import Settings
    from cpe.normalize.options import Options, load_variant_table

    args = run.args
    config = run.config
    notes = args.note_markers or (None, None)
    annotations = args.annotation_markers or (None, None)
    config.settings = Settings(
        normalization=Options(
            note_open=notes[0],
            note_close=notes[1],
            annotation_open=annotations[0],
            annotation_close=annotations[1],
            variation_selectors=args.variation_selectors or "keep",
            whitespace=args.whitespace or "keep",
            line_breaks=args.line_breaks or "delete",
            notes=args.notes or "barrier",
            variants=load_variant_table(config.variants) if config.variants else None,
        )
    )
    config.explicit_normalization = any(
        getattr(args, key) is not None for key in _NORMALIZATION_KEYS
    )
    if config.explicit_normalization:
        return "explicit options; saved MCP normalization is not restored"
    return "defaults; saved MCP normalization may be restored"


def _probe_library(paths: Iterable[Path], settings: Settings) -> int:
    """Validate readability, manifests, bundles and variants without TextViews."""
    from cpe.gui.service import _folder_files, _read_file
    from cpe.local.txt import (
        MANIFEST_SUFFIX,
        InputError,
        parse_manifest_bytes,
        text_options,
    )
    from cpe.match.engine import check_variants
    from cpe.match.segment import FullStop, Punctuation
    from cpe.normalize.options import note_spans

    ids: list[str] = []
    delimiters: list[tuple[Punctuation, FullStop, bool]] = []
    for path in paths:
        if not path.is_dir():
            for local in _read_file(path):
                if local.text_type != "original":
                    raise InputError(
                        f"{local.path}: text_type {local.text_type!r} not matched"
                    )
                note_spans(local.reference, text_options(local, settings.normalization))
                ids.append(local.text_id)
                delimiters.append(
                    (
                        local.punctuation,
                        local.full_stop,
                        local.halfwidth_punctuation,
                    )
                )
            continue
        files, unreadable = _folder_files(path)
        if unreadable:
            raise InputError(f"{path}: unreadable entry: {unreadable[0]}")
        for entry in _walk_library_files(path):
            if entry.name.lower().endswith(MANIFEST_SUFFIX):
                parse_manifest_bytes(entry, entry.read_bytes())
        for file in files:
            relative = file.relative_to(path).as_posix()
            for local in _read_file(file):
                if local.text_type != "original":
                    raise InputError(
                        f"{local.path}: text_type {local.text_type!r} not matched"
                    )
                note_spans(local.reference, text_options(local, settings.normalization))
                ids.append(local.text_id if local.id_from_manifest else relative)
                delimiters.append(
                    (
                        local.punctuation,
                        local.full_stop,
                        local.halfwidth_punctuation,
                    )
                )
    duplicates = sorted(text_id for text_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise InputError(f"duplicate text_id: {', '.join(duplicates)}")
    check_variants(settings, delimiters)
    return len(ids)


def _check_library(run: _Run) -> str:
    config = run.config
    assert config.settings is not None
    if run.doctor:
        count = _probe_library(config.inputs, config.settings)
        message = f"{count} text(s) loaded from startup inputs"
        for path in config.slots.values():
            _probe_library([path], config.settings)
        if config.slots:
            message += f"; {len(config.slots)} library folder(s) loaded"
        return message
    from cpe.gui.service import Library

    config.library = Library.load(config.inputs, config.settings)
    message = f"{len(config.library.texts)} text(s) loaded from startup inputs"
    if config.library.skipped:
        message += (
            f"; {len(config.library.skipped)} file(s) skipped as unreadable "
            "or unparsable"
        )
    return message


def _check_ctp_key(run: _Run) -> str:
    name = run.args.ctp_key_env
    # Only presence is observed; the value must never reach a report or config.
    run.config.ctp_key_present = bool(name) and bool(os.environ.get(name, ""))
    return f"{name}: {'present' if run.config.ctp_key_present else 'unset'}"


_STEPS: tuple[tuple[str, Callable[[_Run], str], tuple[str, ...]], ...] = (
    ("itkc_log", _check_itkc_log, ()),
    ("dependencies", _check_dependencies, ()),
    ("disclosure", _check_disclosure, ()),
    ("online", _check_online, ()),
    ("imports", _check_imports, ()),
    ("inputs", _check_inputs, ()),
    ("library_slots", _check_library_slots, ()),
    ("variants", _check_variants, ()),
    ("path_policy", _check_path_policy, ("inputs", "library_slots", "variants")),
    (
        "workspace_state",
        _check_workspace_state,
        ("inputs", "library_slots", "variants"),
    ),
    ("normalization", _check_normalization, ("path_policy",)),
    ("library", _check_library, ("inputs", "normalization")),
    ("ctp_key", _check_ctp_key, ()),
)


def _execute(run: _Run, *, fail_fast: bool) -> list[Check]:
    from cpe.local.txt import InputError
    from cpe.normalize.options import NormalizationError

    checks: list[Check] = []
    unavailable: set[str] = set()
    for name, step, needs in _STEPS:
        blocked = [need for need in needs if need in unavailable]
        if run.itkc_collision and name in {"workspace_state", "library"}:
            blocked.append("itkc_log")
        if blocked:
            unavailable.add(name)
            checks.append(Check(name, "skip", f"needs {', '.join(blocked)}"))
            continue
        try:
            checks.append(Check(name, "pass", step(run)))
            continue
        except CheckFailed as error:
            failure = Check(name, "fail", error.message)
            exit_code = error.exit_code
        except (InputError, NormalizationError, OSError, ValueError) as error:
            failure = Check(name, "fail", str(error) or type(error).__name__)
            exit_code = 1
        if fail_fast:
            raise StartupError(failure, exit_code)
        unavailable.add(name)
        checks.append(failure)
    return checks


def prepare(args: argparse.Namespace, hold: ExitStack) -> StartupConfig:
    """Validate for serving; the state lock is acquired only after all checks pass.

    Raises ``StartupError`` for the first failing step.
    """
    from cpe.local.file_lock import FileBusyError, exclusive

    run = _Run(args, doctor=False, hold=hold)
    _execute(run, fail_fast=True)
    if run.config.state is not None:
        state = run.config.state
        lock = state.with_name(state.name + ".mcp.lock")
        try:
            state.parent.mkdir(parents=True, exist_ok=True)
            hold.enter_context(exclusive(lock))
            _read_state(state)
        except FileBusyError:
            raise StartupError(
                Check(
                    "workspace_state",
                    "fail",
                    f"MCP state is in use by another process: {state}",
                ),
                1,
            ) from None
        except CheckFailed as error:
            raise StartupError(
                Check("workspace_state", "fail", error.message), error.exit_code
            ) from None
        except OSError as error:
            raise StartupError(
                Check("workspace_state", "fail", str(error) or type(error).__name__),
                1,
            ) from None
    return run.config


def diagnose(args: argparse.Namespace) -> Report:
    """Run every step that can run; nothing is created or left locked afterwards."""
    run = _Run(args, doctor=True, hold=None)
    checks = _execute(run, fail_fast=False)
    return Report(tuple(checks), run.config)


def server_config(args: argparse.Namespace, config: StartupConfig) -> dict[str, Any]:
    """An ``mcpServers`` entry that reproduces exactly the validated flags.

    Host grants (source text, online sources, imports, read and write roots)
    appear only when the user passed them, so the printed entry keeps the
    references-only, offline, read-only defaults.
    """
    argv = [str(path) for path in config.inputs]
    for value in dict.fromkeys(args.enable_online):
        argv += ["--enable-online", value]
    if config.itkc_log is not None:
        argv += ["--itkc-log", str(config.itkc_log)]
    for value in dict.fromkeys(args.allow_source_text):
        argv += ["--allow-source-text", value]
    if args.note_markers:
        argv += ["--note-markers", *args.note_markers]
    if args.annotation_markers:
        argv += ["--annotation-markers", *args.annotation_markers]
    for path in config.read_roots:
        argv += ["--read-root", str(path)]
    for path in config.write_roots:
        argv += ["--write-root", str(path)]
    for code, path in config.slots.items():
        argv += ["--library", code, str(path)]
    for value in dict.fromkeys(args.enable_import):
        argv += ["--enable-import", value]
    if config.state is not None:
        argv += ["--workspace-state", str(config.state)]
    if args.ctp_key_env != DEFAULT_CTP_KEY_ENV:
        argv += ["--ctp-key-env", args.ctp_key_env]
    if args.language is not None:
        argv += ["--language", args.language]
    if args.sort is not None:
        argv += ["--sort", args.sort]
    if config.variants is not None:
        argv += ["--variants", str(config.variants)]
    for option in ("variation_selectors", "whitespace", "line_breaks", "notes"):
        value = getattr(args, option)
        if value is not None:
            argv += ["--" + option.replace("_", "-"), value]
    if getattr(sys, "frozen", False):
        arguments = argv
    else:
        arguments = ["-m", "cpe.mcp", *argv]
    return {"mcpServers": {"cpe": {"command": sys.executable, "args": arguments}}}


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def emit(text: str, stream: TextIO | None = None) -> None:
    """Write UTF-8 with LF line endings whatever the console code page is."""
    target = stream if stream is not None else sys.stdout
    buffer = getattr(target, "buffer", None)
    if buffer is None:
        target.write(text)
        target.flush()
        return
    target.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()
