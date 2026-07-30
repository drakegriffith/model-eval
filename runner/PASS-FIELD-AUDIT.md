# The `pass` field: writer, readers, and what the ungated readers actually read

Ticket 34's AC#3 deliverable. This is the document that decides whether the
defect mattered, so it records measurements rather than reassurances.

## The rule

`exit_reason == "ok"` is this instrument's completeness gate. It is asserted at
`run.py:existing_ids` ("the same completeness gate ladder_from_results.py uses
for analysis"), honored by `ladder_from_results.py:63-64`, which excludes
non-`ok` rows and prints the excluded count rather than dropping it quietly, and
it is the reason `run.py:1116-1117` renames a zero-turn `ok` run to
`no_completion`.

Before ticket 34, `pass` was exempt from that rule. Grading ran unconditionally
and exactly two reasons were special-cased — `cap_exhausted` (forced `False`,
grader's verdict kept as `pass_at_cap`) and `verify_timeout` (forced `False`).
`cli_error` got neither. The defect was the asymmetry, not the row.

After ticket 34, `run.py` forces `pass = False` whenever `exit_reason != "ok"`,
by default rather than by enumeration, and the grader's own verdict survives on
every row as `pass_raw` (`None` when the grader never returned one at all).

## The row that prompted it

```
run_id       sweep2b--fable--medium--bare--t3-a--r1
exit_reason  cli_error        pass  true        pass_at_cap  null
```

One row of 268. Per AC#5 it is **not** backfilled and **not** re-run: the corpus
stays as it was written, so its history stays auditable.

## What it actually reached — measured, not assumed

The ticket filed this field as "currently latent" because all 268 rows are
verifier-pass, so `pass` carries no discrimination. **That is true of the pass
rate and false of the pipeline.** `pass` is not only summarized, it is also used
to *select work*:

- `judge.py:passing_run_ids` picked run_ids to send to the paid judges by reading
  `pass` with no completeness gate. The cli_error row was therefore judged, and
  its score of **8.75** entered the corpus quality means.

Effect of that one score, computed over the corpus at `4221110`:

| figure | with the row | without it | delta |
|---|---|---|---|
| its own cell (fable/medium/bare/t3-a, n=2) | 9.00 | 9.25 | 0.25 |
| fable @ medium, pooled (n=40 judged) | 8.422 | 8.413 | 0.009 |
| corpus pooled quality (n=153 judged) | 8.959 | 8.960 | 0.001 |
| pass rate, any grouping | 1.00 | 1.00 | 0 |

So: below reported precision everywhere except its own two-rep cell, where it
moves a published number by a quarter point. A constant field cannot bias a
comparison — but it was never only a field, it was also a selector, and that is
the half the "latent" reading missed.

**Open, for Drake, deliberately not decided here.** Whether that 9.00 cell gets
restated as 9.25 changes a published number, and this repo's standing rule is
that a changed published number is ratified, not quietly regenerated. Nothing in
this ticket regenerates a table.

## Every `pass` read site in `runner/`

Enumerated by scanning `runner/` for `["pass"]` / `.get("pass")`. Line numbers
are as of this commit.

| # | site | gates on `exit_reason` first? | disposition |
|---|---|---|---|
| 1 | `ladder_from_results.py:66` (`passing_only` filter) | **yes** — `:63-64` drops every non-`ok` row before this line runs, and prints the excluded count | already correct; the pattern the rest inherit |
| 2 | `ladder_from_results.py:90` (`pass_rate`) | **yes** — same upstream filter | already correct |
| 3 | `judge.py:234` (`passing_run_ids`) | **now yes** — gate added by this ticket | **fixed.** The only site that selects work rather than summarizing it: its output costs judge calls and lands in quality means. Gated in code and not left to inherit the writer's fix, because it reads the *historical* corpus, where the bad row still exists by AC#5. Covered by `tests/test_pass_completeness_gate.py` |
| 4 | `stats.py:168` (`is_pass`, feeding `:185, :211, :264, :356, :443`) | **no** — `exit_reason` appears nowhere in `stats.py` | recorded, not fixed — see below |
| 5 | `tables.py:126,127,140,157,185,199,222,230,252` | **no** — `exit_reason` appears nowhere in `tables.py` | recorded, not fixed — see below |
| 6 | `calibration_report.py:117,119,132,135,140` (pass rates and the model×task grid) | **no** — it *reads* `exit_reason` at `:103-104` for a distribution print, but no pass count is gated on it | recorded, not fixed — see below |
| 7 | `calibration_report.py:144` (`fails` list) | **no**, and inverted in sense: an incomplete run wrongly marked `pass=true` is *hidden* from the failure listing | recorded. Self-correcting once the writer gate lands, since the run then appears in this list, and `:147` already prints its `exit_reason` |
| 8 | `run.py:1301` (per-run console line) | n/a — prints `pass` and `exit_reason` side by side | no action; the operator sees both fields together |

## Why 4, 5, 6 and 7 are recorded rather than gated

Not because they are safe by inspection — three of them are demonstrably not,
which is what the table above measures. The reasons, in order:

1. **The fix belongs at the writer.** After ticket 34 no row can be written with
   `pass=true` and a non-`ok` `exit_reason`. Adding the same predicate to four
   more files puts one rule in five places with nothing checking that the five
   agree. That is not the harness's checker≠worker duplication, where the
   duplicate *is* the control and is annotated as such; these are all readers of
   the same data, and a fifth copy is just a fifth thing to drift.
2. **Site 3 is the exception that proves the rule, and it is annotated as such.**
   A selector's mistake compounds — it spends money and injects a score. A
   summarizer's mistake is confined to the number it prints. That asymmetry is
   the whole reason one site got a code change and four got a paragraph.
3. **Gating the summarizers would restate published numbers**, which is a
   ratification decision, not a refactor. Flagged above; left to Drake.

The honest scope of "recorded, not fixed": these four sites are correct for
every row written from ticket 34 onward, and exposed to exactly one row written
before it, whose measured effect is tabulated above. If the corpus is ever
regenerated or extended with a real pass/fail spread, revisit item 3 first.
