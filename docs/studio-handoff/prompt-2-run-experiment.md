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

## Amendments, registered 2026-08-25 before stage 0 (no row exists yet)

Decided by Drake on 2026-08-25 ~23:15 EDT after the three-seat panel
(record: claude-harness docs/panels/2026-08-25-glm-stage1-deviations/,
model-eval issues #18, #19, #22, #25). Each amendment names the option
not taken. Nothing below may change after the first stage-0 row.

A1. Acceptance cap K. Acceptance feedback is metered by the broker at
K=20 model-visible verify.sh requests per run (broker.K_CEILING; the bundle
above registered no K). A run that exhausts K ends with exit_reason
cap_exhausted and is SCORED as an autonomy failure: the request count is
the model's own behaviour and is invariant to server load, which is what
separates it from the wall clock (excluded above). Stage 0 runs with the
broker ON at K=20 so the request count is measured; "no cap" in stage 0
means no turn cap. The stage-1 report states max(acceptance_requests), its
distribution, and the cap_exhausted count beside the pass rate.
Flip, fixed now: if any stage-0 rep reaches >= 10 acceptance requests, K
is a live treatment and must be re-registered from GLM's own distribution
before stage 1 starts. Every "pre-registration section 7" citation in the
runner is re-pointed to this paragraph. Not taken: (i) cap_exhausted
EXCLUDED, because runs that burn 20 checks are disproportionately the
non-converging ones and the exit would flatter the rate; (ii) an uncapped
closed-book broker that counts and never terminates (Codex seat), because
it is a new broker mode plus a prompt change, two unregistered treatments.

A2. Stage 1 is 60 rows: 45 (glm-4.7 x claude-code, 15 tasks x 3 reps)
plus 15 (Sonnet x claude-code control, 1 rep). The pi vehicle contrast
(15 rows) is stage 1b, dispatched only after run.py dispatches on the row's
driver and refuses drivers it cannot launch, with a test asserting argv[0]
for driver=pi (issue #25). Its table is published only when every pi row
carries driver: pi and a cli_binary_path naming the pi binary, and the
tables render the pi arm as its own row. Not taken: running the arm now;
at caec128 the runner launches the claude binary for every row and would
stamp driver=pi on claude-code rows.

A3. Turn cap. N is registered after stage 0 as N = 3 x max(turns) over the
5 stage-0 reps, rounded up to the next multiple of 10 (2 turns -> 10,
12 -> 40). Enforcement is post-hoc: any row with turns > N is re-classed
exit_reason turn_cap, EXCLUDED from the pass denominator and reported
beside the rate as a lost run (findings.md rule 7: cap expiry is a distinct
status, never a task fail), and a turn_cap row is DONE, never re-run. The
wall clock is a hang backstop only: timeout_s = N x 157 s x 1.5, rounded up
to the next 600 s; timeouts stay EXCLUDED lost runs. Stage 0 also reports
max(wall_s)/min(wall_s) over its reps (Kimi seat's 2x check) as a
descriptive, not a gate. Not taken: `claude --max-turns` on the argv (the
claude binary on this machine lists no such flag, checked 2026-08-25);
wiring serving_registry.derive_turn_cap_s as written (prefill-only model,
24 s predicted vs 314 s measured for a 2-turn run).

A4. Rep collapse. Per task, rate = scored passes / scored reps, where a
scored rep is one whose exit_reason class is SCORED (pass, fail,
cap_exhausted); EXCLUDED reps (timeout, infra, turn_cap) leave that task's
denominator. A task with zero scored reps is LOST: it is reported on the
lost-runs line and never counted as 0. Headline = unweighted mean of
per-task rates over tasks that survive the Sonnet control and have >= 1
scored rep. The Wilson 95% interval and the rule-of-three bound use the
task-level binary task-pass = more than half of scored reps pass (2 of 3,
or 2 of 2; a 1 of 2 split is a fail, the conservative side), n = surviving
tasks. Both the fractional mean and the binary count are printed. Chosen by
the conductor under Drake's instruction to register the rule; Drake did not
pick the form. Not taken: task-pass = all scored reps pass (stricter; one
word to flip), task-pass = any rep passes (flattering under a 20pp noise
floor).

A5. Blocker 4 (auto-compaction with ANTHROPIC_BASE_URL at LM Studio)
stays UNVERIFIED and is DEFERRED by Drake on 2026-08-25 to before stage 2.
Stage 1 mitigation: every row records turns and per-run token counts; if
the stage-2 check finds compaction at stage-1 context lengths, the stage-1
turn and token columns are marked UNVERIFIED in the report. The pass rate
is unaffected because pass is judged by the task's own check.

A6. Reps rule after stage 0 (restating the decision rule above so it is
next to the amendments that depend on it): 5/5 identical -> stage-1 reps
cut from 3 to 2 (A4's binary then reads 2 of 2); any flip -> 3 reps kept.
The conductor fills N (A3) and the rep count from the stage-0 comment on
issue #8 and records both in a follow-up commit to this file before the
first stage-1 row.

A7. Supersedes: issue #22's clause "any stage-1 run exhausting K forces
#18 to be resolved before publication" is replaced by A1 (the rule is fixed
now, not chosen after the data). #22's reporting requirement stands.
