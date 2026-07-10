# Statistical appendix — Fable vs Sol gauntlet

Every number below is an **exact** test rendered with its raw counts (Wilson interval, McNemar 2x2 table, or permutation count) — never a bare p-value. Implements `ANALYSIS.md` §3–§4. Stdlib only.

Source: 5 run row(s), 5 passing, 5 judged.

## 1. Per-cell pass rate — 95% Wilson score intervals

Every cell (model x effort x harness) is a set of Bernoulli trials; the interval is the honest small-n coverage behind each pass rate.

| model | effort | harness | pass/n | pass rate | 95% Wilson CI |
| --- | --- | --- | --- | --- | --- |
| fable | medium | bare | 1/1 | 100% | (0.2065, 1.0000) |
| fable | high | bare | 1/1 | 100% | (0.2065, 1.0000) |
| sol | low | bare | 1/1 | 100% | (0.2065, 1.0000) |
| sol | medium | bare | 1/1 | 100% | (0.2065, 1.0000) |
| sol | high | bare | 1/1 | 100% | (0.2065, 1.0000) |

## 2. Head-to-head: Fable vs Sol at best effort (McNemar exact)

Sweep 1 (bare). Each model runs at its winning effort (max pass rate, ties -> fewer tokens -> higher effort); runs are matched by task x rep so task difficulty cancels.

Fable best effort = **high**, Sol best effort = **low**.

**Fable/high vs Sol/low**  (1 paired runs, matched by task x rep)

| | Sol/low pass | Sol/low fail |
| --- | --- | --- |
| **Fable/high pass** | 1 | 0 |
| **Fable/high fail** | 0 | 0 |

Concordant: 1  (1 pass/pass, 0 fail/fail). Discordant: 0  (b=0 Fable/high-only, c=0 Sol/low-only).

No discordant pairs — no evidence of difference (n=1 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 3. Does more effort help? Adjacent effort rungs (McNemar exact)

Sweep 1 (bare), per model, each adjacent effort pair matched by task x rep.

**fable: high vs medium**  (1 paired runs, matched by task x rep)

| | fable/medium pass | fable/medium fail |
| --- | --- | --- |
| **fable/high pass** | 1 | 0 |
| **fable/high fail** | 0 | 0 |

Concordant: 1  (1 pass/pass, 0 fail/fail). Discordant: 0  (b=0 fable/high-only, c=0 fable/medium-only).

No discordant pairs — no evidence of difference (n=1 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol: medium vs low**  (1 paired runs, matched by task x rep)

| | sol/low pass | sol/low fail |
| --- | --- | --- |
| **sol/medium pass** | 1 | 0 |
| **sol/medium fail** | 0 | 0 |

Concordant: 1  (1 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/medium-only, c=0 sol/low-only).

No discordant pairs — no evidence of difference (n=1 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol: high vs medium**  (1 paired runs, matched by task x rep)

| | sol/medium pass | sol/medium fail |
| --- | --- | --- |
| **sol/high pass** | 1 | 0 |
| **sol/high fail** | 0 | 0 |

Concordant: 1  (1 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/high-only, c=0 sol/medium-only).

No discordant pairs — no evidence of difference (n=1 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 4. Does the harness help? Bare vs harnessed (McNemar exact)

Per model, at the winning effort, runs matched by task x rep (sweep 1 T2 vs sweep 2a).

_(no model has both bare and harnessed runs)_

## 5. Who's cheaper per solved task? Sign-flip permutation test

Per-task median log(total tokens) over PASSING runs, Fable minus Sol, paired by task. Exact test: all 2^k sign patterns (k = tasks with passing runs on both sides).

| task | fable med log(tok) | sol med log(tok) | diff (F−S) |
| --- | --- | --- | --- |
| t1-py-a | 9.9262 | 11.8341 | -1.9079 |

Observed sum of diffs = -1.9079 over k=1 tasks. 2 of 2 sign patterns are as-or-more extreme -> two-sided **p = 1.0000000**.

## 6. Can we trust the judges? Dual-judge agreement

Per run, each judge's score is averaged across its four axes; we compare Claude's average to Codex's average.

- Runs compared (both judges present): **5**
- Mean absolute score gap: **0.4500** points (0–10 scale)
- Pearson r (Claude avg vs Codex avg): **0.8729**
- Within ±1 point: **5/5 = 100%**

## 7. Power — what this experiment can and cannot detect

Two-proportion test, per-arm n=24, α=0.05 two-sided, 80% power, baseline p=0.5.

- Minimum detectable effect ≈ **36.7 percentage points** (power 80.0% at that gap).
- _Approximate_: found by a normal-approximation power search over delta (the ONE place this appendix uses a large-sample approximation — everything above is exact).

> Rule (ANALYSIS §4): a gap smaller than the minimum detectable effect gets a confidence interval, not a verdict. We say "no detectable difference," never "they're equal."
