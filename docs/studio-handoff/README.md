# Studio handoff: GLM experiment bundle (2026-08-25)

Provenance: 11-seat design panel, 2026-08-25. Full synthesis and build list:
model-eval issue #8. Seat-disagreement ledger rows: claude-harness commits
7ef8656 and d20d071. Every number below is marked GROUNDED (re-derived),
SEAT-MEASURED (one seat measured it live on the Studio), or UNVERIFIED.

## What is in this bundle

| File | Purpose |
|---|---|
| `findings.md` | Surviving data: measured serving-stack facts, flip facts, blocking bugs, auto-assert rules for new models |
| `prompt-1-build-ui.md` | Prompt part 1: build the model-registry layer + UI with auto-asserted configurations |
| `prompt-2-run-experiment.md` | Prompt part 2: noise probe, then the GLM autonomy experiment (the main experiment), harness dose second |

Run order: prompt 1 before prompt 2. Prompt 2's runs write rows the registry
from prompt 1 must already validate.

## What Drake should see on his end

Configuration every run uses (pre-registered; deviations get logged, not
silently applied):

| Setting | Value | Why |
|---|---|---|
| temperature | 0 | pre-registered; does NOT give reproducibility on this stack (SEAT-MEASURED, see findings) |
| seed | 42 | same; recorded so reps are comparable, not because it pins output (SEAT-MEASURED: non-reproducible even at temp 0 seed 42) |
| LM Studio PARALLEL | 1 during experiment windows | PARALLEL=4 produced a 380x wall-clock swing; scheduler noise lands in the accuracy column (SEAT-MEASURED) |
| context length | 131072 | model max is 202752, so the loaded 65536 was a choice; 131072 at PARALLEL=1 costs no extra KV memory (SEAT-MEASURED) |
| max_tokens | >= 8192 | GLM reasoning tokens consume max_tokens; at a 600 cap 5/6 probes returned empty content (SEAT-MEASURED) |
| permission mode (claude driver) | bypassPermissions | auto mode asks GLM to safety-check itself and it times out (SEAT-MEASURED); authorization GROUNDED: Drake's typed sentence quoted in full in findings.md, recorded in glm_swarm.sh header |
| cap type | turn cap, not wall clock | prefill runs 57-71 tok/s, so wall clock measures the prompt length, not the model; timeouts are a distinct status, never scored as task failure |

Iterations Drake will see land:

1. **Noise probe** (first, ~40 min): 1 task, 1 harness level, 5 sequential
   reps, PARALLEL=1, no cap. Deliverable: flip count out of 5 plus wall-clock
   spread.
2. **Autonomy experiment** (the main one): 15 tasks x 3 reps GLM claude-mode
   seats, plus 15 x 1 Sonnet positive-control arm. Deliverable: per-task
   completion table, unsupervised pass rate with a Wilson interval.
3. **Harness dose** (second-level, only after 1-2): ladder L1-L5 x ~15 tasks
   x 3 reps, plus token-matched placebo arm and leave-one-out row at the top.
   Deliverable: per-level pass rate + adjacent-level delta with CI +
   P(gap < 4pp) for Drake's 4% rule.

Total run count (enumerator: arms x tasks x reps, 15 surviving tasks
assumed): stage 0 = 5; stage 1 = 45 GLM + 15 Sonnet control + 15 pi = 75;
stage 2 = 225 ladder + 45 placebo + 45 leave-one-out = 315. Overall 395
runs. GLM 5 is absent from these counts because it is not installed on the
Studio; the registry's add-model flow plus the stage 0 probe onboards it
later, then stages 1-2 rerun with a GLM 5 arm.

## Recommendation and flip fact

Recommendation: run the noise probe before anything else, and clear all
four items under findings.md "Blocking bugs" before any row counts as data.

Flip fact: if the probe returns 5/5 identical outcomes with tight wall-clock,
nondeterminism drops to MINOR, the rep counts above can shrink, and a cheaper
design is defensible. Any flip in 5 confirms a ~20pp noise floor and the full
rep structure is mandatory.

## Transcription assumptions (correct me if wrong)

Drake's voice message said "FLAC files" and "patent the markdown files". Read
as: "flip facts" (included in findings.md and above) and "vet the markdown
files". Vet record: one adversarial Sonnet seat reviewed the draft bundle
against the verbatim request on 2026-08-25 and returned OBJECT with 10
findings (1 blocking, 4 major); every blocking and major finding was fixed
in this revision before transfer, and the dispatcher re-read the result. No
audio files exist in this project; if FLAC meant literal audio, say so and
I will hunt for what you meant. Both guesses stay Drake-confirmable, since
"flip facts" decides which findings.md section is authoritative.
