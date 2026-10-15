"""Entry point of the packaged window (EXE) and of `python -m cpe.gui`."""

import sys

from cpe.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["gui", *sys.argv[1:]]))
