#!/usr/bin/env bash
# Run from inside a working copy of base/ (which, by the time this runs,
# also contains the candidate's own added files, e.g. cli.py). Installs
# deps into a local venv (idempotent — skips install if already satisfied)
# then runs the acceptance suite, which lives beside this script at
# tasks/t3-a/acceptance/ (NOT inside base/, since base/ must stay
# near-empty for the candidate). We resolve that directory relative to
# this script's own location, not relative to cwd, since cwd will be the
# working copy. Exit 0 = pass, nonzero = fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The runner copies this script into the working copy, orphaning it from
# acceptance/ — it passes the canonical task dir via GAUNTLET_TASK_DIR.
SCRIPT_DIR="${GAUNTLET_TASK_DIR:-$SCRIPT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c "import pytest" >/dev/null 2>&1; then
  "$VENV_DIR/bin/pip" install -q -r requirements.txt
fi

"$VENV_DIR/bin/python" -m pytest -q "$SCRIPT_DIR/acceptance"
