# Statistical methodology — Fable vs Sol gauntlet

Every number in the video's tables is backed by the tests below, computed by
`runner/stats.py` directly from `results.jsonl` / `judgments.jsonl`. All tests are
**exact** (no large-sample approximations) because per-cell n is small and honesty
about that is the point. Grounded in ISYE coursework (Georgia Tech): hypothesis
testing, confidence intervals, design of experiments, stochastic processes.

## 1. Experimental design (DOE framing)

The run matrix is a **full-factorial designed experiment**:

- **Factors**: model (fable, sol) × reasoning effort (2–3 levels) × harness (bare, harnessed)
- **Blocking**: task (8 T1/T2 tasks + 1 T3) — every config sees every task, so
  task difficulty cancels out of paired comparisons
- **Replication**: n=3 reps per cell (T1/T2), n=2 (T3) — because LLM inference is
  non-deterministic, a single run is an anecdote, not a measurement
- **Randomization**: run order is seed-interleaved (seed 1337) so time-of-day /
  API-load drift doesn't systematically favor one model

Vault refs: `doe-factorial-designs`, `doe-blocking-and-randomization`.

## 2. Outcome model (the stochastics)

Each run is a **Bernoulli trial**: pass (verify.sh exit 0) or fail. A cell's pass
rate estimates its Bernoulli parameter p; run-to-run scatter within a cell is the
expected Bernoulli variance p(1−p) plus model non-determinism. The variance table
(Table 5) reports observed scatter against this baseline.

Vault refs: `random-variables`, `sampling-distributions`.

## 3. The exact tests

| Question | Test | Data |
|---|---|---|
| How sure are we about any cell's pass rate? | **95% Wilson score interval** (correct coverage at small n, unlike the naive ±1.96√ formula) | every cell |
| Fable vs Sol head-to-head, best effort each | **McNemar's exact test** on task×rep-paired pass/fail (24 pairs) — only discordant pairs carry information | sweep 1 |
| Does more effort actually help, per model? | pairwise **McNemar exact** between adjacent effort rungs | sweep 1 |
| Does the harness help, per model? | **McNemar exact** on paired bare-vs-harness runs (same task, same rep) | sweep 1 T2 vs sweep 2a |
| Who's cheaper per solved task? | **exact sign-flip permutation test** (2⁸ = 256 sign patterns) on per-task median log-tokens, paired by task | passing runs |
| Who's cheaper at a *fixed* setting? | the same sign-flip test run inside a fixed (model × effort) cell: each model at its winning effort, then every effort label both models ran | passing bare runs |
| Can we trust the judges? | dual-judge agreement: mean absolute score gap, Pearson r, % within ±1 point | judgments |

The two cost rows are separate tests because they answer separate questions and
disagree on this corpus. The models were not run at the same set of effort
levels, so the pooled contrast (§5 of the appendix) mixes "which model is
cheaper" with "which tiers each model happened to be run at". The matched-cell
contrast (§6) holds the cell fixed. Neither one supersedes the other; a cost
claim has to name which of the two it comes from.

Vault refs: `hypothesis-testing-2pop`, `confidence-intervals-1pop`,
`confidence-intervals-2pop`, `effect-size-estimation`.

## 4. Power — what this experiment can and cannot detect

With 24 trials per arm at α=0.05, a two-proportion comparison has 80% power only
for **large** differences (roughly ≥35 percentage points at a 50% baseline;
`stats.py` prints the exact minimum detectable effect). Rule for the video:

> **A gap smaller than the minimum detectable effect gets a confidence interval,
> not a verdict.** We say "no detectable difference," never "they're equal."

Vault ref: `doe-power-and-sample-size`.

## 5. What to say on camera (≈10 seconds, pick one)

- "Every pass rate you see has a 95% confidence interval behind it, and the
  head-to-head is a McNemar exact test on paired runs — same tasks, same order,
  both models. Full statistical appendix is in the repo."
- "These models are non-deterministic, so every cell is repeated runs treated as
  Bernoulli trials — this is a blocked factorial experiment, not one lucky run."
- (when a gap is small) "Statistically, we can't call this one — the confidence
  intervals overlap, and I'd rather tell you that than fake a winner."

## 6. Reproducing the appendix

```
python3 runner/stats.py --results runner/results/results.jsonl \
                        --judgments runner/results/judgments.jsonl
```

Emits `STATS-APPENDIX.md`: per-cell Wilson CIs, every McNemar 2×2 table with its
exact p-value, the token permutation test both pooled and at matched cells,
judge agreement, and the power statement — the exact data behind every on-camera
claim.
