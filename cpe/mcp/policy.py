"""Host-selected filesystem capabilities for MCP tools."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class PolicyError(ValueError):
    """A tool asked for a capability the host did not grant."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PathPolicy:
    """Resolve paths before checking containment, including missing output leaves."""

    def __init__(
        self,
        read_roots: Iterable[Path] = (),
        write_roots: Iterable[Path] = (),
    ) -> None:
        self.write_roots = tuple(path.expanduser().resolve() for path in write_roots)
        self.read_roots = tuple(
            dict.fromkeys(
                [
                    *(path.expanduser().resolve() for path in read_roots),
                    *self.write_roots,
                ]
            )
        )

    def check(self, value: str | Path, *, write: bool = False) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise PolicyError("path_required")
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            raise PolicyError("path_not_allowed") from None
        roots = self.write_roots if write else self.read_roots
        if not any(
            path == root or (root.is_dir() and path.is_relative_to(root))
            for root in roots
        ):
            raise PolicyError("write_not_allowed" if write else "path_not_allowed")
        return path

    def describe(self) -> dict[str, list[str]]:
        return {
            "read_roots": [str(path) for path in self.read_roots],
            "write_roots": [str(path) for path in self.write_roots],
        }
