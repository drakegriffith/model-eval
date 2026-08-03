# LinkedIn draft — model-eval public release (2026-08-03)

**CORRECTION (2026-08-03, later same day) — DO NOT POST AS-IS.** The "Fable
matched Sol on pass rate using ~1/5 the tokens, p=0.004" claim below is
WRONG. It was computed as `tokens_in + tokens_out` for both models, but
Fable's `tokens_in` is `quarantined` on 100% of its rows (no true value
exists anywhere for it — see `runner/usage_ledger.py` and
`runner/corpus_gates.py`). The repo's own canonical stats script
(`runner/stats.py`, fixed 2026-07-30 per ticket 31 AC#3, ratified by Drake)
uses **output tokens only** for exactly this reason and was not re-run before
this draft was written. Re-run under that methodology
(`python3 runner/stats.py`, §5), the Fable-vs-Sol token gap is **not
statistically significant** (p = 0.68, direction mixed across tasks — see
`deliverables/STATS-CURRENT-2026-08-03.md` §5). The chart asset this draft
pointed to has been renamed to
`deliverables/assets/model-eval-token-chart-WRONG-DO-NOT-USE.{html,png}` —
do not use it. Next session: rewrite this post's cost claim (or drop it) and
regenerate a chart, if any, from `runner/stats.py`'s output, not a hand
computation.

Status: DRAFT, not posted. Drake posts manually. No LinkedIn posting tool used.

Numbers below verified against the live corpus this session:
- runner/results/results.jsonl: 268 rows, 8 models/modes, 15 tasks registered (9 run so far)
- runner/results/judgments.jsonl: 154 dual-judged rows (Claude + Codex judges)
- runner/results/transcripts/: 241 files
- Token ratio and judge-agreement stats recomputed directly from the files, not copied from
  the stale STATS-APPENDIX.md/FINDINGS.md/LINKEDIN-DRAFT.md (those describe the same
  underlying Fable-vs-Sol experiment but point at a different, now-dead domain).

---

I open-sourced the benchmark harness I've been using to compare frontier coding models:
github.com/drakegriffith/model-eval

It runs models headlessly against small, verifiable coding tasks (real bugs, real repos,
a script that proves the fix pass/fail, not a vibe check), grades the output with two
independent LLM judges on the same rubric, and publishes the raw transcripts and
judgments next to the code that produced them.

Where it stands right now: 268 runs across 8 models, 241 archived transcripts, 154 of
those runs scored by both judges. First real finding, from a matched-pairs comparison
(same tasks, same reps, each model at its own best-performing effort setting): Claude's
Fable matched Codex's Sol on success rate while using about a fifth of the tokens. That
gap held up under an exact permutation test, not just eyeballing an average (p = 0.004).

Equally important: at this sample size, raw pass/fail mostly doesn't separate these
models; nearly everything I've thrown at them so far gets solved. The signal is in cost
and in judged quality, which is why the dual-judge setup exists and why I checked that
the two judges actually agree with each other (they do, ~96% of the time within a point)
before trusting either one.

No UI, no signup. Try it from a terminal:

git clone https://github.com/drakegriffith/model-eval
cd model-eval
bash tasks/t1-py-a/selftest.sh          # offline, no model calls: proves the task
                                          # is real by showing verify.sh fail, then
                                          # pass, once the reference fix is applied
python3 runner/run.py --mock --limit 1   # exercises the full harness pipeline with
                                          # no tokens spent, no API key required

Point runner/run.py at your own Claude Code or Codex CLI (subscription auth, no key
needed) to run a live model against a task and see how it does.

Repo: https://github.com/drakegriffith/model-eval
