"""Host-selected filesystem capabilities for MCP tools."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from cpe.local.paths import is_directory_link, is_within

_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$|CLOCK\$)(?:\..*)?$",
    re.IGNORECASE,
)
_LIBRARY_SUFFIXES = (
    ".txt",
    ".md",
    ".texts.jsonl",
    ".manifest.toml",
    ".toml",
)


class PolicyError(ValueError):
    """A tool asked for a capability the host did not grant."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _platform_component_problem(part: str) -> bool:
    """Whether a Windows path component is a device, ADS or ambiguous name."""
    if not part:
        return True
    if ":" in part:
        # A drive component is removed by check() before this helper is used.
        return True
    stripped = part.rstrip(" .")
    if stripped != part or not stripped:
        return True
    if any(ord(char) < 32 or char in '<>"|?*' for char in stripped):
        return True
    return bool(_WINDOWS_RESERVED.fullmatch(stripped))


def _is_windows_device_prefix(raw: str) -> bool:
    normalized = raw.replace("/", "\\")
    return normalized in {"\\\\?", "\\\\."} or normalized.startswith(
        ("\\\\?\\", "\\\\.\\")
    )


def _is_unc_prefix(raw: str) -> bool:
    return raw.startswith(("\\\\", "//")) and not _is_windows_device_prefix(raw)


def _parse_unc_anchor(anchor: str) -> tuple[str, str] | None:
    if not anchor.startswith(("\\\\", "//")) or _is_windows_device_prefix(anchor):
        return None
    parts = anchor.replace("/", "\\").strip("\\").split("\\")
    if len(parts) != 2:
        return None
    server, share = parts
    if not server or not share or server in {".", "..", "?"} or share in {".", ".."}:
        return None
    for part in (server, share):
        if ":" in part or part.rstrip(" .") != part:
            return None
        if any(ord(char) < 32 or char in '<>"|?*' for char in part):
            return None
    return server, share


def _unc_path_parts(path: Path) -> tuple[str, ...] | None:
    parsed = _parse_unc_anchor(path.anchor)
    if parsed is None:
        return None
    server, share = parsed
    anchor = f"\\\\{server}\\{share}".casefold()
    return (anchor, *(part.casefold() for part in path.parts[1:]))


def _lexical_unc_parts(value: str | os.PathLike[str]) -> tuple[str, ...] | None:
    raw = os.fspath(value)
    if not _is_unc_prefix(raw):
        return None
    cleaned = raw.replace("/", "\\").rstrip("\\")
    if cleaned.startswith("\\\\\\"):
        return None
    segments = cleaned[2:].split("\\")
    if len(segments) < 2:
        return None
    server, share, *rest = segments
    if _parse_unc_anchor(f"\\\\{server}\\{share}\\") is None:
        return None
    for part in rest:
        if part in {"", ".", ".."} or _platform_component_problem(part):
            return None
    return (
        f"\\\\{server}\\{share}".casefold(),
        *(part.casefold() for part in rest),
    )


def _is_unc_within(
    candidate_parts: tuple[str, ...],
    root_parts: tuple[str, ...],
    *,
    root_is_dir: bool,
) -> bool:
    if candidate_parts == root_parts:
        return True
    if not root_is_dir or len(candidate_parts) <= len(root_parts):
        return False
    return candidate_parts[: len(root_parts)] == root_parts


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _unc_root_entries(
    roots: Iterable[Path], *, dir_only: bool = False
) -> tuple[tuple[tuple[str, ...], bool], ...]:
    if os.name != "nt":
        return ()
    entries: list[tuple[tuple[str, ...], bool]] = []
    for root in roots:
        parts = _unc_path_parts(root)
        if parts is not None:
            entries.append((parts, True if dir_only else _safe_is_dir(root)))
    return tuple(entries)


def _check_raw_platform_string(
    value: str | os.PathLike[str], *, allow_unc: bool = False
) -> None:
    """Reject device prefixes and ungranted UNC strings before resolve()."""
    if os.name != "nt":
        return
    raw = os.fspath(value)
    if _is_windows_device_prefix(raw):
        raise PolicyError("path_not_allowed")
    if not allow_unc and _is_unc_prefix(raw):
        raise PolicyError("path_not_allowed")


def _check_platform_path(path: Path, *, allow_unc: bool = False) -> None:
    """Reject Windows device names, alternate streams and ambiguous leaves."""
    if os.name != "nt":
        return
    if _is_windows_device_prefix(str(path)) or _is_windows_device_prefix(path.anchor):
        raise PolicyError("path_not_allowed")
    parts = path.parts
    if path.anchor.startswith(("\\\\", "//")):
        if not allow_unc or _parse_unc_anchor(path.anchor) is None:
            raise PolicyError("path_not_allowed")
        parts = parts[1:]
    elif parts and parts[0].endswith(":\\"):
        parts = parts[1:]
    if any(_platform_component_problem(part) for part in parts):
        raise PolicyError("path_not_allowed")


def host_path(value: str | Path, *, allow_unc: bool = False) -> Path:
    """Validate the supplied components before Windows can normalize them away."""
    try:
        _check_raw_platform_string(value, allow_unc=allow_unc)
        raw = Path(value).expanduser()
        _check_platform_path(raw, allow_unc=allow_unc)
        resolved = raw.resolve()
        _check_platform_path(resolved, allow_unc=allow_unc)
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise PolicyError("path_not_allowed") from None


class PathPolicy:
    """Resolve paths before checking containment, including missing output leaves."""

    def __init__(
        self,
        read_roots: Iterable[Path] = (),
        write_roots: Iterable[Path] = (),
    ) -> None:
        validated_writes: list[Path] = []
        for path in write_roots:
            resolved = host_path(path, allow_unc=True)
            if not _safe_is_dir(resolved):
                raise PolicyError("write_not_allowed")
            validated_writes.append(resolved)
        self.write_roots = tuple(validated_writes)
        roots = [
            *(host_path(path, allow_unc=True) for path in read_roots),
            *self.write_roots,
        ]
        # An explicit text file includes its exact, conventionally named sidecar,
        # never its whole parent. Keep the sidecar's lexical path: resolving an
        # existing metadata symlink here would silently grant its outside target.
        sidecars = [
            path.with_name(path.name + ".manifest.toml")
            for path in roots
            if path.suffix.lower() in {".txt", ".md"} and not path.is_dir()
        ]
        self.read_roots = tuple(dict.fromkeys([*roots, *sidecars]))
        self._write_unc_roots = _unc_root_entries(self.write_roots, dir_only=True)
        self._read_unc_roots = _unc_root_entries(self.read_roots)

    def check(self, value: str | Path, *, write: bool = False) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise PolicyError("path_required")
        unc_roots = self._write_unc_roots if write else self._read_unc_roots
        if os.name == "nt" and _is_unc_prefix(os.fspath(value)):
            candidate_parts = _lexical_unc_parts(value)
            if candidate_parts is None:
                raise PolicyError("path_not_allowed")
            if not any(
                _is_unc_within(candidate_parts, root_parts, root_is_dir=is_dir)
                for root_parts, is_dir in unc_roots
            ):
                raise PolicyError("write_not_allowed" if write else "path_not_allowed")
            path = host_path(value, allow_unc=True)
        else:
            path = host_path(value, allow_unc=bool(unc_roots))
        roots = self.write_roots if write else self.read_roots
        if not any(
            path == root or (root.is_dir() and path.is_relative_to(root))
            for root in roots
        ):
            raise PolicyError("write_not_allowed" if write else "path_not_allowed")
        return path

    def check_library(self, value: str | Path, *, write: bool = False) -> Path:
        """Check a local library tree without following escaping file links.

        GUI ``Library.load`` intentionally keeps readable file links.  MCP is
        narrower: a source file, bundle, manifest or variant table may be a file
        link only when its resolved target is still inside a granted root.
        Directory links are pruned by the GUI scanner and by this scan.
        """
        root = self.check(value, write=write)
        try:
            is_directory = root.is_dir()
        except OSError:
            raise PolicyError("path_not_allowed") from None
        if not is_directory:
            if root.name.lower().endswith((".txt", ".md")):
                sidecar = root.with_name(root.name + ".manifest.toml")
                if sidecar.exists() or sidecar.is_symlink():
                    self.check(sidecar)
            return root

        def prune(error: OSError) -> None:
            raise PolicyError("path_not_allowed") from error

        try:
            for current, directories, files in os.walk(
                root, followlinks=False, onerror=prune
            ):
                parent = Path(current)
                directories[:] = [
                    name
                    for name in directories
                    if not name.startswith(".")
                    and not is_directory_link(parent / name)
                    and is_within(root, parent / name)
                ]
                for name in files:
                    if name.startswith("."):
                        continue
                    if name.lower().endswith(_LIBRARY_SUFFIXES):
                        self.check(parent / name)
        except (OSError, RuntimeError) as error:
            raise PolicyError("path_not_allowed") from error
        return root

    def describe(self) -> dict[str, list[str]]:
        return {
            "read_roots": [str(path) for path in self.read_roots],
            "write_roots": [str(path) for path in self.write_roots],
        }
