#!/usr/bin/env bash
# Run from inside a working copy of base/. Installs deps (idempotent) then
# runs BOTH suites:
#   1. the visible vitest suite in test/, with normal reporting
#   2. the hidden acceptance suite, which lives beside this script at
#      tasks/t5-ts-a/acceptance/ and NOT inside base/. It is staged into
#      .acceptance/ for the duration of this run only, deleted on exit, and
#      reported through the dot reporter so a failure yields a count and a
#      position — no test names, no assertion detail.
# The runner copies this script into the working copy, orphaning it from
# acceptance/ — it passes the canonical task dir via GAUNTLET_TASK_DIR.
# Exit 0 = pass, nonzero = fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${GAUNTLET_TASK_DIR:-$SCRIPT_DIR}"
STAGE=".acceptance"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund --silent
fi

npm test --silent

echo "--- acceptance ---"
rm -rf "$STAGE"
cp -R "$SCRIPT_DIR/acceptance" "$STAGE"

set +e
npx --no-install vitest run --reporter=dot --silent "$STAGE" >"$STAGE.log" 2>&1
ACCEPT_STATUS=$?
set -e

# Print the count lines only. Test names, file paths and assertion diffs stay
# in the log, which is deleted with the staging directory on exit.
grep -E '^ *(Test Files|Tests) ' "$STAGE.log" || echo "acceptance suite did not run"
rm -f "$STAGE.log"

exit "$ACCEPT_STATUS"
