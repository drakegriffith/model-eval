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

THE TOKEN GATE (added 2026-07-30, ticket 35). That last clause -- "input is the fixed
scaffold and barely moves" -- was a claim about the exact quantity the pre-fix
parse_usage bug was getting wrong, and this module read `tokens_in` ungated while
ticket 31 AC#3 named it as one of the four readers that must not. Every row of the
ladder corpus was recorded 2026-07-25, two days before the fix instant (f11be7e,
2026-07-27T15:38:36Z), carries no `tokens_in_status`, and cannot be retrofitted the way
56 of results.jsonl's rows were, because probe_endpoints.py retains no transcript to
re-parse. So `scaffold_in_median` is now resolved through corpus_gates and comes back
WITHHELD on the whole of the current corpus. The verdicts are untouched: classify()
never sees an input count, and test_effort_verdict_token_gate.py asserts that rather
than trusting this paragraph.

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
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_gates  # noqa: E402  -- the shared reader dispositions; no private copy

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


def usage_block_empty(row):
    """True when the usage block came back empty: BOTH counts zero or absent.

    THE PERMITTED UNGATED READ of `tokens_in` in this module (ticket 35 AC#3).
    Every other read goes through `corpus_gates`; this one must not, and the
    reason is a property of the bug rather than a convenience:

        the pre-fix parse_usage bug undercounted by 30x-400x -- 85, 114, 35670,
        16839 -- and never produced 0.

    So quarantining a row cannot change this predicate's answer on any row in the
    corpus, while routing it through `corpus_gates.tokens_in_usable` WOULD: every
    unstamped ladder row (which is all 256 of them) would read as an empty usage
    block, get dropped as a measurement failure, and leave a corpus of nothing --
    a different falsehood from the one the quarantine exists to fix.

    The distinction that licenses it: this is a PRESENCE read (did the instrument
    return anything at all?), not a MAGNITUDE read (is the number it returned the
    truth?). Only the second needs provenance. A missing block reads as empty for
    the same fail-closed reason the token gate has: absent counts are a
    measurement failure, not a free run worth zero tokens.

    tests/test_effort_verdict_token_gate.py section 1 pins every clause above, so
    the next audit can tell a recorded decision from an omission.
    """
    return not (row.get("tokens_in") or 0) and not (row.get("tokens_out") or 0)


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


def build_report(rows):
    """Classify every model in `rows` and resolve the input-token axis.

    Returns {"report", "dropped", "tokens_in_note"}. Split out of main() so the
    numbers are a value rather than stdout: ticket 35 AC#6 asserts the verdicts
    are invariant under `tokens_in` by classifying the same rows twice with every
    input count corrupted the second time, which is only a test that can exist if
    the report can be compared without parsing print output.

    The two axes are resolved differently and on purpose. `tokens_out` is read
    straight -- it was verified byte-identical pre- and post-fix, so the parser
    bug never reached it. `tokens_in` is medianned and PUBLISHED as
    `scaffold_in_median`, which makes it a magnitude read, so it goes through
    `corpus_gates.tokens_in_rows` per model: a model with no usable row reports
    median None / status "quarantined" rather than a number nobody can stand
    behind. The one ungated read left is `usage_block_empty` -- see its docstring.
    """
    by_model = defaultdict(lambda: defaultdict(list))
    meta = {}
    dropped = 0
    inspected = []
    for r in rows:
        if not r.get("reachable"):
            continue
        # A completion that returned real text but an empty usage block is a
        # measurement failure, not a free run. Counting its 0 tokens as an
        # observation would drag the tier mean toward zero and manufacture a spread.
        if usage_block_empty(r):
            dropped += 1
            continue
        inspected.append(r)
        key = (r["family"], r["model_id"])
        by_model[key][r["effort"]].append(r["tokens_out"])
        meta.setdefault(key, {"rows": [], "correct": [], "wall": []})
        meta[key]["rows"].append(r)
        meta[key]["correct"].append(bool(r.get("answer_correct")))
        meta[key]["wall"].append(r.get("wall_s", 0))

    report = []
    for (family, mid), tiermap in sorted(by_model.items()):
        ordered = [(t, tiermap[t]) for t in TIER_ORDER if t in tiermap]
        res = classify(ordered)
        n_per_tier = [len(v) for _, v in ordered]
        # The magnitude read, gated. Only rows whose own `tokens_in` is the true
        # cache-inclusive total may enter the median; on the current corpus that
        # is none of them, so every entry comes back withheld rather than
        # publishing a pre-fix undercount as a scaffold size.
        in_kept, _ = corpus_gates.tokens_in_rows(meta[(family, mid)]["rows"])
        report.append({
            "family": family,
            "model_id": mid,
            "tiers_probed": [t for t, _ in ordered],
            "out_tokens_by_tier": {t: round(statistics.mean(v)) for t, v in ordered},
            "n_per_tier": n_per_tier,
            "min_n": min(n_per_tier) if n_per_tier else 0,
            "scaffold_in_median": (
                round(statistics.median([r.get("tokens_in") or 0 for r in in_kept]))
                if in_kept else None),
            "scaffold_in_status": "measured" if in_kept else "quarantined",
            "scaffold_in_n": len(in_kept),
            "answer_correct_rate": round(
                sum(meta[(family, mid)]["correct"]) / len(meta[(family, mid)]["correct"]), 2),
            **res,
        })

    # Count the subjects out loud (AC#5). format_exclusions renders the zero case
    # as its own sentence, so a run that found no ladder files cannot print what a
    # run over 256 quarantined rows prints.
    kept, excluded = corpus_gates.tokens_in_rows(inspected)
    note = corpus_gates.format_exclusions(
        "ladder rows", len(inspected), kept, excluded)
    return {"report": report, "dropped": dropped, "tokens_in_note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=None,
                    help="glob of ladder jsonl files (default: runner/results/ladder-*.jsonl)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = sorted(glob.glob(args.results or
                             os.path.join(root, "runner", "results", "ladder-*.jsonl")))
    built = build_report(load(paths))
    report, dropped = built["report"], built["dropped"]

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

    # AC#5 + AC#7. The input axis is reported as a disposition, never as a bare
    # median, and the residual is stated where a reader of the verdicts sees it.
    print("\ninput tokens (scaffold size) — " + built["tokens_in_note"])
    withheld = [r["model_id"] for r in report if r["scaffold_in_median"] is None]
    if withheld:
        print(f"scaffold_in_median WITHHELD for {len(withheld)}/{len(report)} model(s): "
              "every ladder row predates the parse_usage fix (f11be7e, "
              "2026-07-27T15:38:36Z), carries no tokens_in_status, and cannot be "
              "retrofitted — probe_endpoints.py retains no transcript to re-parse.")
        print("  RESIDUAL (ticket 35 AC#7): unrecoverable without spending tokens. "
              "Closing condition = re-run `probe_endpoints.py --phase ladder` under "
              "the current parser. See runner/TOKENS-IN-RESIDUAL.md §9.")
        print("  The verdicts above are unaffected: classify() reads tokens_out only, "
              "which was verified byte-identical pre- and post-fix.")

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
