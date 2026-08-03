# LinkedIn draft: model-eval public release (2026-08-03)

Status: DRAFT, not posted. Drake posts manually. No LinkedIn posting tool used.

Rewritten 2026-08-03 (v4) at Drake's direction: retargeted at the now-live
Actual Intelligence Labs post. v3 pointed at drakegriffith.github.io and led on
the ceiling effect; v2 was a teaser; v1 carried the retracted "~1/5 the tokens,
p=0.004" claim. All in git history.

Link target (RESOLVED): https://actualintelligencelabs.ai/research/open-source-llm-eval-harness
Live as of 2026-08-03, PR #33 in zachk-alt/actual-intelligence-labs, commit 88f4571.

Body follows that post's angle: harness is open source + one cost question has
four defensible framings that give four different answers + the retraction. The
"every model passed everything" / ceiling story belongs to the predecessor post
`/research/claude-vs-gpt-154-run-experiment`, so it is deliberately NOT the hook
here. The effort-ladder finding (4.4x spend, zero flipped outcomes) is also cut
for length; it lives in the AIL post's FAQ.

Numbers: retraction figures from `deliverables/STATS-CURRENT-2026-08-03.md`
(verbatim `python3 runner/stats.py` over 268 rows, 267 scorable, 154
dual-judged). The four cost cells and their p-values match the AIL post's FAQ
and cost section verbatim.

Style constraints applied: no emojis, no em dashes, no en dashes,
correction-first framing.

---

I open-sourced the benchmark harness I run my model comparisons on:
github.com/drakegriffith/model-eval

Then I had to retract my own headline the same day I drafted it.

I had Claude Fable using fewer tokens than Codex Sol on 9 of 9 matched tasks,
p = 0.004. It was a great chart. I had computed cost as input tokens plus output
tokens, and Fable's input counts are quarantined as unreliable on all 64 of its
rows while Sol's are measured on all 112. I was comparing an undercount against
a true count and reading the measurement gap as a performance gap. The repo's
own stats script had been corrected for exactly this four days earlier. I
recomputed by hand instead of running it.

Ran the script. p = 0.68. Nothing detectable in either direction.

What replaced it is the more useful result. "Which model is cheaper per solved
task" turns out to have four defensible ways to ask it, and the same 267 runs
answer all four differently:

Pooled across all effort tiers: no detectable difference, p = 0.68.
Both held at medium: no detectable difference, p = 0.43.
Both held at high: the direction reverses, p = 0.086.
Each at its own cheapest passing tier: Sol beats Fable by roughly 1.6x, p = 0.0078.

One corpus, four framings, four answers. Only the last one clears a
significance bar, and it compares two different tiers against each other, so the
cell names matter more than the p-value does. Every vendor cost chart you have
seen picked one of these framings and did not show you the other three.

The harness is public so you can pick your own. Python runner, tasks that prove
their own difficulty with a verify script that fails before the reference fix
and passes after it, two blind judges scoring every diff, and every transcript
and raw token count committed next to the code that produced it.

The wrong chart is still in the repo, at
assets/model-eval-token-chart-WRONG-DO-NOT-USE.png, because it looks completely
convincing. That is the point.

Full write-up: https://actualintelligencelabs.ai/research/open-source-llm-eval-harness
