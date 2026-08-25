# Prompt part 2: noise probe, then the GLM autonomy experiment

Paste everything below the line into a fresh Claude session on the Mac
Studio AFTER prompt 1's registry has landed. Companion facts:
`findings.md` in this directory.

Framing (Drake, 2026-08-25): the MAIN experiment is autonomy - can GLM 4.7
complete tasks end-to-end without a parent agent fixing them, the way a
swarm seat must. The harness-dose ladder is a second-level abstraction and
runs only after the autonomy answer exists. As of 2026-08-25 the autonomy
test has NOT been run: the only evidence is smoke tests (one 2-turn bash
echo in claude mode, prose-only pi returns).

---

Run the pre-registered GLM experiment for model-eval. Read
`docs/studio-handoff/findings.md` first; clear ALL FOUR "Blocking bugs"
items before any row counts as data - the three code fixes, plus item 4:
actually check claude-code auto-compaction behavior with ANTHROPIC_BASE_URL
pointed at LM Studio (it is UNVERIFIED; a long run that silently compacts
mid-task corrupts the turn and token columns). Serving config for
every run: LM Studio PARALLEL=1, context 131072, temperature 0, seed 42,
max_tokens >= 8192, turn caps not wall-clock caps, claude driver in
bypassPermissions (authorized 2026-08-25). If LM Studio is not already in
this config, stop and ask Drake to set it; do not change it yourself.

## Stage 0: noise probe (~40 min, gate for everything after)

1 task (pick a mid-difficulty gauntlet task), 1 harness level, 5 SEQUENTIAL
reps, no cap. Record per rep: pass/fail, turns, wall_s, tokens_out.
Deliverable: flip count out of 5 plus wall-clock spread, posted as a comment
on model-eval issue #8 before continuing.

Decision rule (pre-registered): 5/5 identical -> nondeterminism is MINOR;
you may cut reps below from 3 to 2. Any flip -> ~20pp noise floor confirmed;
keep every rep count as written.

## Stage 1: autonomy experiment (the main one)

Question: unsupervised end-to-end completion rate. A run counts as
autonomous-pass only if the task's own check passes with ZERO parent
intervention - no retry by the orchestrator, no fixup commit, no manual
nudge. Timeouts and infra errors are distinct statuses, excluded from the
denominator and reported separately, never counted as model failures.

- Arms: glm-4.7 x claude-code (primary), 15 tasks x 3 reps.
  Sonnet x claude-code positive control, same 15 tasks x 1 rep: any task
  Sonnet fails is a broken task and leaves the denominator (a control arm
  failure indicts the task, not GLM).
- Unit: the task. Reps collapse to a per-task rate before any statistic.
- Report: per-task table (task, GLM pass fraction, Sonnet control, turns,
  wall_s, tokens_out), overall unsupervised pass rate with a Wilson 95%
  interval, and if GLM passes all n tasks, the rule-of-three upper bound
  (~3/n) instead of claiming perfection.
- pi arm: same tasks, glm-4.7 x pi, 1 rep, reported in a separate table
  labeled as a vehicle contrast. Never merged with the claude-code numbers:
  pi has no hooks and no subagents, so the driver is part of the treatment.

## Stage 2: harness dose (only after stage 1 lands)

Ladder L1-L5 (cumulative harness levels) x the surviving task set x 3 reps,
claude-code only, plus a token-matched placebo arm (L5-sized inert text) and
a leave-one-out row at the top level. Analysis stack, in order, all
pre-registered in model-eval issue #8: calibration gate -> primary contrast
L5 vs L1 -> isotonic fit + cluster bootstrap over tasks -> Page's L trend
test -> Tango CI on adjacent deltas + Bayesian P(|delta| < 4pp) ->
fixed-sequence gatekeeping (5v4 -> 4v3 -> ... stop at first non-equivalent)
-> scrambled-label negative control -> cost axis (tokens and turns).
Drake's 4% rule is reported as P(gap < 4pp) per adjacent pair; he picks the
level, the table does not.

## Stage 3: on the floor (only if stage 1 passes)

If the unsupervised pass rate clears whatever bar Drake sets after seeing
the stage 1 table, propose glm-4.7 x claude-code as a swarm seat: a PR to
drakegriffith/claude-harness adding a row to `lib/swarm/ROLES.md`'s seat
roster (invoke via `lib/swarm/glm_swarm.sh -m claude`), citing the stage 1
table as the evidence. Drake merges; nothing joins the roster on a seat's
own verdict. GLM 5 is out of scope for this run because it is not installed
on the Studio; when it is, prompt 1's add-model flow plus the stage 0 noise
probe is its onboarding path, then this same experiment reruns with a GLM 5
arm.

## Landing

Every stage: rows committed and pushed on a branch, results table posted to
model-eval issue #8, PR opened when code changed. State the manifest in
every report: rows produced / rows dispatched, with distinct counts for
timeout and infra statuses. Silence is not evidence; a stage that inspected
zero subjects failed.
