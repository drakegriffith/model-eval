# LinkedIn draft: model-eval public release (2026-08-03)

Status: DRAFT, not posted. Drake posts manually. No LinkedIn posting tool used.

Rewritten 2026-08-03 (v3) at Drake's direction: shorter, first person, leading
with the correction rather than the harness feature list. v2 was a teaser
pointing at the blog post; v1 carried the retracted "~1/5 the tokens, p=0.004"
claim. Both are in git history.

Link target: the post is live at drakegriffith.github.io today. An Actual
Intelligence Labs `/research/` version is IN PROGRESS (see
`docs/` handoff in this commit message and the ai-labs repo). If the AIL post
ships first, swap the closing URL to
`https://actualintelligencelabs.ai/research/<slug>` before posting.

Every number below comes from `deliverables/STATS-CURRENT-2026-08-03.md`, the
verbatim output of `python3 runner/stats.py` over the live corpus (268 rows,
267 scorable, 154 dual-judged). Effort claim: §3. Cost retraction: §5 vs the
hand-rolled version. Ladder multiple: §3 medians.

---

I open-sourced the benchmark harness I've been running my model comparisons
on: github.com/drakegriffith/model-eval

The part I didn't plan on writing: I had to retract my own headline the same
day I drafted it.

I had Claude Fable using fewer tokens than Codex Sol on 9 of 9 matched tasks,
p = 0.004. It was a great chart. I had computed cost as input tokens plus
output tokens, and Fable's input counts are quarantined as unreliable on all
64 of its rows while Sol's are measured on all 112. I was comparing an
undercount against a true count and reading the measurement gap as a
performance gap. The repo's own stats script had been corrected for exactly
this four days earlier. I recomputed by hand instead of running it.

Ran the script: p = 0.68, direction mixed, nothing detectable in either
direction.

What did survive, across 268 runs on 8 models: every model passed every task
it attempted, which is a ceiling effect and not a tie. And the effort knob
never flipped a single outcome. 15 comparisons of adjacent effort tiers, 144
matched pairs, zero discordant. Sol's top tier costs about 4.4x its cheapest
tier and buys zero additional passes.

Transcripts, judgments and raw token counts are committed next to the code
that produced them, so every number above can be checked against the
receipts. The wrong chart is still in the repo under
`assets/model-eval-token-chart-WRONG-DO-NOT-USE.png`, because it looks
completely convincing, and that is the point.

Full write-up: https://drakegriffith.github.io/drakes-website/blog/2026-08-03-model-eval-what-happens-when-every-model-passes.html
