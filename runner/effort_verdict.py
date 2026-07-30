#!/usr/bin/env python3
"""effort_verdict.py — decide, per model, whether the reasoning-effort knob is real.

Reads the ladder rows written by probe_endpoints.py and classifies each model's tier
ladder. This is the gate that keeps duplicate points off the frontier: a tier only
earns a place in the study's design if pulling the knob actually moved the spend.

The thresholds below are deliberately fixed BEFORE the data was inspected, so the
classification is a pre-committed rule rather than a line drawn around whatever the
numbers happened to do:

  REAL      spread >= 1.50x, ladder trends upward, AND between-tier variation clearly
            exceeds within-tier variation
            -> tiers are distinct budget settings; keep them as separate frontier points
  NO-OP     spread < 1.20x
            -> the knob does nothing measurable; collapse to ONE point and disclose
  AMBIGUOUS everything in between, non-monotone, or signal not separated from noise
            -> re-run at higher n; the answer is not yet in the data

Spread is computed on output tokens because that is where reasoning is billed; input
is the fixed scaffold and barely moves across tiers.

THE NOISE GATE (added 2026-07-25 after the first pass). Spread alone is not evidence.
The first n=1 pass classified 12 models REAL on spread and monotonicity; the moment
replication arrived for four of them, within-tier CV came back at 0.19-0.60 against
between-tier CV of 0.16-0.68 -- noise as large as the signal in every replicated case.
A ladder can only be credited when the tiers differ by more than the same model differs
from itself on a re-run, so REAL now additionally requires

    between_tier_cv >= NOISE_MARGIN * within_tier_cv

and any model lacking replication returns UNREPLICATED rather than REAL. This is the
gate that keeps a noise-generated 'ladder' from becoming a set of frontier points that
differ only in their label.
"""

import argparse
import glob
import json
import os
import statistics
from collections import defaultdict

# Membership in B''s core, declared on disk for runner/import_gate.py (read via
# ast, never imported). Deleting this line fails the gate rather than quietly
# shrinking the core.
CORE_MODULE = True

REAL_SPREAD = 1.50
NOOP_SPREAD = 1.20
# Between-tier variation must exceed within-tier variation by this factor before the
# ladder counts as signal rather than the model differing from itself on a re-run.
NOISE_MARGIN = 2.0
MIN_N_FOR_VERDICT = 2

# Canonical ordering; models expose different subsets.
TIER_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]


def load(paths):
    rows = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("phase") == "ladder":
                    rows.append(r)
    return rows


def monotone_score(vals):
    """Fraction of adjacent tier steps that do not go down.

    A perfectly ordered ladder scores 1.0. Used to separate 'spend rises with effort'
    from 'spend is merely noisy', which a spread ratio alone cannot distinguish.
    """
    if len(vals) < 2:
        return 1.0
    ups = sum(1 for a, b in zip(vals, vals[1:]) if b >= a * 0.95)
    return ups / (len(vals) - 1)


def cv(vals):
    """Coefficient of variation; 0 when there is nothing to vary."""
    if len(vals) < 2:
        return 0.0
    mu = statistics.mean(vals)
    return (statistics.pstdev(vals) / mu) if mu else 0.0


def classify(tiers):
    """tiers: ordered list of (effort, [out_token samples]) -> verdict dict.

    Compares between-tier variation (the putative effort signal) against within-tier
    variation (the same cell re-run). Without that comparison a spread number cannot
    be told apart from a model that is simply noisy.
    """
    means = [(e, statistics.mean(v)) for e, v in tiers if v]
    if len(means) < 2:
        return {"verdict": "INSUFFICIENT", "spread": None, "monotone": None,
                "between_cv": None, "within_cv": None}
    vals = [m for _, m in means]
    lo, hi = min(vals), max(vals)
    spread = (hi / lo) if lo > 0 else float("inf")
    mono = monotone_score(vals)
    between = cv(vals)

    # Within-tier noise, pooled over every tier that actually has replicates.
    reps = [v for _, v in tiers if len(v) >= 2]
    within = statistics.mean([cv(v) for v in reps]) if reps else None
    min_n = min(len(v) for _, v in tiers) if tiers else 0

    if min_n < MIN_N_FOR_VERDICT or within is None:
        # No noise estimate exists, so no spread can be credited yet -- this is the
        # state the whole first pass was in while reporting 12 confident REALs.
        verdict = "UNREPLICATED"
    elif spread < NOOP_SPREAD:
        verdict = "NO-OP"
    elif spread >= REAL_SPREAD and mono >= 0.6 and between >= NOISE_MARGIN * within:
        verdict = "REAL"
    else:
        verdict = "AMBIGUOUS"

    return {"verdict": verdict, "spread": round(spread, 2), "monotone": round(mono, 2),
            "between_cv": round(between, 2),
            "within_cv": (round(within, 2) if within is not None else None)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=None,
                    help="glob of ladder jsonl files (default: runner/results/ladder-*.jsonl)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = sorted(glob.glob(args.results or
                             os.path.join(root, "runner", "results", "ladder-*.jsonl")))
    rows = load(paths)

    by_model = defaultdict(lambda: defaultdict(list))
    meta = {}
    dropped = 0
    for r in rows:
        if not r.get("reachable"):
            continue
        # A completion that returned real text but an empty usage block is a
        # measurement failure, not a free run. Counting its 0 tokens as an
        # observation would drag the tier mean toward zero and manufacture a spread.
        if r.get("tokens_in", 0) == 0 and r.get("tokens_out", 0) == 0:
            dropped += 1
            continue
        key = (r["family"], r["model_id"])
        by_model[key][r["effort"]].append(r["tokens_out"])
        meta.setdefault(key, {"in": [], "correct": [], "wall": []})
        meta[key]["in"].append(r.get("tokens_in", 0))
        meta[key]["correct"].append(bool(r.get("answer_correct")))
        meta[key]["wall"].append(r.get("wall_s", 0))

    report = []
    for (family, mid), tiermap in sorted(by_model.items()):
        ordered = [(t, tiermap[t]) for t in TIER_ORDER if t in tiermap]
        res = classify(ordered)
        n_per_tier = [len(v) for _, v in ordered]
        report.append({
            "family": family,
            "model_id": mid,
            "tiers_probed": [t for t, _ in ordered],
            "out_tokens_by_tier": {t: round(statistics.mean(v)) for t, v in ordered},
            "n_per_tier": n_per_tier,
            "min_n": min(n_per_tier) if n_per_tier else 0,
            "scaffold_in_median": round(statistics.median(meta[(family, mid)]["in"])),
            "answer_correct_rate": round(
                sum(meta[(family, mid)]["correct"]) / len(meta[(family, mid)]["correct"]), 2),
            **res,
        })

    hdr = (f"{'model':<28} {'verdict':<13} {'spread':>7} {'mono':>5} "
           f"{'btwCV':>6} {'winCV':>6} {'n':>3}  out-tokens by tier")
    print(hdr)
    print("-" * len(hdr))
    for r in report:
        tiers = " ".join(f"{t}={r['out_tokens_by_tier'][t]}" for t in r["tiers_probed"])
        print(f"{r['model_id']:<28} {r['verdict']:<13} "
              f"{str(r['spread']):>7} {str(r['monotone']):>5} "
              f"{str(r['between_cv']):>6} {str(r['within_cv']):>6} {r['min_n']:>3}  {tiers}")

    counts = {}
    for r in report:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if dropped:
        print(f"dropped {dropped} row(s) with an empty usage block (measurement failure)")

    needs_more = [r["model_id"] for r in report
                  if r["verdict"] in ("UNREPLICATED", "AMBIGUOUS")]
    if needs_more:
        print("not yet credited; re-run probe_endpoints.py --phase ladder --models "
              + ",".join(needs_more))

    # Only a credited (REAL) ladder contributes a point per tier. Everything else
    # collapses to one, because uncredited tiers would be duplicate frontier points.
    points = sum(len(r["tiers_probed"]) if r["verdict"] == "REAL" else 1 for r in report)
    print(f"implied frontier points (REAL=per-tier, everything else=1): {points}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()
