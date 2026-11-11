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


def _check_platform_path(path: Path) -> None:
    """Reject Windows device names, alternate streams and ambiguous leaves."""
    if os.name != "nt":
        return
    if path.anchor.startswith("\\\\.\\") or path.anchor.upper().startswith(
        "\\\\?\\GLOBALROOT"
    ):
        raise PolicyError("path_not_allowed")
    parts = path.parts
    if parts and (parts[0].endswith(":\\") or parts[0].startswith("\\\\")):
        parts = parts[1:]
    if any(_platform_component_problem(part) for part in parts):
        raise PolicyError("path_not_allowed")


def host_path(value: str | Path) -> Path:
    """Validate the supplied components before Windows can normalize them away."""
    try:
        raw = Path(value).expanduser()
        _check_platform_path(raw)
        resolved = raw.resolve()
        _check_platform_path(resolved)
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
        self.write_roots = tuple(host_path(path) for path in write_roots)
        roots = [*(host_path(path) for path in read_roots), *self.write_roots]
        # An explicit text file includes its exact, conventionally named sidecar,
        # never its whole parent. Keep the sidecar's lexical path: resolving an
        # existing metadata symlink here would silently grant its outside target.
        sidecars = [
            path.with_name(path.name + ".manifest.toml")
            for path in roots
            if path.suffix.lower() in {".txt", ".md"} and not path.is_dir()
        ]
        self.read_roots = tuple(dict.fromkeys([*roots, *sidecars]))

    def check(self, value: str | Path, *, write: bool = False) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise PolicyError("path_required")
        path = host_path(value)
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
                    if not is_directory_link(parent / name)
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
