# What still rests on quarantined input tokens after ticket 31

Ticket 31 AC#6. Companion to `PASS-FIELD-AUDIT.md`: that file audits the `pass`
field's readers, this one audits `tokens_in`'s. Both exist for the same reason —
a number that a gate now excludes is not the same as a number nobody ever
published, and the difference has to be written down rather than inferred from
the absence of a warning.

The gates landed. 268 rows inspected, 148 carry a measured `tokens_in`, 56 are
recovered from `usage.jsonl` by `run_id`, and 64 are quarantined — all 64
`fable`'s, none with a retained transcript. What follows is what those 64 rows
still hold up.

Those 268 rows are `results.jsonl`, and they are **not** the whole of this repo's
input-token exposure. Item 9 (added 2026-07-30, ticket 35) carries a second corpus
of 256 ladder rows that ticket 31's backfill never reached, 256/256 quarantined.
Items 1–8 are about the first corpus only; read the count above as scoped, not
total.

Every figure below was recomputed against the corpus at this commit, in memory,
by the module that publishes it. Nothing here is quoted from a prior session.

---

## Strike or null — the rule for retracting a published number

Ratified by Drake 2026-07-30 (ticket 35 AC#4). Stated here because both worked
examples are in this document, and because the two retractions in it landed
differently on purpose and a later reader would otherwise read the difference as
drift.

**Strike a published number when nothing can refill it. Null it with a
quarantine status when a named action can — and name that action in the status.**

The test is whether a closing condition exists, not how bad the number was.
Striking says *this quantity is not computed here any more*. Nulling with a
status says *this quantity is withheld, and here is what would restore it*; a
reader who finds a key simply missing learns nothing, which is the right outcome
only when there is nothing to learn.

| retraction | disposition | why |
|---|---|---|
| `stats.py` §5's `tokens_in + tokens_out` sign-flip axis (ticket 31 AC#3) | **struck** — the axis is gone, see `summary_tokens`'s docstring and item 1 below | no closing condition. The axis was wrong in principle, not starved of data; recomputing it on a clean corpus would still be the wrong axis |
| `effort-verdict.json`'s 16 `scaffold_in_median` values (ticket 35 AC#4) | **null + `scaffold_in_status: "quarantined"` + `scaffold_in_n: 0`** — see item 9 below | a live closing condition: re-run `probe_endpoints.py --phase ladder` under the current parser. The quantity is well-defined and measurable; this corpus just cannot supply it |

A status that does not name its refilling action is not compliant with this
rule — `"quarantined"` earns its keep only because item 9 and the closing table
below both state the re-probe. If no action can be named, strike instead.

---

## 1. The retired section-5 finding, and the two places it is still published

`stats.py:section_permutation` used to run the sign-flip test on
`tokens_in + tokens_out`. Ticket 31 AC#3 struck that axis (see `summary_tokens`'s
docstring) and the section now runs on output tokens alone. Recomputing both
axes over the current corpus, with `stats.py`'s own machinery:

| corpus | axis | observed sum | patterns as-or-more extreme | p |
|---|---|---|---|---|
| all 268 rows (pre-ticket-34) | in+out | **-17.9951** | 2 of 512 | 0.0039062 |
| gated, 267 rows | in+out | -17.9853 | 2 of 512 | 0.0039062 |
| all 268 rows | out-only | +0.1314 | 416 of 512 | 0.8125000 |
| **gated, 267 rows — the published one** | **out-only** | **+0.2136** | **348 of 512** | **0.6796875** |

The finding does not weaken on the honest axis. It **inverts and dissolves**: a
strongly significant "Fable is ~18 log-token units cheaper per task" becomes a
sign-flipped null. Every one of the nine per-task diffs was between -1.73 and
-2.27 on the in+out axis and between -0.20 and +0.30 on the out-only one. That
spread is the undercount, not the models: it was `fable`'s quarantined input side
subtracted from `sol`'s measured one, nine times, and the fact that all nine
pointed the same way is what made it look like a result.

Note also that the one-row ticket-34 gate moves the out-only figure by 0.08 and
the in+out figure by 0.01. On a defective axis the corpus hygiene fix is
invisible. That is what a number swamped by its own instrument error looks like.

**It is still published.** Not as a retracted claim — as a live figure in a
frozen deliverable:

- `deliverables/STATS-APPENDIX.md:118` — "Observed sum of diffs = **-17.7172**
  over k=9 tasks. 2 of 512 sign patterns are as-or-more extreme -> two-sided
  **p = 0.0039062**." Computed over the older 154-run corpus
  (`STATS-APPENDIX.md:5`), which is why it reads -17.7172 rather than -17.9951.
  Same defect, same conclusion, smaller corpus. Frozen at `339e203`, 2026-07-10.

- `runner/results/PILOT-STATS.md:79` — §5 of the **5-run pilot**
  (`PILOT-STATS.md:5`: "Source: 5 run row(s)"). Its figure is
  **-1.9079 over k=1 tasks, 2 of 2 patterns, p = 1.0000000** — the same
  `log(total tokens)` axis, not the same number. It carries the defect but not
  the false significance, because k=1 cannot produce one. Recorded here so the
  file is not later "corrected" to a -17.7172 it never contained.

Neither has been regenerated since the fix, and neither is expected to be —
re-running the deliverables against the current corpus is out of scope for
ticket 31 and would change far more than this line. The point of writing it down
is that a reader who lands on `STATS-APPENDIX.md` today gets a significant result
that the code no longer computes and no longer believes.

## 2. Every deliverable is frozen pre-fix

The parser fix shipped in `f11be7e`, 2026-07-27T15:38:36Z. The last commit to
touch each deliverable:

| file | last commit | date |
|---|---|---|
| `deliverables/ANALYSIS.md` | `339e203` | 2026-07-10 |
| `deliverables/BLOG-POST.md` | `c78d67f` | 2026-07-10 |
| `deliverables/FINDINGS.md` | `b323cca` | 2026-07-10 |
| `deliverables/LINKEDIN-DRAFT.md` | `b323cca` | 2026-07-10 |
| `deliverables/STATS-APPENDIX.md` | `339e203` | 2026-07-10 |
| `deliverables/TABLES.md` | `339e203` | 2026-07-10 |
| `deliverables/VIDEO-SCRIPT.md` | `b323cca` | 2026-07-10 |
| `deliverables/COMMENT-GPT56-HYPE-POST.md` | `ba9b15c` | 2026-07-12 |

All eight predate the fix by more than two weeks. Every input-token and dollar
figure in all of them was computed by the pre-fix parser. `TABLES.md:3` still
reads "Source: 154 run row(s), 154 passing, 154 judged" and `TABLES.md:9` still
reports fable/medium quality as 8.42 where the current corpus computes 8.41 —
the corpus itself has moved on underneath the published tables, independently of
the token defect.

## 3. The "vendors count differently" explanation is at least partly our own bug

Four deliverables explain the input-token gap between the two CLIs as a vendor
accounting difference:

- `deliverables/BLOG-POST.md:82` (section heading: "A caveat you cannot skip:
  input tokens do not compare across vendors") and `:84` — "Claude reporting
  around 19.4k median input tokens while GPT reports 95k to 175k. That is not GPT
  reading five to nine times more of your code. The two CLIs count context
  resends and cache reads differently."
- `deliverables/FINDINGS.md:19` — "Input-token accounting is vendor-incomparable.
  Fable CLI reports ~19.4k median input; Sol CLI 95k–175k (counts context
  resends/cache reads differently)."
- `deliverables/VIDEO-SCRIPT.md:87` — "the two vendors don't even **count** the
  same way... because it counts every re-send of context."
- `deliverables/LINKEDIN-DRAFT.md:41` — image alt text, "cross-vendor
  input-token accounting is not directly comparable."

The explanation is not wrong, but it is not the whole cause, and it assigns to
the vendors an error that is partly ours. Claude's ~19.4k side is a **pre-fix
`fable` number**: the v1 parser summed only `usage.input_tokens` and dropped
`cache_creation_input_tokens` and `cache_read_input_tokens` entirely. On the 56
buggy-branch rows where a transcript survived and could be re-parsed, every
recovered value was strictly larger than the number on the row — the haiku arms
by roughly four orders of magnitude (85 → 376,248; 114 → 454,988). `fable`'s own
64 rows have no transcript, so the size of *its* undercount is unmeasured; what
is measured is that the same parser, on the same branch, undercounted every row
it could be checked against.

The codex branch was never buggy. So the 19.4k-vs-175k gap is an undercounted
number set against a correct one, plus whatever genuine vendor difference exists
underneath. We cannot currently separate the two terms, and no deliverable says
so.

**The "dumb zone" disclosure rests on the same axis.** It appears at
`BLOG-POST.md:112`, `FINDINGS.md:40`, `VIDEO-SCRIPT.md:91`, and
`LINKEDIN-DRAFT.md:51`, and in every version the load-bearing contrast is "Sol
exceeded 100k cumulative input on 65 of 88 runs; **Fable peaked at 19.6k**."
That 19.6k is a pre-fix `fable` maximum from the quarantine set. The disclosure
is written as a caveat *against* Sol and is presented as the conservative,
fair-to-the-other-side reading — but the asymmetry it describes is inflated by
our undercount of Fable, in the direction that makes the disclosure look more
generous than it is. It may well survive correction; on the haiku evidence a
corrected Fable input side could plausibly cross 100k, which would delete the
contrast entirely. Unmeasurable for `fable` without transcripts.

## 4. Ungated readers that remain

### `tables.py` pass counts — deliberate, carried forward

The quality means gate on both `pass` and `corpus_gates.summarizable` at
`tables.py:177` and `:310`. The pass **counts** do not, at:

`:171` (table 1, pass ladder) · `:190` (table 2, tokens-per-pass) · `:208`
(table 3, harness delta) · `:236` (table 4) · `:250` (table 5, per-cell reps) ·
`:275` (table 6 best-config selection) · `:337` (`n_pass` in the `Source:` line)

The pass rate is still 1.00 everywhere and the `cli_error` row is still counted
as a pass in all seven. This is the residual `PASS-FIELD-AUDIT.md` hands
forward, restated here with current line numbers because the ones that file
recorded (`:126`, `:199`, `:222`) have drifted.

### `effort_verdict.py` is named in AC#3 and is not in the roster — CLOSED 2026-07-30 (ticket 35)

**Closed by ticket 35.** `effort_verdict.py` now imports `corpus_gates`, resolves
its magnitude read through `tokens_in_rows`, and is the fourth name in
`READER_MODULES` with the roster floor raised 3→4 so it cannot silently shrink
back. The one remaining ungated read is named and pinned rather than merely
surviving: `usage_block_empty` is a *presence* read, permitted because a pre-fix
undercount is 85 or 114 or 35670 and never 0, so the quarantine cannot change its
answer on any row — while routing it through the gate would drop all 256 ladder
rows as measurement failures and manufacture a corpus of nothing.
What the fix surfaced is a new residual, filed as item 9 below: the corpus that
reader reads was never in ticket 31's scope at all. The original finding is kept
below unedited, because the interesting part is not the ungated read — it is that
a roster whose whole job was to make this loud reported three passes and zero
failures over it.

AC#3 names four consumers that must refuse quarantined input tokens:
`tables.py`, `stats.py`, `calibration_report.py`, `effort_verdict.py`. The first
three are gated and are asserted to be gated by
`tests/test_reader_token_gates.py:56`'s `READER_MODULES` roster. **The fourth is
neither.** `effort_verdict.py` reads `tokens_in` ungated at `:155` and `:161`,
and its absence from `READER_MODULES` means the roster assertion — whose stated
purpose is to make an ungated reader loud — passes over it in silence.

This is the exact failure mode that comment describes, realised. It is recorded
here rather than fixed because fixing it is a code change beyond AC#6's "state
the residual" scope, and it should be its own ticket.

## 5. Numbers that are absent rather than wrong

### `fable`'s `$/task` is unavailable, and says so

`tables.py` table 6 renders `unavailable` in `fable`'s dollar cell, with the
count printed: "`fable`: 0 of 26 rows in the winning cell have a true
`tokens_in` (all quarantined pre-fix)". This is the correct outcome, not a gap —
`$/task` is the one cell that needs both axes, so it may not fall back to
output-only, and an empty average must not render like a real one. It is listed
here because "unavailable" for the analysis backbone is itself a published
result about the corpus, and because it means **the headline cost comparison the
project exists to make cannot currently be made for Fable at all.**

### `stats.py` §7 is over the full judgment set, on purpose

(Numbered §6 until 2026-08-03, when the matched-cell cost test was inserted as
§6 and every section below it shifted by one.)

`build_report` applies the summarizable gate once, at the top, so no section can
skip it — except §7, which is computed over the full judgment set by design.
The exclusion note (`stats.py`'s header block) states the reason inline: §7 measures
whether the two judges agreed with each other, not how a model performed, and a
truncated run's judges either agreed or did not. Recorded as a deliberate
deviation so a later reader does not "fix" it.

## 6. `negative-control-28.jsonl` cannot be stamped

`runner/results/negative-control-28.jsonl` is named for ticket 28, not for a row
count: it holds **24 rows**. Its shape has nothing in common with the corpus:

```
agrees · arm · files_touched · loc_changed · output_tail · passed
path · rc · tamper_report · task · ticket · wall_s
```

No `model`, no `ts`, no `exit_reason`, no `run_id`, and the verdict field is
`passed`, not `pass`. AC#1's provenance stamp is derived from a row's `ts`
against the fix instant and keyed by `run_id`; AC#2's backfill needs the same
two fields. Neither exists here, so the file is unstampable — not overlooked.

It is also not a `calibration_report` corpus and must not be handed to one. It
is written by `negative_control/run_arms.py` and read only by
`negative_control/score.py`, which reads `passed` and `agrees`. It carries no
token fields at all, so it is outside the token defect entirely; the reason it
appears in this document is to record *why* it was skipped, since a corpus file
that no gate ever touched is otherwise indistinguishable from one a gate missed.

The same applies to its sibling `negative-control-28-weak.jsonl`.

## 7. Three table-6 winners now rest on a `tokens_out`-only tiebreak

Table 6 picks each model's best config by highest pass rate, tiebroken on lowest
mean tokens (`tables.py:269-280`). Pass rate is 1.00 for every cell, so **the
tiebreak decides every row of that table** — the "best config" column is
entirely a token-axis artifact. Changing that axis from in+out to out-only
changes three of the eight winners. Recomputed both ways over the gated corpus:

| model | winner, out-only (current) | winner, in+out (pre-fix axis) | |
|---|---|---|---|
| `claude-haiku-4-5` | max/bare | max/bare | — |
| `claude-haiku-4-5-20251001` | max/bare | max/bare | — |
| `fable` | **medium/bare** | medium/harness | **flips** |
| `gpt-5.3-codex-spark` | **low/bare** | high/bare | **flips** |
| `gpt-5.6-luna` | low/bare | low/bare | — |
| `hybrid` | medium/harness | medium/harness | — |
| `kimi-k3` | **low/bare** | max/bare | **flips** |
| `sol` | low/bare | low/bare | — |

The out-only column is the honest one — it is the axis that was verified
byte-identical pre- and post-fix on all 56 re-parsed rows. But "honest" here
means "not computed from a broken number", not "measuring the thing a reader
wants". A reader looking for the cheapest config wants total cost, and for
`fable` we cannot compute it. `fable`'s published winner in
`deliverables/TABLES.md:105` is still medium/harness, the pre-fix answer.

`gpt-5.3-codex-spark` and `kimi-k3` are the sharper cases: both flip from a
high-effort winner to a low-effort one. On the correct axis they no longer
support "more effort was worth it", and neither model appears in any published
deliverable, so nothing external has to be retracted — but any future write-up
that pulls table 6 will be pulling a different answer than the corpus gave in
July.

## 8. Prices are placeholders and `$/task` is load-bearing

`tables.py:44-55` labels `PRICES` "List-price ESTIMATES ... Placeholders — edit
to taste". Six of the eight models in table 6 currently render a dollar figure
from them. Ticket 08's unverified prices are one blocker on the pricing ticket
and this ticket was the other; with this document AC#6 is met, but the dollar
column stays an estimate built on placeholder rates until 08 lands. No cost
claim should leave this repo before then.

## 9. The ladder corpus is a second corpus, and ticket 31's backfill never reached it

Added 2026-07-30 by ticket 35. Items 1–8 are all about `results.jsonl`. There is a
**second** input-token corpus that this document did not carry as its own item:
`runner/results/ladder-*.jsonl`, written by `probe_endpoints.py` and read only by
`effort_verdict.py`. Nothing asserted about `results.jsonl` could have caught it,
because it is not the same file. Measured 2026-07-30:

| Property | Ladder corpus |
|---|---|
| files | 11 (`ladder-{claude,codex,kimi}`, `-rep2..rep4`) |
| rows | 256 (239 reachable with a non-empty usage block; 7 dropped as measurement failures) |
| rows carrying `tokens_in_status` | **0** |
| `ts` range | 2026-07-25T17:59:50Z → 2026-07-25T21:43:43Z |
| fix instant (`f11be7e`) | 2026-07-27T15:38:36Z |
| retained transcripts | **0** — `probe_endpoints.py` writes none; `results/transcripts/` holds 241 files, none from a ladder probe |

Every row predates the parser fix by two days, and none can be retrofitted the way
ticket 31's amendment recovered 56 of `results.jsonl`'s 120 rows —
`usage_ledger.retrofit()` re-parses retained transcripts, and there are none. Under
`corpus_gates.tokens_in_usable`'s fail-closed rule the honest disposition is
**256/256 quarantined, unrecoverable without a fresh probe**, and that is now what
the tool prints: `ladder rows: inspected=239 kept=0 excluded=239 — unstamped=239`.

What that changed on disk: `runner/results/effort-verdict.json` published a bare
`scaffold_in_median` for each of its 16 entries (15,469 – 30,098), every one a
median over pre-fix input tokens and none of them labelled. All 16 are now `null`
with `scaffold_in_status: "quarantined"` and `scaffold_in_n: 0`. The module
docstring's claim that "input is the fixed scaffold and barely moves across tiers"
is retracted in place, since it was a claim about the exact quantity the broken
parser was getting wrong.

**The verdicts are not in doubt, and that is asserted rather than assumed.**
`classify()` runs on `tokens_out`, which was verified byte-identical pre- and
post-fix on all 56 re-parsed rows.
`test_verdicts_are_invariant_under_tokens_in` classifies the same rows twice with
every input count corrupted the second time and demands byte-identical output; the
regenerated JSON was diffed against the pre-fix file across 192 non-scaffold key
comparisons with zero differences. REAL=10, AMBIGUOUS=6, dropped=7 and 53 implied
frontier points all hold.

Deliberately **not** done: stamping the ladder rows `quarantined` by `ts`. Ticket 31
AC#1 forbids inferring provenance from a timestamp at read time, and a backfill that
writes that inference down is the same inference with a write step. Unstamped
already fails closed, which is the same answer honestly reached.

---

## What would close each item

| # | residual | closes when |
|---|---|---|
| 1 | retired §5 finding still published | deliverables regenerated against the current corpus, or annotated in place |
| 2 | all deliverables frozen pre-fix | same |
| 3 | vendor-counting explanation partly our bug | `fable` transcripts recovered (none exist) or arms re-run — out of scope, costs tokens |
| 4a | `tables.py` pass counts ungated | deliberate; closes only if the pass rate stops being 1.00 |
| 4b | `effort_verdict.py` ungated and off the roster | **CLOSED 2026-07-30, ticket 35** — gated through `corpus_gates`, fourth name in `READER_MODULES`, roster floor 3→4 |
| 5 | `fable` `$/task` unavailable | `fable` re-run post-fix; nothing cheaper exists |
| 6 | `negative-control-28.jsonl` unstampable | never — recorded as permanently out of scope |
| 7 | table-6 winners on an out-only tiebreak | `fable` re-run, or the tiebreak axis is documented in the table itself |
| 8 | placeholder prices | ticket 08 verifies real list prices |
| 9 | ladder corpus input tokens 256/256 quarantined | re-run `probe_endpoints.py --phase ladder` under the current parser; costs tokens, and no published verdict depends on it |

Items 3, 5 and 7 all reduce to the same 64 rows with no transcripts. Re-running
`fable` is the only thing that closes them, it is explicitly out of scope for
this ticket, and it is the honest price of the residual.
