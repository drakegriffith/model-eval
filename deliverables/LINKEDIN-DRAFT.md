# LinkedIn draft — Fable vs Sol gauntlet

DRAFT for Drake Griffith review. Not scheduled, not auto-posted. Source: `FINDINGS.md`.

---

## 1. Main post

Both models scored 100%. That's the finding.

I ran a 154-run controlled experiment: Claude Fable 5 vs GPT-5.6 Sol, 9 provably-solvable coding tasks, full factorial design (model x reasoning effort x harness), blind dual-judge scoring.

Result: every one of the 152 solo runs passed. Every effort level, every config. No detectable gap between the two models on this tier of task.

What actually moved: cost. Pushing Sol from low to high reasoning effort bought +80% output tokens and ~1.8x the input tokens for the same passing code. Fable's high setting cost +16% more output tokens than medium for the same outcome.

The "reasoning effort" dial isn't buying better answers here. It's buying tokens.

One setup did break: one model orchestrating the other as implementer. That was the only unreliable config in the run — 2 of 4 attempts produced planning notes and zero shipped code.

If you're paying for max-effort settings on routine engineering work, test it first. Most tasks don't need the expensive dial, and a single side-by-side run can't tell you which model is "better" — you need enough trials to separate signal from noise.

Full technical report (Wilson CIs, McNemar tests, the works): https://actualintelligencelabs.ai/research/claude-vs-gpt-154-run-experiment
Video breakdown coming: [VIDEO-LINK]

#AIEngineering #LLM #DataScience

**Character count: 1,263** (main post text, excluding this heading)

---

## 2. Image plan

Attach all three charts, in this order:

1. `assets/1-pass-rate-ceiling.png`
   Alt text: "Pass rates by model and reasoning effort, all at 100% with Wilson 95% confidence intervals — the ceiling effect."
2. `assets/2-effort-buys-nothing.png`
   Alt text: "Output tokens by reasoning effort level for both models, showing cost rising while pass rate stays flat at 100%."
3. `assets/3-input-token-economics-DO-NOT-USE.png`
   Alt text: "Input tokens by model configuration, with a caveat that cross-vendor input-token accounting is not directly comparable."

Order rationale: chart 1 backs the headline (both hit ceiling), chart 2 backs the cost claim (effort dial = tokens, not quality), chart 3 is the honest caveat for anyone who wants to dig into the token numbers.

---

## 3. First comment (methodology receipts)

154 runs, full factorial (model x effort x harness), blocked by task, blind dual-judge (Claude + Codex judges, identities stripped). Stats: Wilson 95% CIs, McNemar exact paired tests, exact permutation tests on tokens. Honesty rule: gaps smaller than the experiment can detect get a confidence interval, not a verdict — n=24/arm only resolves differences of ~37pp or more.

One disclosed asymmetry: 65 of 88 GPT runs reported >100k cumulative input tokens (the "dumb zone" where long-context degradation kicks in, per Matt Pocock's heuristic); Claude peaked at 19.6k. That bias runs against GPT — which still passed everything — so the tie stands, but quality-score comparisons carry the caveat.

**Character count: 372**
