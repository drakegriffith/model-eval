# Kimi K3 paper-grade run — HANDOFF (2026-07-17)

**Status:** 40-run K3 matrix RUNNING in background (bash id `btref3wa5`), `$12` kimi-only cost cap.
Goal: fold Kimi K3 into the existing Fable-vs-Sol gauntlet → AI-Engineering "build your own eval"
paper (Chip Huyen thesis). Draft at `~/brain-actual-intelligence/research/2026-07-17-kimi-k3-vs-frontier-private-eval.md`.

## RESUME (fresh context) — do these in order
1. **Check run finished:** `wc -l runner/results/results-kimi-paper.jsonl` (expect up to 40 rows);
   `tail -20 runner/results/kimi-paper.log` for final `kimi_spend=$X` and any `[cap]` skip lines.
   Also read the bg output file if still around: `.../tasks/btref3wa5.output`.
2. **If incomplete/died:** re-run the SAME cmd (resume-friendly, skips done run_ids):
   `cd ~/code/model-gauntlet && python3 runner/run.py --config runner/runs-kimi-paper.yaml --results runner/results/results-kimi-paper.jsonl --max-usd 12`
3. **Judge passing K3 runs** (Opus+GPT-5.6, subscription = $0):
   `python3 runner/judge.py --from-results runner/results/results-kimi-paper.jsonl`
   ⚠️ CHECK judge.py writes to a separate `--out judgments-kimi-paper.jsonl` (don't clobber the
   canonical judgments.jsonl). Verify it locates each diff in `.scratch/<run_id>` (scratch persists).
4. **Combined tables/stats — DO NOT mutate canonical files.** Concatenate into new files:
   `cat runner/results/results.jsonl runner/results/results-kimi-paper.jsonl > /tmp/results-all.jsonl`
   (same for judgments), then `python3 runner/tables.py --results /tmp/results-all.jsonl` and
   `python3 runner/stats.py --results /tmp/results-all.jsonl --judgments /tmp/judgments-all.jsonl`.
5. **Finalize paper:** slot real numbers into the draft, run `stop-slop`, then
   `python3 scripts/tag_index_mode.py --apply` in the vault root.

## What's built THIS session (all validated)
- `runner/run.py`: `kimi` branch = `claude -p --model kimi-k3` pointed at Moonshot's
  Anthropic-compatible endpoint. `load_kimi_key()` reads `~/brain-actual-intelligence/.secrets/kimi.env`
  (`MOONSHOT_API_KEY`) at runtime — NEVER echoed/committed. `run_cli` sets ANTHROPIC_BASE_URL +
  ANTHROPIC_API_KEY for kimi only. `parse_usage` treats kimi like `claude` (result JSON). `--max-usd`
  kimi-only hard cap in main loop.
- `runner/tables.py`: `PRICES["kimi"] = {in:3.0, out:15.0}` (cache-miss; cache-hit input is $0.30).
- New: `runner/smoke_kimi.py`, `runner/runs-kimi-pilot.yaml`, `runner/runs-kimi-paper.yaml`.

## Findings so far (verified)
- **Route:** Codex 0.144 dropped `wire_api="chat"`; Moonshot 404s on `/v1/responses` (confirmed) →
  had to drive K3 via Claude Code → Moonshot Anthropic endpoint. **Confound to disclose:** Fable + K3
  run in Claude Code, Sol runs in Codex — different agent scaffolds (real-world-representative, not
  pure model isolation). Fable-vs-K3 IS clean (same scaffold).
- **Overhead finding:** Claude Code injects ~29–30k tokens of system-prompt/tool overhead per K3 call
  (smoke 29,091 in for 1 word; pilot 29,728 in) → dominates short-task input cost. Claude Code caps
  K3 context at 200k, not the advertised 1M.
- **Pilot (1 run):** `t1-py-a` bare PASS, 29,728/942 tok, 70.4s, 7 turns, 7 LOC, $0.103. Correct +
  minimal fix, first try. (Row in `results-kimi.jsonl`; NOT part of the 40-run paper set.)
- Moonshot `/v1/models` confirms: id `kimi-k3`, context_length 1048576, valid_efforts `["max"]`,
  supports_thinking_type `"only"`.

## GUARDRAILS
- NEVER echo or commit `MOONSHOT_API_KEY`. **Rotate the key** — a partial value hit the transcript
  earlier this session.
- `runner/results/*.jsonl` are NOT gitignored — decide committal policy before any `git commit`.
- The paper's whole thesis is leaderboard-skepticism: do NOT present unverified web benchmark numbers
  as fact. Flag confidence on every external claim.
