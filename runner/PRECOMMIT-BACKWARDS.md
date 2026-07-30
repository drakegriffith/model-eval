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
| Fixed in commit | **`758140c`** — `feat(ticket-42): fix the BACKWARDS rule before deriving anything`. Still 0.95 after the derivation. |

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
never an edit in place.** Predictions 1–5 are checked in §6 below
and pinned by `runner/tests/test_effort_verdict_backwards.py`.

## 6. Derivation, part 1 of 2 — the PROBE corpus (`results/ladder-*.jsonl`)

Run at `758140c`, `python3 runner/effort_verdict.py`. 256 ladder rows in, 7 dropped for an
empty usage block, 16 models classified. **No new runs**: `results.jsonl` is 268 lines and
sha1 `a2a146b4…` before and after; the probe corpus is 256 rows before and after.

    ticket 42 transitions over 16 model(s):
      AMBIGUOUS -> AMBIGUOUS: 4    AMBIGUOUS -> BACKWARDS: 2    REAL -> REAL: 10

    claude-haiku-4-5           AMBIGUOUS -> BACKWARDS  spread 2.15  end/1 0.85  mono 0.5
    claude-haiku-4-5-20251001  AMBIGUOUS -> BACKWARDS  spread 1.90  end/1 0.81  mono 0.5

Frontier points unchanged at 53 — `BACKWARDS` collapses to one point exactly as
`AMBIGUOUS` did. Both models remain in the needs-more-n list.

### Prediction 2 did not survive as written — recorded, not edited away

Prediction 2 said `claude-haiku-4-5` **stays `NO-OP`**. On this corpus it does not: it was
`AMBIGUOUS` at spread 2.15 and moved to `BACKWARDS`.

The prediction was not wrong about the reading it cited; it was **wrong about which corpus
it named**. Ticket 13's `NO-OP` at spread 1.06–1.08x with within-cell CV 0.01–0.10 is a
reading on `results.jsonl` real-task rows, derived through `ladder_from_results.py`. The
probe corpus is a different measurement of the same model on toy puzzles, and there it was
already `AMBIGUOUS` before this ticket touched anything. Two corpora, two readings, one
model id — and the prediction elided the distinction.

That is a defect in the prediction's wording, so it is logged here rather than reworded.
`BACKWARDS_END_RATIO` did not move. The `NO-OP` half of prediction 2 is tested where the
reading it refers to actually lives, in part 2 below.

### Part 2 of 2 — the REAL-TASK corpus (`results.jsonl`, the six `t13-*-ladder.json`)

Run 2026-07-30, `ladder_from_results.py --sweep t13-* --pooled --json-out`, once per sweep.
**No new runs**: `results.jsonl` is 268 lines and sha1 `a2a146b4…` before and after; the six
tables were regenerated from rows already on disk. Diffed field by field against the versions
they replaced, the *only* changes are the added `end_ratio` column and one verdict:

    ticket 42 transitions over 12 task block(s):
      NO-OP -> NO-OP: 2   AMBIGUOUS -> AMBIGUOUS: 5
      AMBIGUOUS -> BACKWARDS: 1   REAL -> REAL: 4

    t13-haiku-pin  t1-ts-b  AMBIGUOUS -> BACKWARDS  spread 1.39  end/1 0.72  mono 0.0
                            low=5551 high=5061 max=3985

Every other block's spread, monotone score and both CVs came back byte-identical to the
2026-07-26/27 tables — the re-derivation moved a label, not a measurement.

**Predictions 1, 3, 4, 5 all held, and prediction 2's `NO-OP` half held here.**

1. **CONFIRMED, exactly as written.** `claude-haiku-4-5-20251001` on `t1-ts-b`: `max` 3985 vs
   `low` 5551, end ratio 0.72, `AMBIGUOUS -> BACKWARDS`. This is the corpus prediction 1's
   numbers were quoted from.
2. **The `NO-OP` half CONFIRMED, here, where the cited reading actually lives.**
   `claude-haiku-4-5` stays `NO-OP` on both blocks — spread 1.08 and 1.06, the 1.06–1.08x §5
   named. Note `t1-ts-b` ends **7% down** (end/1 0.93, under 0.95) and is `NO-OP` anyway,
   because spread never reaches `NOOP_SPREAD` so the branch is never consulted. That is §3's
   "a `NO-OP` ladder that happens to end 6% down stays `NO-OP`" exercised by the corpus rather
   than merely asserted. The part-1 failure stands as recorded above; it was a naming defect,
   and this is the reading it should have named.
3. **CONFIRMED over both corpora.** Every transition in both tallies has `AMBIGUOUS` on its
   left, or is a fixed point. Nothing left `REAL`, `NO-OP`, `UNREPLICATED` or `INSUFFICIENT`.
4. **CONFIRMED, and it discriminates.** Six `AMBIGUOUS` blocks; end ratios 0.72, 0.97, 1.40,
   1.42, 1.71, 4.38. Exactly the 0.72 moved. `t13-haiku-pin`'s `t2-py-b` at **0.97** is the
   near-miss that makes this a two-sided result: it ends down and stays `AMBIGUOUS`.
5. **CONFIRMED.** `REAL_SPREAD` 1.50, `NOOP_SPREAD` 1.20, `NOISE_MARGIN` 2.0,
   `MIN_N_FOR_VERDICT` 2 unmoved; `BACKWARDS_END_RATIO` still 0.95 after both derivations.

The `POOLED` rows are excluded from the tally (task is the blocking factor; `--pooled` exists
only to show what ignoring the block costs). For the record `t13-haiku-pin`'s pooled row also
moved, at end/1 0.84.

### A boundary found while writing the tests, not predicted

Ticket 35's invariance holds under corrupted `tokens_in` **only when the corruption preserves
zeroness**. 17 ladder rows report a wholly empty usage block; 10 are unreachable and skipped
before usage is consulted, leaving the 7 `dropped`. Inventing a nonzero `tokens_in` for those
7 un-drops them and moves `claude-opus-5[1m]` `BACKWARDS -> AMBIGUOUS`, because rows worth 0
tokens then drag a tier mean down.

This is not `tokens_in` leaking into `classify()`. It is `usage_block_empty` — the one
permitted **presence** read — behaving exactly as its docstring specifies, on an input the
`parse_usage` bug could not have produced (it undercounted 30x–400x and never returned 0).
Ticket 35's licence for that read is precisely that no real quarantine decision can flip the
predicate. Recorded here and pinned by
`test_the_presence_read_is_where_tokens_in_still_bites` so the next audit reads a decision
instead of re-finding a surprise.

## 7. Out of scope, explicitly

- Retuning any of the four existing thresholds.
- Reaching for `tokens_in` to separate the new state. The ladder corpus's input counts are
  256/256 quarantined (ticket 35); the split reads `tokens_out` only, and ticket 35's
  invariance property is re-asserted under the new vocabulary rather than inherited.
- Any new probe, run or spend. The re-derivation runs over rows already recorded.
