#!/usr/bin/env python3
"""tables.py — generate the six deliverable tables (video chapters) from the
runner's JSONL state. Separate script from the worker (checker != worker,
state-in-files): it only READS runner/results/{results,judgments}.jsonl.

Tables (per spec section 5):
  1. Pass rate x effort ladder
  2. Tokens-per-pass efficiency frontier (money chart)
  3. Harness delta per model (pass% + tokens, bare -> harnessed)
  4. Hybrid vs solo on T3
  5. Variance min/med/max per cell ("same prompt, 3 outcomes")
  6. When-to-use-which decision matrix + $/task

Stdlib only. Emits GitHub-flavored markdown to stdout (or --out FILE).
Degrades gracefully: any table with no matching rows prints "(no data)".

$/task uses documented list-price PLACEHOLDERS (runs are on subscription, so the
dollar column is a list-price-equivalent estimate, not billed spend).
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")

# List-price ESTIMATES, USD per 1M tokens (input, output). Placeholders — edit to
# taste; the dollar column is labelled as an estimate everywhere it appears.
PRICES = {
    "fable":  {"in": 15.0, "out": 75.0},
    "opus":   {"in": 15.0, "out": 75.0},
    "sol":    {"in": 10.0, "out": 40.0},
    "hybrid": {"in": 15.0, "out": 75.0},
}

EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
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


def judge_mean(j):
    """Mean of the four axis scores for one judge's object, or None."""
    if not isinstance(j, dict):
        return None
    vals = [v.get("score") for v in j.values()
            if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    return sum(vals) / len(vals) if vals else None


def quality_by_run(judgments):
    """run_id -> averaged quality score across both judges (0-10), or None."""
    out = {}
    for row in judgments:
        means = [m for m in (judge_mean(row.get("judge_claude")),
                             judge_mean(row.get("judge_codex"))) if m is not None]
        out[row.get("run_id")] = round(sum(means) / len(means), 2) if means else None
    return out


def dollars(model, tin, tout):
    p = PRICES.get(model, {"in": 15.0, "out": 75.0})
    return (tin / 1e6) * p["in"] + (tout / 1e6) * p["out"]


def pct(k, n):
    return f"{100.0 * k / n:.0f}%" if n else "-"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fnum(x, nd=0):
    if x is None:
        return "-"
    return f"{x:.{nd}f}" if nd else f"{round(x):,}"


def group(rows, keyfn):
    g = {}
    for r in rows:
        g.setdefault(keyfn(r), []).append(r)
    return g


def md_table(headers, data_rows):
    if not data_rows:
        return "_(no data)_\n"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def table1_effort_ladder(rows, qual):
    g = group(rows, lambda r: (r["model"], r["effort"]))
    data = []
    for (model, effort), rs in sorted(
            g.items(), key=lambda kv: (kv[0][0], EFFORT_ORDER.get(kv[0][1], 9))):
        n = len(rs)
        passes = sum(1 for r in rs if r.get("pass"))
        q = mean([qual.get(r["run_id"]) for r in rs if r.get("pass")])
        data.append([model, effort, n, passes, pct(passes, n),
                     fnum(q, 2) if q is not None else "-"])
    return md_table(
        ["model", "effort", "n", "pass", "pass_rate", "avg_quality(/10)"], data)


def table2_efficiency_frontier(rows):
    g = group(rows, lambda r: (r["model"], r["effort"]))
    data = []
    for (model, effort), rs in sorted(
            g.items(), key=lambda kv: (kv[0][0], EFFORT_ORDER.get(kv[0][1], 9))):
        n = len(rs)
        passes = sum(1 for r in rs if r.get("pass"))
        tot = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rs]
        tpp = (sum(tot) / passes) if passes else None
        data.append([model, effort, pct(passes, n), fnum(mean(tot)),
                     fnum(tpp) if tpp is not None else "inf (0 pass)"])
    return md_table(
        ["model", "effort", "pass_rate", "mean_tokens/run", "tokens_per_pass"], data)


def table3_harness_delta(rows):
    models = sorted({r["model"] for r in rows})
    data = []
    for model in models:
        cell = {}
        for tag, hv in (("bare", False), ("harness", True)):
            rs = [r for r in rows if r["model"] == model and bool(r.get("harness")) == hv]
            n = len(rs)
            passes = sum(1 for r in rs if r.get("pass"))
            toks = mean([r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rs])
            cell[tag] = (n, passes, (100.0 * passes / n if n else None), toks)
        b, h = cell["bare"], cell["harness"]
        if b[0] == 0 and h[0] == 0:
            continue
        dpass = (h[2] - b[2]) if (b[2] is not None and h[2] is not None) else None
        dtok = (h[3] - b[3]) if (b[3] is not None and h[3] is not None) else None
        data.append([
            model,
            pct(b[1], b[0]), fnum(b[3]),
            pct(h[1], h[0]), fnum(h[3]),
            (f"{dpass:+.0f} pp" if dpass is not None else "-"),
            (f"{dtok:+,.0f}" if dtok is not None else "-"),
        ])
    return md_table(
        ["model", "bare pass%", "bare tok", "harness pass%", "harness tok",
         "delta pass", "delta tok"], data)


def table4_hybrid_vs_solo(rows):
    t3 = [r for r in rows if str(r.get("task", "")).startswith("t3")]
    if not t3:
        return "_(no data - no T3 runs present in results)_\n"
    g = group(t3, lambda r: r["model"])
    data = []
    for model, rs in sorted(g.items()):
        n = len(rs)
        passes = sum(1 for r in rs if r.get("pass"))
        toks = mean([r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rs])
        kind = "hybrid" if model == "hybrid" else "solo"
        data.append([model, kind, n, pct(passes, n), fnum(toks)])
    return md_table(["model", "kind", "n", "pass_rate", "mean_tokens"], data)


def table5_variance(rows):
    g = group(rows, lambda r: (r["model"], r["effort"],
                               "harness" if r.get("harness") else "bare", r["task"]))
    data = []
    for (model, effort, htag, task), rs in sorted(g.items()):
        if len(rs) < 2:
            continue  # variance needs repeated cells
        passes = sum(1 for r in rs if r.get("pass"))
        locs = [r.get("loc_changed", 0) for r in rs]
        toks = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rs]
        data.append([
            f"{model}/{effort}/{htag}", task, len(rs), f"{passes}/{len(rs)}",
            f"{min(locs)}/{round(statistics.median(locs))}/{max(locs)}",
            f"{min(toks):,}/{round(statistics.median(toks)):,}/{max(toks):,}",
        ])
    return md_table(
        ["cell (model/effort/harness)", "task", "reps", "passed",
         "loc min/med/max", "tokens min/med/max"], data)


def table6_decision_matrix(rows, qual):
    models = sorted({r["model"] for r in rows})
    data = []
    for model in models:
        # best config = highest pass rate, tiebreak lowest mean tokens
        g = group([r for r in rows if r["model"] == model],
                  lambda r: (r["effort"], "harness" if r.get("harness") else "bare"))
        best, best_key = None, None
        for key, rs in g.items():
            n = len(rs)
            passes = sum(1 for r in rs if r.get("pass"))
            rate = passes / n if n else 0
            toks = mean([r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rs]) or 0
            score = (rate, -toks)
            if best is None or score > best:
                best, best_key = score, (key, rate, toks, rs)
        (effort, htag), rate, toks, rs = best_key
        dol = mean([dollars(model, r.get("tokens_in", 0), r.get("tokens_out", 0)) for r in rs])
        q = mean([qual.get(r["run_id"]) for r in rs if r.get("pass")])
        rate_pct = 100.0 * rate
        if rate_pct >= 90:
            use = "reliable — default choice for this class"
        elif rate_pct >= 50:
            use = "usable with harness / review gate"
        else:
            use = "not yet reliable here"
        data.append([
            model, f"{effort}/{htag}", f"{rate_pct:.0f}%",
            fnum(q, 2) if q is not None else "-",
            f"${dol:.4f}" if dol is not None else "-", use,
        ])
    note = ("\n\n> `$/task` is a **list-price estimate** (runs execute on "
            "subscription, so no per-run billing); token counts are 0 under `--mock`.\n")
    return md_table(
        ["model", "best config", "pass_rate", "avg_quality(/10)",
         "$/task (est)", "when to use"], data) + note


def build_report(results, judgments):
    qual = quality_by_run(judgments)
    n_pass = sum(1 for r in results if r.get("pass"))
    parts = [
        "# model-gauntlet results",
        "",
        f"Source: {len(results)} run row(s), {n_pass} passing, "
        f"{len(judgments)} judged.",
        "",
        "## 1. Pass rate x effort ladder", "",
        table1_effort_ladder(results, qual),
        "## 2. Tokens-per-pass efficiency frontier (money chart)", "",
        table2_efficiency_frontier(results),
        "## 3. Harness delta per model (bare -> harnessed)", "",
        table3_harness_delta(results),
        "## 4. Hybrid vs solo on T3", "",
        table4_hybrid_vs_solo(results),
        "## 5. Variance per cell (same prompt, N outcomes)", "",
        table5_variance(results),
        "## 6. When-to-use-which decision matrix + $/task", "",
        table6_decision_matrix(results, qual),
    ]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet table generator")
    ap.add_argument("--results", default=os.path.join(RUNNER_DIR, "results", "results.jsonl"))
    ap.add_argument("--judgments", default=os.path.join(RUNNER_DIR, "results", "judgments.jsonl"))
    ap.add_argument("--out", default=None, help="write markdown here (default: stdout)")
    args = ap.parse_args()

    results = load_jsonl(args.results)
    judgments = load_jsonl(args.judgments)
    if not results:
        print(f"no results found at {args.results}", file=sys.stderr)
    report = build_report(results, judgments)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
