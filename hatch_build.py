"""Keep source-archive launchers executable even when built on Windows."""

import copy
import gzip
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

LAUNCHERS = frozenset({"cpe-gui.command", "cpe-gui.sh"})


def launcher_permissions(artifact: Path) -> None:
    descriptor, name = tempfile.mkstemp(dir=artifact.parent, suffix=".tar.gz")
    temporary = Path(name)
    found: set[str] = set()
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as output:
                    with tarfile.open(artifact, "r:gz") as source:
                        for original in source:
                            member = copy.copy(original)
                            parts = PurePosixPath(member.name).parts
                            if len(parts) == 2 and parts[-1] in LAUNCHERS:
                                if not member.isfile() or parts[-1] in found:
                                    raise ValueError("invalid source-archive launcher")
                                # Windows stat modes omit the executable bit; the
                                # archive must support the documented direct launch.
                                member.mode = 0o755
                                found.add(parts[-1])
                            if member.isfile():
                                stream = source.extractfile(original)
                                assert stream is not None
                                with stream:
                                    output.addfile(member, stream)
                            else:
                                output.addfile(member)
        if found != LAUNCHERS:
            raise ValueError("source archive is missing a launcher")
        os.replace(temporary, artifact)
    finally:
        temporary.unlink(missing_ok=True)


class CustomBuildHook(BuildHookInterface):
    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        launcher_permissions(Path(artifact_path))
