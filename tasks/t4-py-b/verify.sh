#!/usr/bin/env bash
# Run from inside a working copy of base/. Installs deps into a local venv
# (idempotent) then runs BOTH suites:
#   1. the visible suite in tests/, with full tracebacks
#   2. the hidden acceptance suite, which lives beside this script at
#      tasks/t4-py-b/acceptance/ and NOT inside base/, with --tb=no so a
#      failure reports a test id and nothing else
# The runner copies this script into the working copy, orphaning it from
# acceptance/ — it passes the canonical task dir via GAUNTLET_TASK_DIR.
# Exit 0 = pass, nonzero = fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${GAUNTLET_TASK_DIR:-$SCRIPT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c "import pytest" >/dev/null 2>&1; then
  "$VENV_DIR/bin/pip" install -q -r requirements.txt
fi

"$VENV_DIR/bin/python" -m pytest -q tests

echo "--- acceptance ---"
"$VENV_DIR/bin/python" -m pytest -q --tb=no -rN -p no:cacheprovider "$SCRIPT_DIR/acceptance"
