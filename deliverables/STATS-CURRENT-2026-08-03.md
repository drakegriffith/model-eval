# Statistical appendix — Fable vs Sol gauntlet

Every number below is an **exact** test rendered with its raw counts (Wilson interval, McNemar 2x2 table, or permutation count) — never a bare p-value. Implements `ANALYSIS.md` §3–§4. Stdlib only.

Source: 267 run row(s), 267 passing, 154 judged.

> **exclusions** — results rows: inspected=268 kept=267 excluded=1 — cli_error=1. Excluded rows are gone from every test on this page, counts included. §6 is the one section computed over the FULL judgment set: it measures whether the two judges agree with each other, not how a model performed, and a truncated run's judges either agreed or did not. Token axes here are `tokens_out` only (ticket 31 AC#3).

## 1. Per-cell pass rate — 95% Wilson score intervals

Every cell (model x effort x harness) is a set of Bernoulli trials; the interval is the honest small-n coverage behind each pass rate.

| model | effort | harness | pass/n | pass rate | 95% Wilson CI |
| --- | --- | --- | --- | --- | --- |
| claude-haiku-4-5 | low | bare | 6/6 | 100% | (0.6097, 1.0000) |
| claude-haiku-4-5 | high | bare | 6/6 | 100% | (0.6097, 1.0000) |
| claude-haiku-4-5 | max | bare | 6/6 | 100% | (0.6097, 1.0000) |
| claude-haiku-4-5-20251001 | low | bare | 6/6 | 100% | (0.6097, 1.0000) |
| claude-haiku-4-5-20251001 | high | bare | 6/6 | 100% | (0.6097, 1.0000) |
| claude-haiku-4-5-20251001 | max | bare | 6/6 | 100% | (0.6097, 1.0000) |
| fable | medium | bare | 25/25 | 100% | (0.8668, 1.0000) |
| fable | medium | harness | 14/14 | 100% | (0.7847, 1.0000) |
| fable | high | bare | 24/24 | 100% | (0.8620, 1.0000) |
| gpt-5.3-codex-spark | low | bare | 6/6 | 100% | (0.6097, 1.0000) |
| gpt-5.3-codex-spark | high | bare | 6/6 | 100% | (0.6097, 1.0000) |
| gpt-5.3-codex-spark | xhigh | bare | 6/6 | 100% | (0.6097, 1.0000) |
| gpt-5.6-luna | low | bare | 6/6 | 100% | (0.6097, 1.0000) |
| gpt-5.6-luna | high | bare | 6/6 | 100% | (0.6097, 1.0000) |
| gpt-5.6-luna | max | bare | 6/6 | 100% | (0.6097, 1.0000) |
| hybrid | medium | harness | 2/2 | 100% | (0.3424, 1.0000) |
| kimi-k3 | low | bare | 6/6 | 100% | (0.6097, 1.0000) |
| kimi-k3 | high | bare | 6/6 | 100% | (0.6097, 1.0000) |
| kimi-k3 | max | bare | 6/6 | 100% | (0.6097, 1.0000) |
| sol | low | bare | 32/32 | 100% | (0.8928, 1.0000) |
| sol | low | harness | 14/14 | 100% | (0.7847, 1.0000) |
| sol | medium | bare | 24/24 | 100% | (0.8620, 1.0000) |
| sol | high | bare | 30/30 | 100% | (0.8865, 1.0000) |
| sol | xhigh | bare | 6/6 | 100% | (0.6097, 1.0000) |
| sol | ultra | bare | 6/6 | 100% | (0.6097, 1.0000) |

## 2. Head-to-head: Fable vs Sol at best effort (McNemar exact)

Sweep 1 (bare). Each model runs at its winning effort (max pass rate, ties -> fewer tokens -> higher effort); runs are matched by task x rep so task difficulty cancels.

Fable best effort = **medium**, Sol best effort = **low**.

**Fable/medium vs Sol/low**  (25 paired runs, matched by task x rep)

| | Sol/low pass | Sol/low fail |
| --- | --- | --- |
| **Fable/medium pass** | 25 | 0 |
| **Fable/medium fail** | 0 | 0 |

Concordant: 25  (25 pass/pass, 0 fail/fail). Discordant: 0  (b=0 Fable/medium-only, c=0 Sol/low-only).

No discordant pairs — no evidence of difference (n=25 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 3. Does more effort help? Adjacent effort rungs (McNemar exact)

Sweep 1 (bare), per model, each adjacent effort pair matched by task x rep.

**claude-haiku-4-5: high vs low**  (6 paired runs, matched by task x rep)

| | claude-haiku-4-5/low pass | claude-haiku-4-5/low fail |
| --- | --- | --- |
| **claude-haiku-4-5/high pass** | 6 | 0 |
| **claude-haiku-4-5/high fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 claude-haiku-4-5/high-only, c=0 claude-haiku-4-5/low-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**claude-haiku-4-5: max vs high**  (6 paired runs, matched by task x rep)

| | claude-haiku-4-5/high pass | claude-haiku-4-5/high fail |
| --- | --- | --- |
| **claude-haiku-4-5/max pass** | 6 | 0 |
| **claude-haiku-4-5/max fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 claude-haiku-4-5/max-only, c=0 claude-haiku-4-5/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**claude-haiku-4-5-20251001: high vs low**  (6 paired runs, matched by task x rep)

| | claude-haiku-4-5-20251001/low pass | claude-haiku-4-5-20251001/low fail |
| --- | --- | --- |
| **claude-haiku-4-5-20251001/high pass** | 6 | 0 |
| **claude-haiku-4-5-20251001/high fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 claude-haiku-4-5-20251001/high-only, c=0 claude-haiku-4-5-20251001/low-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**claude-haiku-4-5-20251001: max vs high**  (6 paired runs, matched by task x rep)

| | claude-haiku-4-5-20251001/high pass | claude-haiku-4-5-20251001/high fail |
| --- | --- | --- |
| **claude-haiku-4-5-20251001/max pass** | 6 | 0 |
| **claude-haiku-4-5-20251001/max fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 claude-haiku-4-5-20251001/max-only, c=0 claude-haiku-4-5-20251001/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**fable: high vs medium**  (24 paired runs, matched by task x rep)

| | fable/medium pass | fable/medium fail |
| --- | --- | --- |
| **fable/high pass** | 24 | 0 |
| **fable/high fail** | 0 | 0 |

Concordant: 24  (24 pass/pass, 0 fail/fail). Discordant: 0  (b=0 fable/high-only, c=0 fable/medium-only).

No discordant pairs — no evidence of difference (n=24 pairs). McNemar exact test needs discordant pairs to have anything to test.

**gpt-5.3-codex-spark: high vs low**  (6 paired runs, matched by task x rep)

| | gpt-5.3-codex-spark/low pass | gpt-5.3-codex-spark/low fail |
| --- | --- | --- |
| **gpt-5.3-codex-spark/high pass** | 6 | 0 |
| **gpt-5.3-codex-spark/high fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 gpt-5.3-codex-spark/high-only, c=0 gpt-5.3-codex-spark/low-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**gpt-5.3-codex-spark: xhigh vs high**  (6 paired runs, matched by task x rep)

| | gpt-5.3-codex-spark/high pass | gpt-5.3-codex-spark/high fail |
| --- | --- | --- |
| **gpt-5.3-codex-spark/xhigh pass** | 6 | 0 |
| **gpt-5.3-codex-spark/xhigh fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 gpt-5.3-codex-spark/xhigh-only, c=0 gpt-5.3-codex-spark/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**gpt-5.6-luna: high vs low**  (6 paired runs, matched by task x rep)

| | gpt-5.6-luna/low pass | gpt-5.6-luna/low fail |
| --- | --- | --- |
| **gpt-5.6-luna/high pass** | 6 | 0 |
| **gpt-5.6-luna/high fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 gpt-5.6-luna/high-only, c=0 gpt-5.6-luna/low-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**gpt-5.6-luna: max vs high**  (6 paired runs, matched by task x rep)

| | gpt-5.6-luna/high pass | gpt-5.6-luna/high fail |
| --- | --- | --- |
| **gpt-5.6-luna/max pass** | 6 | 0 |
| **gpt-5.6-luna/max fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 gpt-5.6-luna/max-only, c=0 gpt-5.6-luna/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**kimi-k3: high vs low**  (6 paired runs, matched by task x rep)

| | kimi-k3/low pass | kimi-k3/low fail |
| --- | --- | --- |
| **kimi-k3/high pass** | 6 | 0 |
| **kimi-k3/high fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 kimi-k3/high-only, c=0 kimi-k3/low-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**kimi-k3: max vs high**  (6 paired runs, matched by task x rep)

| | kimi-k3/high pass | kimi-k3/high fail |
| --- | --- | --- |
| **kimi-k3/max pass** | 6 | 0 |
| **kimi-k3/max fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 kimi-k3/max-only, c=0 kimi-k3/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

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

**sol: xhigh vs high**  (6 paired runs, matched by task x rep)

| | sol/high pass | sol/high fail |
| --- | --- | --- |
| **sol/xhigh pass** | 6 | 0 |
| **sol/xhigh fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/xhigh-only, c=0 sol/high-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol: ultra vs xhigh**  (6 paired runs, matched by task x rep)

| | sol/xhigh pass | sol/xhigh fail |
| --- | --- | --- |
| **sol/ultra pass** | 6 | 0 |
| **sol/ultra fail** | 0 | 0 |

Concordant: 6  (6 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol/ultra-only, c=0 sol/xhigh-only).

No discordant pairs — no evidence of difference (n=6 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 4. Does the harness help? Bare vs harnessed (McNemar exact)

Per model, at the winning effort, runs matched by task x rep (sweep 1 T2 vs sweep 2a).

**fable at effort medium: bare vs harness**  (13 paired runs, matched by task x rep)

| | fable harness pass | fable harness fail |
| --- | --- | --- |
| **fable bare pass** | 13 | 0 |
| **fable bare fail** | 0 | 0 |

Concordant: 13  (13 pass/pass, 0 fail/fail). Discordant: 0  (b=0 fable bare-only, c=0 fable harness-only).

No discordant pairs — no evidence of difference (n=13 pairs). McNemar exact test needs discordant pairs to have anything to test.

**sol at effort low: bare vs harness**  (14 paired runs, matched by task x rep)

| | sol harness pass | sol harness fail |
| --- | --- | --- |
| **sol bare pass** | 14 | 0 |
| **sol bare fail** | 0 | 0 |

Concordant: 14  (14 pass/pass, 0 fail/fail). Discordant: 0  (b=0 sol bare-only, c=0 sol harness-only).

No discordant pairs — no evidence of difference (n=14 pairs). McNemar exact test needs discordant pairs to have anything to test.

## 5. Who's cheaper per solved task? Sign-flip permutation test

Per-task median log(**output** tokens) over PASSING runs, Fable minus Sol, paired by task. Exact test: all 2^k sign patterns (k = tasks with passing runs on both sides).

Output tokens, not total: fable's input side is quarantined and sol's is measured (ticket 31 AC#3), so a total-token contrast here would compare an undercount against a true count.

| task | fable med log(out tok) | sol med log(out tok) | diff (F−S) |
| --- | --- | --- | --- |
| t1-py-a | 7.0214 | 6.8916 | +0.1298 |
| t1-py-b | 6.9788 | 7.0596 | -0.0809 |
| t1-ts-a | 6.9022 | 7.0579 | -0.1557 |
| t1-ts-b | 7.4494 | 7.4495 | -0.0001 |
| t2-py-a | 7.7915 | 7.4902 | +0.3014 |
| t2-py-b | 7.9593 | 7.8628 | +0.0964 |
| t2-ts-a | 8.0740 | 8.0994 | -0.0254 |
| t2-ts-b | 7.8932 | 7.7483 | +0.1449 |
| t3-a | 7.8083 | 8.0053 | -0.1969 |

Observed sum of diffs = +0.2136 over k=9 tasks. 348 of 512 sign patterns are as-or-more extreme -> two-sided **p = 0.6796875**.

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
