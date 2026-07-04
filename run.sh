#!/usr/bin/env bash
#
# run.sh — launch LLM Scanner directly.
#
# Creates a virtualenv and installs dependencies on first run, then starts
# the GUI. Safe to run repeatedly; subsequent launches skip setup and start
# immediately. No prior steps required.
#
#   ./run.sh            # launch the GUI
#   ./run.sh --cli ...  # run the command-line interface instead
#   ./run.sh --reinstall# force-reinstall dependencies, then launch
#
set -euo pipefail

# Always operate from the project root (the directory holding this script),
# regardless of where it was invoked from.
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
PY_BIN="${VENV}/bin/python"
STAMP="${VENV}/.deps-installed"

# Pick a Python 3 interpreter for bootstrapping the venv.
if command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PY="python3"
elif command -v python >/dev/null 2>&1; then
    BOOTSTRAP_PY="python"
else
    echo "error: no python3 found on PATH; please install Python 3.9+" >&2
    exit 1
fi

# Handle --reinstall by clearing the stamp so deps are reinstalled below.
if [[ "${1:-}" == "--reinstall" ]]; then
    rm -f "$STAMP"
    shift
fi

# Create the venv if it is missing.
if [[ ! -x "$PY_BIN" ]]; then
    echo "==> Creating virtual environment in ${VENV}"
    "$BOOTSTRAP_PY" -m venv "$VENV"
fi

# Install/refresh dependencies once. The stamp records that it succeeded so we
# don't pay pip's cost on every launch. pyproject.toml is the source of truth;
# installing the project (-e .) pulls in httpx + customtkinter, plus the
# optional CLI extras (rich).
if [[ ! -f "$STAMP" || "pyproject.toml" -nt "$STAMP" ]]; then
    echo "==> Installing dependencies (first run or pyproject.toml changed)"
    "$PY_BIN" -m pip install --upgrade pip >/dev/null
    "$PY_BIN" -m pip install -e ".[cli]"
    touch "$STAMP"
fi

# --cli routes to the command-line interface; anything else launches the GUI.
if [[ "${1:-}" == "--cli" ]]; then
    shift
    exec "$PY_BIN" -m llmscanner.cli "$@"
else
    exec "$PY_BIN" -m llmscanner "$@"
fi
