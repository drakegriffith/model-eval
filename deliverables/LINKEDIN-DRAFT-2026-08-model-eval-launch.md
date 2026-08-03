# LinkedIn draft: model-eval public release (2026-08-03)

Status: DRAFT, not posted. Drake posts manually. No LinkedIn posting tool used.

Rewritten 2026-08-03 as a short teaser pointing at the blog post, replacing
the earlier full-analysis draft (which carried the retracted "~1/5 the
tokens, p=0.004" claim; see git history for that version and
`deliverables/BLOG-POST-2026-08-model-eval-launch.md` for why it was wrong).

Every number below comes from `deliverables/STATS-CURRENT-2026-08-03.md`, the
verbatim output of `python3 runner/stats.py` over the live corpus:
- `runner/results/results.jsonl`: 268 rows, 267 scorable, 8 models/modes
- `runner/results/judgments.jsonl`: 154 dual-judged rows
- `runner/results/transcripts/`: 241 files
- Effort-tier claim: §3 (15 adjacent-rung McNemar comparisons, 144 matched
  pairs, zero discordant). Token ladder: median output tokens per solved run,
  bare invocation.

Blog post published 2026-08-03; the live URL is filled in below. Ready to
paste. Drake posts it manually.

---

I open-sourced the benchmark harness I've been using to compare frontier
coding models: github.com/drakegriffith/model-eval

It runs models headlessly against small, verifiable coding tasks (real bugs
in real repos, with a script that decides pass or fail, not a vibe check),
grades the diffs with two independent LLM judges, and commits the transcripts
and judgments next to the code that produced them. Every number in it can be
checked against the receipts.

Where it stands: 268 runs across 8 models, 241 archived transcripts, 154 runs
scored by both judges.

The result I did not expect: the effort knob never mattered. Across 15
comparisons of adjacent effort tiers, 144 runs matched task for task, turning
effort up never once changed a pass/fail outcome. It only changed the bill.
Sol's top tier costs about 4.4x its cheapest tier for zero additional passes.
The honest caveat is that on tasks this size nearly everything passes at
every tier, which is its own finding, and the reason the next batch of tasks
is going to be harder.

The write-up also covers the headline finding I had to retract the same day I
drafted it, and how the repo's own tooling caught a mistake I made by hand.

Full detail: https://drakegriffith.github.io/drakes-website/blog/2026-08-03-model-eval-what-happens-when-every-model-passes.html
