# Results schema audit: what's captured today, and what local-model runs need

Purpose: before Drake starts running his own problems through this repo
against local models, know what the results pipeline already records and
where it falls short of what a local-model run needs to be comparable
(within model-eval) and legible against the external baseline
(`external-benchmarks-2026-08.json`).

Method: read the writer code (`runner/run.py`, `runner/usage_ledger.py`,
`runner/token_units.py`) and live rows in `runner/results/*.jsonl` on
`master` as of this branch's fork point. No `runner/` files were edited —
this seat owns `docs/` only; a parallel seat owns `runner/`.

## 1. What `runner/results/*.jsonl` actually contains

There is no single schema — `runner/results/` holds one canonical file plus
several purpose-built side files with their own ad hoc fields. Enumerated
from the writer code and confirmed against live rows.

### `results.jsonl` — canonical per-run outcome row

Written at `runner/run.py` (the `row = {...}` block, ~line 1303-1333), one
row appended per model invocation via `append_row`.

| field | what it is |
|---|---|
| `run_id` | `{sweep}--{model}--{effort}--{harness_tag}--{task}--r{rep}` |
| `ts` | ISO timestamp at write time |
| `sweep` | which named sweep config produced this run |
| `model` | model alias as written in the run config (stable across CLI id changes) |
| `model_id` | resolved CLI model id actually invoked |
| `effort` | reasoning-effort tier |
| `harness` | bool, bare vs harnessed |
| `task` | task id (`tasks/<id>`) |
| `rep` | replicate number |
| `pass` | grader verdict, forced `False` whenever `exit_reason != "ok"` (ticket 34) |
| `pass_raw` | grader's verdict with no completeness gate applied (`None` if never returned) |
| `pass_at_cap` | `pass_raw` narrowed to the broker-cap-exhausted case, else `None` |
| `tokens_in` / `tokens_out` | usage as parsed from the CLI's own output |
| `usage_parser_version` | which parse formula wrote `tokens_in`/`tokens_out` |
| `tokens_in_status` | `"measured"` vs `"quarantined"`, per model family + parser version |
| `wall_s` | wall-clock seconds for the run |
| `turns` | CLI-reported turn count (family-dependent meaning; barred from cross-family comparison, see `runner/CLI-FACTS.md`) |
| `loc_changed` | lines changed in the scratch tree at grading time |
| `exit_reason` | `"ok"` / `"cap_exhausted"` / `"verify_timeout"` / `"cli_error"` / `"no_completion"` / etc — this repo's completeness gate |
| `sealed` | whether the OS-level sandbox seal was active |
| `write_contained` | whether writes stayed inside the scratch allowlist |
| `invocation_mode` | `"single_shot"` vs `"multi_turn"`, which instrument shape produced the row |
| `brokered` / `k_cap` / `acceptance_requests` | metered-broker bookkeeping for tasks with hidden acceptance suites |
| `cap_exhausted` | bool, derived from `exit_reason` |
| `tampered` / `tamper_files` | whether the model touched files the tamper-check watches |

### `usage.jsonl` — billing/usage detail, joinable to `results.jsonl` by `run_id`

Written by `runner/usage_ledger.py:build_usage_row`, one row per run, prospective-only (ticket 08 — never retrofit onto prior rows).

`run_id, ts, model, model_id, family, tokens_in, tokens_out, cache_read_tokens, cache_creation_tokens, scaffold_overhead_tokens, scaffold_overhead_source, billing_mode, usd_estimate, usd_estimate_kind, pricing_date, retrofit_status, kind, judged_run_id`

18 fields total. A prior draft of this table listed 16 and omitted `kind`
and `judged_run_id` — both written by the same `build_usage_row` `return {}`
block, ~line 513 (found by the PR #2 review, not re-derived independently
here). `kind` distinguishes `"worker"` rows from `"judge"` rows;
`judged_run_id` links a judge row back to the run it graded. **Anyone
summing `usd_estimate` across `usage.jsonl` for a cost comparison without
filtering on `kind` will silently double-count judge-row cost against the
worker run it graded** — treat `kind == "worker"` as the default filter for
any cost rollup built from this file.

### `context_series.jsonl` — per-request context occupancy (Claude/Codex only, snapshot-driven)

Written by `runner/token_units.py snapshot`, reading each CLI's own session
log (`~/.claude/projects/...` or `~/.codex/sessions/...`) — a separate,
manually-triggered step, not part of the main `run.py` write path. Per run:
`run_id, family, model_id, source, requests` where `requests` is a list of
`[fresh, cache_creation, cache_read, out]` tuples, one per model request in
the session. `runner/token_units.py:figures()` reduces this to
`session_total`, `peak_context_final`, `peak_context_max`, `cache_weighted`,
`cache_read_total`.

### Other side files (not part of the canonical schema, listed for completeness)

`ladder-*.jsonl` (probe-endpoint results, own `family/cmd/text/answer_correct` schema), `judgments.jsonl` (judge rubric scores, keyed by `run_id`, no outcome fields), `regrade-36*.jsonl` (regrade audit trail with `failing_tests`/`canonical_sha256`/etc), `quarantine.jsonl` (excluded rows with a `quarantine_note`), `negative-control-28*.jsonl` (sabotage-arm results, own `arm/passed/tamper_report` schema), `calibration*.jsonl` (same shape as `results.jsonl`, separate file per sweep). None of these add fields relevant to the gap table below beyond what's already covered by `results.jsonl`/`usage.jsonl`/`context_series.jsonl`.

## 2. Gap table: what a local-model run additionally needs

| need | status | detail |
|---|---|---|
| **tokens/sec** | partially derivable, not written | `tokens_out / wall_s` on any `results.jsonl` row gives a blended rate (queue time + prefill + decode all folded into `wall_s`), not a clean decode-only figure. **Needs a new field** — e.g. `decode_tokens_per_sec`, sourced from the local backend's own streaming timestamps (llama.cpp/ollama/LM Studio all expose per-token or per-chunk timing that hosted CLIs don't). Writer site: `runner/run.py`, same block that parses `tokens_in`/`tokens_out` via `usage_ledger.parse_usage_detailed` (~line 1230-1232) — would need a local-backend-specific parser added to `usage_ledger.py` alongside the existing per-family ones. |
| **prefill vs decode** | not captured | No field on any row splits time-to-first-token from decode time; `wall_s` is a single undifferentiated span. **Needs new fields** — e.g. `ttft_s` (time to first token) and `decode_s`. Same writer site as above. Note: local inference backends generally report this split natively (it's a standard llama.cpp/ollama metric), so this is *easier* to add for local runs than it would have been to retrofit onto the hosted-CLI rows already in the corpus. |
| **quant level** | not captured anywhere | No field records how a model was quantized (e.g. `Q4_K_M`, `int4`, `bf16`). This is a static property of how the local model was loaded, not something parsed from a CLI response. **Needs a new field** — e.g. `quant`, threaded from the run config the same way `model`/`effort`/`harness` already are (`conf["model"]` pattern, `runner/run.py` ~line 346, flowing into the `row = {...}` block at ~line 1303). Runner would also need a `runs.yaml` field to source it from. |
| **hardware id** | not captured anywhere | No field identifies which machine (chip, RAM) ran a given row — irrelevant while every run was API/subscription-hosted, load-bearing once runs are local. **Needs a new field** — e.g. `hardware_id`, either a fixed constant sourced from a local env var/config (pattern: `usage_ledger.py`'s `billing_mode` is written as a constant today) or read from `os.uname()` at run time. Writer site: same `row = {...}` block. |
| **wall-clock** | already captured | `wall_s` is on every `results.jsonl` row today. No gap. |
| **context length** | partially derivable, with real caveats | `context_series.jsonl` (via `runner/token_units.py`) gives `peak_context_max`/`peak_context_final` — actual context occupancy per run — but only for Claude/Codex, only via a separate manually-triggered `snapshot` step reading each CLI's own session log outside the repo, and it measures *occupancy at time of request*, not the model's *configured context window/limit*. Neither figure is on the canonical `results.jsonl` row. **Needs a new field** for the configured limit — e.g. `context_window`, static and threaded from run config the same way `quant` would be (same `runs.yaml`/`conf["model"]` pattern). If per-request occupancy is wanted for local runs too, the local backend needs an equivalent of `token_units.py`'s session-log parser — local backends generally expose this more directly (no separate CLI session-log file to reverse-engineer) than the Claude/Codex path did. |

Everything in this table is a recommendation naming the field and the
writer site — **no `runner/` file was edited by this seat**; the other seat
working `runner/` on its own branch owns implementing any of these.

## 3. Cross-check against `docs/design/2026-08-19-task-difficulty.md` (branch `design/task-difficulty`, PR #1)

That design doc's ticket 1 proposes three new `results.jsonl` fields —
`acceptance_total`, `acceptance_failed`, `pass_frac` — written at the same
`run.py` writer site as the fields in the gap table above (the `row = {...}`
block). Its purpose is orthogonal to this audit's: pass_frac is an
**outcome-resolution** fix (turning a saturated pass/fail bit into a
k-of-N fraction so arms of differing ability actually separate), not a
**performance/hardware** field. There is no field-name overlap between that
proposal and this table.

The one thing that does connect the two: both proposals land in the exact
same block of `run.py`. If/when ticket 1 lands, whoever implements the local-
model fields above (`quant`, `hardware_id`, `context_window`,
`decode_tokens_per_sec`, `ttft_s`/`decode_s`) should do it in the same pass
as `pass_frac` rather than as a second migration of the same writer — one
schema change to `results.jsonl`'s row shape, not two, is the cheaper
sequencing. This is a sequencing note for whoever picks up runner/ work
next, not a request to change anything on this branch.

No other field in the design doc's tickets (2 through 5: the informative-
band gate, the t6 harvest, the Rasch fit, the judge discrimination check)
touches the results-row schema at all — they're all readers of
`results.jsonl`/`judgments.jsonl`, not writers, so nothing else to cross-
check against this table.
