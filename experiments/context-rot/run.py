#!/usr/bin/env python3
"""Context-rot micro-experiment (v2).

Question: holding the task fixed, does a model's retrieval accuracy and
instruction adherence fall as the amount of irrelevant context in front of the
task grows?

v1 (commit 8d7edb2) returned a null result but could not support it. It planted
three facts and scored one composite integer, so a single missed fact and a
single arithmetic slip were the same observation, and each run yielded exactly
one bit. The control condition scored worst (7/10 at 2k tokens), which is the
tell: at 2k tokens there is nothing to get lost in, so those three failures were
measuring something other than context length.

v2 changes three things:

  1. Every planted value is scored separately. Nine facts per instance, each
     retrieved on its own line, so one run yields nine retrieval observations
     plus one arithmetic observation instead of one conflated bit. Arithmetic is
     scored twice: against the true values (`arith_ok`) and against the values
     the model itself reported (`arith_consistent`). A model that retrieves
     badly but computes correctly is now distinguishable from one that does the
     reverse.

  2. Near-miss distractors. Each planted key gets two decoys with confusable
     names (`legacy_retry_budget`, `retry_budget_default`) and adjacent values,
     scattered at unrelated depths. Without them a wrong answer is unattributable
     and a fuzzy match scores as a hit; with them, `distractor_hits` says whether
     a miss was a decoy grab or a fabrication.

  3. Position is recorded, not assumed. Each fact's true character offset in the
     finished prompt is measured after assembly, so accuracy can be cut by depth
     (the Lost-in-the-Middle effect) independently of total length. Listing order
     in the question is shuffled against depth order, so sequential reading earns
     nothing.

Also fixed: `usage` comes back empty on long prompts (every 200k and 400k run in
v1 logged 0 input tokens), so token accounting now falls back to `modelUsage`.
Without it there is no evidence the long conditions were ever actually long, and
a silently truncated prompt is indistinguishable from a model that handled it.

Design (paired, within-instance):
  - One instance = nine planted config facts with random values, eighteen decoys,
    and a question requiring all nine plus one derived total.
  - The same instance is asked at every padding length, so the only thing that
    changes between conditions is how much unrelated text sits between the facts
    and the question.
  - Padding is the Python standard library's own source, concatenated in sorted
    order as a fake session log. It is inert: unlike this repo's task files, no
    line of it addresses a model, so nothing in the padding competes with the
    prompt for authority. Deterministic given a Python version.
  - All scores are deterministic string and integer comparisons.

Every invocation is subscription-authenticated through the local `claude` CLI
with API keys stripped from the child environment, tools disabled, MCP sealed
empty, and cwd set to an empty temp dir so no CLAUDE.md is discovered. The
`cost_usd` field is therefore a list-price equivalent, not money billed.

Usage:
  python3 experiments/context-rot/run.py --instances 20 --model sonnet
  python3 experiments/context-rot/run.py --summarize results/context-rot-v2-sonnet.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import sysconfig
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "results"
# The control is 5k, not v1's 2k: nine facts plus eighteen decoys plus the
# instructions are ~1,900 tokens on their own, so a 2k condition would be all
# planted material and no padding, which is a different task, not a shorter one.
DEFAULT_LENGTHS = [5_000, 25_000, 50_000, 100_000, 200_000, 400_000, 700_000]
CHARS_PER_TOKEN = 2.7  # calibrated against measured usage on Python source

# name, file it is planted in, (lo, hi), multiplier
FACTS = [
    ("retry_budget",      "limits.yaml",    (3, 9),     1),
    ("shard_count",       "topology.yaml",  (2, 9),     1),
    ("timeout_ms",        "gateway.yaml",   (2, 18),    50),
    ("batch_size",        "ingest.yaml",    (4, 64),    1),
    ("queue_depth",       "buffers.yaml",   (16, 512),  1),
    ("worker_slots",      "pool.yaml",      (2, 48),    1),
    ("flush_interval_ms", "flush.yaml",     (5, 60),    10),
    ("max_payload_kb",    "ingress.yaml",   (32, 1024), 1),
    ("drain_grace_s",     "shutdown.yaml",  (5, 120),   1),
]
DECOY_FORMS = ["legacy_{k}", "{k}_default", "{k}_max", "prev_{k}", "{k}_fallback"]
DECOYS_PER_FACT = 2
# TOTAL keeps v1's formula so the arithmetic half stays comparable across versions.
TOTAL_KEYS = ("retry_budget", "shard_count", "timeout_ms")

CONTRACT = """\
OUTPUT CONTRACT (governs your entire reply, no exceptions):
- Reply with exactly {nlines} lines.
- The first {nfacts} lines report the requested values, one per line, in the
  order the question lists them, formatted as: <key_name>: <integer>
  Spell each key name exactly as the question spells it, lowercase. For example:
  retry_budget: 4
- The final line is formatted exactly: TOTAL: <integer>
- No prose, no explanation, no restating the question, no code fences, no blank
  lines.

TASK BRIEF
Below is a session log: a long series of files that were read during an earlier
working session. A number of deploy settings are defined somewhere inside it. At
the end of the log you will be asked to report some of them. Read the log.

Key names are exact. The log also contains settings whose names merely resemble
the ones you will be asked for (for example `legacy_retry_budget` or
`retry_budget_default`). Those are different settings. Report only the value
defined for the exact key name requested.

=== BEGIN SESSION LOG ===
"""

QUESTION = """\
=== END SESSION LOG ===

Using only the settings defined in the session log above, report the value of
each of these keys, in this order, one per line:

{keylist}

Then, on the final line, report:

    TOTAL = retry_budget * shard_count + timeout_ms

Follow the output contract stated at the top of this message.
"""

FACT_BLOCK = """\

=== FILE: deploy/{fname} ===
# generated by ops-sync, do not edit by hand
service: ingest-gateway
{key}: {value}
region: us-east-1

"""


def corpus() -> str:
    """Deterministic padding: the Python stdlib's sources, sorted, concatenated.

    Deliberately inert. The first cut of this experiment padded with this repo's
    own files, and one reply came back answering a question nobody asked: the
    padding was full of task prompts written at a model, and the model obeyed
    one. Padding has to be text that does not instruct.
    """
    root = Path(sysconfig.get_paths()["stdlib"])
    keys = [k for k, _, _, _ in FACTS]
    parts = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "test" in rel or "__pycache__" in rel or "site-packages" in rel:
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(k in body for k in keys):
            continue  # keep the planted keys unique in the prompt
        parts.append(f"\n=== FILE: stdlib/{rel} ===\n{body}\n")
    text = "".join(parts)
    if not text:
        sys.exit("empty padding corpus")
    return text


def instance(seed: int) -> dict:
    """One instance: true values, decoys, planting depths, and question order.

    Fixed by seed alone, so the same instance is reproducible and is asked
    identically at every padding length.
    """
    rng = random.Random(seed)
    vals, blocks = {}, []

    n = len(FACTS)
    # True facts spread evenly across the log so depth is a controlled variable.
    depths = [0.05 + 0.90 * i / (n - 1) for i in range(n)]
    for (key, fname, (lo, hi), mult), depth in zip(FACTS, depths):
        vals[key] = rng.randrange(lo, hi + 1) * mult
        blocks.append({"depth": depth, "key": key, "fname": fname,
                       "value": vals[key], "decoy_for": None})

    # Decoys: confusable name, adjacent value, unrelated depth.
    decoy_values: dict[str, list[int]] = {}
    for key, fname, (lo, hi), mult in FACTS:
        for form in rng.sample(DECOY_FORMS, DECOYS_PER_FACT):
            offset = rng.choice([-3, -2, -1, 1, 2, 3]) * mult
            dv = max(mult, vals[key] + offset)
            if dv == vals[key]:
                dv = vals[key] + mult
            decoy_values.setdefault(key, []).append(dv)
            blocks.append({"depth": rng.uniform(0.02, 0.98),
                           "key": form.format(k=key), "fname": fname,
                           "value": dv, "decoy_for": key})

    order = [k for k, _, _, _ in FACTS]
    rng.shuffle(order)  # question order != depth order

    total = vals[TOTAL_KEYS[0]] * vals[TOTAL_KEYS[1]] + vals[TOTAL_KEYS[2]]
    return {"seed": seed, "values": vals, "decoy_values": decoy_values,
            "blocks": blocks, "order": order, "total": total}


def build_prompt(pad: str, inst: dict, target_tokens: int) -> tuple[str, dict]:
    """Assemble the prompt and measure where each fact actually landed.

    Two things here are easy to get wrong and were wrong in the first cut:

    - Cut points are computed against the *pristine* padding, before anything is
      inserted. Computing them as you go makes each inserted block a new
      `=== FILE:` boundary that the next block's search snaps onto, so blocks
      chain-stack: nine facts meant to sit at 0.05 through 0.95 piled into a
      0.27-0.32 band and depth stopped being a controlled variable.
    - The planted blocks count against the length budget. Otherwise the 2k
      control is really a 4k prompt that is 60% planted material, and the
      shortest condition is not the same task as the longest.
    """
    nfacts = len(FACTS)
    head = CONTRACT.format(nlines=nfacts + 1, nfacts=nfacts)
    tail = QUESTION.format(keylist="\n".join(f"    {k}" for k in inst["order"]))
    texts = [(b, FACT_BLOCK.format(**b)) for b in inst["blocks"]]
    overhead = len(head) + len(tail) + sum(len(t) for _, t in texts)

    want = max(500, int(target_tokens * CHARS_PER_TOKEN) - overhead)
    body = pad[:want] if want <= len(pad) else pad * (want // len(pad) + 1)
    body = body[:want]

    # Resolve every cut against the untouched body, then insert deepest-first so
    # the shallower offsets stay valid as the body grows underneath them.
    cuts, used = [], set()
    window = max(200, len(body) // 50)  # a boundary is only worth snapping to if it is near
    for b, text in texts:
        raw = max(1, min(len(body) - 1, int(len(body) * b["depth"])))
        # Snap to a file boundary, but only a nearby one: some stdlib files run
        # tens of thousands of characters, and an unbounded backward search drags
        # a fact several percent off its intended depth.
        cut = body.rfind("\n=== FILE:", max(0, raw - window), raw)
        if cut <= 0 or cut in used:
            cut = body.rfind("\n", max(0, raw - window), raw)  # else keep lines intact
        if cut <= 0 or cut in used:
            cut = raw
        used.add(cut)
        cuts.append((cut, text))
    for cut, text in sorted(cuts, key=lambda ct: -ct[0]):
        body = body[:cut] + text + body[cut:]

    prompt = head + body + tail

    # Measured, not assumed: the fraction of the finished prompt each fact sits at.
    positions = {}
    for key in inst["values"]:
        idx = prompt.find(f"\n{key}: {inst['values'][key]}\n")
        positions[key] = round(idx / len(prompt), 4) if idx >= 0 else None
    return prompt, positions


def invoke(prompt: str, model: str, timeout: int) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    with tempfile.TemporaryDirectory() as cwd:
        started = time.time()
        try:
            proc = subprocess.run(
                [
                    "claude", "-p",
                    "--model", model,
                    "--output-format", "json",
                    "--allowed-tools", "",
                    "--mcp-config", '{"mcpServers":{}}',
                    "--strict-mcp-config",
                ],
                input=prompt, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"timeout after {timeout}s", "wall_s": round(time.time() - started, 1)}
        wall = time.time() - started
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout)[-400:], "wall_s": round(wall, 1)}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "unparseable CLI output", "wall_s": round(wall, 1)}

    u = payload.get("usage") or {}
    inp = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
           + u.get("cache_read_input_tokens", 0))
    out = u.get("output_tokens", 0)
    source = "usage" if inp else None
    if not inp:
        # `usage` comes back empty on long prompts. Every 200k and 400k run in v1
        # logged 0 input tokens for this reason, which left no evidence the long
        # conditions were ever long. modelUsage still carries the counts.
        for mu in (payload.get("modelUsage") or {}).values():
            inp += (mu.get("inputTokens", 0) + mu.get("cacheCreationInputTokens", 0)
                    + mu.get("cacheReadInputTokens", 0))
            out = out or mu.get("outputTokens", 0)
        source = "modelUsage" if inp else "none"
    return {
        "reply": payload.get("result", ""),
        "wall_s": round(wall, 1),
        "cost_usd": payload.get("total_cost_usd"),
        "input_tokens": inp,
        "output_tokens": out,
        "usage_source": source,
        "stop_reason": payload.get("stop_reason"),
        "terminal_reason": payload.get("terminal_reason"),
        "api_error": payload.get("api_error_status"),
    }


def score(reply: str, inst: dict) -> dict:
    """Deterministic scoring. Every planted value is judged on its own."""
    text = (reply or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Case-insensitive: the first cut of the contract wrote the template as
    # `KEY: <integer>`, the model read that as a style instruction and upcased
    # every key, and a case-sensitive match scored nine perfect retrievals as
    # nine misses. Casing is not the thing under test.
    claimed: dict[str, int] = {}
    for line in lines:
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?\d+)", line)
        if m:
            claimed[m.group(1).lower()] = int(m.group(2))

    per_key, hits = {}, {}
    for key, truth in inst["values"].items():
        got = claimed.get(key)
        per_key[key] = got == truth
        if got is not None and got != truth:
            hits[key] = "decoy" if got in inst["decoy_values"].get(key, []) else "other"
        elif got is None:
            hits[key] = "missing"

    n_keys = len(inst["values"])
    n_correct = sum(per_key.values())

    total_claim = claimed.get("total")
    arith_ok = total_claim == inst["total"]
    # Did the model compute correctly *from the numbers it reported*? Separates
    # an arithmetic slip from a retrieval miss.
    a, b, c = (claimed.get(k) for k in TOTAL_KEYS)
    arith_consistent = (None not in (a, b, c, total_claim)) and total_claim == a * b + c

    fmt = (len(lines) == n_keys + 1
           and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*:\s*-?\d+", l) for l in lines)
           and lines[-1].lower().startswith("total"))

    return {"per_key": per_key, "miss_kind": hits,
            "n_keys": n_keys, "n_keys_correct": n_correct,
            "all_keys_correct": n_correct == n_keys,
            "arith_ok": arith_ok, "arith_consistent": arith_consistent,
            "format_ok": fmt, "claimed": claimed}


def one_run(pad, inst, tokens, model, timeout, retries=1):
    prompt, positions = build_prompt(pad, inst, tokens)
    for attempt in range(retries + 1):
        res = invoke(prompt, model, timeout)
        if "error" not in res and (res.get("reply") or "").strip():
            break
        if attempt < retries:
            time.sleep(5)
    row = {"seed": inst["seed"], "target_tokens": tokens, "model": model,
           "attempts": attempt + 1, "prompt_chars": len(prompt),
           "values": inst["values"], "total": inst["total"],
           "positions": positions, "order": inst["order"], **res}
    if "reply" in res:
        row.update(score(res["reply"], inst))
        row["reply"] = res["reply"][:400]
    return row


def summarize(rows: list[dict]) -> None:
    scored = [r for r in rows if "n_keys_correct" in r]
    if not scored:
        print("no scored rows")
        return

    by: dict[int, list[dict]] = {}
    for r in scored:
        by.setdefault(r["target_tokens"], []).append(r)

    print(f"\n{'target':>8} {'measured_in':>12} {'runs':>5} {'keys':>6} {'key_acc':>8} "
          f"{'all9':>6} {'arith':>6} {'consist':>8} {'format':>7} {'decoy':>6} {'med_s':>7}")
    for t in sorted(by):
        rs = by[t]
        n = len(rs)
        meas = sorted(r.get("input_tokens") or 0 for r in rs)[n // 2]
        keys = sum(r["n_keys"] for r in rs)
        acc = sum(r["n_keys_correct"] for r in rs) / keys
        allk = sum(r["all_keys_correct"] for r in rs) / n
        ari = sum(r["arith_ok"] for r in rs) / n
        con = sum(r["arith_consistent"] for r in rs) / n
        fmt = sum(r["format_ok"] for r in rs) / n
        dec = sum(sum(1 for v in r["miss_kind"].values() if v == "decoy") for r in rs)
        secs = sorted(r["wall_s"] for r in rs)[n // 2]
        print(f"{t:>8} {meas:>12,} {n:>5} {keys:>6} {acc:>7.1%} {allk:>5.0%} "
              f"{ari:>5.0%} {con:>7.0%} {fmt:>6.0%} {dec:>6} {secs:>7.1f}")

    # Depth: does position in the prompt matter, independent of total length?
    print(f"\n{'depth':>10} {'n':>6} {'key_acc':>8}   (all lengths pooled)")
    buckets: dict[int, list[bool]] = {}
    for r in scored:
        for key, ok in r["per_key"].items():
            pos = (r.get("positions") or {}).get(key)
            if pos is None:
                continue
            buckets.setdefault(min(9, int(pos * 10)), []).append(ok)
    for b in sorted(buckets):
        v = buckets[b]
        print(f"{b*10:>3}-{b*10+10:<6} {len(v):>6} {sum(v)/len(v):>7.1%}")

    # Which keys get missed, and how.
    kinds: dict[str, int] = {}
    for r in scored:
        for kind in r["miss_kind"].values():
            kinds[kind] = kinds.get(kind, 0) + 1
    print("\nmisses by kind:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "none")

    errs = [r for r in rows if "error" in r]
    print(f"\n{len(errs)} run(s) errored.")
    for e in errs[:5]:
        print(f"  seed={e['seed']} tokens={e['target_tokens']}: {str(e['error'])[:120]}")
    trunc = [r for r in scored if r.get("stop_reason") not in (None, "end_turn", "stop_sequence")]
    if trunc:
        print(f"{len(trunc)} run(s) with unexpected stop_reason: "
              f"{ {r.get('stop_reason') for r in trunc} }")
    nomeasure = [r for r in scored if not r.get("input_tokens")]
    if nomeasure:
        print(f"WARNING: {len(nomeasure)} scored run(s) still report 0 input tokens.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, default=20)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--lengths", type=int, nargs="+", default=DEFAULT_LENGTHS)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default=None)
    ap.add_argument("--summarize", default=None)
    args = ap.parse_args()

    if args.summarize:
        summarize([json.loads(l) for l in Path(args.summarize).read_text().splitlines() if l.strip()])
        return

    OUT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT / f"context-rot-v2-{args.model}.jsonl"
    pad = corpus()
    print(f"padding corpus: {len(pad):,} chars ({len(pad)/CHARS_PER_TOKEN:,.0f} est tokens), "
          f"python {sys.version.split()[0]}")
    insts = [instance(1000 + i) for i in range(args.instances)]
    jobs = [(i, t) for t in args.lengths for i in insts]
    print(f"{len(jobs)} runs: {args.instances} instances x {len(args.lengths)} lengths, "
          f"{len(FACTS)} facts + {len(FACTS)*DECOYS_PER_FACT} decoys each, model={args.model}")

    rows, done = [], 0
    with out.open("w") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(one_run, pad, i, t, args.model, args.timeout) for i, t in jobs]
        for f in futs:
            row = f.result()
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            done += 1
            mark = "!" if "error" in row else ("." if row.get("all_keys_correct") else
                                               str(row.get("n_keys_correct", "?")))
            print(mark, end="", flush=True)
            if done % 25 == 0:
                print(f" {done}/{len(jobs)}", flush=True)
    spent = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"\n\nwrote {out}  (list-price equivalent ${spent:.2f})")
    summarize(rows)


if __name__ == "__main__":
    main()
