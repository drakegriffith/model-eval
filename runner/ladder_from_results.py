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

Vocabulary note (2026-07-30, ticket 42). classify() now emits BACKWARDS for a ladder that
would have been AMBIGUOUS and whose top tier spends at or below 0.95x its bottom tier.
This module imports the state along with the thresholds rather than restating either, and
prints a `was` column plus a transition tally so that a block whose verdict moved under the
new vocabulary is visible in the output instead of being re-labelled in silence.
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_gates  # noqa: E402
import report_acceptance  # noqa: E402  (issue #22: acceptance-request summary, single source)
import run_status  # noqa: E402
import tables  # noqa: E402  (issue #25: model_key/multi_driver_models, single source)
from effort_verdict import (  # noqa: E402  (thresholds + vocabulary: single source)
    TIER_ORDER, classify, pre_split_verdict, transition_tally,
)

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
            # Kept when EITHER axis can use the row: cleanly-exited rows feed the
            # token ladder, and run_status-scored rows feed the pass rate. The
            # two sets differ by cap_exhausted, which is a scored model failure
            # whose tokens_out is truncated -- so it belongs in one axis and not
            # the other, and dropping it here would have removed real failures
            # from the pass rate. tiers_for() re-applies the token gate.
            if not (corpus_gates.summarizable(r) or run_status.in_denominator(r)):
                excluded[r.get("exit_reason") or "unknown"] += 1
                continue
            if passing_only and not r.get("pass"):
                excluded["not_passing"] += 1
                continue
            kept.append(r)
    return kept, dict(excluded)


def tiers_for(rows):
    """Group output tokens by effort, ordered canonically. -> [(effort, [samples])]

    The TOKEN gate lives here, so it applies wherever the ladder is measured and
    not only where rows were loaded. A truncated run's tokens_out records where
    generation was cut off, not what the tier chose to spend, so cap_exhausted
    is excluded here even though the pass axis counts it.
    """
    by_tier = defaultdict(list)
    for r in rows:
        if not corpus_gates.summarizable(r):
            continue
        by_tier[r["effort"]].append(r.get("tokens_out", 0))
    return [(t, by_tier[t]) for t in TIER_ORDER if t in by_tier]


def report_block(label, rows):
    tiers = tiers_for(rows)
    res = classify(tiers)
    # TWO AXES, TWO DENOMINATORS (issue #12 d).
    #
    # The TOKEN ladder is measured over `rows` as the caller supplied them --
    # load_rows() has already dropped anything that did not exit cleanly, and
    # that gate is correct for tokens: a truncated run's tokens_out measures
    # where it was cut off, not what the tier chose to spend.
    #
    # It is the WRONG gate for a pass rate, because it also drops cap_exhausted:
    # a run the model got a full attempt at and did not converge on, which
    # pre-registration section 7 scores as a failure. Excluding it removes real
    # failures and inflates the rate. So the pass axis goes through the one
    # shared predicate, and both denominators are reported rather than one being
    # silently reused for the other.
    scored, excluded = run_status.partition_for_rate(rows)
    return {
        "block": label,
        "n_runs": len(rows),
        "tiers": [t for t, _ in tiers],
        "n_per_tier": [len(v) for _, v in tiers],
        "out_tokens_by_tier": {t: round(statistics.mean(v)) for t, v in tiers if v},
        "n_scored": len(scored),
        "pass_excluded": excluded,
        "pass_rate": (round(sum(1 for r in scored if r.get("pass")) / len(scored), 2)
                      if scored else None),
        **res,
    }


def blocks_for(rows):
    """One report_block per (task, driver), driver-suffixed only where a task
    actually ran under more than one driver (issue #25) -- mirrors
    tables.model_key/multi_driver_models so a single-driver corpus (every
    archived result predates the field) renders exactly one block per task,
    unchanged. A block that pooled a claude-code row with a pi row would
    average two vehicles findings.md says must never be merged: pi has no
    hooks and no subagents, so the driver is part of the treatment.
    """
    mixed = tables.multi_driver_models(rows)
    by_block = defaultdict(list)
    for r in rows:
        mkey = tables.model_key(r, mixed)
        label = r["task"] if mkey == r["model"] else f"{r['task']} [{tables.driver_of(r)}]"
        by_block[label].append(r)
    return [report_block(label, by_block[label]) for label in sorted(by_block)]


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

    report = blocks_for(rows)
    if args.pooled:
        report.append(report_block("POOLED (no task block)", rows))

    # `was` = the pre-split verdict, on every block and not only the moved ones
    # (ticket 42 AC#4). `end/1` is the top tier over the bottom -- the signed reading
    # the split turns on, which `spread` (max/min) is blind to.
    # issue #21 (1): `n_tok` and `n_scr` are two different denominators over
    # the same block -- n_tok is the token axis's floor (the smallest tier
    # sample, gated on corpus_gates.summarizable), n_scr is run_status's
    # scored count that `pass` is a rate OVER. Printing one number labelled
    # `n` beside a pass_rate computed on the other let a reader multiply
    # pass_rate x n and get a passing-run count that does not exist. Both
    # are printed, both are labelled, and neither name is left free to be
    # misread as the other.
    hdr = (f"{'block':<24} {'was':<12} {'verdict':<13} {'spread':>7} {'end/1':>6} "
           f"{'mono':>5} {'btwCV':>6} {'winCV':>6} {'n_tok':>5} {'n_scr':>5} "
           f"{'pass':>5}  out-tokens by tier")
    print(hdr)
    print("-" * len(hdr))
    for r in report:
        tiers = " ".join(f"{t}={r['out_tokens_by_tier'][t]}" for t in r["tiers"]
                         if t in r["out_tokens_by_tier"])
        n_tok = min(r["n_per_tier"]) if r["n_per_tier"] else 0
        print(f"{r['block']:<24} {pre_split_verdict(r['verdict']):<12} "
              f"{r['verdict']:<13} {str(r['spread']):>7} {str(r['end_ratio']):>6} "
              f"{str(r['monotone']):>5} {str(r['between_cv']):>6} "
              f"{str(r['within_cv']):>6} {n_tok:>5} {r['n_scored']:>5} "
              f"{str(r['pass_rate']):>5}  {tiers}")

    blocks = [r for r in report if not r["block"].startswith("POOLED")]
    counts = {}
    for r in blocks:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\ntask blocks: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
          + f"   ({len(rows)} complete runs)")
    tally = transition_tally(r["verdict"] for r in blocks)
    print("ticket 42 transitions over " + f"{len(blocks)} task block(s): "
          + "  ".join(f"{k}: {v}" for k, v in sorted(tally.items())))
    if excluded:
        print("excluded rows: " + "  ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
              + "   (incomplete generation is not a spend measurement)")

    # issue #22 / A1 (a0cef36): the report states max(acceptance_requests),
    # its distribution, and the cap_exhausted count beside the pass rate.
    # Printed once over `rows`, the same scope the "excluded rows" line above
    # already reports at -- which is the WHOLE corpus main() loaded only when
    # --passing-only was not given. With that flag, load_rows() has already
    # dropped every non-passing row before `rows` reaches here, so this max
    # is a max over passing runs, not the corpus's true max. A reader who
    # ran with --passing-only and read this line as the corpus max would
    # under-count exactly the runs most likely to carry a high
    # acceptance_requests value (a run that did not converge kept spending
    # requests), so the scope is named on the line itself rather than left
    # to be inferred from a flag the reader may not have typed themselves.
    acc_line = report_acceptance.format_acceptance_summary(
        report_acceptance.acceptance_summary(rows))
    if args.passing_only:
        acc_line += "  (scope: --passing-only rows)"
    print(acc_line)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()
