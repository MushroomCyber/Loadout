#!/usr/bin/env bash
# Single-command launcher for Kali Tools Manager.
# Usage:  ./run.sh          (interactive CLI)
#         ./run.sh --tui    (full-screen Textual TUI)
#         ./run.sh <any kalitools args>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

# Auto-install if not already installed
if ! python -c "import kalitools" 2>/dev/null; then
    echo "[*] Installing dependencies..."
    pip install -e ".[notifications,disk,tui,fuzzy]" -q
fi

exec kalitools "$@"
