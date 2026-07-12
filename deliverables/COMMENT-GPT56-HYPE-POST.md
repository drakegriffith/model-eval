# LinkedIn comment — reply to GPT-5.6 hype post (ChadGBT)

Drafted 2026-07-11, v2 in Drake's voice (peer-to-peer, not rebuttal). Source of truth: FINDINGS.md.
Voice memory: feedback_drake-public-comment-voice.md.

---

Good post, and funny timing. I spent last week running my own head-to-head on this: Fable 5 vs Sol on 9 real coding tasks, blind judges, 154 runs. The result was almost boring. Both went 152/152. On my task tier I could not tell them apart, which I guess means the real winner was my token bill.

Your multi-agent point is the one I'd poke at. The only setup in my whole experiment that flaked was the one model orchestrating another, it shipped working code 2 times out of 4. So I'm curious whether ultra mode's native coordination beats my duct-taped version. It might. Mine was held together with hope.

One thing I learned the hard way on the cost claims: the two vendor CLIs don't count tokens the same way. Same task, one logged ~19k input tokens, the other 95k+. Makes any "1/16th the cost" math slippery.

Caveats on my end, my tasks were mid-tier and I only tested Sol, so Terra and Luna are outside anything I can speak to.

[link to writeup at the bottom, once it's live]

---

## Notes for Drake
- v1 (rebuttal-style, "we" voice) rejected 2026-07-11 — read as product marketing. This v2: friendly opener, "I ran my own study", constraints owned, two jokes, link at bottom.
- BLOG-POST.md still unpublished — don't add the link until it ships.
- Skipped his "US government delayed the launch" claim: unverifiable, off-data.
- Outward-send rubric v2: voice 9 / factual 10 / slop 9 / strategic 9 = 37/40. Drake posts it himself.
