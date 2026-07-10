#!/usr/bin/env bash
# Run from inside a working copy of base/. Installs deps (idempotent — skips
# install if node_modules already present) then runs the full vitest suite.
# Exit 0 = pass, nonzero = fail.
set -euo pipefail

if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund --silent
fi

npm test --silent
