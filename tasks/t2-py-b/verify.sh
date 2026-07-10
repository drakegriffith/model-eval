#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
if ! "$VENV_DIR/bin/python" -c "import pytest" >/dev/null 2>&1; then
  "$VENV_DIR/bin/pip" install -q -r requirements.txt
fi
"$VENV_DIR/bin/python" -m pytest -q
