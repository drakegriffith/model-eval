---
title: "We Ran 154 Controlled Trials of Claude vs GPT. Both Scored Perfect. That's the Finding."
description: "A blocked factorial experiment pitting Claude Fable 5 against GPT-5.6 on nine proven-solvable coding tasks, judged blind by two models, with every pass rate carrying a Wilson confidence interval."
date: 2026-07-10
author: Drake Griffith
tags: [ai-evaluation, benchmarks, claude, gpt, statistics]
---

# We Ran 154 Controlled Trials of Claude vs GPT. Both Scored Perfect. That's the Finding.

**TL;DR**

- I ran 154 controlled coding trials pitting Claude Fable 5 against GPT-5.6, as a blocked full-factorial experiment: model × reasoning effort × harness, every configuration facing every task, repeated runs treated as coin flips because model inference is non-deterministic.
- Both models passed everything solvable. Sweep 1 was 120 of 120; the solo runs across both sweeps were 152 of 152. The head-to-head McNemar test had zero discordant pairs, so there is nothing to test. No detectable difference.
- Turning up the reasoning-effort dial changed cost and nothing else. Same 100% pass rate at every setting; GPT-5.6 at high effort burned about 80% more output tokens than at low, for the same result.
- The two models solve differently. Claude iterates over 6–7 turns; GPT one-shots in a single turn. Same destination, different route.
- The honest read: I built the exam too easy, and I am telling you that instead of crowning a winner. The next experiment raises the difficulty until failures appear.

## Why "vibes" benchmarks fail

Most model comparisons you read are one person running one prompt through two tools and reporting which output they liked better. That is not a measurement. It is an anecdote wearing a lab coat.

The problem is non-determinism. Ask the same model the same coding question twice and you can get two different answers, two different token counts, two different outcomes. A single run tells you what happened once. It tells you nothing about what happens on average, which is the only thing you actually care about when you are deciding which tool your team ships with. Run the comparison again tomorrow and the "winner" can flip.

There is a second, quieter problem. The public benchmarks that do use repeated, structured evaluation are running out of headroom. Frontier models now cluster near the top of the standard suites, which makes those suites worse at telling models apart. Contamination compounds it: when benchmark problems, or text closely derived from them, land in a model's training data, the model can recall an answer instead of reasoning to it. The team behind [LiveCodeBench](https://arxiv.org/abs/2403.07974) (Jain et al., 2024) documented exactly this for coding, showing that HumanEval and MBPP are no longer sufficient on their own and building a time-segmented benchmark of fresh contest problems specifically to dodge the leakage.

So I did not want another leaderboard number. I wanted a controlled experiment: same tasks, same conditions, enough repetition to separate the model from the noise, and honest statistics about what the sample size can and cannot support. My background is industrial engineering (Georgia Tech), and this is a textbook design-of-experiments problem. I treated it like one.

## The method

The two contestants were Claude Fable 5, driven through Anthropic's `claude` CLI, and GPT-5.6, driven through OpenAI's `codex` CLI.

The design is a **full-factorial designed experiment**, blocked by task:

- **Factors:** model (Claude, GPT) × reasoning effort (2–3 levels) × harness (bare CLI vs. a shared agent-instruction harness).
- **Blocking:** every configuration sees every task, so task difficulty cancels out of any paired comparison.
- **Replication:** n=3 repetitions per cell on the smaller tasks, n=2 on the greenfield build. Repetition is the whole point. A single run is an anecdote; repeated runs let me treat each outcome as a Bernoulli trial and estimate a real pass rate.
- **Randomization:** run order is seed-interleaved (seed 1337) so that time-of-day and API-load drift can't systematically favor one model.

The tasks matter as much as the design. There are nine, and every one was **proven solvable before any model touched it**. Four are seeded-bug tasks with deliberately subtle defects: an off-by-one in pagination, a naive-versus-timezone-aware datetime bug, a cache key that silently drops a dimension, an async dedup race. Four are feature tickets written in a strict six-section format. One is a greenfield CLI build, a roughly 200-line cost-splitting tool with a reference implementation. Each task ships a self-test that fails on the broken starting code and passes after the reference patch. All nine were green before the experiment began, so a failure would mean the model failed, not that the task was impossible.

Conditions were held byte-identical: same harness instruction files, same prompts, same deterministic `verify.sh` gate deciding pass or fail. Every transcript and diff was then stripped of any identifying markers and scored **blind by two separate judges**, a Claude judge and a GPT judge, on a four-dimension rubric. I ran two judges precisely because a single-model judge is a known failure mode, which I will come back to.

Statistics are exact, not large-sample approximations, because the per-cell sample sizes are small and pretending otherwise would be dishonest. Wilson score intervals for every pass rate, McNemar's exact test for paired head-to-heads, an exact sign-flip permutation test on tokens, and an explicit power statement for what the sample can detect.

Here is the sweep-1 configuration table, medians, n=24 per row:

| config | pass | out tok | in tok | wall | turns |
|---|---|---|---|---|---|
| Fable / medium | 24/24 | 1,736 | 19,412 | 68.2s | 7 |
| Fable / high | 24/24 | 2,008 | 19,398 | 58.8s | 6 |
| Sol / low | 24/24 | 1,095 | 95,248 | 31.9s | 1 |
| Sol / medium | 24/24 | 1,534 | 138,252 | 49.0s | 1 |
| Sol / high | 24/24 | 1,966 | 174,662 | 63.7s | 1 |

## Result 1: both models hit the ceiling

Sweep 1 was 120 passes out of 120 attempts, across both models and every effort level. Fold in the solo runs from the second sweep (the harnessed tasks and the bare-plus-harnessed greenfield build) and the total is **152 of 152** clean passes on every configuration where a model worked alone.

The honest way to report a perfect score is with its confidence interval, not as a bare "100%." Twenty-four passes out of twenty-four gives a 95% Wilson interval of **(86.2%, 100%)**. That is the correct reading: I did not prove the model never fails on tasks like these. I proved that if it has a failure rate, the data are consistent with it being anywhere from zero up to about 14%. A perfect sample is evidence, not a guarantee, and the interval is where the honesty lives.

For the head-to-head I used McNemar's exact test, which only extracts information from *discordant* pairs, the cases where one model passed and the other failed on the same task and repetition. There were zero discordant pairs. Every matched pair was pass/pass. With nothing discordant, there is nothing to test, and the correct conclusion is **no detectable difference between the models on this suite**. Not "they are equal." No detectable difference. Those are different claims, and I am careful to only make the one the data supports.

*[Figure: assets/1-pass-rate-ceiling.png — Pass rate by configuration with 95% Wilson confidence intervals. Every bar sits at 100% with an interval whose lower bound reflects the per-cell sample size. This is the honesty chart: perfect scores, drawn with their uncertainty.]*

## Result 2: the effort dial bought nothing but tokens

Every model here exposes a reasoning-effort setting. The pitch is that higher effort makes the model think harder and do better. On this suite, it did not.

Pass rate was 100% at every effort level for both models. What moved was cost. Claude at high effort spent about 16% more output tokens than at medium (2,008 vs. 1,736 median) for the identical result. GPT-5.6 at high effort spent about 80% more output tokens than at low (1,966 vs. 1,095) and pulled roughly 1.8× the input tokens (174.7k vs. 95.2k). The pairwise McNemar tests between adjacent effort rungs again found zero discordant pairs. More effort, same outcome, higher bill.

This is not a quirk of my nine tasks. It matches a growing body of work on what researchers call the overthinking phenomenon. The survey ["Stop Overthinking: A Survey on Efficient Reasoning for Large Language Models"](https://arxiv.org/abs/2503.16419) (Sui et al., 2025) catalogs how reasoning models generate verbose, redundant chains that add computational cost without adding correctness. The empirical study ["Between Underthinking and Overthinking"](https://arxiv.org/abs/2505.00127) (Su et al., 2025) is sharper still: models overthink easy problems, producing unnecessarily long outputs, precisely because they are poor at gauging problem difficulty. My tasks were, in hindsight, easy for these models. Cranking the effort dial on an easy task is spending money to watch a model deliberate over a conclusion it already had.

*[Figure: assets/2-effort-buys-nothing.png — Output tokens by effort level, colored by pass rate. Tokens climb left to right; every marker is a 100% pass. Effort buys tokens, not outcomes.]*

## Result 3: different animals, same destination

The two models do not solve problems the same way, and the trace makes it obvious.

Claude iterates. Median 6–7 turns per task, roughly 59–68 seconds of wall-clock. It reads, edits, checks, edits again, converging on a fix. GPT one-shots. Median of a single turn, roughly 26–64 seconds. It reasons internally and emits a solution in one pass. Neither style won on outcomes at this tier, because both styles cleared the tier. If you have watched one of these tools and formed an opinion about which "works harder," this is a useful reminder that turn count is a workflow signature, not a quality signal.

### A caveat you cannot skip: input tokens do not compare across vendors

Look back at the config table and you will see Claude reporting around 19.4k median input tokens while GPT reports 95k to 175k. That is not GPT reading five to nine times more of your code. The two CLIs count context resends and cache reads differently, so their input-token numbers are measuring different things under the same label.

The practical rule: **within-model** comparisons are safe, because each CLI is consistent with itself. **Cross-model** input-token or dollar comparisons are indicative only, and I refuse to publish them as hard claims. The axes that *are* honestly comparable across models are output tokens, turns, and wall-clock time. Those are what I lean on.

*[Figure: assets/3-input-token-economics.png — Input tokens by configuration, annotated with the accounting caveat. The gap between the two models is an artifact of CLI bookkeeping, not workload, and the chart says so on its face.]*

### The one configuration that wobbled

I also tested a hybrid: Claude orchestrating GPT as the implementer. This is the only setup that did not run clean, and it deserves the careful version, not a headline.

Across four attempts, two produced planning notes and zero code, and two shipped working solutions of roughly 160–176 lines. So: **2 of 4**. The official results file records the two later, successful attempts; the two earlier failures I am disclosing here and in the full report. The rig was identical across all four, which means the most defensible reading is genuine run-to-run variance in a multi-agent handoff, not a fixed defect. With n=4, that is where I stop. I will not build a story about orchestration fragility on four data points, and I would be suspicious of anyone who did. It is a flag for the next experiment, not a conclusion.

### The judges are a finding too

Running two judges was not decoration. Automated judges carry a documented **self-preference bias**: the paper ["Self-Preference Bias in LLM-as-a-Judge"](https://arxiv.org/abs/2410.21819) (Wataoka et al., 2024) shows GPT-4 systematically favoring certain outputs when it grades, apparently because it rewards text that is more familiar, lower-perplexity, to itself. A single-model judge grading its own model's work is a conflict of interest baked into the evaluator.

So I stripped identity from every transcript and had both models grade everything. Across 139 dual-judged runs, the two judges landed within one point of each other on 96% of runs, with a mean absolute gap of **0.53 points** on a ten-point scale and a Pearson correlation of 0.76. On the pilot, the GPT judge ran about half a point more generous than the Claude judge on the same diffs, and both judges leaned slightly toward GPT's code. That last detail is exactly why the judging is blind and dual, and exactly why I report the disagreement instead of averaging it away.

## Limitations

This section is the credibility, so I am going to be blunt about what this experiment cannot tell you.

- **The sample is small per arm.** With 24 trials per arm, the design has 80% power to detect only large gaps, roughly 36.7 percentage points or more at a 50% baseline. A real difference smaller than that would not show up. It gets a confidence interval, not a verdict.
- **It is a ceiling effect.** Because both models passed everything, this suite cannot *rank* them. It can only establish that both clear this tier. Anyone who reads "154 perfect runs" as "the models are identical" is over-reading it. The correct claim is narrower.
- **The judges are models, not humans.** Blind and dual and with disagreement reported, but still automated graders with their own biases.
- **One run was re-run.** A single Claude greenfield run hit a transient CLI error, 2.3 seconds and zero tokens, and was re-run. Disclosed.
- **The tasks came from an AI-assisted pipeline.** They were proven solvable, but their difficulty was mis-calibrated low. That miscalibration is not a footnote. It *is* the headline finding.
- **Cost figures use list-price placeholders.** I am not publishing dollar claims off vendor-incomparable token counts.
- **One model may have operated in the "dumb zone."** Practitioners who run these models at scale — Matt Pocock has been the loudest on this — observe that output quality degrades once context grows past roughly 100k tokens, a pattern consistent with published long-context research ([Liu et al., 2024](https://arxiv.org/abs/2307.03172)). By its own CLI's accounting, GPT-5.6 exceeded 100k cumulative input tokens on 65 of its 88 runs (median 142k, max 353k); Claude peaked at 19.6k. Two honest qualifiers: codex counts context resends across internal steps, so live context occupancy at any moment is not observable from our data; and the bias runs *against* GPT, which passed everything anyway. The tie is therefore safe, but the blind-judge quality comparisons carry this asymmetry, and it is on the record.

**[Download the full technical report (PDF)](https://actualintelligencelabs.ai/assets/docs/claude-vs-gpt-154-run-experiment-technical-report.pdf)** — the complete experimental design, every typeset formula (Wilson intervals, McNemar exact tests, the sign-flip permutation test, the power derivation), all tables and figures, and the limitations, in one document.

## What's next

The most useful result of a ceiling is that it tells you exactly where to point the next experiment. Both models beat this tier, so the tier was too easy, so the tier goes up.

Experiment 2 is the T4 hard tier: the same rig, the same blocking, the same blind dual-judge, the same exact statistics, and harder tasks pushed up in difficulty until failures start appearing. Failures are what you need. A gap only becomes measurable once at least one model starts missing, and the effort dial only earns its cost once "think harder" changes an outcome instead of just a token count. Same exam room, harder exam. That is where Claude versus GPT gets a real answer, and I will report that one the same way I reported this one: with the intervals drawn in, the disagreements shown, and no winner invented that the data did not earn.
