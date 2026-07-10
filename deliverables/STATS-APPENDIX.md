# Statistical appendix — Fable vs Sol gauntlet

Every number below is an **exact** test rendered with its raw counts (Wilson interval, McNemar 2x2 table, or permutation count) — never a bare p-value. Implements `ANALYSIS.md` §3–§4. Stdlib only.

Source: 154 run row(s), 154 passing, 154 judged.

## 1. Per-cell pass rate — 95% Wilson score intervals

Every cell (model x effort x harness) is a set of Bernoulli trials; the interval is the honest small-n coverage behind each pass rate.

| model | effort | harness | pass/n | pass rate | 95% Wilson CI |
| --- | --- | --- | --- | --- | --- |
| fable | medium | bare | 26/26 | 100% | (0.8713, 1.0000) |
| fable | medium | harness | 14/14 | 100% | (0.7847, 1.0000) |
| fable | high | bare | 24/24 | 100% | (0.8620, 1.0000) |
| hybrid | medium | harness | 2/2 | 100% | (0.3424, 1.0000) |
| sol | low | bare | 26/26 | 100% | (0.8713, 1.0000) |
| sol | low | harness | 14/14 | 100% | (0.7847, 1.0000) |
| sol | medium | bare | 24/24 | 100% | (0.8620, 1.0000) |
| sol | high | bare | 24/24 | 100% | (0.8620, 1.0000) |

## 2. Head-to-head: Fable vs Sol at best effort (McNemar exact)

Sweep 1 (bare). Each model runs at its winning effort (max pass rate, ties -> fewer tokens -> higher effort); runs are matched by task x rep so task difficulty cancels.

Fable best effort = **medium**, Sol best effort = **low**.

**Fable/medium vs Sol/low**  (26 paired runs, matched by task x rep)

| | Sol/low pass | Sol/low fail |
| --- | --- | --- |
| **Fable/medium pass** | 26 | 0 |
| **Fable/medium fail** | 0 | 0 |

Concordant: 26  (26 pass/pass, 0 fail/fail). Discordant: 0  (b=0 Fable/medium-only, c=0 Sol/low-only).

No discordant pairs — no evidence of difference (n=26 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 3. Does more effort help? Adjacent effort rungs (McNemar exact)

Sweep 1 (bare), per model, each adjacent effort pair matched by task x rep.

**fable: high vs medium**  (24 paired runs, matched by task x rep)

| | fable/medium pass | fable/medium fail |
| --- | --- | --- |
| **fable/high pass** | 24 | 0 |
| **fable/high fail** | 0 | 0 |

Concordant: 24  (24 pass/pass, 0 fail/fail). Discordant: 0  (b=0 fable/high-only, c=0 fable/medium-only).

No discordant pairs — no evidence of difference (n=24 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol: medium vs low**  (24 paired runs, matched by task x rep)

| | sol/low pass | sol/low fail |
| --- | --- | --- |
| **sol/medium pass** | 24 | 0 |
| **sol/medium fail** | 0 | 0 |

Concordant: 24  (24 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/medium-only, c=0 sol/low-only).

No discordant pairs — no evidence of difference (n=24 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol: high vs medium**  (24 paired runs, matched by task x rep)

| | sol/medium pass | sol/medium fail |
| --- | --- | --- |
| **sol/high pass** | 24 | 0 |
| **sol/high fail** | 0 | 0 |

Concordant: 24  (24 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/high-only, c=0 sol/medium-only).

No discordant pairs — no evidence of difference (n=24 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 4. Does the harness help? Bare vs harnessed (McNemar exact)

Per model, at the winning effort, runs matched by task x rep (sweep 1 T2 vs sweep 2a).

**fable at effort medium: bare vs harness**  (14 paired runs, matched by task x rep)

| | fable harness pass | fable harness fail |
| --- | --- | --- |
| **fable bare pass** | 14 | 0 |
| **fable bare fail** | 0 | 0 |

Concordant: 14  (14 pass/pass, 0 fail/fail). Discordant: 0  (b=0 fable bare-only, c=0 fable harness-only).

No discordant pairs — no evidence of difference (n=14 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol at effort low: bare vs harness**  (14 paired runs, matched by task x rep)

| | sol harness pass | sol harness fail |
| --- | --- | --- |
| **sol bare pass** | 14 | 0 |
| **sol bare fail** | 0 | 0 |

Concordant: 14  (14 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol bare-only, c=0 sol harness-only).

No discordant pairs — no evidence of difference (n=14 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 5. Who's cheaper per solved task? Sign-flip permutation test

Per-task median log(total tokens) over PASSING runs, Fable minus Sol, paired by task. Exact test: all 2^k sign patterns (k = tasks with passing runs on both sides).

| task | fable med log(tok) | sol med log(tok) | diff (F−S) |
| --- | --- | --- | --- |
| t1-py-a | 9.9293 | 11.7945 | -1.8652 |
| t1-py-b | 9.9256 | 11.7996 | -1.8739 |
| t1-ts-a | 9.9218 | 11.7104 | -1.7886 |
| t1-ts-b | 9.9579 | 11.9564 | -1.9985 |
| t2-py-a | 9.9333 | 11.6615 | -1.7282 |
| t2-py-b | 9.9590 | 12.0185 | -2.0595 |
| t2-ts-a | 10.0253 | 12.2987 | -2.2734 |
| t2-ts-b | 9.9256 | 11.9755 | -2.0499 |
| t3-a | 9.8712 | 11.9511 | -2.0799 |

Observed sum of diffs = -17.7172 over k=9 tasks. 2 of 512 sign patterns are as-or-more extreme -> two-sided **p = 0.0039062**.

## 6. Can we trust the judges? Dual-judge agreement

Per run, each judge's score is averaged across its four axes; we compare Claude's average to Codex's average.

- Runs compared (both judges present): **139**
- Mean absolute score gap: **0.5252** points (0–10 scale)
- Pearson r (Claude avg vs Codex avg): **0.7589**
- Within ±1 point: **133/139 = 96%**

## 7. Power — what this experiment can and cannot detect

Two-proportion test, per-arm n=24, α=0.05 two-sided, 80% power, baseline p=0.5.

- Minimum detectable effect ≈ **36.7 percentage points** (power 80.0% at that gap).
- _Approximate_: found by a normal-approximation power search over delta (the ONE place this appendix uses a large-sample approximation — everything above is exact).

> Rule (ANALYSIS §4): a gap smaller than the minimum detectable effect gets a confidence interval, not a verdict. We say "no detectable difference," never "they're equal."
