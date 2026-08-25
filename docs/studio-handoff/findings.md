# Surviving data and rules (2026-08-25 panel)

Marks: GROUNDED = re-derived by the dispatcher; SEAT-MEASURED = one seat
measured it live on the Studio serving stack; UNVERIFIED = stated but not
checked. Source of record: model-eval issue #8; ledger rows claude-harness
7ef8656, d20d071.

## Measured serving-stack facts (SEAT-MEASURED unless noted)

- Prefill throughput 57-71 tok/s, degrading with prompt length; a 61k-token
  prefill took 1077 s. Wall clock therefore measures prompt size before it
  measures the model.
- Under PARALLEL=4, one seat's decode fell to 0.05 tok/s while a neighbor
  prefilled: a 380x wall-clock swing on identical work. The runner scores
  non-ok exits as fail, so under contention the scheduler grades the model.
- Determinism does not exist on this stack: identical prompt, temperature 0,
  seed 42 gave 3/3 distinct outputs concurrently and 2/3 distinct
  sequentially (llama.cpp batching, upstream issue #7052, kv_unified).
  "Deterministic loop" is replaced by replication plus a variance column.
- Loaded context 65536 was a choice: the model's max_context_length is
  202752. PARALLEL=1 at 131072 costs no extra KV memory versus the old
  PARALLEL=4 at 65536.
- GLM reasoning tokens consume max_tokens: at a 600-token cap, 5/6 probes
  returned empty content. Floor max_tokens at 8192.
- pi driver surface: 7 tools (grep/find/ls off by default), --skill,
  --prompt-template, -e extensions. NO hooks, NO subagents. High harness
  levels are structurally impossible under pi, so pi is a separately labeled
  vehicle contrast, never merged into the dose table.
- claude driver on GLM: auto permission mode asks the serving model to
  safety-check its own tool calls and GLM times out on the check, so seats
  run bypassPermissions. Authorization (GROUNDED - Drake's typed sentence,
  2026-08-25, recorded in the header of
  `~/.claude/lib/swarm/glm_swarm.sh`): "regarding this tool using Claude
  mode GLM seats, I want my Claude to have the ability to use models like
  GLM. If you want to invoke a quick Opus 5 agent in the backend to do
  that, I allow you to bypass and make it happen on the Mac Studio and this
  computer." Scope: GLM seats on the two machines, nothing else.
  Best-case anchor: 314 s for a 2-turn bash task with the small ~450-token
  gauntlet harness (~157 s/turn), zero contention. The 25k-token personal
  harness costs 20+ minutes of prefill per turn and is not an experiment arm.

## Blocking bugs (clear all four before any row counts as data)

1. GROUNDED: `runner/usage_ledger.py:107` - family "local" falls into the
   codex-JSONL parser branch, so every GLM row silently records tokens=0,
   turns=0.
2. GROUNDED: `runner/run.py` env construction (~line 656) never isolates
   HOME or CLAUDE_CONFIG_DIR, so the harness=False "bare" arm loads Drake's
   real ~25k-token global harness. The baseline is contaminated.
3. GROUNDED: `runner/judge.py:67-70` parses run_id positionally from the
   END. New segments (agent, harness_level) must be inserted BEFORE the last
   two segments or resume-dedupe collides across cells.
4. UNVERIFIED: claude-code auto-compaction behavior under ANTHROPIC_BASE_URL
   pointed at LM Studio. Blocking in the check-it sense: run the check
   before long runs; a silent mid-task compaction corrupts turn and token
   columns.

## Flip facts

- Noise probe 5/5 identical sequential outcomes -> nondeterminism demoted to
  MINOR; rep counts shrink; cheaper design defensible. Any flip -> ~20pp
  noise floor stands; full rep structure mandatory.
- 15 tasks resolve pass-rate gaps of roughly 20-40pp, never 4pp. Proving
  "within 4pp" formally needs 260-800 paired tasks. Drake's 4% rule is
  therefore reported as a probability, P(gap < 4pp), not a pass/fail verdict.
  The fact that flips this: funding ~10x more tasks.
- Sonnet positive-control arm failing a task -> that task is broken, not GLM;
  it leaves the autonomy denominator (dispatch invariant 3).

## Auto-assert rules when adding a new model to the registry/UI

Adding a model asserts these defaults; overriding any one requires recorded
evidence in the model's row:

1. `deterministic_loops: false` - llama.cpp batch physics; flips to true only
   after a 5/5-identical sequential probe on THIS serving config.
2. Driver capability manifest recorded per driver: pi -> subagents=false,
   hooks=false, tools=7; claude-code -> full surface. Cells a driver cannot
   express are marked structurally-impossible, not failed.
3. Serving config pinned to the row: PARALLEL, context length, max_tokens
   floor, temperature, seed, quant. Comparisons are valid only between rows
   with identical serving config.
4. Reasoning-token probe on add: if a small max_tokens cap returns empty
   content, record the floor (glm-4.7: 8192).
5. Noise probe required before the model enters any cross-model comparison;
   store the flip rate on the row.
6. Permission mode recorded per driver-model pair; bypassPermissions entries
   carry the authorizing typed sentence and date, scoped to that pair.
7. Timeout basis derived from measured prefill rate x prompt size, stored as
   a turn cap; wall-clock timeouts log a distinct status, never a task fail.
