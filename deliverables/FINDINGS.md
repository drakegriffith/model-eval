# Gauntlet findings brief — canonical numbers (2026-07-10)

Single source of truth for the video script, blog post, and LinkedIn draft.
Data: `runner/results/results.jsonl` (154 official runs) + `judgments.jsonl`.
Methodology contract: `ANALYSIS.md`. Repo: `~/code/model-gauntlet` (private).

## The experiment
- **Models:** Claude Fable 5 (`claude` CLI, Anthropic) vs GPT-5.6 Sol (`codex` CLI, OpenAI).
- **Design:** full-factorial — model × reasoning effort × harness (bare/harnessed), **blocked by task** (every config sees every task), n=3 reps (T1/T2) / n=2 (T3), seed-interleaved run order.
- **9 tasks, proven solvable first:** 4 seeded-bug (subtle: off-by-one pagination, naive-vs-aware datetime, cache key dropping a dimension, async dedup race), 4 feature tickets (6-section ticket format), 1 greenfield CLI build (`splitcost`, ~200-line reference). Every task ships a selftest: verify fails on base, passes after reference patch — 9/9 green before any model ran.
- **Identical conditions:** byte-identical harness instruction files, same prompts, same deterministic verify.sh gate.
- **Blind dual-judge:** transcripts/diffs stripped of identity; judged by BOTH a Claude judge and a Codex judge on a 4-dim rubric.
- **Stats:** Wilson 95% CIs, McNemar exact paired tests, exact sign-flip permutation on tokens, MDE/power statement (STATS-APPENDIX.md).

## Headline results
1. **Perfect scores everywhere (ceiling).** Sweep 1: 120/120 across both models and every effort level. All solo sweep-2 runs (harnessed T2, greenfield T3 bare+harnessed) also passed → **152/152 solo**. Honest framing: 24/24 per cell = 95% CI (86.2%, 100%). No detectable difference between models — McNemar has zero discordant pairs.
2. **The effort dial bought nothing but tokens.** Same 100% pass at every setting; Fable high used +16% output tokens vs medium (2,008 vs 1,736 median); Sol high used +80% vs low (1,966 vs 1,095) and ~1.8× the input tokens (174.7k vs 95.2k). On tasks of this tier, "think harder" settings are pure cost.
3. **Different animals, same destination.** Fable iterates: median 6–7 turns, ~59–68s. Sol one-shots: median 1 turn, ~26–64s. Neither approach won on outcomes at this tier.
4. **Input-token accounting is vendor-incomparable.** Fable CLI reports ~19.4k median input; Sol CLI 95k–175k (counts context resends/cache reads differently). Within-model comparisons safe; cross-model input-token or $ comparisons are indicative only. Output tokens/turns/wall-clock are the honest cross-model axes.
5. **The harness didn't move pass rate on this suite** (100% → 100%; it was designed for longer, messier work than these tasks).
6. **Hybrid (Fable orchestrating Codex as implementer) was the only unreliable config: 2/4 attempts.** Two attempts produced planning notes and zero code; two shipped working ~160–176-line solutions. Official results file records the 2 later attempts; the 2 earlier failures are disclosed here and in the report (identical rig, so we treat it as genuine run-to-run variance, n too small for a strong claim).
7. **Judge behavior is itself a finding:** on the pilot the Codex judge scored ~0.5 points higher than the Claude judge on the same diffs, and both judges slightly favored Sol's diffs — exactly why judging is blind and dual. (Final agreement stats from full judgments when judging completes.)

## Sweep-1 config table (medians, n=24 each)
| config | pass | out tok | in tok | wall | turns |
|---|---|---|---|---|---|
| Fable / medium | 24/24 | 1,736 | 19,412 | 68.2s | 7 |
| Fable / high | 24/24 | 2,008 | 19,398 | 58.8s | 6 |
| Sol / low | 24/24 | 1,095 | 95,248 | 31.9s | 1 |
| Sol / medium | 24/24 | 1,534 | 138,252 | 49.0s | 1 |
| Sol / high | 24/24 | 1,966 | 174,662 | 63.7s | 1 |

## Limitations (state them; they're the credibility)
- n=24/arm detects only gaps ≥ ~36.7 pp at 80% power — smaller gaps get a CI, not a verdict.
- Ceiling effect: this suite cannot rank the models, only establish both clear the tier.
- LLM judges (blind, dual, disagreement reported) — not human graders.
- One Fable t3 run hit a transient CLI error (2.3s, 0 tokens) and was re-run; disclosed.
- Tasks authored by an AI-assisted pipeline; proven solvable but tier difficulty was mis-calibrated low → that IS finding #1.
- Cost figures use list-price placeholders; not published as dollar claims.
- **Context-window ("dumb zone") caveat:** practitioner experience (Matt Pocock's heuristic, consistent with published long-context degradation research) holds that model quality degrades past ~100k tokens of context. Fable never approached it (max reported input 19.6k). Sol's CLI reported >100k cumulative input on 65 of 88 runs (median 142k, max 353k) — though codex counts context resends across internal steps, so live context occupancy at any single moment is not observable from our data. If degradation applied, it biases against Sol, which still passed everything — the ceiling finding is unaffected, but cross-model judge-quality comparisons could carry this bias.

## Next (experiment 2 tease)
T4 hard tier — increase difficulty until failures appear; that's where the effort
ladder and model gaps get a real test. Same rig, harder exam.

## Assets
- `assets/1-pass-rate-ceiling.png` — pass rates w/ Wilson CIs (the honesty chart)
- `assets/2-effort-buys-nothing.png` — output tokens by effort, all 100% pass
- `assets/3-input-token-economics.png` — input tokens by config (w/ accounting caveat)
