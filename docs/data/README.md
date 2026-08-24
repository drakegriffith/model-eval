# docs/data

External benchmark baseline for local-model comparison. This directory exists
so that model-eval's own gauntlet results (`runner/results/*.jsonl`) have
something outside the repo to sit next to when Drake starts running his own
problems through local models.

## What's here

- `external-benchmarks-2026-08.json` — vendor and independent SWE-bench
  Verified claims for the local-model candidates Drake is weighing (GLM-4.7,
  GLM-4.6, DeepSeek V4-Flash, Qwen3-Coder-Next, Kimi K2.5, Devstral 2,
  gpt-oss-120b), gathered 2026-08-21 through 2026-08-24. Values are recorded
  as-handed-off, including the gaps (missing source URLs, unassessed
  contamination/fit fields) — see `_meta.known_gaps` in the file itself.
- `results-schema-audit.md` — what fields `runner/results/*.jsonl` actually
  writes today (read from the writer code and live rows, not from docs), and
  a gap table against what a local-model run additionally needs to record
  (tokens/sec, prefill vs decode, quant level, hardware id, wall-clock,
  context length).

## Own-gauntlet numbers vs external numbers: not the same instrument

`external-benchmarks-2026-08.json` reports SWE-bench Verified — a fixed,
public suite of real GitHub issues, graded by whatever scaffold each vendor
or evaluator chose to run.

`runner/results/*.jsonl` reports model-eval's own gauntlet — a small
in-repo task set (`tasks/t1`..`t5`), graded by this repo's own
`verify.sh`/broker pipeline, run through this repo's own CLI harness.

Different tasks, different scaffolds, different grading code. A model
scoring higher on SWE-bench Verified than another model is not guaranteed to
score higher on model-eval's tasks, and the reverse holds too. Treat any
comparison between the two files as **directional only** — "this model
claims to be strong on hard real-world bugs, let's see if that shows up
here" — never as a calibration or a correction factor. Per the design doc at
`docs/design/2026-08-19-task-difficulty.md` (branch
`design/task-difficulty`), model-eval's own outcome is currently a single
saturated pass/fail bit on most corpora, so even the internal number needs
more resolution before it is a strong signal on its own — a second reason
not to lean on either number's precision on the third decimal.

## Ingesting into a Claude session

One-liner that pulls both instruments into context for a session comparing
own-gauntlet results against the external baseline:

```
cat runner/results/*.jsonl docs/data/external-benchmarks-2026-08.json
```

Point Claude at the concatenated output (or just the file paths) and ask it
to line up model names across both; there is no shared schema or join key
between the two files today (model-eval keys results by `run_id`/`model`,
the baseline file keys by `model` as a free string) — that's a manual
lookup, not an automatic join, until/unless the models get a shared id.
