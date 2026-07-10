# YouTube Video — Script & Production Doc

**Title:** I Ran a Real Scientific Experiment on Claude vs GPT (154 Runs)

**Runtime target:** ~10 minutes · ~1,400 spoken words

**Thumbnail text options (pick one, ≤3 words):** "BOTH GOT 100%" · "TOO EASY?" · "154 RUNS. TIED."

**Tone:** confident, direct, zero hype. The brand is the guy who shows his math. Every stats term gets a plain-English translation in the same breath.

---

## COLD OPEN (0:00–0:35)

**[SHOW: fast montage — terminal running, JSONL lines scrolling, the two CLI logos side by side]**

I put the two most advanced AI models on the planet — Anthropic's Claude Fable and OpenAI's GPT Sol — through 154 controlled experiments. Real code. Real bugs. Real statistics.

Not a vibes video. An actual designed experiment — the kind I was trained to run as an industrial engineer.

**[SHOW: chart 1 flash — every dot at 100%]**

And the result surprised me more than any winner could: **they both got a perfect score.**

Stay with me — because that turns out to be the most interesting result I could have gotten.

**[TITLE CARD]**

## PART 1 — WHY EVERY AI COMPARISON YOU'VE SEEN IS BROKEN (0:35–1:45)

Here's how most model comparisons work: someone types one prompt into two chatbots, screenshots the answers, and crowns a winner.

There's a problem with that. These models are **random**. Ask the same model the same question twice, you get different code. Sometimes better, sometimes worse.

**[SHOW: two diffs from the same model, same task, different reps — visibly different code]**

So one run isn't a result. **One run is an anecdote. Repeated runs are a measurement.**

At Georgia Tech, in industrial engineering, we design experiments for factories — where a wrong conclusion costs millions. There's a whole discipline for this: design of experiments. Control everything, randomize what you can't control, repeat, and then do the math on what you saw.

Nobody was doing that for AI models. So I did.

## PART 2 — BUILDING A FAIR EXAM (1:45–3:15)

**[SHOW: repo tree — 9 task folders]**

Nine real coding tasks, three difficulty tiers:

- **Four planted bugs** — the sneaky kind: an off-by-one that drops the last page of results, a cache that serves one region's prices to another.
- **Four feature tickets** — written exactly like a real dev team writes them.
- **One build-from-scratch project** — a working expense-splitting tool from an empty folder.

And before either model saw any task, four fairness rules:

**Rule one: prove the exam is solvable.** For every task, a script proves the test fails before the correct fix and passes after it. **[SHOW: selftest output — 9 green PASS lines]** If a model fails, it can't blame the exam.

**Rule two: identical conditions.** Same prompts, byte-for-byte identical instruction files, same automated pass/fail gate. **[SHOW: diff of the two harness files returning empty]**

**Rule three: repetition and blocking.** Every configuration ran every task three times, in shuffled order. "Blocking" is stats-speak for: both models face the exact same tasks, so task difficulty cancels out of the comparison.

**Rule four: blind judging.** Two AI judges — one from each company — graded every solution without knowing who wrote it. Why both? Because in my pilot, each judge scored slightly differently. If your referee has a lean, you want a second referee and full disclosure.

## PART 3 — RESULT #1: THE PERFECT SCORES (3:15–5:00)

**[SHOW: chart 1 — pass-rate ceiling with confidence intervals, hold on screen]**

One hundred and twenty runs in the first sweep. Every single one passed. Both models. Every effort setting. 24 out of 24 per configuration.

Now here's where I'm going to be more honest than the average benchmark video. 24 out of 24 does **not** mean these models are 100% reliable. The math says: with 24 tries, a perfect score is consistent with a true success rate as low as 86%. That bar you see behind each dot? That's the confidence interval — the honest version of the number.

That's a rule for this whole channel: **if the gap is smaller than what the experiment can detect, you get a confidence interval, not a verdict.** I ran a formal head-to-head test — McNemar's test, built for paired comparisons like this — and its answer was: no detectable difference. So that's what I'm telling you. No fake winner.

But zoom out, because the perfect score IS the story: on everyday coding work — the bugs and tickets that fill a developer's actual week — **the frontier models from both labs are past this difficulty tier.** I watched benchmark saturation happen live, on my own hardware.

## PART 4 — RESULT #2: THE "THINK HARDER" DIAL DID NOTHING (5:00–6:30)

Both companies sell a dial: reasoning effort. Turn it up, the model "thinks harder," and you pay more.

**[SHOW: chart 2 — effort vs output tokens, every bar labeled 100% pass]**

On these tasks, that dial changed exactly one thing: **the bill.** GPT Sol on high effort burned 80% more output tokens than on low — for the same perfect score. Claude Fable, same pattern.

If you're running AI coding at scale, that's real money: **for routine work, run the cheap setting.** You are very likely paying for thinking your tasks don't need.

**[SHOW: chart 3 — input tokens, caveat visible]**

And when I pulled the raw bills, something weirder: the two vendors don't even **count** the same way. Claude's tooling reported about 19 thousand input tokens per task. OpenAI's reported up to 175 thousand — because it counts every re-send of context. So when someone shows you a cross-vendor dollar-per-task chart with no footnote — now you know to ask what got counted.

One more personality note. **[SHOW: split screen — Fable's 7 small commits vs Sol's single one-shot diff]** Claude works like a careful developer: six, seven small steps, checking as it goes. GPT one-shots the whole thing in a single pass. Completely different animals. Same destination.

## PART 5 — RESULT #3: THE TWIST (6:30–8:15)

For the build-from-scratch project I added two wrinkles.

First, a **harness** — structured instructions, the stuff serious teams wrap around AI. Result: no change here — both models aced the build with and without it. (Harnesses earn their keep on longer, messier work than a one-session build — that test is coming.)

Then the twist: **hybrid mode.** Claude as the manager, GPT as the employee. Claude plans and delegates, GPT writes the code.

**[SHOW: hybrid run terminal — the NOTES.md-only failure, then the passing run's diff]**

Four attempts. Twice, the "manager" produced beautiful planning notes… and **zero lines of code.** Twice, it shipped working software. Fifty-fifty.

Every solo configuration: rock solid. The moment I stacked AI on AI, reliability fell off a cliff — and the failure mode was painfully human: a manager who plans instead of shipping.

**That's the actual frontier.** Not "which model is smarter" — but whether you can chain them and still trust the pipeline.

## PART 6 — WHAT YOU SHOULD TAKE FROM THIS (8:15–9:30)

Three things:

**One.** Stop paying for maximum effort by default. On routine tasks it bought nothing, at up to 80% markup.

**Two.** Stop trusting single-run comparisons. Randomness plus a screenshot is content, not evidence.

**Three.** At this difficulty tier, the top two models are **tied** — so the real comparison is cost per solved task and consistency, not IQ points.

And the biggest one: my exam was too easy — and I can *prove* it was a fair exam, which is exactly what makes "too easy" a real finding instead of an excuse.

So here's experiment two: **I make the tasks harder until these models start failing.** That's where the effort dial gets its real test, and where a winner might actually emerge. Subscribe if you want the data when it lands.

## OUTRO (9:30–10:00)

Full technical report — every test, every confidence interval, every raw number — is linked below on our research page. If your business wants this level of rigor applied to its AI decisions instead of vibes — that's literally what we do at Actual Intelligence.

See you in experiment two.

---

## VIDEO DESCRIPTION (paste under video)

I ran 154 controlled runs pitting Claude (Fable 5) against OpenAI's GPT (5.6 "Sol") on 9 real coding tasks — planted bugs, feature tickets, and a from-scratch build — using actual design-of-experiments methodology: proven-solvable tasks, identical conditions, repeated randomized runs, blind dual-model judging, and exact statistical tests (Wilson intervals, McNemar).

Result: a perfect 100% tie — and that's the finding. Benchmark saturation, a "think harder" dial that only raises the bill, vendors that can't agree on how to count a token, and one genuinely unreliable configuration: AI managing AI.

📄 Full technical report + statistical appendix: [LINK]
🧵 Chapters:
0:00 Both models got a perfect score
0:35 Why one-prompt comparisons are broken
1:45 Building a provably fair exam
3:15 Result 1: the ceiling
5:00 Result 2: the effort dial did nothing
6:30 Result 3: AI managing AI is a coin flip
8:15 What to actually do with this
9:30 Experiment 2

## PRODUCTION NOTES

- Charts to shoot: `assets/1-pass-rate-ceiling.png`, `assets/2-effort-buys-nothing.png`, `assets/3-input-token-economics.png` (1600×900, light surface).
- B-roll to capture from repo: 9-line green selftest run · empty `diff harness/AGENTS.md harness/CLAUDE.md` · a resumed run skipping completed rows · hybrid NOTES.md failure vs passing diff · STATS-APPENDIX.md scroll.
- On-camera stats phrasing is already written into the beats — say the plain-English line, let the lower-third carry the term ("McNemar exact test, paired" / "95% Wilson interval, n=24").
- Do NOT quote dollar figures — token counts and percentages only (accounting caveat).
- Hybrid claim discipline: say "four attempts, fifty-fifty" — never "hybrid fails half the time" (n=4).
