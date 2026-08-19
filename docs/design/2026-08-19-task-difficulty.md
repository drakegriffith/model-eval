# Design: task difficulty, or why the gauntlet stopped telling models apart

Date: 2026-08-19
Status: proposed
Branch: design/task-difficulty
Inputs read: `~/.claude/docs/measurements/2026-08-19-flip-fact-check.md`,
`~/.claude/docs/decisions/2026-08-19-evolutionary-harness-ab.md`,
`runner/run.py`, `runner/judge.py`, `runner/broker.py`, `runner/results/*.jsonl`,
`tasks/*/PROMPT.md`, `tasks/*/verify.sh`, `runner/PASS-FIELD-AUDIT.md`, `ANALYSIS.md`.

## 1. Zoom out: what we are deciding, and why now

The evolutionary harness A/B (ADR 2026-08-19) is about to start accumulating a
dataset that compares "arms": a harnessed worker vs a bare one, one model vs
another, one effort tier vs another. An arm is just a named configuration you
dispatch work to. The whole experiment stands on one assumption: when two arms
differ in ability, the tasks they are given produce different outcomes.

The flip-fact check measured that assumption on every dataset on this machine
and it fails almost everywhere. Three gauntlet corpora with perfect arm
bookkeeping (results.jsonl 268/268 pass, regrade-36 78/78, calibration 36/36)
have a pass rate of exactly 100 percent for every arm. A column that reads
"true" on every row cannot tell you anything about who wrote the row. The only
corpus that discriminates at all is one trivial probe prompt, and even there 12
of 16 arms sit at 100 percent.

So the decision in front of us: before the A/B starts logging, change the
gauntlet so that outcomes vary with arm ability, and change it in a way that
KEEPS varying as models improve. Otherwise S1 logging (already licensed by the
ADR) produces a well-formed dataset that answers nothing, and we find out a
quarter later.

One analogy carries the whole document: an eye chart. The current gauntlet asks
every patient to read the giant E on the top row. Everyone reads it, the chart
records "20/20" for all of them, and the optometrist learns nothing. A useful
chart keeps rows near each patient's threshold, and, critically, when patients
get better glasses next year, smaller rows must exist. That last property, the
instrument not saturating as its subjects improve, is the evolutionary property
this design has to deliver.

## 2. Diagnosis: why the ceiling exists

### What a task is here

A task is a small self-contained repo snapshot under `tasks/<id>/base/` plus a
prompt. Two concrete instances:

- `t1-py-a`: a product-catalog Python project whose test suite has one failing
  test; the prompt says find and fix the bug, do not touch `tests/`.
- `t5-py-a`: the VAULT-7 adapter. A vendored legacy store whose method names
  mean the opposite of what they say (`append` deletes, `flush` discards), an
  eight-rule spec, and a hidden acceptance suite the model cannot read,
  reachable only through a metered broker that returns counts, never test
  names.

t5 was deliberately built to be adversarial: hidden suite, count-only feedback,
capped feedback budget (K=10 requests via `runner/broker.py`). It is the
hardest thing the corpus contains.

### How grading works, and where the resolution is thrown away

Grading is deterministic and binary. `run.py:run_verify` re-runs the task's own
canonical `verify.sh` (never the model's copy, tamper-checked) and the outcome
is `returncode == 0`. One bit per run.

Two lines above it, `graded_run` captures the FULL suite output, because the
broker needs to parse it into passed/failed counts (`broker.parse_counts`) to
sell the model count-only feedback. So the instrument already computes "7 of 31
acceptance tests failing" mid-run, then discards everything but the final bit
at grading time. The regrade path (`regrade-36.jsonl`) even carries
`failing_tests` and `first_failing_test` fields. The resolution exists in the
pipeline; the outcome column just never kept it.

### The three causes, in order of weight

**Cause 1: the model holds the oracle and is told to iterate until green.**
Every prompt ends with `DONE_GATE_SENTENCE` (`run.py:113`): "Your work is
judged solely by running bash verify.sh ... it must exit 0. Run it yourself and
confirm a clean exit before you finish." An oracle is a perfect answer-checker
the subject can consult. Give a competent agent a small repo, a complete
failing test suite, and unlimited attempts against the exact judge it will be
graded by, and "eventually green" is close to guaranteed for every frontier
arm. The binary pass therefore measures loop-closing ability, which all current
arms have, not the capability differences between them. Difficulty does show
up, but it shows up in HOW MUCH the loop costs, and that is exactly what the
rows already record and the outcome ignores: on t2-py-b, median output tokens
per arm range from 2,338 (kimi-k3) to 5,494 (haiku-4-5-20251001), a 2.3x spread
on a task where every one of those arms "tied" at pass=true.

**Cause 2: the outcome is one Bernoulli bit at a ceiling.** A pass/fail run is
a Bernoulli trial (a coin flip with some success probability p) and its
variance is p(1-p). At p=1 the variance is zero, so no sample size helps:
ANALYSIS.md's own power section says 24 trials per arm detect only differences
of roughly 35 percentage points, and at a 100 percent ceiling the detectable
difference is literally nothing. In testing language, an item is informative
about a subject only when the subject has a real chance of missing it;
information peaks near a 50 percent pass rate and dies at the extremes.

**Cause 3: even the hardest tier sits below every arm's ability.** The
calibration-domain2 corpus ran the adversarial t5 tier against 4 models: 34/36
pass. The only failures on the entire machine are claude-haiku-4-5 at LOW
effort, twice, on t5-py-a. The task ladder tops out below where 2026 frontier
arms operate. This is not a design error someone made; it is instrument decay.
The tasks were separating models when they were authored (the Fable-vs-Sol
sweeps were built around them) and the subjects improved past the chart.

### Proof the ceiling is the tasks, not the pipeline

Three positive controls, all already in the repo:

- The sabotage arms (`negative-control-28.jsonl`, broken/empty patches) FAIL
  verify correctly. The grader detects bad work when bad work exists.
- The trivial ladder probe (substring-graded, `probe_endpoints.py`)
  discriminates 16 arms at permutation p=0.00010 with a clean scrambled-label
  control. The statistics stack works when the outcome varies.
- The dual judge (Opus + Codex, 0-10 rubric in `judge.py`) saturates too:
  pooled quality mean 8.959/10 over 153 judged runs. A judge asked to score 268
  green diffs compresses into the top of its scale, and this judge has never
  passed a discrimination check of its own (the AUC 0.541 lesson from T2
  retrieval: a scorer that cannot separate known-good from known-bad says
  nothing about anything else).

So: grader honest, statistics sound, judge unfalsified, tasks saturated. The
fix is the task/outcome layer.

## 3. Options

Terms used below: "arm" as defined above; "discriminate" means outcomes differ
across arms at p<0.05 with a scrambled-label negative control staying
non-significant; "human-eval minutes" is Drake's time, costed per the
design-the-sample doctrine (nested arms bought 5 verdicts from 41 minutes).

### Option A: keep the tasks, raise the outcome resolution

Stop reducing a run to one bit. Candidates, all deterministic, all computable
from data the runner already touches:

- **Acceptance pass fraction.** For tasks with hidden acceptance suites (t3,
  t4, t5), record k-of-N acceptance tests passing instead of all-or-nothing.
  `broker.parse_counts` already parses this; `run_verify` throws it away. Turns
  one Bernoulli into a binomial over ~N items per run, so two arms that both
  "mostly succeed" can still separate.
- **Attempts-to-green under the metered broker.** How many of the K feedback
  requests a run spends before green. Already counted server-side per run.
- **Cost-to-green.** tokens_out, turns, wall_s: already on every row, already
  varying 2.3x across arms. Caveat: this measures economy, not correctness, so
  it is a secondary axis, never the headline (a cheap wrong answer must not
  outrank an expensive right one; on an all-green corpus that risk is muted but
  the corpus will stop being all-green if this design works).
- **Mutation kill rate** for fix-the-bug tasks: plant a set of mutants
  (deliberate small breakages) in the base repo and score the model's patch by
  what fraction of planted breakages its final tree rejects. Deterministic, but
  the only candidate here needing real new build.

Build cost: pass fraction and attempts-to-green are roughly half a day each
(the writer in `run.py` plus schema; readers via `corpus_gates`). Mutation kill
is 2-3 days. Human-eval minutes: zero recurring; grading stays deterministic.

What it buys: immediate variance on the existing corpus, zero new tasks
authored. What it cannot buy: if every arm scores 1.0 on the acceptance
fraction too, resolution alone does not help; and it does nothing about
saturation as models improve.

### Option B: harder task corpus

Where harder tasks come from, three sources, cheapest signal first:

1. **Crank dials the repo already owns.** Lower the broker budget (K=3),
   remove visible tests entirely (t5 already does count-only), grow the base
   repo so the read set stops fitting in one glance, put the bug across module
   boundaries. Cheap per unit, but it is hand-tuning against current arms and
   saturates again next generation.
2. **Harvest real failed work.** Sandcastle issues that went RED, got
   reopened, or shipped a wrong fix are, by construction, tasks at least one
   real agent failed under real conditions. Freeze the repo snapshot plus
   issue text, write a hidden acceptance suite after the fact, serve through
   the existing broker. Ecologically valid (difficulty came from the world,
   not from an author's imagination). Cost: the acceptance suite is the
   expensive part, call it half a day per task, and each needs one human
   ratification that the suite encodes the real requirement.
3. **Adversarial generation.** A generator model writes task + reference
   solution + acceptance suite. Unbounded supply, but generated tasks are
   frequently broken or trivially gameable, so they are worthless WITHOUT an
   admission gate (below). With the gate, the human cost per admitted task
   drops to near zero and the junk gets refused mechanically.

The admission gate is the piece that makes any source safe, and it is this
fleet's existing doctrine applied here: a new task is admitted only if a
strong reference arm passes it AND a weak reference arm fails it (or their
pass fractions separate). A task both controls ace is refused as
uninformative; a task both fail is refused as broken. This is exactly
`control.sh oracle`: a signal that cannot separate the two known cases says
nothing about a third. The weak control exists today for free: haiku-4-5 at
low effort, the only configuration the current corpus ever failed.

Build cost: harvest of 10 tasks, 2-4 days plus one designed ratification pass
(Drake reviews 10 acceptance suites, not 10 solutions, in one sitting: ~40
minutes for the batch, the nested-arms trick applied to review). Generator:
2-3 days after the gate exists.

### Option C: IRT-style joint calibration (measurement layer)

IRT, item response theory, is the psychometrics of exams: estimate every
task's difficulty and every arm's ability jointly on one scale, instead of
comparing raw pass rates on whatever tasks happened to run. The simplest form
(Rasch / 1PL) says P(arm passes task) = sigmoid(ability_arm -
difficulty_task) and fits by plain logistic regression: ~150 lines of
dependency-free Python over (arm, task, outcome) rows. Sigmoid is the S-curve
that maps any number to a probability.

What it buys: per-arm ability with a standard error even when arms saw
different task subsets; per-task "information" telling you which tasks are in
the useful band for the current population; a principled retirement signal.
What it costs: ~1 day, zero human minutes. What it cannot do: create variance.
Fit it on an all-pass matrix and every difficulty estimate runs off to
negative infinity. C is the lens you put on top of A and B, not an
alternative to them, and its script must refuse loudly (exit 2, subjects
counted) when the outcome matrix has no variance.

### Option D: the difficulty ladder with an informative-band gate (the evolutionary property)

This is the piece Drake actually asked for: an instrument that does not
saturate as arms improve. Mechanism, not aspiration:

- The corpus is explicitly rungs (t1..t5 already are, by intent; they just
  all sit below current ability).
- Every sweep, a gate computes each task's pooled pass rate (or mean pass
  fraction) and classifies it: **saturated** (>0.9), **in-band** (0.2-0.9),
  **too-hard** (<0.2).
- Saturated tasks retire from the comparison corpus into a regression floor,
  still run at n=1 per sweep as a canary: a frontier arm failing the giant E
  is exactly the silent-model-regression signal harness rule 9 wants, so
  retirement never means deletion.
- The gate FAILS the sweep report (nonzero exit, loud) whenever the in-band
  count drops below a floor. That failure is the demand signal that triggers
  the supply pump (Option B's admission pipeline) to mint the next rung.
- Admission of every new rung uses the strong/weak differential-kill gate
  from Option B, so the band refills with tasks that are informative BY
  MEASUREMENT, not by an author's guess.

This is a control loop in the Mistele sense: measurable (per-task pass rate),
incremental (one task at a time), feedback (the band count moves when tasks
are added or retired). Run the loop screen on it and it passes all three.

Build cost: the gate itself is half a day inside `corpus_gates.py` /
`ladder_from_results.py`, which already own read-time gating. The recurring
cost is task supply, which is Option B's cost, on demand instead of up front.
Band thresholds get duplicated into the gate rather than read from sweep
config (checker and worker must not share a rule surface).

## 4. Recommendation

Do A + D now, B on the gate's demand signal, C once variance exists. Judges
stay out of every headline number until they pass their own discrimination
check (a ready-made one exists, see ticket 5).

Concretely: log the acceptance pass fraction (cheapest real variance, half a
day, zero human minutes), land the informative-band gate (it goes red on
today's corpus, and that red is the correct reading of the instrument), then
harvest one t6 tier of ~10 tasks from real failed sandcastle work through the
differential-kill admission gate. Fit Rasch on top once any of that produces
variance. Every grading path stays deterministic; no retrieval plumbing, no
embeddings, nothing the freeze covers.

Why this ordering and not "write harder tasks first": task authoring is the
expensive, human-heavy option, and we do not yet know how much of the ceiling
is outcome RESOLUTION versus task DIFFICULTY. The two haiku-low failures and
the 2.3x token spread say the existing t4/t5 tier may already separate arms
once the outcome stops rounding everything to 1. Half a day of plumbing
answers that before days of authoring are spent.

**The flip fact.** Rerun the existing calibration arms (4 models x t4/t5
tiers) with the pass-fraction outcome. If the fractions separate arms
(permutation p<0.05, scrambled-label control non-significant), harder-task
supply drops to a slow burner behind the band gate. If the fractions come
back 1.0 on nearly every row, resolution was not the binding constraint,
ticket 3 becomes the critical path, and this document's ordering inverts.
Either result arrives for roughly 36 model invocations and zero human
minutes.

**Human-eval budget, totaled.** Tickets 1, 2, 4, 5: zero recurring human
minutes (deterministic grading, existing corpora). Ticket 3: one designed
~40-minute ratification pass per 10-task batch, reviewing acceptance suites
only. That is the whole ask.

## 5. Tracer-bullet tickets (listed, not filed)

**1. Log acceptance pass fraction beside pass**
`graded_run` already returns the suite output and `broker.parse_counts`
already parses counts; `run_verify` keeps only rc==0. Write
`acceptance_total`, `acceptance_failed`, `pass_frac` onto every result row at
the writer, defaulting to null where a task has no suite to count. Positive
control: replay the sabotage runs (broken/empty) and assert frac 0.0; a known
green run must read 1.0. Done when a calibration-domain2 rerun shows
haiku-low below 1.0 while stronger arms sit at 1.0.

**2. Informative-band gate on the corpus**
In `corpus_gates.py` / `ladder_from_results.py`: per task, pooled pass rate
(or mean pass_frac) over the sweep; classify saturated / in-band / too-hard
with thresholds duplicated into the gate; print all three counts and exit
nonzero when in-band count is 0. Positive control: run it on today's
results.jsonl and it MUST go red with 0 in-band tasks; that red is the
finding this whole design rests on, not a bug to fix.

**3. Harvest a t6 tier from real failed work**
Source: sandcastle issues that went RED or were reopened, frozen as repo
snapshot + issue text + after-the-fact hidden acceptance suite, served
through the existing broker at K=5, count-only. Admission per task: strong
arm passes, weak arm (haiku-4-5 low effort) fails or separates on pass_frac;
a task both controls treat identically is refused. Budget 10 tasks; Drake
ratifies the batch in one ~40-minute pass over acceptance suites only.

**4. Rasch fit over the pooled corpus**
A dependency-free script fitting P(pass) = sigmoid(ability - difficulty) by
logistic regression over (arm, task, outcome) rows, expanding a k-of-N
pass_frac row into N item trials. Reports per-arm ability with standard
error, per-task information, and a retirement list for the band gate.
Refuses (exit 2, subjects counted) when the outcome matrix has no variance:
a fit over zero variance is a guess wearing coefficients.

**5. Judge discrimination check before any judge score is believed**
Material already exists: negative-control-28 sabotage diffs (known bad) vs
verified passing diffs (known good). Run both judges blind over the mix,
compute AUC against a pre-registered floor of 0.8. Below the floor, judge
scores stay out of every published mean (the pooled 8.959 is currently
unfalsified); above it, the judge earns a role as a graded outcome on tasks
where determinism runs out. Either verdict is a deliverable.

## 6. Constraints honored

- No vector DB, no embeddings, no retrieval plumbing: nothing here touches
  retrieval; the freeze is untouched.
- Deterministic grading preferred: every recommended outcome (pass fraction,
  attempts-to-green, mutation kill, Rasch over both) is computed by code, not
  by a model.
- Judges gated on discrimination: ticket 5 is the AUC 0.541 lesson applied
  before a single judge score enters a comparison.
- Checker vs worker: band thresholds and admission rules are duplicated into
  the gates, annotated so a tidy-up does not DRY them back together.
- Silence is not evidence: both new gates count subjects and treat zero as a
  hard error; the band gate going red on day one is asserted as the expected
  first output, not discovered.
