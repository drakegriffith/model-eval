# GLM 4.7 substitute: open-weights models ranked near it that decode faster on the Mac Studio

Date: 2026-08-26. Funding sentence (Drake): "another model that at least ranked
publicly similarly will most likely have a similar skill set - find that model,
but it MUST run faster than GLM 4.7 locally."

Method: two Sonnet research seats (leaderboards; Apple Silicon throughput), one
Opus verify seat that re-derived every load-bearing number from primary sources
(vendor HF cards and config.json, HF API file trees, swebench.com, tbench.ai,
Artificial Analysis model pages, `gh api` on llama.cpp issues). Seat outputs
and the disagreement log are in this session's transcript; only verified values
appear below. Trailhead preflight: no prior art on candidate names in the vault,
model-eval, or claude-harness; `runner/registry.py:122` already registers
`qwen3-coder-next-local` (family local, no serving row), which this doc adopts.

## Fixed facts (this machine)

- Mac Studio, Apple M3 Ultra, 256 GB. LM Studio, llama.cpp GGUF engine,
  PARALLEL=1, context 131072.
- Incumbent GLM 4.7: 355B total / 32B active, GQA 96/8, 92 layers, MIT.
  unsloth UD-Q3_K_XL, 158.74 GB (= 147.84 GiB; the "148 GB on disk" and
  "158.74 GB loaded" figures were one number in two units). 3.58 bpw, so
  14.3 GB of expert weights per decoded token.
- Measured decode: 8.6 tok/s at stage-0 context (31988 tok / 61.9 min);
  median 9.9 tok/s over the 356 llama.cpp `eval time` lines in the
  2026-08-25/26 server logs (max 27.4; the verify seat's enumerator, which
  replaces an earlier "median 20 over 713 lines" figure whose filter also
  counted non-decode lines); prefill 58-147 tok/s over the 26 `prompt eval`
  lines with prompts over 2k tokens (registry row pins 57-71 from the
  2026-08-25 panel, a narrower enumerator). 8.6 tok/s is 15% of the 819 GB/s
  bandwidth ceiling, so most decode time is attention and MoE overhead, not
  weight streaming. Byte ratios below are upper bounds on speedup.

## What the verify seat refuted

1. Every SWE-bench Verified number in circulation for these models (GLM 4.7
   73.8, Qwen3.6-35B-A3B 73.4, Step-3.5-Flash 74.4, Qwen3-Coder-Next 70.6) is a
   vendor self-report under an unnamed or differing scaffold. None of the four
   is on swebench.com. They are not comparable to each other.
2. Terminal-Bench 2.0 reverses sign on the official board: vendor cards say
   Qwen3.6-35B-A3B 51.5 vs GLM 4.7 41.0; tbench.ai says GLM 4.7 33.4 (Terminus
   2, verified) vs Qwen3.6-35B-A3B 24.6 (little-coder, unverified). Different
   harnesses; no clean pair exists.
3. Both seats' "71 tok/s" and "74 tok/s on M3 Ultra" are not llama.cpp
   measurements: willitrunai.com computes estimates from spec sheets;
   siliconscore's 74 tok/s is MLX, unstated context, sourced from Reddit.
   The one primary-source benchmark (github.com/stared/benching-local-llms-on-apple-silicon)
   is an M5 Max 128 GB: Qwen3.6-35B-A3B Q8 on llama.cpp+MTP 105 tok/s at
   128 ctx, 97 at 8k.
4. llama.cpp issues cited as live risks are closed: #22581 and #15012 (Qwen
   tool-call parser) closed completed; #19081 (GLM-4.7-Flash slow) closed
   not_planned. #26965 (DeepSeek V4 Flash tokenizer on long tool output) is
   open, bug-unconfirmed.

## The one independent board holding all candidates

Artificial Analysis Intelligence Index (reasoning mode where available):
GLM 4.7 34 · Qwen3.6-35B-A3B 32 · MiniMax-M2.7 39 · Step-3.5-Flash 27 ·
Qwen3-Coder-Next 21 (non-thinking only). Composite includes HLE, GPQA,
CritPt; the per-eval breakdown is client-rendered and was not extracted.

## Candidate table (verified values)

| Model | Total/Active | Attention | License | GGUF (repo, quant, size) | Weight bytes/tok | Ratio vs GLM 4.7 | Release |
|---|---|---|---|---|---|---|---|
| GLM 4.7 | 355B/32B | GQA 96/8 | MIT | unsloth UD-Q3_K_XL 158.74 GB | 14.3 GB | 1.0x | 2025-12-22 |
| Qwen3-Coder-Next | 80B/3B | Gated DeltaNet 3:1 full attn, GQA 16/2 | Apache-2.0 | lmstudio-community Q4_K_M 48.49 GB; Q6_K 65.53 GB | 1.82 / 2.46 GB | 7.9x / 5.8x | 2026-01-30 |
| Qwen3.6-35B-A3B | 35B/3B (vision-language) | Gated DeltaNet 3:1, GQA 16/2, MTP | Apache-2.0 | unsloth UD-Q6_K_XL 31.84 GB; Q8_0 36.90 GB; lmstudio-community present | 2.73 / 3.16 GB | 5.2x / 4.5x | 2026-04-15 |
| MiniMax-M2.7 | 240B/~10B | not confirmed | other | lmstudio-community Q4_K_M 138.34 GB | 5.75 GB | 2.5x | 2026-04-09 |
| Step-3.5-Flash | 196B/11B | sliding window 512, 3:1 | Apache-2.0 | ggml-org Q4_K 118.71 GB; no lmstudio-community repo | 6.63 GB | 2.2x | 2026-02-01 |

Ratios are weight-byte ratios. The Gated DeltaNet models also shrink the
attention term (2 KV heads, 1 in 4 layers full attention) which is where GLM
loses 85% of its ceiling, so the realized speedup on the two Qwen models
should be at least the byte ratio; unmeasured on this machine when this
section was written (see "Measured 2026-08-27" below for Qwen3.6-35B-A3B).

## Tool-calling and llama.cpp state (checked 2026-08-26)

- Qwen3-Coder-Next: Qwen XML tool format, vendor deploy line uses
  `--tool-call-parser qwen3_coder`; card names Claude Code as a target
  harness. Issues #19382, #19430, #20164 (tool-call JSON, crashes,
  long-context tool failures) all closed. Non-thinking only.
- Qwen3.6-35B-A3B: same tool format. Open: #27767 (tool_choice not enforced,
  opened 2026-08-26), #26817 (temp-0 tool calling nondeterministic across
  prompt-cache mode and restarts, 2026-08-09), #26425 (MTP build retains
  inter-request state, nondeterministic output). Metal gated_delta_net cache
  fusion is an open PR (#25788), affecting both Qwen models.
- Step-3.5-Flash: three issues, all closed. Manual GGUF import required.
- MiniMax-M2.7: beats GLM 4.7 on both independent boards (AA 39, official
  TB2 45.1 vs 33.4) but only 2.5x, license `other`, 138 GB.

## Recommendation

Download two, probe both, let the existing stage-0 probe decide:

1. Qwen3-Coder-Next, lmstudio-community Q6_K (65.53 GB). Fastest class,
   purpose-built for coding agents with claude-code named on the card, seven
   months of closed llama.cpp tool-call fixes, and the runner already names
   `qwen3-coder-next-local`. Cost: AA index 21 vs 34, no Terminal-Bench entry,
   no thinking mode, so it may fail t4/t5 where GLM would not.
2. Qwen3.6-35B-A3B, unsloth UD-Q6_K_XL (31.84 GB), non-MTP build. Closest
   public rank to GLM 4.7 (AA 32). Cost: two open temp-0 tool-calling
   determinism bugs on exactly this stack's doctrine (deterministic loops,
   noise probe), and it carries unused vision weights.

Rejected: Step-3.5-Flash (2.2x is not materially faster; 119 GB plus KV is a
fit risk; manual import; its only edge is a vendor SWE-bench number).
MiniMax-M2.7 is the fallback if 5x turns out not to be realized on this
machine and rank matters more than speed.

Flips-if: the stage-0 probe on t3-a. GLM 4.7 passed 5/5 with 0 flips at
16-81 min per rep. A candidate that fails t3-a is out regardless of speed;
one that passes 5/5 in under 10 min per rep replaces GLM for stage 1.

## Measured 2026-08-27: Qwen3.6-35B-A3B stage 0 (PR #60, master d75d25d)

Drake lifted the LM Studio rule for this work ("You drive it on your end",
typed 2026-08-26; session record in the claude-harness handoff, 122b5f6,
which also records the two hung LM Studio downloads cited below).
Loaded unsloth UD-Q6_K_XL (31.84 GB GGUF plus the 1.79 GB mmproj-F32 that
LM Studio loads beside it, 33.63 GB total; the registry row's note
"mmproj not loaded" is wrong and is corrected in a follow-up) at parallel
1 / context 131072, GLM unloaded. Conductor-measured on the loaded model
(server log 2026-08-26.3.log, lines after the 22:03 load): prefill 1442 /
1563 / 1594 tok/s on three fresh 11.4k-token prompts (GLM 58-147 by the
same log enumerator); decode 59.8 tok/s on a 600-token short-context
sample (GLM median 9.9 by the same enumerator). Registry row hand-applied
(#51). Drake launched the probe from his shell after the permission
classifier refused the conductor the `claude -p` spawning command (session
record: claude-harness handoff 122b5f6).

| t3-a | GLM 4.7 (PR #55), effort high, bare | Qwen3.6-35B-A3B (PR #60), effort high, bare |
|---|---|---|
| pass | 5/5, 0 flips | 5/5, 0 flips |
| turns | 43 to 84 | 15 to 25 |
| wall per rep | 963 to 4853 s | 158 to 302 s |
| tokens out | 5738 to 38380 | 6090 to 14190 |
| acceptance requests, max | 1 | 2 |
| probe decisions (A1/A3/A6) | reps 2, K unchanged, N 260 | reps 2, K unchanged, N 80 |
| cloud models, same task (9 exit-ok rows: sol, fable, hybrid; effort low/medium, harness on and off) | 49.7 to 298.9 s | |

Verified by an Opus seat against the probe artifacts and the corpus (PR
#60 comments); the seat's hand-back also matched the prefill and decode
lines in the server log. Same verdict as GLM. Wall time per rep, paired
rep-for-rep (GLM rN / Qwen rN): 30.6x, 12.9x, 11.4x, 11.7x, 3.2x. Four of
five Qwen reps fall inside the cloud models' 49.7 to 298.9 s band; rep 5
at 301.65 s sits just above it. Depth of the comparison is t3-a only;
t4/t5 is stage 1. This measurement reverses the 26 Aug recommendation
order above (Coder-Next first): Qwen3.6 was probed first because its
download landed first, and Coder-Next's stage 0 follows.

Also checked on Drake's question: Qwen3.8-27B (AA 52) is dense, so at a
usable quant it reads more bytes per token than GLM 4.7 and is not faster;
Qwen3.8-Flash-Next (180B/6B, AA 56) has the right shape but its llama.cpp
architecture PR (#27742) is unmerged, so LM Studio cannot load it yet.

## Measured 2026-08-27: Qwen3-Coder-Next stage 0 (PR #62, master a3d56ad)

Q6_K shards fetched by parallel ranged curl after LM Studio's downloader
hung at 0% twice; both sha256 match the HF LFS metadata (re-hashed
independently by the PR #62 verify seat). LM Studio 0.4.21 refused to
index the files under models/lmstudio-community/ (its own hung download
of that repo left zombie records); renaming the publisher directory to
lmstudio-community-manual/ made it index within seconds. Loaded at
parallel 1 / context 131072 (llama.cpp engine 2.29.1), Qwen3.6 unloaded.
Conductor-measured (server log 2026-08-27.1.log after the 15:33 server
start; verify seat re-derived every line): prefill 875 / 1159 / 1163
tok/s on three fresh ~11.9k-token prompts, 875 being the cold first
request after load; decode 53.85 tok/s on a 600-token sample, all visible
content, zero reasoning tokens. Drake launched the probe from his shell
(same classifier refusal as the qwen probe).

Table enumerator: results.jsonl rows filtered on sweep (glm-stage0 /
qwen3.6-35b-a3b-stage0 / qwen3-coder-next-stage0), task t3-a, and
exit_reason ok. The GLM sweep has 10 such t3-a rows, of which 5 are ok
(the other 5 are cli_error aborts, pass=False, excluded from the pass
denominator per the registered estimand); both qwen sweeps are 5 of 5 ok.

| t3-a | GLM 4.7 (PR #55) | Qwen3.6-35B-A3B (PR #60) | Qwen3-Coder-Next |
|---|---|---|---|
| pass | 5/5, 0 flips | 5/5, 0 flips | 5/5, 0 flips |
| turns | 43 to 84 | 15 to 25 | 28 to 414 |
| wall per rep | 963 to 4853 s | 158 to 302 s | 243 to 1496 s |
| tokens out | 5738 to 38380 | 6090 to 14190 | 5534 to 33288 |
| acceptance requests, max | 1 | 2 | 2 |
| probe decisions (A1/A3/A6) | reps 2, K unchanged, N 260 | reps 2, K unchanged, N 80 | reps 2, K unchanged, N 1250 |

Rep 3 is the row to look at before trusting the averages: 414 turns,
1495.97 s, 33288 tokens out, against 28 to 50 turns for the other four
reps (wall_s max/min ratio 6.15, the probe's own line). One rep in five
took a 10x-turn detour and still passed; it alone sets the derived turn
cap N=1250 (3 x max(turns), the same rule that gave Qwen3.6 N=80). The
pass rate matches Qwen3.6; the variance does not. On the numbers
measured so far Qwen3.6-35B-A3B stays the recommended substitute: 37 to
65 percent faster prefill (1442-1594 vs 875-1163 tok/s), 11 percent
faster decode (59.8 vs 53.85; caveat: the two 600-token samples differ
in composition, qwen's was all reasoning tokens and coder-next's all
visible content), and turn counts four reps of five tighter, with no
detour rep in its probe. The fact that would
flip this: a Coder-Next stage 1 showing the detour behavior is confined
to this one rep while its per-task pass rate beats Qwen3.6's on t4/t5.

Speed ratio caveat (issue #63, filed from the PR #62 verify seat's
finding): the GLM 57-71 prefill band in the registry has no recorded
enumerator (prompt size, request count, log), so GLM-relative prefill
ratios compare a stated measurement against an unstated one. The
qwen-relative numbers in this section share one enumerator throughout.

## Next steps

1. Stage 1 for qwen3.6-35b-a3b (45 rows at 3 to 5 min each, a few hours;
   human-launched while the classifier refuses the conductor).
2. Reasoning-token probe for the qwen and coder-next rows (validate names
   them as the remaining gaps).
3. Re-measure the GLM prefill band with a recorded enumerator (issue #63;
   needs GLM loaded, 158 GB, human decision).
