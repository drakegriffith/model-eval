# Pre-commitment — the `BACKWARDS` verdict (ticket 42)

Written **before** the derivation ran. Nothing in this file was chosen after seeing what
the corpus does under the new rule; the commit that contains this file is the commit that
fixed the threshold, and the derivation is in the commit *after* it. That ordering is the
whole point — the four thresholds already in `effort_verdict.py` earn their authority from
having been set before the data was inspected, and a fifth added afterwards borrows none
of it unless it is fixed the same way.

## The threshold

| | |
|---|---|
| Name | `BACKWARDS_END_RATIO` |
| Value | **0.95** |
| Home | `runner/effort_verdict.py` — exactly one definition, nothing else restates it |
| Fixed in commit | the commit adding this file (see §5) |

## 1. The rule

A ladder is **`BACKWARDS`** when it would otherwise have been `AMBIGUOUS` **and**

    mean(highest probed tier) <= BACKWARDS_END_RATIO * mean(lowest probed tier)

with tiers ordered by `TIER_ORDER` and means taken over that tier's samples. In one
sentence: the dial's top end spends at least 5% *less* than its bottom end.

## 2. Why 0.95, and why that is not a fitted number

0.95 is not new to this module. `monotone_score()` already treats `b >= a * 0.95` as
"this step did not go down" — a 5% tolerance band for step-to-step wobble, fixed
2026-07-25 before any of the ladder data existed. `BACKWARDS_END_RATIO` applies that same
already-committed tolerance end-to-end instead of step-to-step. It is a re-use of a
pre-committed constant, not a fifth free parameter tuned against the answer.

The alternative I rejected: a bespoke drop threshold (e.g. 0.80, "a 20% reversal"). Any
such number would have had to be picked with ticket 13's 28% reversal already on the page,
which is exactly the move this file exists to prevent.

## 3. Why the split carves **only** out of `AMBIGUOUS`

The branch sits inside the existing `else`. `NO-OP`, `REAL`, `UNREPLICATED` and
`INSUFFICIENT` are reached by precisely the conditions that reached them before, so
`BACKWARDS` cannot steal a model from any of them. Two consequences, both deliberate:

- The pre-split verdict of any ladder is recoverable by pure rename
  (`BACKWARDS -> AMBIGUOUS`), which is what makes the transition tally computable without
  a second classification pass over the corpus.
- A `NO-OP` ladder that happens to end 6% down stays `NO-OP`. That is correct: at spread
  below 1.20x the whole dial is inside the noise floor, and "runs backwards" is a stronger
  claim than the measurement supports. `claude-haiku-4-5` is the corpus's example.

## 4. What `BACKWARDS` claims, and what it does not

It claims: **the recorded ladder's direction is inverted**, so this reading must not be
described as "close to real" the way a genuinely shallow-but-forward ladder can be. That
is ticket 13's warning made machine-readable.

It does **not** claim a credited effect running the other way. `REAL` requires
`between_cv >= NOISE_MARGIN * within_cv`; `BACKWARDS` deliberately carries no such noise
gate, because it is a *description of an unresolved ladder's direction*, not a promotion
out of the unresolved bucket. Ticket 13's own reading of the subject — *"not a shallower
ladder — no ladder plus more noise"* — would fail a noise gate, and a state that excluded
the case it was built for would be worthless. `BACKWARDS` sits alongside `AMBIGUOUS` in
every downstream consumer: not credited, one frontier point, still listed as needing more n.

## 5. Falsifiable predictions, recorded before the derivation

These come from readings ticket 13 already published (quoted in ticket 42), not from any
inspection of the corpus under the new rule.

1. `claude-haiku-4-5-20251001` on `t1-ts-b` moves **`AMBIGUOUS` -> `BACKWARDS`**. Recorded
   means: `max` 3985 vs `low` 5551, end ratio 0.72, comfortably under 0.95.
2. `claude-haiku-4-5` stays **`NO-OP`** — spread 1.06–1.08x is below `NOOP_SPREAD`, so the
   new branch is never reached for it.
3. No model in either corpus leaves `REAL`, `UNREPLICATED`, `NO-OP` or `INSUFFICIENT`.
   Every transition in the printed tally has `AMBIGUOUS` on its left-hand side.
4. Among the remaining `AMBIGUOUS` blocks, exactly those whose highest probed tier mean is
   at or below 0.95x their lowest probed tier mean move, and no others.
5. `REAL_SPREAD` = 1.50, `NOOP_SPREAD` = 1.20, `NOISE_MARGIN` = 2.0 and
   `MIN_N_FOR_VERDICT` = 2 are unchanged, and `BACKWARDS_END_RATIO` is still 0.95 after
   the derivation.

**If the data makes this rule look wrong, that is a new ticket with this rule on record —
never an edit in place.** Predictions 1–5 are checked in `§6` of the ticket 42 close-out
and pinned by `runner/tests/test_effort_verdict_backwards.py`.

## 6. Out of scope, explicitly

- Retuning any of the four existing thresholds.
- Reaching for `tokens_in` to separate the new state. The ladder corpus's input counts are
  256/256 quarantined (ticket 35); the split reads `tokens_out` only, and ticket 35's
  invariance property is re-asserted under the new vocabulary rather than inherited.
- Any new probe, run or spend. The re-derivation runs over rows already recorded.
