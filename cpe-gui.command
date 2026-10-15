#!/bin/sh
# macOS: double-click to open the search window (cpe gui) from this repository.
# Needs uv (https://docs.astral.sh/uv/); uv fetches Python and the dependencies on the
# first run. Finder runs .command files in Terminal; the file must keep its executable
# bit (recorded in git).
exec "$(dirname "$0")/cpe-gui.sh" "$@"
