# CLI-FACTS.md — re-probed 2026-07-25

Supersedes the 2026-07-10 edition, which had gone stale in two load-bearing ways (see
**Corrections** below). Generated from real invocations, not from docs or `--help` text:
every row here was produced by `runner/probe_endpoints.py` and the raw evidence is in
`runner/results/endpoint-probe-2026-07-25.jsonl` and `runner/results/ladder-*.jsonl`.

Both first-party CLIs run on Drake's **subscriptions**; `run.py`, `judge.py`, and
`probe_endpoints.py` never set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` for them. Kimi is
the sole metered path and is the only one given an injected key.

Detected versions at probe time:
- claude: `2.1.220 (Claude Code)`  (was 2.1.206)
- codex : `codex-cli 0.144.0`

Re-run after any CLI upgrade: `python3 runner/probe_endpoints.py --phase floor` then
`--phase ladder`, then `python3 runner/effort_verdict.py`. Both are resumable, so a
re-probe only pays for cells that are actually missing.

## Corrections to the 2026-07-10 edition

1. **Codex effort is not `low|medium|high`.** `~/.codex/models_cache.json` declares up to
   six tiers — `low, medium, high, xhigh, max, ultra` — for `gpt-5.6-sol` and
   `gpt-5.6-terra`. Drake's own `config.toml` already runs `model_reasoning_effort =
   "xhigh"`, a tier the old file said did not exist. All six were probed and accepted.
2. **Codex is a roster, not one model.** Eight ids are reachable, not just `gpt-5.6-sol`.
3. **Kimi K3 does expose an effort knob** through the Claude Code path. `run.py` currently
   hardcodes no `--effort` for Kimi and labels every Kimi run `"max"` — that label is
   wrong, and the probe shows spend responding to the flag. See the standing caveat below
   before acting on this.

## Reachability and scaffold floor

One-word prompt (`reply with the single word ok`), lowest effort, cwd = empty scratch dir.
`tokens_in` is the **scaffold overhead floor** — what a run costs before the task is even
read. An invalid model id 404s with `usage.input_tokens == 0`, so id-existence checking is
free; that is how `kimi-k2.7` was ruled out without spending anything.

| Family | Model id | Reachable | Scaffold floor (tokens in) |
|---|---|---|---|
| claude | `claude-opus-5` | yes | 28,496 |
| claude | `claude-opus-5[1m]` | yes | 28,509 |
| claude | `claude-opus-4-8` | yes | 27,506 |
| claude | `claude-fable-5` | yes | 28,822 |
| claude | `claude-sonnet-5` | yes | 36,496 |
| claude | `claude-haiku-4-5` | yes | 26,619 |
| claude | `claude-haiku-4-5-20251001` | yes | 26,623 |
| codex | `gpt-5.6-sol` | yes | 20,860 |
| codex | `gpt-5.6-terra` | yes | 17,847 |
| codex | `gpt-5.6-luna` | yes | 16,656 |
| codex | `gpt-5.5` | yes | 18,136 |
| codex | `gpt-5.4` | yes | 16,751 |
| codex | `gpt-5.4-mini` | yes | 16,399 |
| codex | `gpt-5.3-codex-spark` | yes | 15,399 |
| codex | `codex-auto-review` | yes | 16,216 |
| kimi | `kimi-k3` | yes | 32,795 |
| kimi | `kimi-k2.7` | **NO — 404** | n/a |

**Scaffold asymmetry, quantified.** Codex floors run 15.4k–20.9k; Claude 26.6k–36.5k;
Kimi highest at 32.8k. So a Codex run starts roughly 10k tokens cheaper than a Claude run
on identical work, before any task tokens. This is the agent-scaffold confound the map
rules out of scope to *solve* — these are the numbers to *disclose* it with.

Two flags worth noting: `gpt-5.3-codex-spark` reports `supported_in_api: false` and
`codex-auto-review` reports `visibility: hide` in the model cache, yet both are reachable
via `codex exec`. The cache's own metadata does not predict CLI reachability, so neither
should be assumed stable — they are reachable today, not supported.

## Exact invocations (verified, recorded verbatim in the JSONL)

Claude family — cwd = scratch copy:
```
claude -p "<prompt>" --output-format json \
  --model <id> --effort <low|medium|high|xhigh|max> --dangerously-skip-permissions
```

Codex family — cwd = scratch copy, stdin = /dev/null:
```
codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  -m <id> -c model_reasoning_effort=<low|medium|high|xhigh|max|ultra> "<prompt>"
```

Kimi K3 — driven through Claude Code against Moonshot's Anthropic-compatible endpoint
(Codex 0.144 speaks only the Responses API, which Moonshot does not serve):
```
ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic \
ANTHROPIC_API_KEY=<MOONSHOT_API_KEY> ANTHROPIC_AUTH_TOKEN=<same> \
claude -p "<prompt>" --output-format json --model kimi-k3 --dangerously-skip-permissions
```
Key from `~/brain-actual-intelligence/.secrets/kimi.env` (600). Injected into the
subprocess env only — never in argv, never logged.

## Effort tiers

Declared tiers per model (source: `~/.codex/models_cache.json` for Codex; `claude --help`
for Claude). **Declared ≠ real** — see the next section.

| Model | Declared tiers |
|---|---|
| all `claude-*` | low, medium, high, xhigh, max |
| `gpt-5.6-sol`, `gpt-5.6-terra` | low, medium, high, xhigh, max, **ultra** |
| `gpt-5.6-luna` | low, medium, high, xhigh, max |
| `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex-spark`, `codex-auto-review` | low, medium, high, xhigh |
| `kimi-k3` (via Claude Code) | accepts the Claude set; see caveat |

Every declared tier above was **accepted** by its CLI. Acceptance is the weak claim and is
not what the study needs.

## Whether the effort knob actually does anything — MEASURED, n≥3

Settled at n=3–4 per tier over 256 ladder runs. Run `python3 runner/effort_verdict.py`
to regenerate; machine-readable copy in `runner/results/effort-verdict.json`.

A ladder is **credited** only when spread ≥ 1.5×, the ladder trends upward, and
between-tier CV ≥ 2× within-tier CV. That last clause is the one that matters: the first
n=1 pass called 12 of 16 models REAL on spread alone, and replication cut that to 10 while
flipping several verdicts outright.

**Credited — treat each tier as a distinct budget point (10):**
`claude-opus-5`, `claude-opus-5[1m]`, `claude-opus-4-8`, `claude-fable-5`,
`claude-sonnet-5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-terra`,
`codex-auto-review`.

**NOT credited — collapse to a single point until re-measured (6):**

| Model | Spread | btw CV | win CV | Reading |
|---|---|---|---|---|
| `gpt-5.6-sol` | 1.34 | 0.11 | 0.12 | six declared tiers (incl. `ultra`), spend 88→80→78→73→98→98; signal below noise |
| `gpt-5.6-luna` | 1.38 | 0.11 | 0.12 | same pattern as sol |
| `kimi-k3` | 6.58 | 0.64 | 0.33 | ratio 1.94, just under the 2.0 margin — suggestive, unproven |
| `gpt-5.3-codex-spark` | 3.72 | 0.51 | 0.36 | non-monotone, peaks at `high` then falls |
| `claude-haiku-4-5` | 2.15 | 0.29 | 0.36 | thrashing, see below |
| `claude-haiku-4-5-20251001` | 1.90 | 0.23 | 0.32 | thrashing, see below |

**The flagship is the headline.** `gpt-5.6-sol` is the current Codex default and advertises
the widest tier range of any model here, yet its spend is flat across all six. `gpt-5.6-luna`
matches it, while the older `gpt-5.4`/`gpt-5.5` and the sibling `gpt-5.6-terra` all respond
normally. Plausible reading — the 5.6 generation self-manages reasoning depth and treats the
flag as advisory — but this probe cannot confirm the mechanism, only the flat spend.

Known instrument limitation: the ladder prompt is a single fixed reasoning puzzle, and its
difficulty is not calibrated per model. Strong models answer it cheaply (`claude-sonnet-5`
spent 9 output tokens at `low`), while `claude-haiku-4-5*` thrashes on it — 8k–21k output
tokens, non-monotone across tiers. For Haiku the ladder measures failure to converge, not
reasoning budget. Any effort ladder the study relies on should be re-measured **on real
gauntlet tasks**, not on this probe prompt.

**Implied frontier points: 53** (credited models contribute one point per tier; everything
else collapses to one). This is the number the ceiling resize must budget against — not the
73 that counting declared tiers would have produced.

Known instrument limitation: the ladder prompt is a single fixed reasoning puzzle, and its
difficulty is not calibrated per model. Strong models answer it cheaply (`claude-sonnet-5`
spent 16 output tokens at `low`), while `claude-haiku-4-5*` thrashes on it — 6k–28k output
tokens, wildly non-monotone across tiers. For Haiku the ladder is measuring failure to
converge, not reasoning budget. Any effort ladder that the study actually relies on should
be re-measured **on real gauntlet tasks**, not on this probe prompt.

## Context windows — DECLARED ONLY, not observed

Codex declares 272,000 for every id except `gpt-5.3-codex-spark` (128,000). Claude context
limits were **not** measured: `claude -p --output-format json` does not report a limit, and
observing one means driving a near-limit prompt, which is expensive and was not authorised
in this probe. The one hard datapoint is prior work: Claude Code capped K3 at **200k**, not
Moonshot's advertised 1M.

Recorded as a known hole rather than filled with the advertised numbers.

## Flags NOT to use / gotchas

- Never pass `--bare` to claude — it forces `ANTHROPIC_API_KEY` auth and breaks
  subscription auth.
- `codex exec` rejects `--ask-for-approval`; use `--dangerously-bypass-approvals-and-sandbox`.
- codex prints "Reading additional input from stdin..." if stdin is a pipe; set
  `stdin=/dev/null`.
- `codex models` requires a TTY and fails under automation — read
  `~/.codex/models_cache.json` instead.
- macOS `pgrep` has no `-c`; use `pgrep -f pattern | wc -l` in probe tooling.
- Both CLIs write into the current working directory; always launch with cwd set to the
  per-run scratch copy so edits land there and `git diff` measures them.
- The global `~/.claude/CLAUDE.md` loads for every `claude` run and is a large share of the
  ~27k scaffold floor. It is constant across cells, so it is fair — but it is not free, and
  it does not apply to `codex`, which is part of why the two floors differ.
