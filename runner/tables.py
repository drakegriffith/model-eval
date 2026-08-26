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
  6. When-to-use-which decision matrix + true input tokens/task

Emits GitHub-flavored markdown to stdout (or --out FILE).
Degrades gracefully: any table with no matching rows prints "(no data)".

NO MONEY COLUMN (ticket 20, option C). These tables publish measured tokens
only. A price is the reader's to compute from a rate the READER supplies, and
any such rate input must take a cache-read rate as well as a fresh-input rate:
86-94% of session tokens are cache reads billed at roughly a tenth, so one rate
times these totals overstates 4.4x-6.5x (ticket 20 §5). The per-alias
list-price placeholders that used to live here were removed under ticket 20
("must never reach the dashboard"); the one real metered rate (kimi via
Moonshot) stays in usage_ledger, where it caps spend rather than rendering.

TOKEN AXIS (ticket 31 AC#3). Every `tokens_in + tokens_out` total is gone; the
token columns report `tokens_out` ONLY and say so in their headers. 64 of 268
rows carry a pre-fix `tokens_in` undercounted 30x-400x, and a total that silently
mixes 204 good inputs with 64 bad ones is worse than no input axis at all.
Table 6's input column is the one cell that genuinely needs the other axis, so
it cannot fall back to tokens_out; it resolves each row's input through
`corpus_gates` + `usage_ledger` and drops -- loudly, with a count -- what it
cannot resolve.

Both dispositions come from `corpus_gates`; this module holds no private copy of
either rule.
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")
sys.path.insert(0, RUNNER_DIR)

import corpus_gates  # noqa: E402
import run_status  # noqa: E402
import usage_ledger  # noqa: E402
# issue #19 round 2: reuse run.py's parse_yaml (via turn_cap_n_from_config /
# resolve_turn_cap_n) for this reader's --config default. run.py does not
# import this module back, so this is a new edge, not a cycle.
import run as runner_mod  # noqa: E402

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


def out_tokens(r):
    """The only token axis these tables may sum across the whole corpus.

    Named as a function rather than inlined so that `tokens_in` cannot creep back
    into a total by someone adding one term to an expression.
    """
    return r.get("tokens_out", 0)


def resolve_tokens_in(row, ledger):
    """The row's TRUE cache-inclusive input tokens, or None.

    Three states, three answers -- the dispositions come from `corpus_gates`, the
    join from `usage_ledger`, and this function only routes between them:
      measured             -> the number on the row is the truth
      recovered_in_ledger  -> the row's own number is the wrong pre-fix one; the
                              truth is in usage.jsonl under this run_id
      anything else        -> None. Quarantined, unstamped, or recoverable-but-
                              absent-from-the-ledger all mean the same thing to a
                              consumer: there is no input number here. Fail closed;
                              a missing join is not a zero.
    """
    if corpus_gates.tokens_in_usable(row):
        tin = row.get("tokens_in")
        return tin if isinstance(tin, (int, float)) else None
    if corpus_gates.tokens_in_recoverable(row):
        return ledger.get(row.get("run_id"))
    return None


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


# --------------------------------------------------------------------------- #
# The driver is part of the treatment, never a detail of delivery
# --------------------------------------------------------------------------- #
# findings.md reports pi as a SEPARATELY-REPORTED vehicle contrast: pi has no
# hooks and no subagents, so two rows differing only in driver are two
# populations, not two samples of one. Pooled, a corpus of 3/3 claude-code and
# 0/3 pi renders as a single model row at 50% -- a number describing nothing that
# exists, and exactly the merge findings.md forbids.
#
# The label is applied ONLY where a model actually ran under more than one
# driver. All 268 archived rows predate the field entirely, so a corpus with
# nothing to disambiguate renders exactly as before and no published number is
# restated.
# THE TOKEN AXIS. Separate from the pass axis on purpose, and this is the one
# place that says which rows a spend number may average.
#
# `run_status.in_denominator` counts cap_exhausted as SCORED -- correctly per
# pre-registration amendment A1 (docs/studio-handoff/prompt-2-run-experiment.md
# at a0cef36, registered 2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip
# at >= 10 requests), not an inherited section: a model that spent its K
# acceptance requests and did not converge -- this table's reading: DID get
# a fair attempt, so it belongs in the pass denominator as a failure. But
# its tokens_out records where the BROKER cut generation off, not what the
# tier chose to spend, so it must not enter a spend mean.
#
# `ladder_from_results.tiers_for` and `stats.section_cost_matched` already draw
# the line here. Until this commit tables 2-6 drew it at in_denominator instead,
# so one corpus published two different spend means -- 1000 via the ladder and
# 752 via table2 -- while table2's own comment cited the ladder as its authority.
def token_rows(rs):
    """(rows a spend number may average, count excluded as truncated)."""
    kept = [r for r in rs if corpus_gates.summarizable(r)]
    return kept, len(rs) - len(kept)


def driver_of(row):
    return row.get("driver")


def multi_driver_models(rows):
    """Models that ran under more than one driver in this corpus."""
    seen = {}
    for r in rows:
        seen.setdefault(r["model"], set()).add(driver_of(r))
    return {m: sorted(d for d in ds if d) for m, ds in seen.items() if len(ds) > 1}


def model_key(row, mixed):
    """The grouping name for a model, split by driver only where it must be."""
    if row["model"] in mixed and driver_of(row):
        return f"{row['model']} [{driver_of(row)}]"
    return row["model"]


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
    mixed = multi_driver_models(rows)
    g = group(rows, lambda r: (model_key(r, mixed), r["effort"]))
    data = []
    for (model, effort), rs in sorted(
            g.items(), key=lambda kv: (kv[0][0], EFFORT_ORDER.get(kv[0][1], 9))):
        # issue #12 (d). The denominator is the runs that produced a MEASUREMENT
        # of the model, not every row in the cell. A wall-clock timeout or an
        # infra fault is a distinct status, reported beside the rate and never
        # counted as a model failure -- the pre-registered bundle's rule, which
        # this table used to contradict by taking n = len(rs).
        #
        # Not cosmetic on the local stack: under PARALLEL=4 a neighbour's prefill
        # starved a decode to 0.05 tok/s, a 380x wall-clock swing on identical
        # work. Counting that as task difficulty lets the SCHEDULER grade the
        # model, and the high-harness arms carry the largest prompts, so the bias
        # runs one way along the dose ladder.
        scored, excluded = run_status.partition_for_rate(rs)
        n = len(scored)
        passes = sum(1 for r in scored if r.get("pass"))
        # Quality gates on BOTH `pass` and a clean exit (ticket 34): a truncated
        # run's judged score describes a truncated run.
        q = mean([qual.get(r["run_id"]) for r in scored
                  if r.get("pass") and corpus_gates.summarizable(r)])
        # An empty denominator has not measured a 0% pass rate; it has measured
        # nothing, and 0% is the most misleading thing it could print.
        rate = pct(passes, n) if n else "no measured runs"
        data.append([model, effort, n, passes, rate,
                     run_status.format_excluded(excluded) or "-",
                     fnum(q, 2) if q is not None else "-"])
    return md_table(
        ["model", "effort", "n", "pass", "pass_rate", "excluded",
         "avg_quality(/10)"], data)


def table2_efficiency_frontier(rows):
    # ticket 32: this is the one table that pools token counts ACROSS families,
    # i.e. across instruments -- `codex exec` one-shots vs `claude -p` agentic
    # sessions. Each cell says which instrument produced it (the recorded field
    # via corpus_gates, never `turns`), and mixing modes in one table is said
    # out loud below rather than left for a reader to reconstruct from t13.
    mixed = multi_driver_models(rows)
    g = group(rows, lambda r: (model_key(r, mixed), r["effort"]))
    data = []
    table_modes = set()
    for (model, effort), rs in sorted(
            g.items(), key=lambda kv: (kv[0][0], EFFORT_ORDER.get(kv[0][1], 9))):
        # Same denominator as table1, from the same predicate. Before this, five
        # tables took n = len(rs) while table1 took the scored set, so one corpus
        # published two different pass rates for the same runs.
        scored, _excl = run_status.partition_for_rate(rs)
        n = len(scored)
        passes = sum(1 for r in scored if r.get("pass"))
        # The token axis moves with it: tokens_out from a truncated run measures
        # where the run was cut off, not what the tier chose to spend --
        # ladder_from_results.py makes that argument for the same reason.
        spend, _dropped = token_rows(rs)
        tot = [out_tokens(r) for r in spend]
        tpp = (sum(tot) / passes) if passes else None
        # The MODE column is not a pass-rate question -- it reports which
        # instrument produced this cell's rows, and that is true of a row
        # whatever its exit status. Gating it on `scored` would blank the column
        # for a cell whose runs all timed out, which is precisely when a reader
        # most needs to know what produced them.
        cell_modes = sorted({corpus_gates.invocation_mode_of(r) for r in rs})
        table_modes.update(cell_modes)
        data.append([model, effort, "/".join(cell_modes), pct(passes, n),
                     fnum(mean(tot)),
                     fnum(tpp) if tpp is not None else "inf (0 pass)"])
    out = md_table(
        ["model", "effort", "mode", "pass_rate", "mean_tokens_out/run",
         "tokens_out_per_pass"], data)
    if len(table_modes) > 1:
        out += ("\n> **mixed invocation modes** (ticket 32): this table pools "
                + " and ".join(f"`{m}`" for m in sorted(table_modes))
                + " rows — different instruments, not one measurement. Whether "
                "the pooled comparison is publishable is tickets 03/20's "
                "ruling; this note only makes the mixing visible.\n")
    return out


def table3_harness_delta(rows):
    mixed = multi_driver_models(rows)
    rows = [dict(r, model=model_key(r, mixed)) for r in rows]
    models = sorted({r["model"] for r in rows})
    data = []
    for model in models:
        cell = {}
        for tag, hv in (("bare", False), ("harness", True)):
            rs = [r for r in rows if r["model"] == model and bool(r.get("harness")) == hv]
            scored, _excl = run_status.partition_for_rate(rs)
            n = len(scored)
            passes = sum(1 for r in scored if r.get("pass"))
            toks = mean([out_tokens(r) for r in token_rows(rs)[0]])
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
        ["model", "bare pass%", "bare tok_out", "harness pass%", "harness tok_out",
         "delta pass", "delta tok_out"], data)


def table4_hybrid_vs_solo(rows):
    t3 = [r for r in rows if str(r.get("task", "")).startswith("t3")]
    if not t3:
        return "_(no data - no T3 runs present in results)_\n"
    # issue #25: a model that ran under more than one driver (e.g. a T3 task
    # run once under claude-code and once under pi) gets its own row per
    # driver, the same split table1 already applies -- pi has no hooks and no
    # subagents, so pooling it with claude-code into one hybrid/solo line
    # would average two different vehicles into one number.
    mixed = multi_driver_models(t3)
    g = group(t3, lambda r: model_key(r, mixed))
    data = []
    for model, rs in sorted(g.items()):
        scored, _excl = run_status.partition_for_rate(rs)
        n = len(scored)
        passes = sum(1 for r in scored if r.get("pass"))
        toks = mean([out_tokens(r) for r in token_rows(rs)[0]])
        kind = "hybrid" if model == "hybrid" else "solo"
        data.append([model, kind, n, pct(passes, n), fnum(toks)])
    return md_table(["model", "kind", "n", "pass_rate", "mean_tokens_out"], data)


def table5_variance(rows):
    mixed = multi_driver_models(rows)
    g = group(rows, lambda r: (model_key(r, mixed), r["effort"],
                               "harness" if r.get("harness") else "bare", r["task"]))
    data = []
    for (model, effort, htag, task), rs in sorted(g.items()):
        # Variance is measured over the runs that produced a measurement. A
        # timeout's loc_changed and tokens_out describe where it was cut off, so
        # including them measures the scheduler's spread, not the model's.
        rs, _excl = run_status.partition_for_rate(rs)
        if len(rs) < 2:
            continue  # variance needs repeated cells
        passes = sum(1 for r in rs if r.get("pass"))
        spend, _dropped = token_rows(rs)
        locs = [r.get("loc_changed", 0) for r in spend]
        toks = [out_tokens(r) for r in spend]
        data.append([
            f"{model}/{effort}/{htag}", task, len(rs), f"{passes}/{len(rs)}",
            f"{min(locs)}/{round(statistics.median(locs))}/{max(locs)}",
            f"{min(toks):,}/{round(statistics.median(toks)):,}/{max(toks):,}",
        ])
    return md_table(
        ["cell (model/effort/harness)", "task", "reps", "passed",
         "loc min/med/max", "tokens_out min/med/max"], data)


def table6_decision_matrix(rows, qual, ledger=None):
    ledger = {} if ledger is None else ledger
    mixed = multi_driver_models(rows)
    rows = [dict(r, model=model_key(r, mixed)) for r in rows]
    models = sorted({r["model"] for r in rows})
    data = []
    dropped_note = []
    for model in models:
        # best config = highest pass rate, tiebreak lowest mean tokens
        g = group([r for r in rows if r["model"] == model],
                  lambda r: (r["effort"], "harness" if r.get("harness") else "bare"))
        best, best_key = None, None
        for key, rs in g.items():
            # "Best config" is chosen BY pass rate, so an ungated denominator
            # here does not just misreport a number -- it picks a different
            # winner. A config whose runs timed out most often would have looked
            # worst on exactly the axis that is not the model's.
            rs, _excl = run_status.partition_for_rate(rs)
            n = len(rs)
            passes = sum(1 for r in rs if r.get("pass"))
            rate = passes / n if n else 0
            toks = mean([out_tokens(r) for r in token_rows(rs)[0]]) or 0
            score = (rate, -toks)
            if best is None or score > best:
                best, best_key = score, (key, rate, toks, rs)
        (effort, htag), rate, toks, rs = best_key

        # The input column is the ONE cell that needs the other axis, so it may
        # not quietly fall back to the tokens_out-only axis the other five tables
        # use. Rows whose input cannot be resolved are dropped WITH their count;
        # a cell that lost every row says unavailable and why, because an empty
        # average and a real one must not render alike. No money renders here:
        # a price is the reader's to compute (ticket 20, option C), and it needs
        # the fresh/cache-read split, which a single mean cannot carry.
        resolved = []
        for r in rs:
            tin = resolve_tokens_in(r, ledger)
            if tin is None:
                continue
            resolved.append(tin)
        n_cell, n_resolved = len(rs), len(resolved)
        if n_resolved == 0:
            tin_cell = "unavailable"
            dropped_note.append(
                f"`{model}`: 0 of {n_cell} rows in the winning cell have a true "
                f"`tokens_in` (all quarantined pre-fix)")
        elif n_resolved < n_cell:
            tin_cell = f"{fnum(mean(resolved))} (n={n_resolved}/{n_cell})"
            dropped_note.append(
                f"`{model}`: resolved over {n_resolved} of {n_cell} rows; "
                f"{n_cell - n_resolved} dropped for want of a true `tokens_in`")
        else:
            tin_cell = fnum(mean(resolved))

        q = mean([qual.get(r["run_id"]) for r in rs
                  if r.get("pass") and corpus_gates.summarizable(r)])
        rate_pct = 100.0 * rate
        # An empty denominator is not a 0% pass rate. This table turns the number
        # into ADVICE ("not yet reliable here"), so rendering 0% for a cell whose
        # every run timed out would recommend against a model on the strength of
        # a measurement nobody took. Same rule as table1's "no measured runs".
        if not rs:
            rate_cell, use = "no measured runs", "unmeasured — no basis to advise"
        else:
            rate_cell = f"{rate_pct:.0f}%"
            if rate_pct >= 90:
                use = "reliable — default choice for this class"
            elif rate_pct >= 50:
                use = "usable with harness / review gate"
            else:
                use = "not yet reliable here"
        data.append([
            model, f"{effort}/{htag}", rate_cell,
            fnum(q, 2) if q is not None else "-",
            tin_cell, use,
        ])
    note = ("\n\n> `input tokens/task` is each row's TRUE cache-inclusive input "
            "— measured on the row, or joined from `usage.jsonl` by `run_id` for "
            "`recovered_in_ledger` rows — never the pre-fix `tokens_in`; counts "
            "are 0 under `--mock`. **No price renders here** (ticket 20, option "
            "C): a price is computed from a rate the reader supplies, and that "
            "computation must rate cache reads separately from fresh input — "
            "86–94% of these tokens are cache reads billed at roughly a tenth, "
            "so one flat rate times this column overstates 4.4×–6.5×.\n")
    if dropped_note:
        note += ("\n> **rows dropped from `input tokens/task`**: "
                 + "; ".join(dropped_note) + ".\n")
    return md_table(
        ["model", "best config", "pass_rate", "avg_quality(/10)",
         "input tokens/task (true)", "when to use"], data) + note


def build_report(results, judgments, ledger=None, turn_cap_n=None,
                 turn_cap_n_source="flag"):
    # Amendment A3, applied ONCE, here, before any table sees a row -- same
    # posture as corpus_gates/run_status below: one call site, not one per
    # table. This module takes no config, so N (the registered turn cap)
    # arrives as `turn_cap_n`, and main() resolves it from --turn-cap-n or,
    # falling back, from --config's defaults.turn_cap_n (issue #19 round 2:
    # one source of truth, not two that can silently disagree) --
    # `turn_cap_n_source` names which one won, "flag" by default for a
    # direct-argument caller that never went through that resolution (e.g. a
    # test). turn_cap_n=None (the default, and the only state before the
    # conductor registers N) makes apply_turn_cap a no-op -- the positive
    # control: every table below sees exit_reason exactly as `results`
    # already carried it.
    # Reclassifying `exit_reason` itself (rather than threading a second
    # predicate through every table) is what makes this ONE call site correct
    # for both axes without touching table1-6's bodies: corpus_gates.summarizable
    # and run_status.status_class both key off row["exit_reason"], so a row
    # turn_cap re-classes out of the token axis (§2,3,5,6) the same way it
    # re-classes out of the pass axis (§1) and every other table.
    turns_missing = sum(1 for r in results if r.get("turns") is None)
    results = run_status.apply_turn_cap(results, turn_cap_n)
    qual = quality_by_run(judgments)
    # The headline count is the ESTIMAND's, not every row's. It used to read
    # "N run row(s), P passing" with P taken over the whole corpus, so the
    # sentence a reader meets first disagreed with every table beneath it.
    scored, excl_status = run_status.partition_for_rate(results)
    n_pass = sum(1 for r in scored if r.get("pass"))

    # AC#4: count the subjects out loud, in the header, before any table. A
    # filtered corpus and an unfiltered one must not render identically, and a
    # corpus of zero rows must not render as a clean one.
    kept_s, excl_s = corpus_gates.summarizable_rows(results)
    kept_t, excl_t = corpus_gates.tokens_in_rows(results)
    n_recoverable = sum(1 for r in results if corpus_gates.tokens_in_recoverable(r))

    parts = [
        "# model-gauntlet results",
        "",
        f"Source: {len(results)} run row(s), "
        + (f"{n_pass} passing of {len(scored)} scored" if scored
           else "no measured runs (every row left the denominator)")
        + f", {len(judgments)} judged.",
        "",
        (("> **vehicle contrast** — this corpus contains more than one DRIVER "
          "for the same model, so those rows are reported as separate lines "
          "(`model [driver]`) and never pooled: "
          + "; ".join(f"`{m}`: {', '.join(ds)}"
                      for m, ds in sorted(multi_driver_models(results).items()))
          + ". pi has no hooks and no subagents, so the driver is part of the "
            "treatment, not a detail of how it was delivered.\n")
         if multi_driver_models(results) else ""),
        "> **estimand** (issue #12 d) — a pass rate counts only runs that "
        "produced a measurement of the model. Timeouts, infra faults and "
        "structurally-impossible cells are distinct statuses, excluded from "
        "every denominator above and reported here, never as model failures: "
        + (run_status.format_excluded(excl_status) or "nothing excluded")
        + f" (scored={len(scored)} of {len(results)}).",
        "",
        "> **turn cap** (amendment A3, issue #19) — turn_cap_n="
        + (f"{turn_cap_n} (source={turn_cap_n_source})" if turn_cap_n is not None
           else "unset")
        + (f", turns_missing={turns_missing} (no `turns` field, not capped)"
           if turn_cap_n is not None else "")
        + ".",
        "",
        "> **dispositions** (ticket 31 AC#4 / ticket 34) — "
        + corpus_gates.format_exclusions(
            "quality means", len(results), kept_s, excl_s)
        + ". "
        + corpus_gates.format_exclusions(
            "input tokens on the row", len(results), kept_t, excl_t)
        + f"; {n_recoverable} of the excluded are recoverable from `usage.jsonl` "
          "by `run_id` and reach table 6's input column through that join. "
          "Token columns elsewhere report `tokens_out` only.",
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
        "## 6. When-to-use-which decision matrix + true input tokens/task", "",
        table6_decision_matrix(results, qual, ledger),
    ]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet table generator")
    ap.add_argument("--results", default=os.path.join(RUNNER_DIR, "results", "results.jsonl"))
    ap.add_argument("--judgments", default=os.path.join(RUNNER_DIR, "results", "judgments.jsonl"))
    ap.add_argument("--usage", default=os.path.join(RUNNER_DIR, "results", "usage.jsonl"),
                    help="ledger joined by run_id for recovered input tokens")
    ap.add_argument("--out", default=None, help="write markdown here (default: stdout)")
    ap.add_argument("--turn-cap-n", type=int, default=None,
                    help="amendment A3's registered turn cap N, overriding "
                         "--config's defaults.turn_cap_n; rows with turns > N "
                         "are excluded as exit_reason turn_cap. Neither this "
                         "nor the config set (the default) is the positive "
                         "control: behaviour is unchanged from before A3.")
    ap.add_argument("--config", default=runner_mod.DEFAULT_TURN_CAP_CONFIG,
                    help="sweep config this reader falls back to for "
                         "defaults.turn_cap_n when --turn-cap-n is not given "
                         "(issue #19 round 2: one source of truth for N, not "
                         "two that can silently disagree)")
    args = ap.parse_args()

    results = load_jsonl(args.results)
    judgments = load_jsonl(args.judgments)
    ledger = usage_ledger.recovered_tokens_in(args.usage)
    if not results:
        print(f"no results found at {args.results}", file=sys.stderr)
    n, n_source = runner_mod.resolve_turn_cap_n(args.config, args.turn_cap_n)
    report = build_report(results, judgments, ledger, n, n_source)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
