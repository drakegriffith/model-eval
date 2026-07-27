#!/usr/bin/env bash
# Proves this task is solvable end-to-end:
#   1. verify.sh MUST FAIL against the unpatched base/
#   2. applying solution.patch MUST make verify.sh PASS
# Exit 0 + "SELFTEST PASS <task>" only if both hold. Offline, no network.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_NAME="$(basename "$TASK_DIR")"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

WORK="$TMP_DIR/work"
cp -R "$TASK_DIR/base" "$WORK"

# --- Phase 1: unpatched base must FAIL verify.sh ---
( cd "$WORK" && bash "$TASK_DIR/verify.sh" ) >"$TMP_DIR/pre.log" 2>&1
PRE_STATUS=$?

if [ "$PRE_STATUS" -eq 0 ]; then
  echo "SELFTEST FAIL $TASK_NAME: verify.sh passed on unpatched base (expected a failure)"
  echo "--- verify.sh output (pre-patch) ---"
  cat "$TMP_DIR/pre.log"
  exit 1
fi

# --- Phase 2: apply the reference solution ---
if ! ( cd "$WORK" && git apply --whitespace=nowarn "$TASK_DIR/solution.patch" ) >"$TMP_DIR/apply.log" 2>&1; then
  echo "SELFTEST FAIL $TASK_NAME: solution.patch did not apply cleanly"
  cat "$TMP_DIR/apply.log"
  exit 1
fi

# --- Phase 3: patched base must PASS verify.sh ---
( cd "$WORK" && bash "$TASK_DIR/verify.sh" ) >"$TMP_DIR/post.log" 2>&1
POST_STATUS=$?

if [ "$POST_STATUS" -ne 0 ]; then
  echo "SELFTEST FAIL $TASK_NAME: verify.sh failed after applying solution.patch"
  echo "--- verify.sh output (post-patch) ---"
  cat "$TMP_DIR/post.log"
  exit 1
fi

echo "SELFTEST PASS $TASK_NAME"
exit 0
