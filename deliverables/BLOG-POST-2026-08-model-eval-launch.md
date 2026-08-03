# model-eval: what happens when every model passes

**CORRECTION (2026-08-03, later same day) — DO NOT PUBLISH AS-IS.** Finding 2
below ("same pass rate, roughly a fifth of the tokens") and its chart are
WRONG, along with the effort-ladder table in Finding 3. Both were computed as
`tokens_in + tokens_out`, but Fable's `tokens_in` is `quarantined` on 100% of
its rows (no true value exists anywhere for it). The repo's own canonical
stats script (`runner/stats.py`, corrected 2026-07-30 per ticket 31 AC#3,
ratified by Drake) uses **output tokens only** for exactly this reason and
was not consulted before this draft was written — it should have been run
first, not reimplemented by hand. Re-run under `python3 runner/stats.py`
(output written to `deliverables/STATS-CURRENT-2026-08-03.md`), the
Fable-vs-Sol token gap is **not statistically significant** (§5: p = 0.68,
sign of the per-task diff mixed, not consistently in Fable's favor). Judge
agreement (95.7%/96%) is unaffected and still checks out against §6. The
image this post references has been renamed to
`assets/model-eval-token-chart-WRONG-DO-NOT-USE.png` — do not use it.

Next session, before touching this file: read
`deliverables/STATS-CURRENT-2026-08-03.md` in full (it's the ground truth,
regenerable any time with `python3 runner/stats.py`), then rewrite Finding 2
and Finding 3 from its §3/§5 numbers — the real, defensible headline is
closer to "effort tiers never flipped a single pass/fail outcome anywhere in
the corpus (§3, zero discordant McNemar pairs across every model and every
adjacent effort rung) and the Fable/Sol cost gap is not distinguishable from
noise at this sample size (§5)." That's a less flashy claim than the one
below but it's the one the data actually supports. Do not hand-roll token
totals again — call `summary_tokens()` semantics (output-only) or just read
the appendix.

Status: DRAFT, not published. Numbers verified against the live corpus on
2026-08-03: `runner/results/results.jsonl` (268 rows), `runner/results/judgments.jsonl`
(154 dual-judged rows), `runner/results/transcripts/` (241 files). Recomputed
directly from those files for this post, not copied from the 2026-07-10
`ANALYSIS.md` / `STATS-APPENDIX.md` / `TABLES.md`, which cover an earlier,
now-superseded pass of the same harness.

Repo: github.com/drakegriffith/model-eval

---

## Why I built this

Every benchmark I could find for "is model A better than model B at coding"
gave me a leaderboard number and nothing to check it against. No transcript,
no grading rubric, no way to tell whether the task was actually hard or just
phrased to favor one vendor's house style. I wanted something I could point at
my own tasks, run against whatever CLI I already had subscription access to
(Claude Code, Codex), and get back not just a score but the receipts.

model-eval is that harness. It runs a model headlessly against a small,
verifiable coding task, scores the result with two independent LLM judges on
a shared rubric, and writes the transcript, the judgment, and the raw token
counts to the same repo the code lives in. Nothing about a run's outcome
depends on a number I typed in by hand afterward.

## How a run works

A task is a directory: a starting repo state with a real bug or a real
missing feature, a `verify.sh` that fails against the unpatched state and
passes against a reference fix, and a prompt handed to the model verbatim.
The model gets the repo and the prompt, runs headlessly to completion, and
`verify.sh` either passes or it doesn't. That's the only pass/fail signal
in the corpus — no judge ever overrides a failing test suite.

Two things back that up:

- **`selftest.sh` on every task.** Each task proves, offline, that its own
  `verify.sh` fails on the unpatched base and passes once the reference patch
  is applied. A task that doesn't wire up cleanly fails CI, not silently
  skipped.
- **Negative controls.** A subset of runs is an "empty" arm — the harness
  goes through the full prepare/verify/grade path with no fix applied at all,
  to confirm the grader reports a fail when nothing changed. Every negative
  control in the corpus reports `passed: false`. The grader isn't rubber-
  stamping.

Runs are unattended, which means `--dangerously-skip-permissions` /
`--dangerously-bypass-approvals-and-sandbox` on the CLI side. That's a real
reduction in the CLI's own safety net, so an OS-level `sandbox-exec` profile
and a process-level containment layer sit underneath every invocation
instead — deny-by-default on credential reads, writes contained to the run's
own scratch tree, API keys popped from the child's environment so every run
is subscription-authenticated, never key-authenticated. Full detail in the
README's security section.

Once a run finishes, two separate LLM judges (one Claude, one Codex) score
the diff independently on four dimensions — correctness, simplicity,
idiomatic style, spec adherence — without seeing each other's verdict.

## Finding 1: pass/fail stopped being the interesting number

Across all 268 runs in the current corpus, every model passed every task it
attempted. 100%. Sol, Fable, Kimi K3, GPT-5.6 Luna, GPT-5.3 Codex Spark, both
Claude Haiku 4.5 snapshots — all of it.

That's not a claim that these models are equivalent. It's a ceiling effect:
the task set (bug fixes and small feature additions in real repos, the kind
of thing t1/t2-tier tasks in this corpus cover) is easy enough for current
frontier and near-frontier models that pass/fail no longer separates them.
If I want a benchmark that still discriminates as models get better, either
the tasks get harder or I stop treating pass/fail as the metric that matters.
For this corpus, I did the second one: the real signal turned out to be cost
and judged quality, not whether the test suite went green.

## Finding 2: same pass rate, roughly a fifth of the tokens

![Same pass rate. About a fifth of the tokens.](assets/model-eval-token-chart.png)

Comparing Claude's Fable against Codex's Sol head to head, at each model's
own best-performing effort tier (Fable at `medium`, Sol at `low`, both
without the harness's optional scaffolding), on the 9 tasks both models ran
in that configuration:

| | median tokens/run | pass rate |
|---|---|---|
| Fable (medium) | 20,567 | 100% |
| Sol (low) | 98,536 | 100% |

Sol used about 4.8x Fable's tokens for an identical pass rate. That's not an
artifact of one easy task dragging the average — Sol used more tokens than
Fable on **all 9** of the 9 matched tasks, individually, at the per-task
median. An exact sign-flip permutation test on that (all-same-direction, n=9)
puts the two-sided p-value at 0.004. This isn't "Sol happened to be verbose
on average," it's "Sol was more expensive on every single task in the
comparison."

Worth being honest about the limits here: 9 matched tasks is a small
comparison, and it's specific to this task set, these two effort tiers, and
the "bare" (non-scaffolded) invocation mode. It's a real, statistically
supported difference in this corpus, not a claim about all Claude vs. all
Codex models everywhere.

## Finding 3: effort tiers don't reliably buy anything here

Every model in this corpus exposes an "effort" knob (low/medium/high, or
similar), and the working assumption going in was that higher effort should
mean better (or at least different) outcomes. What it actually bought, in
this corpus, was mostly just more tokens — not more passes, since pass rate
was already at the ceiling everywhere.

A few effort ladders, median total tokens per run:

| model | low | medium/high | max/ultra |
|---|---|---|---|
| Sol | 117,462 (low) | 140,556 (medium) / 183,284 (high) | 437,108 (ultra) |
| GPT-5.6 Luna | 111,310 (low) | 146,582 (high) | 268,250 (max) |
| Kimi K3 | 37,554 (low) | 37,079 (high) | 30,826 (max) |
| Claude Haiku 4.5 | 3,498 (low) | 2,754 (high) | 2,828 (max) |

Sol and Luna scale up roughly monotonically with effort tier — "ultra" costs
Sol nearly 4x what "low" does. Kimi K3 and Haiku barely move, and in Haiku's
case "low" is actually the most expensive tier, not the cheapest. Pass rate
was 100% at every tier for every model, so none of that extra spend bought a
single additional passing run on this task set. Whether it buys anything on
harder tasks is an open question this corpus can't answer yet — that's the
argument for adding harder tasks next.

## Judged quality: two judges, checked against each other

Pass/fail is binary and, on this corpus, saturated. The judges are where the
finer-grained signal lives — correctness, simplicity, idiomatic style, and
spec adherence, each scored 0-10 by two separately-prompted judges (Claude
and Codex) that never see each other's output.

Before trusting either judge's score, I checked whether they agree. Averaging
each judge's four dimension scores into one per-run number and comparing
those, the two judges land within a point of each other on 95.7% of the 139
runs both judges scored. Looking at the four dimensions individually instead
of the per-run average, agreement is tighter but still solid at 93.2% (518 of
556 judge-pairs within 1 point). Exact score matches are rarer (about 15% at
the per-run level) — the judges converge on the same rough quality tier far
more often than they converge on the exact same integer, which is what I'd
expect from two independently-prompted graders and not a sign that one of
them is noise.

With that agreement established, average judged scores (both judges, all
four dimensions) for the two models with enough judged runs to be worth
reading:

| model | correctness | simplicity | idiomatic | spec | n |
|---|---|---|---|---|---|
| Sol | 9.33 | 9.06 | 8.93 | 9.55 | 80 |
| Fable | 8.83 | 8.83 | 8.54 | 8.96 | 57 |

Sol scores slightly higher across all four dimensions in this corpus. Put
next to Finding 2, the fuller picture isn't "Fable wins" or "Sol wins" — it's
a cost/quality tradeoff: Fable matched Sol's pass rate at roughly a fifth of
the tokens, while Sol's diffs scored modestly higher with the judges. Which
one is the right call depends on what you're optimizing for on a given task,
which is exactly the kind of decision a single leaderboard number can't help
you make.

## One data-quality note, in the open

Not every run's token count came from a clean, direct measurement. Of 268
runs, 148 have `tokens_in_status: measured` (read directly off the CLI's own
usage reporting), 56 are `recovered_in_ledger` (reconstructed from a
secondary usage log after the primary read was incomplete), and 64 are
`quarantined` (flagged as unreliable and excluded from the token comparisons
above, including Finding 2's matched-pairs analysis — Fable's 24 `high`
sample happens to be entirely quarantined, which is why Finding 2 uses
Fable's `medium` tier instead). This is a known rough edge in how different
CLIs report usage over long headless sessions, not a hidden one. The field
exists specifically so a bad token read fails loud in the data rather than
quietly averaging into a number that looks trustworthy.

## Try it

No UI, no signup, no API key required for the smoke test:

```
git clone https://github.com/drakegriffith/model-eval
cd model-eval
bash tasks/t1-py-a/selftest.sh          # offline: proves the task is real by
                                          # showing verify.sh fail, then pass,
                                          # once the reference fix is applied
python3 runner/run.py --mock --limit 1  # exercises the full harness pipeline,
                                          # no tokens spent, no API key needed
```

Point `runner/run.py` at your own Claude Code or Codex CLI (subscription
auth, no key needed) to run a live model against a task and see how it does.
`CONTRIBUTING.md` covers adding a new task or a new model to the registry.

Repo: https://github.com/drakegriffith/model-eval
