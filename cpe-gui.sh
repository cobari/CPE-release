#!/bin/sh
# Linux and macOS: open the search window (cpe gui) from this repository.
# Run it from a file manager ("Run" / "Run in terminal") or a shell. Arguments are
# passed to cpe gui (text files or folders); without them the window opens the folder
# used last time.
set -eu
root="$(cd "$(dirname "$0")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            PATH="$(dirname "$candidate"):$PATH"
            export PATH
        fi
    done
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "cpe gui needs uv (https://docs.astral.sh/uv/), which is not installed."
    printf "Install uv now with its official installer? [y/N] "
    read -r answer
    case "$answer" in
        y|Y|yes|YES)
            curl -LsSf https://astral.sh/uv/install.sh | sh
            PATH="$HOME/.local/bin:$PATH"
            export PATH
            ;;
        *)
            echo "Not installed. See https://docs.astral.sh/uv/ and run this file again."
            exit 1
            ;;
    esac
fi

cd "$root"
exec uv run --project "$root" cpe gui "$@"
