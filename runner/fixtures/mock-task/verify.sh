#!/usr/bin/env bash
# Runs from inside a working copy of base/. Exit 0 = pass.
set -e
cd "$(dirname "$0")"
python3 test_solve.py
