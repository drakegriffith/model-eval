"""test_token_axis_gate.py -- the TOKEN columns belong on corpus_gates, not on
the pass-rate gate.

WHAT WAS OPEN. PR #17 routed every published pass rate through
`run_status.in_denominator`, and in five of the six tables it moved the token
columns onto that same set. `in_denominator` counts `cap_exhausted` as SCORED --
correctly, because a model that spent its K revisions and did not converge did
get a fair attempt. But its `tokens_out` records where the BROKER cut generation
off, not what the tier chose to spend.

So tables 2, 3, 5 and 6 averaged truncated outputs into "what this model spends",
while `ladder_from_results.tiers_for` and `stats.section_cost_matched` excluded
exactly those rows. One corpus, two published spend means. The verifier's probe:
three 1000-token runs plus one 10-token cap_exhausted run rendered as **1000 via
ladder_from_results and 752 via table2**, and table2's own comment cited
ladder_from_results as its authority while the code below it did the opposite.

WHY IT IS LIVE. `runner/runs-glm-stage1.yaml` ships `k_acceptance: 20`, so
cap_exhausted is a status stage 1 can actually produce. The bias is directional
and flattering in the wrong way: truncated runs are short, so pooling them drags
GLM's spend DOWN on the cost axis -- the money chart -- making the model look
cheaper than it is, and most on the arms that do the most work.

THE RULE, stated once so both axes can be checked against it:

    pass columns    run_status.in_denominator   did the model get a fair attempt
    token columns   corpus_gates.summarizable   is this session truncated

`cap_exhausted` is the one status where they differ: IN the pass denominator,
OUT of the token means.
"""
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import corpus_gates  # noqa: E402
import ladder_from_results as ladder  # noqa: E402
import run_status  # noqa: E402
import tables  # noqa: E402

# The verifier's corpus: three clean 1000-token runs, one cap_exhausted run
# truncated at 10 tokens. The declared spend mean is 1000; the ungated mean is
# (1000*3 + 10) / 4 = 752.5.
UNGATED_MEAN = 752
DECLARED_MEAN = 1000


def probe_rows(model="glm-4.7-local", effort="high", task="t1-py-a"):
    rows = []
    for i, (reason, tok, passed) in enumerate([
            ("ok", 1000, True), ("ok", 1000, True), ("ok", 1000, False),
            ("cap_exhausted", 10, False)]):
        rows.append({
            "run_id": f"s--{model}--{effort}--bare--{task}--r{i}",
            "sweep": "s", "model": model, "model_id": model, "effort": effort,
            "task": task, "rep": i, "harness": False, "driver": "claude-code",
            "pass": passed, "exit_reason": reason,
            "status_class": run_status.status_class(reason),
            "tokens_in": 100, "tokens_out": tok, "wall_s": 1.0,
            "loc_changed": 10, "turns": 1, "tokens_in_status": "measured",
            "invocation_mode": "multi_turn",
        })
    return rows


# --------------------------------------------------------------------------- #
# The corpus separates the two gates
# --------------------------------------------------------------------------- #
def test_the_probe_corpus_can_tell_the_two_gates_apart():
    """A control on the FIXTURE. cap_exhausted is the only status where the two
    predicates disagree, so a corpus without one cannot detect this bug at all --
    which is exactly why the 268-row archive renders identically either way."""
    rows = probe_rows()
    scored, _ = run_status.partition_for_rate(rows)
    summarizable = [r for r in rows if corpus_gates.summarizable(r)]

    assert len(scored) == 4 and len(summarizable) == 3
    assert round(sum(r["tokens_out"] for r in scored) / len(scored)) == UNGATED_MEAN
    assert (sum(r["tokens_out"] for r in summarizable)
            / len(summarizable)) == DECLARED_MEAN


def test_ladder_already_publishes_the_declared_mean():
    """The reference the other readers have to agree with. Unchanged by this
    commit -- it is what tables.py was already citing and contradicting."""
    tiers = ladder.tiers_for(probe_rows())

    assert dict(tiers)["high"] == [1000, 1000, 1000]


# --------------------------------------------------------------------------- #
# Every tables.py token column
# --------------------------------------------------------------------------- #
def test_table2_mean_tokens_out_excludes_the_truncated_run():
    """The verifier's exact probe: 1000 via ladder, 752 via table2."""
    out = tables.table2_efficiency_frontier(probe_rows())

    assert str(UNGATED_MEAN) not in out, (
        f"table2 still averages the truncated run into spend:\n{out}")
    assert "1,000" in out or "1000" in out, out


def test_table3_harness_token_column_excludes_the_truncated_run():
    out = tables.table3_harness_delta(probe_rows())

    assert str(UNGATED_MEAN) not in out, out


def test_table4_token_column_excludes_the_truncated_run():
    rows = [dict(r, task="t3-a") for r in probe_rows()]

    out = tables.table4_hybrid_vs_solo(rows)

    assert str(UNGATED_MEAN) not in out, out


def test_table5_token_spread_excludes_the_truncated_run():
    """A truncated run is the minimum of the spread by construction, so leaving
    it in makes the variance column describe the broker."""
    out = tables.table5_variance(probe_rows())

    # Assert on the TOKEN column specifically. `10/` alone also matches the loc
    # column, which legitimately reads 10/10/10 in this fixture -- a looser
    # assertion here passed for the wrong reason.
    token_cell = out.strip().splitlines()[-1].split("|")[-2].strip()
    assert token_cell == "1,000/1,000/1,000", (
        f"the 10-token truncated run is in the spread: {token_cell!r}")


def test_table6_token_column_excludes_the_truncated_run():
    out = tables.table6_decision_matrix(probe_rows(), {})

    assert str(UNGATED_MEAN) not in out, out


# --------------------------------------------------------------------------- #
# The exclusion is disclosed, not silent
# --------------------------------------------------------------------------- #
def test_the_report_discloses_the_truncated_rows_dropped_from_spend():
    """'Excluded and reported separately' applies to this axis too. A spend mean
    that quietly drops a row is a cleaner-looking number over a smaller corpus."""
    out = tables.build_report(probe_rows(), {})

    assert "cap_exhausted=1" in out, out


# --------------------------------------------------------------------------- #
# Controls: the pass axis must NOT move
# --------------------------------------------------------------------------- #
def test_the_pass_rate_still_counts_the_capped_run():
    """The control that keeps this from becoming the opposite bug. cap_exhausted
    is a scored failure -- pre-registration amendment A1
    (docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, registered
    2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip at >= 10 requests) --
    so the pass denominator is 4, not 3, even though the token mean is over 3."""
    out = tables.table1_effort_ladder(probe_rows(), {})

    assert "| 4 |" in out, f"the capped run left the pass denominator:\n{out}"
    assert "50%" in out


def test_a_corpus_without_truncated_runs_is_unchanged():
    """The no-op control: with nothing to exclude, the token mean is what it
    always was, so no archived number is restated."""
    rows = [r for r in probe_rows() if r["exit_reason"] == "ok"]

    out = tables.table2_efficiency_frontier(rows)

    assert "1,000" in out or "1000" in out
