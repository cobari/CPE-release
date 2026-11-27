"""Entry point of the packaged window (EXE) and of `python -m cpe.gui`."""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from collections.abc import Sequence
from pathlib import Path


def add_gui_arguments(parser: argparse.ArgumentParser) -> None:
    """Register CLI arguments for opening the desktop search window."""
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="*",
        help="UTF-8 text files (.txt, .md), text bundles (.texts.jsonl), or folders",
    )
    for flag, what in (
        ("--note-markers", "interlinear notes"),
        ("--annotation-markers", "attached annotations"),
    ):
        parser.add_argument(
            flag, nargs=2, metavar=("OPEN", "CLOSE"), help=f"markers of {what}"
        )


def _emit_stderr(text: str) -> None:
    """Write stderr lines as UTF-8 when redirected under legacy code pages."""
    stream = sys.stderr
    if stream is None:
        return
    buffer = getattr(stream, "buffer", None)
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("_", "-")
    isatty_fn = getattr(stream, "isatty", None)
    try:
        is_tty = bool(isatty_fn()) if callable(isatty_fn) else False
    except OSError:
        is_tty = False
    line = text + "\n"
    if buffer is not None and not is_tty and encoding not in ("utf-8", "utf8"):
        stream.flush()
        buffer.write(line.encode("utf-8", errors="replace"))
        buffer.flush()
        return
    try:
        stream.write(line)
        stream.flush()
    except UnicodeEncodeError:
        stream.flush()
        if buffer is not None:
            buffer.write(line.encode("utf-8", errors="replace"))
            buffer.flush()
        else:
            fallback = getattr(stream, "encoding", None) or "utf-8"
            stream.write(line.encode(fallback, errors="replace").decode(fallback))
            stream.flush()


def run_gui(args: argparse.Namespace) -> int:
    """Load initial texts or remembered folders and open the desktop GUI."""
    from cpe.gui.app import open_window, start_library
    from cpe.gui.preferences import Preferences
    from cpe.local.txt import InputError
    from cpe.match.engine import Settings
    from cpe.normalize.options import NormalizationError, Options

    notes = args.note_markers or (None, None)
    annotations = args.annotation_markers or (None, None)
    try:
        options = Options(
            note_open=notes[0],
            note_close=notes[1],
            annotation_open=annotations[0],
            annotation_close=annotations[1],
        )
        library, folder = start_library(
            args.inputs, Settings(normalization=options), Preferences()
        )
    except (InputError, NormalizationError, OSError) as error:
        _emit_stderr(f"error: {error}")
        return 1
    _emit_stderr(f"{len(library.texts)} texts loaded; opening the window")
    open_window(library, folder)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse GUI window arguments cleanly without importing CLI/MCP extras."""
    parser = argparse.ArgumentParser(
        prog="cpe-gui",
        description=(
            "Open the search window. The given texts (files, or folders whose .txt, "
            ".md, and .texts.jsonl files are read with their subfolders) are read once "
            "at start; without them the window opens the folder "
            "used last time, and another folder can be opened from the window."
        ),
    )
    add_gui_arguments(parser)
    args = parser.parse_args(argv)
    return run_gui(args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
