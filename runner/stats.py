#!/usr/bin/env python3
"""stats.py — the exact statistical appendix for the model-gauntlet.

Implements ANALYSIS.md §3–§4 verbatim: every on-camera number gets an EXACT
test (no large-sample approximations), and every result is rendered with its
raw counts — a Wilson interval, a McNemar 2x2 table, a permutation count — never
a bare p-value. Reads runner/results/{results,judgments}.jsonl (same row shapes
as tables.py / run.py) and writes STATS-APPENDIX.md.

Stdlib ONLY (no numpy/scipy): the whole repo is zero-dependency. checker != worker
— this script only READS state files, exactly like tables.py.

    python3 runner/stats.py --results runner/results/results.jsonl \
                            --judgments runner/results/judgments.jsonl

Self-check (unit tests vs known values, exits nonzero on any FAIL):

    python3 runner/stats.py --selftest
"""
import argparse
import itertools
import json
import math
import os
import statistics
import sys

# Membership in B''s core, declared on disk for runner/import_gate.py (read via
# ast, never imported). Deleting this line fails the gate rather than quietly
# shrinking the core.
CORE_MODULE = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")

# 95% two-sided normal quantile (z_{0.975}), full precision.
Z95 = 1.959963984540054
# z for 80% power (z_{0.80}), used only in the labelled-approximate power section.
Z80 = 0.8416212335729143

EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}


# --------------------------------------------------------------------------- #
# Exact statistical primitives
# --------------------------------------------------------------------------- #
def wilson_ci(k, n, z=Z95):
    """95% Wilson score interval for k successes in n Bernoulli trials.

    Correct coverage at small n, unlike the naive p +/- z*sqrt(p(1-p)/n).
    Returns (lo, hi); (None, None) when n == 0.
    """
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (center - half, center + half)


def mcnemar_exact_p(b, c):
    """Two-sided exact McNemar p-value: binomial test at 1/2 on the b+c
    discordant pairs. Returns None when there are no discordant pairs (the
    caller renders the 'no evidence of difference' message instead).
    """
    n = b + c
    if n == 0:
        return None
    m = min(b, c)
    tail = sum(math.comb(n, i) for i in range(m + 1))
    return min(1.0, 2.0 * tail * (0.5 ** n))


def signflip_p(diffs, tol=1e-12):
    """Exact two-sided sign-flip permutation test on paired differences.

    Enumerates all 2^k sign assignments (k = len(diffs)); statistic is the sum
    of signed differences. p = (# assignments with |stat| >= |observed|) / 2^k.
    Returns (p, k, n_extreme, total) or None when k == 0.
    """
    k = len(diffs)
    if k == 0:
        return None
    observed = abs(sum(diffs))
    total = 2 ** k
    n_extreme = 0
    for signs in itertools.product((1.0, -1.0), repeat=k):
        stat = abs(sum(s * d for s, d in zip(signs, diffs)))
        if stat >= observed - tol:
            n_extreme += 1
    return (n_extreme / total, k, n_extreme, total)


def pearson_r(xs, ys):
    """Pearson correlation; None if < 2 points or either axis has zero variance."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_prop_power(delta, n, p1=0.5, z_alpha=Z95):
    """APPROXIMATE (normal-approx) power of a two-sided two-proportion z-test at
    per-arm size n, baseline p1, effect size delta. Used ONLY for the labelled
    minimum-detectable-effect search."""
    p2 = p1 + delta
    if not (0.0 <= p2 <= 1.0):
        return 0.0
    pbar = (p1 + p2) / 2.0
    denom = math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    if denom <= 0:
        return 1.0 if delta != 0 else 0.0
    z = (math.sqrt(n) * abs(delta) - z_alpha * math.sqrt(2 * pbar * (1 - pbar))) / denom
    return normal_cdf(z)


def min_detectable_effect(n=24, p1=0.5, target_power=0.80, step=1e-4):
    """Smallest delta whose approximate two-proportion power >= target at n/arm."""
    d = step
    while d <= 1.0 - p1 + 1e-9:
        if two_prop_power(d, n, p1) >= target_power:
            return d
        d += step
    return None


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def load_jsonl(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def summary_tokens(r):
    """The token axis every summary in this file reports: **output tokens only**.

    Was `tokens_in + tokens_out`. Struck on 2026-07-30 (ticket 31 AC#3, ratified
    by Drake) because that sum is not comparable across models: only 148 of 268
    corpus rows carry a true cache-inclusive `tokens_in`, and which rows those are
    varies by model family. A "total" that means a different thing per row is not
    a measurement, and a footnote does not make it one. `tokens_out` was verified
    byte-identical pre- and post-fix on all 56 re-parsed rows, so it is the axis
    that was actually measured. `tokens_out`-only paths are explicitly permitted
    by AC#3 and need no provenance gate.

    An input number for the 56 recovered rows is available by joining
    `usage.jsonl` on `run_id`; it is deliberately not summed in here.
    """
    return r.get("tokens_out", 0) or 0


def logtok(total):
    """log of total tokens; 0.0 when total <= 0 (mock rows carry 0 tokens)."""
    return math.log(total) if total > 0 else 0.0


def is_pass(r):
    return bool(r.get("pass"))


def group(rows, keyfn):
    g = {}
    for r in rows:
        g.setdefault(keyfn(r), []).append(r)
    return g


def best_effort(model_rows):
    """Winning effort for a model's rows: max pass rate, tiebreak lower mean
    tokens, then higher reasoning effort."""
    by_eff = group(model_rows, lambda r: r["effort"])
    best, best_eff = None, None
    for eff, rs in by_eff.items():
        n = len(rs)
        rate = sum(1 for r in rs if is_pass(r)) / n if n else 0.0
        mean_tok = sum(summary_tokens(r) for r in rs) / n if n else 0.0
        score = (rate, -mean_tok, EFFORT_ORDER.get(eff, 9))
        if best is None or score > best:
            best, best_eff = score, eff
    return best_eff


def judge_axis_mean(j):
    """Mean of one judge object's per-axis scores, or None."""
    if not isinstance(j, dict):
        return None
    vals = [v.get("score") for v in j.values()
            if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    return sum(vals) / len(vals) if vals else None


# --------------------------------------------------------------------------- #
# McNemar rendering
# --------------------------------------------------------------------------- #
def build_pairs(rows_a, rows_b):
    """Pair rows_a vs rows_b by (task, rep). Returns list of (pass_a, pass_b)."""
    idx_a = {(r["task"], r["rep"]): r for r in rows_a}
    idx_b = {(r["task"], r["rep"]): r for r in rows_b}
    pairs = []
    for key in sorted(set(idx_a) & set(idx_b)):
        pairs.append((is_pass(idx_a[key]), is_pass(idx_b[key])))
    return pairs


def mcnemar_block(title, label_a, label_b, pairs):
    """Render one McNemar comparison: the 2x2 concordant/discordant table plus
    its exact p-value (or the no-discordant-pairs message)."""
    a = sum(1 for pa, pb in pairs if pa and pb)       # both pass
    b = sum(1 for pa, pb in pairs if pa and not pb)   # a pass, b fail (discordant)
    c = sum(1 for pa, pb in pairs if not pa and pb)   # a fail, b pass (discordant)
    d = sum(1 for pa, pb in pairs if not pa and not pb)  # both fail
    npairs = len(pairs)
    out = [f"**{title}**  ({npairs} paired runs, matched by task x rep)", ""]
    if npairs == 0:
        out.append("_(no paired runs available)_\n")
        return "\n".join(out)
    out += [
        f"| | {label_b} pass | {label_b} fail |",
        "| --- | --- | --- |",
        f"| **{label_a} pass** | {a} | {b} |",
        f"| **{label_a} fail** | {c} | {d} |",
        "",
        f"Concordant: {a + d}  ({a} pass/pass, {d} fail/fail). "
        f"Discordant: {b + c}  (b={b} {label_a}-only, c={c} {label_b}-only).",
        "",
    ]
    p = mcnemar_exact_p(b, c)
    if p is None:
        out.append(f"No discordant pairs — no evidence of difference "
                   f"(n={npairs} pairs). McNemar exact test needs discordant "
                   "pairs to have anything to test.")
    else:
        out.append(f"McNemar exact two-sided **p = {p:.5f}** "
                   f"(binomial at 1/2 on the {b + c} discordant pairs).")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Report sections
# --------------------------------------------------------------------------- #
def section_wilson(results):
    lines = ["## 1. Per-cell pass rate — 95% Wilson score intervals", "",
             "Every cell (model x effort x harness) is a set of Bernoulli trials; "
             "the interval is the honest small-n coverage behind each pass rate.",
             "",
             "| model | effort | harness | pass/n | pass rate | 95% Wilson CI |",
             "| --- | --- | --- | --- | --- | --- |"]
    g = group(results, lambda r: (r["model"], r["effort"],
                                  "harness" if r.get("harness") else "bare"))
    for (model, effort, htag), rs in sorted(
            g.items(), key=lambda kv: (kv[0][0], EFFORT_ORDER.get(kv[0][1], 9), kv[0][2])):
        n = len(rs)
        k = sum(1 for r in rs if is_pass(r))
        lo, hi = wilson_ci(k, n)
        rate = f"{100.0 * k / n:.0f}%" if n else "-"
        ci = f"({lo:.4f}, {hi:.4f})" if lo is not None else "-"
        lines.append(f"| {model} | {effort} | {htag} | {k}/{n} | {rate} | {ci} |")
    lines.append("")
    return "\n".join(lines)


def section_head_to_head(results):
    """(a) fable-best-effort vs sol-best-effort, bare, paired by task x rep."""
    lines = ["## 2. Head-to-head: Fable vs Sol at best effort (McNemar exact)", "",
             "Sweep 1 (bare). Each model runs at its winning effort (max pass "
             "rate, ties -> fewer tokens -> higher effort); runs are matched by "
             "task x rep so task difficulty cancels.", ""]
    bare = [r for r in results if not r.get("harness")]
    fable = [r for r in bare if r["model"] == "fable"]
    sol = [r for r in bare if r["model"] == "sol"]
    if not fable or not sol:
        lines.append("_(no bare fable/sol runs to compare)_\n")
        return "\n".join(lines)
    fe, se = best_effort(fable), best_effort(sol)
    fable_best = [r for r in fable if r["effort"] == fe]
    sol_best = [r for r in sol if r["effort"] == se]
    lines.append(f"Fable best effort = **{fe}**, Sol best effort = **{se}**.\n")
    pairs = build_pairs(fable_best, sol_best)
    lines.append(mcnemar_block(f"Fable/{fe} vs Sol/{se}",
                               f"Fable/{fe}", f"Sol/{se}", pairs))
    return "\n".join(lines)


def section_effort_rungs(results):
    """(b) adjacent effort rungs within each model (bare)."""
    lines = ["## 3. Does more effort help? Adjacent effort rungs (McNemar exact)", "",
             "Sweep 1 (bare), per model, each adjacent effort pair matched by "
             "task x rep.", ""]
    bare = [r for r in results if not r.get("harness")]
    any_block = False
    for model in sorted({r["model"] for r in bare}):
        mrows = [r for r in bare if r["model"] == model]
        efforts = sorted({r["effort"] for r in mrows},
                         key=lambda e: EFFORT_ORDER.get(e, 9))
        for lo_e, hi_e in zip(efforts, efforts[1:]):
            lo_rows = [r for r in mrows if r["effort"] == lo_e]
            hi_rows = [r for r in mrows if r["effort"] == hi_e]
            pairs = build_pairs(hi_rows, lo_rows)
            lines.append(mcnemar_block(
                f"{model}: {hi_e} vs {lo_e}", f"{model}/{hi_e}", f"{model}/{lo_e}",
                pairs))
            any_block = True
    if not any_block:
        lines.append("_(no model has two or more effort levels in the bare "
                     "runs)_\n")
    return "\n".join(lines)


def section_harness(results):
    """(c) bare vs harness within each model, at winning effort, paired by task x rep."""
    lines = ["## 4. Does the harness help? Bare vs harnessed (McNemar exact)", "",
             "Per model, at the winning effort, runs matched by task x rep "
             "(sweep 1 T2 vs sweep 2a).", ""]
    any_block = False
    for model in sorted({r["model"] for r in results}):
        mrows = [r for r in results if r["model"] == model]
        bare = [r for r in mrows if not r.get("harness")]
        harn = [r for r in mrows if r.get("harness")]
        if not bare or not harn:
            continue
        # winning effort common to both harness states, else best of bare.
        common = sorted(({r["effort"] for r in bare} & {r["effort"] for r in harn}),
                        key=lambda e: EFFORT_ORDER.get(e, 9))
        eff = best_effort([r for r in mrows if r["effort"] in common]) if common \
            else best_effort(bare)
        bare_e = [r for r in bare if r["effort"] == eff]
        harn_e = [r for r in harn if r["effort"] == eff]
        pairs = build_pairs(bare_e, harn_e)
        lines.append(mcnemar_block(
            f"{model} at effort {eff}: bare vs harness",
            f"{model} bare", f"{model} harness", pairs))
        any_block = True
    if not any_block:
        lines.append("_(no model has both bare and harnessed runs)_\n")
    return "\n".join(lines)


def section_permutation(results):
    """(3) exact sign-flip permutation on per-task median log(OUTPUT tokens) of
    PASSING runs, fable vs sol, paired by task.

    The axis is output tokens, not total. This is the one comparison in the file
    where that distinction was load-bearing: fable's 64 rows are `quarantined`
    and sol's 112 are `measured`, so a total-token contrast here was fable's
    undercounted input side against sol's correct one. See `summary_tokens`.
    """
    lines = ["## 5. Who's cheaper per solved task? Sign-flip permutation test", "",
             "Per-task median log(**output** tokens) over PASSING runs, Fable "
             "minus Sol, paired by task. Exact test: all 2^k sign patterns "
             "(k = tasks with passing runs on both sides).", "",
             "Output tokens, not total: fable's input side is quarantined and "
             "sol's is measured (ticket 31 AC#3), so a total-token contrast here "
             "would compare an undercount against a true count.", ""]
    passing = [r for r in results if is_pass(r)]
    fable = group([r for r in passing if r["model"] == "fable"], lambda r: r["task"])
    sol = group([r for r in passing if r["model"] == "sol"], lambda r: r["task"])
    tasks = sorted(set(fable) & set(sol))
    if not tasks:
        lines.append("_(no task has passing fable and sol runs)_\n")
        return "\n".join(lines)
    lines += ["| task | fable med log(out tok) | sol med log(out tok) | diff (F−S) |",
              "| --- | --- | --- | --- |"]
    diffs = []
    for t in tasks:
        fmed = statistics.median([logtok(summary_tokens(r)) for r in fable[t]])
        smed = statistics.median([logtok(summary_tokens(r)) for r in sol[t]])
        diffs.append(fmed - smed)
        lines.append(f"| {t} | {fmed:.4f} | {smed:.4f} | {fmed - smed:+.4f} |")
    lines.append("")
    res = signflip_p(diffs)
    p, k, n_extreme, total = res
    lines.append(f"Observed sum of diffs = {sum(diffs):+.4f} over k={k} tasks. "
                 f"{n_extreme} of {total} sign patterns are as-or-more extreme "
                 f"-> two-sided **p = {p:.7f}**.")
    lines.append("")
    if all(abs(d) == 0 for d in diffs):
        lines.append("_(all diffs are 0 — token counts are identical or 0 under "
                     "--mock, so there is nothing to distinguish.)_")
        lines.append("")
    return "\n".join(lines)


def section_judges(judgments):
    """(4) dual-judge agreement: mean |gap|, Pearson r, % within +/-1."""
    lines = ["## 6. Can we trust the judges? Dual-judge agreement", "",
             "Per run, each judge's score is averaged across its four axes; we "
             "compare Claude's average to Codex's average.", ""]
    claude, codex = [], []
    for row in judgments:
        cm = judge_axis_mean(row.get("judge_claude"))
        km = judge_axis_mean(row.get("judge_codex"))
        if cm is not None and km is not None:
            claude.append(cm)
            codex.append(km)
    n = len(claude)
    if n == 0:
        lines.append("_(no judgments with both judges scored)_\n")
        return "\n".join(lines)
    gaps = [abs(a - b) for a, b in zip(claude, codex)]
    mean_gap = sum(gaps) / n
    within1 = sum(1 for g in gaps if g <= 1.0)
    r = pearson_r(claude, codex)
    r_str = f"{r:.4f}" if r is not None else "undefined (no variance in one judge)"
    lines += [
        f"- Runs compared (both judges present): **{n}**",
        f"- Mean absolute score gap: **{mean_gap:.4f}** points (0–10 scale)",
        f"- Pearson r (Claude avg vs Codex avg): **{r_str}**",
        f"- Within ±1 point: **{within1}/{n} = {100.0 * within1 / n:.0f}%**",
        "",
    ]
    return "\n".join(lines)


def section_power(results):
    """(5) approximate minimum detectable effect at n=24/arm."""
    lines = ["## 7. Power — what this experiment can and cannot detect", "",
             "Two-proportion test, per-arm n=24, α=0.05 two-sided, 80% power, "
             "baseline p=0.5.", ""]
    mde = min_detectable_effect(n=24, p1=0.5, target_power=0.80)
    if mde is None:
        lines.append("Even a 50pp gap is under-powered at n=24 "
                     "(no detectable effect within [0, 0.5]).")
    else:
        pw = two_prop_power(mde, 24, 0.5)
        lines += [
            f"- Minimum detectable effect ≈ **{mde * 100:.1f} percentage points** "
            f"(power {pw * 100:.1f}% at that gap).",
            "- _Approximate_: found by a normal-approximation power search over "
            "delta (the ONE place this appendix uses a large-sample approximation "
            "— everything above is exact).",
            "",
            "> Rule (ANALYSIS §4): a gap smaller than the minimum detectable "
            "effect gets a confidence interval, not a verdict. We say \"no "
            "detectable difference,\" never \"they're equal.\"",
            "",
        ]
    return "\n".join(lines)


def build_report(results, judgments):
    n_pass = sum(1 for r in results if is_pass(r))
    parts = [
        "# Statistical appendix — Fable vs Sol gauntlet",
        "",
        "Every number below is an **exact** test rendered with its raw counts "
        "(Wilson interval, McNemar 2x2 table, or permutation count) — never a "
        "bare p-value. Implements `ANALYSIS.md` §3–§4. Stdlib only.",
        "",
        f"Source: {len(results)} run row(s), {n_pass} passing, "
        f"{len(judgments)} judged.",
        "",
        section_wilson(results),
        section_head_to_head(results),
        section_effort_rungs(results),
        section_harness(results),
        section_permutation(results),
        section_judges(judgments),
        section_power(results),
    ]
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Self-test (unit checks vs known values)
# --------------------------------------------------------------------------- #
def selftest():
    checks = []

    def check(name, cond, got, want):
        checks.append((name, cond, got, want))

    # 1. McNemar exact, discordant b=1 c=5 -> p = 0.21875 exactly.
    p = mcnemar_exact_p(1, 5)
    check("McNemar exact b=1,c=5 -> 0.21875", p == 0.21875, p, 0.21875)

    # 2. Wilson 95% CI for 8/10 -> (0.4901, 0.9433) within +/-0.001.
    lo, hi = wilson_ci(8, 10)
    check("Wilson 8/10 lo≈0.4901", abs(lo - 0.4901) < 1e-3, round(lo, 4), 0.4901)
    check("Wilson 8/10 hi≈0.9433", abs(hi - 0.9433) < 1e-3, round(hi, 4), 0.9433)

    # 3. Sign-flip, 8 positive diffs -> 2/256 = 0.0078125.
    res = signflip_p([1.0] * 8)
    check("Sign-flip 8 positive -> 0.0078125",
          res is not None and abs(res[0] - 2 / 256) < 1e-12,
          res[0] if res else None, 2 / 256)

    # 4. Pearson r of a perfectly linear pair set -> 1.0.
    xs = [1, 2, 3, 4, 5, 6]
    ys = [2 * x + 1 for x in xs]
    r = pearson_r(xs, ys)
    check("Pearson perfectly linear -> 1.0",
          r is not None and abs(r - 1.0) < 1e-9, r, 1.0)

    ok = True
    for name, cond, got, want in checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {name}  (got={got}, want={want})")
    print(f"\n{'ALL PASS' if ok else 'SELFTEST FAILED'} "
          f"({sum(1 for _, c, _, _ in checks if c)}/{len(checks)})")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="model-gauntlet exact-stats appendix")
    ap.add_argument("--results",
                    default=os.path.join(RUNNER_DIR, "results", "results.jsonl"))
    ap.add_argument("--judgments",
                    default=os.path.join(RUNNER_DIR, "results", "judgments.jsonl"))
    ap.add_argument("--out", default=None,
                    help="output markdown path (default: STATS-APPENDIX.md next "
                         "to --results)")
    ap.add_argument("--selftest", action="store_true",
                    help="run unit checks vs known values and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    results = load_jsonl(args.results)
    judgments = load_jsonl(args.judgments)
    if not results:
        print(f"no results found at {args.results}", file=sys.stderr)

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.results)),
                                   "STATS-APPENDIX.md")
    report = build_report(results, judgments)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
