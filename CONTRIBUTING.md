# Contributing to model-eval

This is a benchmark instrument. Its value comes from every task and every model
being graded the same way every time — so contributions that add coverage are
welcome, and contributions that quietly change what "passing" means are not.
That's what the recipes and the frozen-surfaces list below are for.

## Adding a task

A task is a self-contained, verifiable coding problem the models under test
attempt headlessly. Each task lives in its own directory under `tasks/<id>/`:

```
tasks/<id>/
  PROMPT.md        the exact prompt handed to the model
  base/             the starting repo state (with a failing/incomplete state)
  verify.sh         runs from inside a copy of base/; exit 0 = solved, nonzero = not
  solution.patch    a reference fix — `git apply` from base/ must make verify.sh pass
  selftest.sh       proves the task is well-formed (see below)
```

Requirements:

- `verify.sh` MUST fail against the unpatched `base/`. A task whose own
  reference state already passes doesn't measure anything.
- `solution.patch` MUST apply cleanly to `base/` with `git apply` and MUST
  make `verify.sh` pass afterward.
- `selftest.sh` checks exactly those two properties end to end, offline, with
  no network access, and prints `SELFTEST PASS <task>` on success. Copy an
  existing task's `selftest.sh` (they're all the same shape) rather than
  writing one from scratch.
- Register the task id in `runner/runs.yaml` under whichever sweep it belongs
  to (or a new sweep) so the runner actually schedules it.

Run your own task's selftest before opening a PR:

```
bash tasks/<id>/selftest.sh
```

CI runs every task's `selftest.sh` on every PR and asserts the number found
matches what's registered — a task directory that doesn't wire up cleanly
fails the build, not silently gets skipped.

## Adding a model

A model is a row in the registry plus a proven invocation:

1. Add one entry to `MODELS` in `runner/registry.py` — model id, `family`
   (`claude`, `codex`, or `kimi`; family determines which CLI drives it and
   how its output is parsed), and which effort tiers it exposes.
2. Add a corresponding entry to `runner/CLI-FACTS.md` with the exact
   non-interactive command you verified actually works — resolved model id,
   auth path, and any scaffold-token floor you observed. Don't guess this;
   run it and paste the real command.

New models run only on the subscription auth path (`claude -p` /
`codex exec`) unless they're metered like `kimi-k3`, which requires an
injected API key and a hard spend cap — see `runner/run.py`'s `--max-usd`
handling before adding a second metered family.

## Frozen comparability surfaces

These files define what a "pass" and a "score" mean for every row already in
the corpus. A PR that changes them changes the meaning of 193 archived
judgments and 241 archived transcripts retroactively, which is a different
and much larger claim than "I added a task" or "I added a model." CI fails
any PR that touches:

- Any **existing** task's `verify.sh` or `base/tests/` (new tasks are fine —
  editing an existing task's grading criteria is not).
- `runner/corpus_gates.py` — the `summarizable` / `tokens_in_usable`
  predicates that decide which rows are safe to read.
- `runner/effort_verdict.py` — the REAL / NO-OP / BACKWARDS / AMBIGUOUS
  thresholds that classify whether a model's effort knob is real.
- `runner/judge.py` — the `RUBRIC` the LLM judge grades against.

If a change to one of these is genuinely necessary, say so explicitly in the
PR description and an admin will merge over the check — the freeze is a
speed bump for accidental drift, not a hard wall. The rule itself lives in
`.github/workflows/ci.yml`, not in a file this document could quietly get out
of sync with.

## Running the suite locally

```
python3 -m venv .venv && .venv/bin/pip install -r runner/requirements.txt  # if present
.venv/bin/python -m pytest -q runner --ignore=runner/fixtures
for t in tasks/*/; do bash "$t/selftest.sh"; done
python3 runner/import_gate.py
python3 runner/stats.py --selftest
```

Live model runs (anything that actually calls a CLI against a real model) are
excluded from this offline suite and from CI — they need local subscription
auth and, for `kimi-k3`, a private API key. Those tests are named
`test_live_*` and are meant to be run by hand, not in a PR check.
