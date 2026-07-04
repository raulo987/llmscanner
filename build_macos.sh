#!/usr/bin/env bash
#
# build_macos.sh — build a single-file macOS executable of LLM Scanner.
#
# Produces `dist/LLMScanner`: one self-contained binary that bundles Python,
# Tkinter/customtkinter and the app. No Python install needed to run it. Just:
#
#   ./build_macos.sh          # build dist/LLMScanner
#   open dist/LLMScanner      # (or double-click it in Finder)
#
# The icon is generated programmatically at runtime, so no image assets need to
# be shipped. customtkinter's theme JSON is collected via --collect-all.
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
PY="${VENV}/bin/python"

if [[ ! -x "$PY" ]]; then
    echo "==> Creating virtual environment"
    if command -v python3 >/dev/null 2>&1; then BOOT=python3; else BOOT=python; fi
    "$BOOT" -m venv "$VENV"
fi

echo "==> Installing app + PyInstaller"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -e . pyinstaller >/dev/null

echo "==> Building single-file executable"
"$PY" -m PyInstaller --noconfirm --clean --onefile \
    --name LLMScanner \
    --collect-all customtkinter \
    --collect-submodules llmscanner \
    --osx-bundle-identifier me.orav.llmscanner \
    app_entry.py

echo
echo "==> Done: $(pwd)/dist/LLMScanner"
echo "    Run it with:  open dist/LLMScanner   (or double-click in Finder)"
