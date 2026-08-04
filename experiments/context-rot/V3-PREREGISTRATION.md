# Context-rot v3: pre-registration

Written before any v3 run. The point of writing it first is that v2 returned
1251/1251 and the temptation now is to design until something breaks. The
thresholds below are fixed in advance and taken from published work, not chosen
after seeing our numbers.

## Why v3 exists

v2 scored 100% at every length through 700k, every depth decile, zero decoy
grabs. That is not evidence long context is fine. It is evidence that **exact
lexical retrieval is the one thing immune to the mechanism that causes context
rot**, and that is all v2 measured.

Four mechanisms from the literature, and what each predicts:

1. **Softmax dilution.** Attention is a softmax over all N tokens; the mass sums
   to 1 regardless of N, and its entropy grows ~log N, so attention flattens
   toward uniform as context grows. An exact string match produces a sharp logit
   spike that survives dilution. A latent semantic association produces a weak
   one that washes out. This is the mechanistic explanation for v2's null.
   (Scalable-Softmax, arXiv:2501.19399; Sparse Attention, arXiv:2506.16640)
2. **Training distribution.** 32K-128K documents are rare in pretraining and skew
   to books and code; context windows are extended by rescaling positional
   encodings, mapping under-trained position ranges onto trained ones. Predicts
   depth effects and a cliff past trained length. (arXiv:2409.04774)
3. **Retrieval != reasoning.** Chroma states in their own limitations that they
   hold task difficulty constant and would expect "even more severe" degradation
   on synthesis or multi-step tasks, which they did not test. Open ground.
4. **Composition, not length.** Agent failure modes are context poisoning,
   distraction, confusion, and clash: properties of what is in the window, not
   how much. Argued from literature in the writeup, not measured here.

Quadratic attention cost is a compute/bill argument, not a quality one. The
writeup must keep those separate; "the smart zone is 100k" is substantially
economics wearing a quality costume.

## The external prediction we are testing against

NoLiMa (ICML 2025, Adobe Research, arXiv:2502.05167) is NIAH with question and
needle sharing minimal lexical overlap, so the model must infer a latent
association. Their "effective length" = longest context retaining >=85% of the
short-context base score.

| Model | Claimed | Effective | Base | 32K |
|---|---|---|---|---|
| GPT-4.1 | 1M | 16K | 97.0 | 79.8 |
| GPT-4o | 128K | 8K | 99.3 | 69.7 |
| Llama 3.3 70B | 128K | 2K | 97.3 | 42.7 |

Ten of twelve models fell below half their baseline at 32K. **No Claude model
appears in their tables, and nothing anywhere runs past 128K.** That is the gap
v3 fills.

## Arms

- **`exact`** — v2's task, unchanged. Control. Already known to be 100% at all
  lengths; re-run only at the pilot length as a within-v3 sanity check.
- **`latent`** — PRIMARY. Value stated in prose with zero lexical overlap with
  the question and one required inference hop (e.g. planted "the gateway should
  abandon a request after a quarter of a second", asked as `timeout_ms`, answer
  250). Semantic decoys state a superseded value in equally plausible prose.
- **`synth`** — NOVEL. Ten services, each with `retry_budget` planted in the
  shallow half of the log and `timeout_ms` in the deep half. Questions are
  **selections, not computations** ("among services with retry_budget >= X,
  which has the highest timeout_ms"), so the measured thing is the cross-window
  join and not arithmetic. v2's lesson was that conflating two failure modes in
  one score destroys the result; do not reintroduce it.

`overwrite` (same key rewritten at several depths, asked for the current value)
was designed and cut for budget. It is the weakest of the four: context clash is
better argued from the literature than measured badly at low n.

## Pre-registered thresholds

- **Difficulty calibration band: 90-95% at the 5k control length.** At 100% there
  is no headroom and a null is indistinguishable from a saturated instrument,
  which is exactly what v1, v2, and the 154-trial Claude-vs-GPT run all hit.
  Below 85% task difficulty dominates and length becomes noise, which is the
  confound Chroma names. Tune question difficulty at 5k until the base lands in
  band, THEN spend on the grid.
- **Effective length: longest length retaining >=85% of the 5k base score.**
  NoLiMa's definition, adopted unchanged so we are not inventing a flattering bar.
- **Degradation is the 5k-to-long gap on an identical task**, never an absolute
  score, so harder questions cannot masquerade as context rot.

## Statistics

- The outcome is binary per key, so n>=30 (a rule of thumb for the sampling
  distribution of a *mean*) is the wrong test. Proportions need np and n(1-p)
  above ~10, and near p=1 the normal approximation fails at any n. v2 sat at
  p=1.0 where a normal CI has zero width. **Use Wilson intervals.**
- Keys inside one instance share a prompt and a haystack, so they are clustered,
  not independent. v2's 180 key-observations per length were effectively n=20,
  not n=180. **n counts instances.** v3 uses n=30.
- n=30 instances detects a drop of roughly 15 points or more. NoLiMa's drops are
  20-60 points, so this is adequately powered and going higher buys nothing.

## Spend plan

Measured per-run cost from v2 (list-price equivalent, unbilled: runs go through
the local `claude` CLI on the subscription with API keys stripped):

| length | $/run | in-tok/run | n=30, one arm |
|---|---|---|---|
| 5k | 0.14 | 43,930 | $4.20 |
| 25k | 0.26 | 63,340 | $7.71 |
| 50k | 0.42 | 89,724 | $12.57 |
| 100k | 0.73 | 142,157 | $21.94 |
| 200k | 1.34 | 241,843 | $40.06 |
| 400k | 2.64 | 456,376 | $79.14 |
| 700k | 4.06 | 694,572 | $121.66 |

The top two lengths are 70% of the money; a full seven-length grid is $287 per
arm. Staged instead:

1. **Pilot** — `latent` and `synth` at 5k only, n=10. ~$3 and ~4 min per
   iteration. Iterate difficulty until the 5k base is in the 90-95 band. Budget
   2-3 iterations (~$8). This gate costs a coffee and would have caught v1, v2,
   and the 154-trial run.
2. **Grid** — `latent` and `synth`, n=30, at 5k and 50k. $33.54. NoLiMa's drops
   are already large by 32K, so 50k is a real test point, not a compromise.
3. **Then decide from data.** Degradation at 50k -> fill intermediate lengths
   plus a small n=8 cell at 700k (~$32) to map the curve and keep the past-128K
   novelty. No degradation through 50k -> that is itself the strong result, and
   spending on 200k+ is a conversation, not a default.

Ceiling for stages 1-2: ~$42.

## Both outcomes are publishable, which is the point

- `latent` or `synth` degrades: "claimed 1M, effective X" with the first Claude
  numbers on this task and the first data past 128K.
- Both hold: "Sonnet 5 closed the NoLiMa gap that thirteen models failed a year
  ago, and here is the receipt at 700k."

Neither outcome requires the other to be true for the writeup to work. If that
stops being the case, stop and re-read this file.
