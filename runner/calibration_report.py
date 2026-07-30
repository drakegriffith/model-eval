#!/usr/bin/env python3
"""Report a calibration stratum as measured actuals.

Deterministic. Reads one calibration results file plus the usage ledger and
prints the numbers ticket 05's "Session 2026-07-27b" reported, so a later
calibration round is reported the same way rather than by hand:

  * grade verification (sealed / tampered / brokered / k_cap / exit_reason)
  * per-task pooled roster pass rate with n
  * the full model x task grid, not a summary
  * Clopper-Pearson exact 95% lower bound beside every point estimate
  * broker distribution (max, mean, runs >= 8, cap-terminated)
  * cost (runs, metered spend, worker tokens in/out, wall clock median/max)

Calibration output is exploratory instrument characterisation under the sealed
pre-registration section 1. This script reports a stratum; it never pools two
results files, and the caller passes exactly one.

Usage: calibration_report.py <results.jsonl> [--sweep NAME] [--expect N]
"""
import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_gates  # noqa: E402

ALPHA = 0.05  # two-sided 95%, matching 05's 0.735 on 12/12 and 0.903 on 36/36


def binom_sf(p, x, n):
    """P(X >= x) for X ~ Bin(n, p), computed with exact integer coefficients."""
    if x <= 0:
        return 1.0
    total = 0.0
    c = 1.0
    # iterate k = x..n
    from math import comb
    for k in range(x, n + 1):
        c = comb(n, k)
        total += c * (p ** k) * ((1 - p) ** (n - k))
    return total


def clopper_pearson_lower(x, n, alpha=ALPHA):
    """Exact (Clopper-Pearson) two-sided lower confidence bound."""
    if n == 0:
        return float("nan")
    if x == 0:
        return 0.0
    target = alpha / 2
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if binom_sf(mid, x, n) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def load(path, sweep=None):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if sweep:
        rows = [r for r in rows if r.get("sweep") == sweep]
    return rows


def fmt_rate(x, n):
    return f"{x / n:.3f}" if n else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--sweep", default=None)
    ap.add_argument("--expect", type=int, default=None, help="expected row count; mismatch is reported, never silently accepted")
    ap.add_argument("--ledger", default=None, help="usage ledger jsonl (default: alongside results)")
    args = ap.parse_args()

    rows = load(args.results, args.sweep)
    print(f"# calibration stratum: {args.results}" + (f"  sweep={args.sweep}" if args.sweep else ""))
    print(f"rows: {len(rows)}" + (f" (expected {args.expect})" if args.expect else ""))
    if args.expect is not None and len(rows) != args.expect:
        print(f"!! ROW COUNT MISMATCH — expected {args.expect}, got {len(rows)}")

    # ---------------- grade verification, before anything is read into the number
    print("\n## grade verification")
    checks = {
        "sealed: true on every row": [r for r in rows if r.get("sealed") is not True],
        "tampered: false on every row": [r for r in rows if r.get("tampered") is not False],
        "brokered: true on every row": [r for r in rows if r.get("brokered") is not True],
        "k_cap == 10 on every row": [r for r in rows if r.get("k_cap") != 10],
        "tamper_files empty on every row": [r for r in rows if r.get("tamper_files")],
        # Was a bare distribution print below, which reports a dirty exit just as
        # calmly as a clean one and rules nothing out. As a check it can FAIL the
        # grade. No-op on the stamped calibration corpora (all `ok`) — which is a
        # verified fact, not an assumption; see the tally line under `## cost`.
        "exit_reason summarizable on every row":
            [r for r in rows if not corpus_gates.summarizable(r)],
    }
    failed = False
    for name, bad in checks.items():
        status = "PASS" if not bad else f"FAIL ({len(bad)} rows: {[b['run_id'] for b in bad][:5]})"
        if bad:
            failed = True
        print(f"  {name}: {status}")
    exits = {}
    for r in rows:
        exits[r.get("exit_reason")] = exits.get(r.get("exit_reason"), 0) + 1
    print(f"  exit_reason distribution: {exits}")
    if failed:
        print("\n!! GRADE NOT VERIFIED — stop. Do not rule on this grade.")

    # ---------------- pass rates
    tasks = sorted({r["task"] for r in rows})
    models = sorted({r["model_id"] for r in rows})

    print("\n## per-task pooled roster pass rate (point estimate, n, CP95 lower bound)")
    print("| task | pooled pass rate | n | CP95 lower |")
    print("|---|---|---|---|")
    for t in tasks:
        tr = [r for r in rows if r["task"] == t]
        k = sum(1 for r in tr if r.get("pass"))
        print(f"| {t} | {fmt_rate(k, len(tr))} | {len(tr)} | {clopper_pearson_lower(k, len(tr)):.3f} |")
    k_all = sum(1 for r in rows if r.get("pass"))
    print(f"| POOLED (all tasks) | {fmt_rate(k_all, len(rows))} | {len(rows)} | {clopper_pearson_lower(k_all, len(rows)):.3f} |")

    print("\n## model x task grid — passes/n (CP95 lower)")
    header = "| model id @ effort | " + " | ".join(tasks) + " |"
    print(header)
    print("|" + "---|" * (len(tasks) + 1))
    for m in models:
        mr = [r for r in rows if r["model_id"] == m]
        eff = sorted({r["effort"] for r in mr})
        cells = []
        for t in tasks:
            c = [r for r in mr if r["task"] == t]
            k = sum(1 for r in c if r.get("pass"))
            cells.append(f"{k}/{len(c)} ({clopper_pearson_lower(k, len(c)):.3f})")
        print(f"| {m} @ {'/'.join(eff)} | " + " | ".join(cells) + " |")
        krow = sum(1 for r in mr if r.get("pass"))
        # per-model pooled, printed after the grid row for auditability
    print("\nper-model pooled:")
    for m in models:
        mr = [r for r in rows if r["model_id"] == m]
        k = sum(1 for r in mr if r.get("pass"))
        print(f"  {m}: {k}/{len(mr)} = {fmt_rate(k, len(mr))} (CP95 lower {clopper_pearson_lower(k, len(mr)):.3f})")

    # failures, named
    fails = [r for r in rows if not r.get("pass")]
    print(f"\nfailures: {len(fails)}")
    for r in fails:
        print(f"  {r['run_id']}  exit_reason={r.get('exit_reason')} wall_s={r.get('wall_s')} "
              f"acceptance_requests={r.get('acceptance_requests')} cap_exhausted={r.get('cap_exhausted')} "
              f"turns={r.get('turns')}")

    # ---------------- broker
    print("\n## broker distribution")
    ar = [r.get("acceptance_requests") for r in rows]
    nonnull = [a for a in ar if a is not None]
    print(f"  acceptance_requests non-null: {len(nonnull)}/{len(rows)}")
    if nonnull:
        dist = {}
        for a in nonnull:
            dist[a] = dist.get(a, 0) + 1
        print(f"  max (M): {max(nonnull)}")
        print(f"  mean: {statistics.mean(nonnull):.2f}")
        print(f"  distribution: " + ", ".join(f"{v}x{k}" for k, v in sorted(dist.items())))
        print(f"  runs reaching >= 8: {sum(1 for a in nonnull if a >= 8)}")
    print(f"  cap-terminated (cap_exhausted): {sum(1 for r in rows if r.get('cap_exhausted'))}")
    # per CLI family
    for fam_name, ids in (("codex", [m for m in models if m.startswith('gpt')]),
                          ("claude", [m for m in models if m.startswith('claude')])):
        fr = [r.get("acceptance_requests") for r in rows if r["model_id"] in ids]
        fr = [a for a in fr if a is not None]
        if fr:
            print(f"  {fam_name}: n={len(fr)} max={max(fr)} mean={statistics.mean(fr):.2f}")

    # ---------------- cost
    #
    # The combined `tokens_in + tokens_out` total is STRUCK (ticket 31 AC#3,
    # ratified 2026-07-30). It summed two axes of unequal provenance into one
    # number: `tokens_out` was verified byte-identical pre- and post-fix, while
    # `tokens_in` is the true cache-inclusive total on some rows and a 30x-400x
    # undercount on others. The two axes are now reported separately, and the
    # input axis is gated to rows whose number is actually measured.
    print("\n## cost — measured actuals")
    tin_kept, tin_excluded = corpus_gates.tokens_in_rows(rows)
    tin = sum(r.get("tokens_in") or 0 for r in tin_kept)
    tout = sum(r.get("tokens_out") or 0 for r in rows)
    walls = [r.get("wall_s") or 0 for r in rows]
    print(f"  runs: {len(rows)}")
    print("  " + corpus_gates.format_exclusions(
        "tokens_in", len(rows), tin_kept, tin_excluded))
    print(f"  worker tokens out: {tout:,} over {len(rows)} runs"
          + (f" (mean {tout / len(rows):,.0f})" if rows else ""))
    print(f"  worker tokens in:  {tin:,} over {len(tin_kept)} gated runs"
          + (f" (mean {tin / len(tin_kept):,.0f})" if tin_kept else ""))
    for m in models:
        mr = [r for r in rows if r["model_id"] == m]
        mk, _ = corpus_gates.tokens_in_rows(mr)
        so = sum(r.get("tokens_out") or 0 for r in mr)
        si = sum(r.get("tokens_in") or 0 for r in mk)
        print(f"    {m}: n={len(mr)} out={so:,} mean_out={so / len(mr):,.0f}"
              f"  in={si:,} over {len(mk)}/{len(mr)} gated"
              + (f" mean_in={si / len(mk):,.0f}" if mk else ""))
    if walls:
        print(f"  wall clock: total {sum(walls) / 60:.0f} min; median {statistics.median(walls):.0f} s; max {max(walls):.0f} s")

    ledger_path = Path(args.ledger) if args.ledger else Path(args.results).parent / "usage.jsonl"
    if ledger_path.exists():
        run_ids = {r["run_id"] for r in rows}
        lr = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        lr = [r for r in lr if r.get("run_id") in run_ids]
        modes = {}
        for r in lr:
            modes[r.get("billing_mode")] = modes.get(r.get("billing_mode"), 0) + 1
        metered = sum(r.get("usd_estimate") or 0 for r in lr if r.get("billing_mode") == "metered")
        print(f"  ledger rows matched: {len(lr)}/{len(rows)}  billing_mode: {modes}")
        print(f"  metered spend: ${metered:.4f}")
    else:
        print(f"  (no usage ledger at {ledger_path})")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
