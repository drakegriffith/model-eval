#!/usr/bin/env python3
"""ladder_from_results.py — classify an effort ladder measured on REAL gauntlet tasks.

`effort_verdict.py` reads the endpoint probe's `ladder-*.jsonl` files, where every row
is one toy-puzzle answer. This reads `results.jsonl`, where every row is one real task
run, and applies the SAME classifier to it. The thresholds live in effort_verdict and
are imported, never restated: a pre-committed rule that gets re-typed next to new data
is no longer pre-committed.

Blocks on task by default. That is not cosmetic. Pooling the 2026-07-10 sol rows across
tasks returns AMBIGUOUS purely because task-to-task variance inflates within-tier CV
(0.12 -> 0.43) and swamps the effort signal; blocking the identical rows returns REAL on
5 of 8. Task is the study's standing blocking factor, so the per-task table is the
answer and `--pooled` exists only to show what ignoring the block costs.

Contract
  in   : results.jsonl rows (one JSON object per line) with keys
         sweep, model, effort, task, tokens_out, pass, exit_reason
  out  : a per-task verdict table on stdout, one row per task block, plus a pooled
         row when --pooled is passed; optional machine-readable --json-out
  gate : only rows with exit_reason == "ok" are counted (see below)
  exit : 0 always; this reports, it does not judge fitness for merge

Excluded rows: anything whose exit_reason is not "ok". A timeout or cli_error truncates
generation mid-stream, so its output-token count measures where the run was cut off, not
what the tier chose to spend. Counting those would let a rate-limit blip manufacture or
erase a ladder. Excluded counts are printed, never silently dropped.

Failing-but-complete runs ARE counted: spend is the measurement here, and a tier that
reasons at length and still gets the wrong answer has spent those tokens. --passing-only
restricts to passing runs for contrast.
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from effort_verdict import TIER_ORDER, classify  # noqa: E402  (thresholds: single source)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rows(path, sweep, model, passing_only):
    """Return (kept_rows, excluded_by_reason). Keeps only complete runs."""
    kept, excluded = [], defaultdict(int)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if sweep and r.get("sweep") != sweep:
                continue
            # match either the alias as written or the canonical id behind it
            if model and model not in (r.get("model"), r.get("model_id")):
                continue
            if r.get("exit_reason") != "ok":
                excluded[r.get("exit_reason") or "unknown"] += 1
                continue
            if passing_only and not r.get("pass"):
                excluded["not_passing"] += 1
                continue
            kept.append(r)
    return kept, dict(excluded)


def tiers_for(rows):
    """Group output tokens by effort, ordered canonically. -> [(effort, [samples])]"""
    by_tier = defaultdict(list)
    for r in rows:
        by_tier[r["effort"]].append(r.get("tokens_out", 0))
    return [(t, by_tier[t]) for t in TIER_ORDER if t in by_tier]


def report_block(label, rows):
    tiers = tiers_for(rows)
    res = classify(tiers)
    return {
        "block": label,
        "n_runs": len(rows),
        "tiers": [t for t, _ in tiers],
        "n_per_tier": [len(v) for _, v in tiers],
        "out_tokens_by_tier": {t: round(statistics.mean(v)) for t, v in tiers if v},
        "pass_rate": (round(sum(1 for r in rows if r.get("pass")) / len(rows), 2)
                      if rows else None),
        **res,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results",
                    default=os.path.join(ROOT, "runner", "results", "results.jsonl"))
    ap.add_argument("--sweep", default=None, help="filter to one sweep name")
    ap.add_argument("--model", default=None, help="alias or canonical id")
    ap.add_argument("--pooled", action="store_true",
                    help="also show the unblocked (probe-style) pooled verdict")
    ap.add_argument("--passing-only", action="store_true",
                    help="count only passing runs (default counts all complete runs)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    rows, excluded = load_rows(args.results, args.sweep, args.model, args.passing_only)
    if not rows:
        print(f"no matching complete rows in {args.results}"
              f" (sweep={args.sweep} model={args.model})")
        if excluded:
            print("excluded: " + "  ".join(f"{k}={v}" for k, v in sorted(excluded.items())))
        return

    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)

    report = [report_block(t, by_task[t]) for t in sorted(by_task)]
    if args.pooled:
        report.append(report_block("POOLED (no task block)", rows))

    hdr = (f"{'block':<24} {'verdict':<13} {'spread':>7} {'mono':>5} {'btwCV':>6} "
           f"{'winCV':>6} {'n':>3} {'pass':>5}  out-tokens by tier")
    print(hdr)
    print("-" * len(hdr))
    for r in report:
        tiers = " ".join(f"{t}={r['out_tokens_by_tier'][t]}" for t in r["tiers"]
                         if t in r["out_tokens_by_tier"])
        n = min(r["n_per_tier"]) if r["n_per_tier"] else 0
        print(f"{r['block']:<24} {r['verdict']:<13} {str(r['spread']):>7} "
              f"{str(r['monotone']):>5} {str(r['between_cv']):>6} "
              f"{str(r['within_cv']):>6} {n:>3} {str(r['pass_rate']):>5}  {tiers}")

    blocks = [r for r in report if not r["block"].startswith("POOLED")]
    counts = {}
    for r in blocks:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\ntask blocks: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
          + f"   ({len(rows)} complete runs)")
    if excluded:
        print("excluded rows: " + "  ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
              + "   (incomplete generation is not a spend measurement)")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()
