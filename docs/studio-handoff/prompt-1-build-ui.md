# Prompt part 1: build the model registry + UI

Paste everything below the line into a fresh Claude session on the Mac
Studio. Companion facts: `findings.md` in this directory. Run this part
before prompt 2.

---

Build a model registry with a thin UI for the model-eval project. Read
`docs/studio-handoff/findings.md` in the model-eval checkout first; its
"Auto-assert rules" section is the spec for this build. If no checkout
exists, clone drakegriffith/model-eval (a checkout likely exists already;
the studio pushed branch studio/baseline-data).

## What to build

1. **Registry file** `runner/models.yaml` (or extend `runner/registry.py` if
   a registry already exists there - read it first and extend, do not
   duplicate). Same rule for the UI itself: before building anything, search
   the repo and the Studio home for an existing model-management UI (Drake's
   phrasing "the UI" may name something that already exists); extend it if
   found, and say in the PR which case you hit. One entry per (model, driver) pair. Required fields: model id,
   driver (claude-code | pi), serving config (parallel, context_length,
   max_tokens_floor, temperature, seed, quant), capability manifest
   (subagents, hooks, tool count), deterministic_loops (default false),
   noise_probe (flip rate + date, or absent), permission_mode (with
   authorizing sentence + date if bypassPermissions), timeout basis
   (turn cap + measured prefill tok/s).

2. **Auto-assertion on add**: a `add-model` entry point (CLI subcommand is
   fine; a small local web page over the same code counts as the UI) that
   writes a new entry with the seven auto-assert defaults from findings.md
   applied. The two that are never optional:
   - `deterministic_loops: false` until a 5/5-identical sequential probe on
     this exact serving config is recorded (llama.cpp batch physics).
   - pi driver entries get `subagents: false, hooks: false` and any harness
     level above L2 marked structurally-impossible.

3. **Validation gate**: a check the runner calls before dispatch that
   refuses (a) any run whose requested config differs from the registry row,
   (b) any cross-model comparison between rows with different serving
   configs, (c) any cell a driver cannot express (fail loudly as
   structurally-impossible, never score it 0).

4. **Seed data**: one entry for glm-4.7 x claude-code and one for glm-4.7 x
   pi, using the measured values in findings.md (parallel 1, context 131072,
   max_tokens floor 8192, temperature 0, seed 42, prefill 57-71 tok/s,
   deterministic_loops false, bypassPermissions with the 2026-08-25
   authorization sentence for the claude-code entry).

## Constraints

- Test-first where a check has a testable contract (the validation gate
  does). Small diffs, one concern per commit, pathspec-scoped commits.
- Do not touch LM Studio's own settings from code; the registry records the
  expected serving config and the gate refuses on mismatch. Changing LM
  Studio config stays a human action.
- Land the work: commit and push to a branch, open a PR against
  drakegriffith/model-eval, reference issue #8.

Done means: `add-model` writes an entry with all defaults asserted, the
validation gate has tests proving all three refusal cases, the two glm-4.7
seed entries exist, and the PR is open.
