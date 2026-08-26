"""test_estimand_readers.py -- the estimand has to reach the READERS, not just
the row.

WHAT THE VERIFIER CAUGHT. The first pass of this branch defined run_status,
stamped `status_class` on every row run.py writes, and routed exactly ONE
pass-rate site through it (tables.table1_effort_ladder). Six other published
pass rates -- tables 2, 3, 4, 5, 6 and build_report's own header line -- still
computed `sum(pass) / len(rs)` over every row in the cell, timeouts included.
ladder_from_results gated on `exit_reason == "ok"`, which drops cap_exhausted,
a status this branch deliberately declares SCORED.

A rule stamped on a row and honoured by one of seven readers is not a rule; it
is a field. So every pass-rate denominator now comes from the one predicate, and
every reader reports what left it.

THE TWO AXES, because they are genuinely different questions and collapsing them
is what makes this confusing:

    run_status.in_denominator     may this row enter a PASS RATE?
                                  -> "did the model get a fair attempt"
    corpus_gates.summarizable     may this row's NUMBERS be published?
                                  -> "is this a truncated session"

They disagree on exactly one status, and the disagreement is correct.
`cap_exhausted` is a model measurement -- the model spent its K revisions and did
not converge, which pre-registration section 7 scores as a failure -- whose token
counts describe a truncated session. So it is IN the pass denominator and OUT of
the token means. Any test that only exercises ok/timeout/cli_error cannot tell
the two rules apart, because those three agree under both; the corpus below
includes cap_exhausted and structurally_impossible precisely so it can.

THE PROBE CORPUS is the verifier's, unchanged: 2 ok-pass, 1 ok-fail, 1 timeout,
1 cli_error, 1 cap_exhausted, 1 structurally_impossible.

    denominator (SCORED)  = 3 ok + 1 cap_exhausted        = 4
    passes                = 2
    declared pass rate    = 2/4 = 50%
    excluded              timeout=1 infra=1 structurally_impossible=1

Three rules, three different answers, which is the point of choosing this corpus:

    ungated  (the old bug)     2/7 = 29%
    summarizable only          2/3 = 67%
    the declared estimand      2/4 = 50%
"""
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import corpus_gates  # noqa: E402
import ladder_from_results as ladder  # noqa: E402
import run_status  # noqa: E402
import stats  # noqa: E402
import tables  # noqa: E402

MODEL = "glm-4.7-local"
EFFORT = "high"

# (exit_reason, pass) -- the verifier's probe corpus.
PROBE = [
    ("ok", True),
    ("ok", True),
    ("ok", False),
    ("timeout", False),
    ("cli_error", False),
    ("cap_exhausted", False),
    ("structurally_impossible", None),
]

DECLARED_DENOMINATOR = 4
DECLARED_PASSES = 2
DECLARED_RATE_PCT = 50


def probe_rows(model=MODEL, effort=EFFORT, harness=False, task="t1-py-a"):
    rows = []
    for i, (reason, passed) in enumerate(PROBE):
        rows.append({
            "run_id": f"s--{model}--{effort}--bare--{task}--r{i}",
            "sweep": "s", "model": model, "model_id": model, "effort": effort,
            "task": task, "rep": i, "harness": harness, "mode": "solo",
            "pass": passed, "exit_reason": reason,
            "status_class": run_status.status_class(reason),
            "tokens_in": 100, "tokens_out": 100, "wall_s": 1.0,
            "loc_changed": 10, "turns": 1,
            "tokens_in_status": "measured",
            "invocation_mode": "multi_turn",
        })
    return rows


# --------------------------------------------------------------------------- #
# The corpus separates the three candidate rules
# --------------------------------------------------------------------------- #
def test_the_probe_corpus_actually_distinguishes_the_rules():
    """A control on the FIXTURE, not on the code. The previous version of this
    test used a corpus of ok/timeout/cli_error only -- three statuses on which
    the old rule and the new one agree -- so it would have passed against the
    unfixed readers. If this assertion ever fails, the corpus has stopped being
    able to detect the bug and every test below is decorative."""
    rows = probe_rows()
    ungated = sum(1 for r in rows if r.get("pass")) / len(rows)
    summarizable_only = [r for r in rows if corpus_gates.summarizable(r)]
    scored, _ = run_status.partition_for_rate(rows)

    assert round(ungated * 100) == 29
    assert round(100 * sum(1 for r in summarizable_only if r["pass"])
                 / len(summarizable_only)) == 67
    assert round(100 * sum(1 for r in scored if r["pass"]) / len(scored)) == 50
    # And the two predicates must genuinely disagree on this corpus.
    assert len(scored) != len(summarizable_only)


def test_cap_exhausted_is_the_row_the_two_predicates_disagree_on():
    """Named explicitly, because it is the whole reason the two gates cannot be
    collapsed into one."""
    row = {"exit_reason": "cap_exhausted"}

    assert run_status.in_denominator(row) is True
    assert corpus_gates.summarizable(row) is False


# --------------------------------------------------------------------------- #
# tables.py -- every published pass rate
# --------------------------------------------------------------------------- #
def test_table1_publishes_the_declared_denominator():
    out = tables.table1_effort_ladder(probe_rows(), {})

    assert f"| {DECLARED_DENOMINATOR} |" in out, out
    assert "50" in out
    assert "29" not in out and "67" not in out


@pytest.mark.parametrize("fn", ["table2_efficiency_frontier",
                                "table3_harness_delta",
                                "table4_hybrid_vs_solo",
                                "table5_variance",
                                "table6_decision_matrix"])
def test_every_other_table_uses_the_same_denominator(fn):
    """The blocking finding. Five tables computed sum(pass)/len(rs) over every
    row in the cell, so a timeout was published as a model failure in five
    places while table1 said something different about the same runs."""
    rows = probe_rows()
    func = getattr(tables, fn)
    out = func(rows, {}) if fn in ("table6_decision_matrix",) else func(rows)

    assert "29" not in out, f"{fn} still counts every row in the denominator:\n{out}"
    assert "2/7" not in out and "/7" not in out, (
        f"{fn} published a denominator of 7:\n{out}")


def test_the_report_header_counts_the_estimand_not_every_row():
    rows = probe_rows()

    out = tables.build_report(rows, {})

    assert "7 run row(s)" in out, "the manifest must still say how many arrived"
    assert f"{DECLARED_PASSES} passing of {DECLARED_DENOMINATOR}" in out, out


def test_the_report_names_every_excluded_status_and_its_count():
    """'Excluded from the denominator and reported separately' is one
    instruction. Dropping the rows quietly just makes a cleaner-looking table."""
    out = tables.build_report(probe_rows(), {})

    for fragment in ("timeout=1", "infra=1", "structurally_impossible=1"):
        assert fragment in out, f"the report never says {fragment}:\n{out}"


def test_a_corpus_with_no_scored_rows_says_so_rather_than_printing_zero():
    rows = [r for r in probe_rows() if r["exit_reason"] in
            ("timeout", "cli_error", "structurally_impossible")]

    out = tables.build_report(rows, {})

    assert "0%" not in out
    assert "no measured runs" in out or "0 of 0" in out


# --------------------------------------------------------------------------- #
# ladder_from_results.py
# --------------------------------------------------------------------------- #
def test_the_ladders_pass_rate_uses_the_estimand_denominator(tmp_path):
    """ladder gated everything on exit_reason == 'ok', which drops
    cap_exhausted -- a status this branch declares SCORED. Its TOKEN axis is
    right to exclude it (a capped run's tokens_out measures where it was cut
    off), so the two axes get their own denominators and the block reports
    both."""
    block = ladder.report_block("probe", probe_rows())

    assert block["pass_rate"] == round(DECLARED_PASSES / DECLARED_DENOMINATOR, 2)
    assert block["n_scored"] == DECLARED_DENOMINATOR


def test_the_ladder_still_excludes_truncated_rows_from_the_token_axis(tmp_path):
    """The control that must not move: tokens_out from a truncated run measures
    where the run was cut off, not what the tier chose to spend."""
    tiers = ladder.tiers_for(probe_rows())
    samples = sum(len(v) for _t, v in tiers)

    assert samples == 3, (
        f"the token ladder took {samples} samples; only the 3 cleanly-exited "
        f"runs may contribute a tokens_out figure")

    # load_rows keeps a row when EITHER axis can use it, so the capped run
    # survives to the pass rate while tiers_for still refuses its tokens.
    path = tmp_path / "results.jsonl"
    import json
    with open(path, "w", encoding="utf-8") as f:
        for r in probe_rows():
            f.write(json.dumps(r) + "\n")

    kept, excluded = ladder.load_rows(str(path), None, None, False)

    assert sorted(r["exit_reason"] for r in kept) == [
        "cap_exhausted", "ok", "ok", "ok"]
    assert excluded.get("timeout") == 1
    assert excluded.get("cli_error") == 1
    assert excluded.get("structurally_impossible") == 1


def test_the_ladders_printed_table_shows_both_denominators_and_they_differ(
        tmp_path, capsys, monkeypatch):
    """Issue #21 (1). The printed table used to pair a token-axis `n` with a
    pass_rate computed over `n_scored` and never printed `n_scored` at all --
    a rendering gap only, since report_block's own dict always carried both
    keys. This fixture's one cap_exhausted row is scored (counts toward
    n_scored) but not summarizable (excluded from the token axis's n_tok), so
    the two printed numbers must both appear and must differ -- proving the
    reader can no longer mistake one for the other."""
    path = tmp_path / "results.jsonl"
    import json
    with open(path, "w", encoding="utf-8") as f:
        for r in probe_rows():
            f.write(json.dumps(r) + "\n")

    monkeypatch.setattr(sys, "argv", ["ladder_from_results.py",
                                       "--results", str(path)])
    ladder.main()
    out = capsys.readouterr().out

    assert "n_tok" in out and "n_scr" in out, (
        f"both denominators must be labelled, not just printed:\n{out}")
    data_line = [ln for ln in out.splitlines() if ln.startswith("t1-py-a")][0]
    n_tok = int(data_line.split()[8])
    n_scr = int(data_line.split()[9])
    assert n_tok != n_scr, (
        f"n_tok and n_scr must differ on a corpus with a cap_exhausted row "
        f"(scored but not summarizable): {data_line!r}")
    assert n_scr == DECLARED_DENOMINATOR


# --------------------------------------------------------------------------- #
# stats.py
# --------------------------------------------------------------------------- #
def test_the_statistical_appendix_gates_the_pass_axis_on_the_estimand():
    """stats.py applied corpus_gates.summarizable once, globally, which dropped
    cap_exhausted from every exact test. Under the declared estimand a capped
    run is a scored failure and belongs in the Wilson denominator."""
    out = stats.build_report(probe_rows(), [])

    assert f"{DECLARED_PASSES}/{DECLARED_DENOMINATOR}" in out, out


def test_the_statistical_appendix_reports_what_it_excluded():
    out = stats.build_report(probe_rows(), [])

    assert "timeout" in out and "structurally_impossible" in out


def test_the_cost_section_still_refuses_truncated_token_counts():
    """The control for the change above: admitting cap_exhausted to the pass axis
    must not admit its tokens to the cost axis."""
    rows = probe_rows()

    kept = [r for r in rows if corpus_gates.summarizable(r)]

    assert len(kept) == 3
    assert all(r["exit_reason"] == "ok" for r in kept)


# --------------------------------------------------------------------------- #
# No reader may keep a private copy of the rule
# --------------------------------------------------------------------------- #
def test_every_reader_module_routes_through_the_shared_predicate():
    """The structural guard, stated as a POSITIVE assertion.

    An earlier draft of this test grepped for `sum(... r.get("pass") ...)` over
    `rs` and called every hit an offender. That heuristic is unsound: after the
    fix, several sites rebind `rs` to the scored set first, so the correct code
    matches the offending pattern and a passing site reports as a defect. A
    checker that cannot tell the fix from the bug is worse than no checker --
    it trains the next reader to silence it.

    What can be asserted honestly is that every module publishing a rate imports
    and calls the one predicate. The DENOMINATORS themselves are pinned
    behaviourally by the probe-corpus tests above, which is where a seventh
    ungated site would actually show up."""
    inspected = []
    for name in ("tables.py", "stats.py", "ladder_from_results.py"):
        with open(os.path.join(RUNNER_DIR, name), encoding="utf-8") as f:
            source = f.read()
        inspected.append(name)
        assert "import run_status" in source, f"{name} does not import the predicate"
        assert ("run_status.partition_for_rate" in source
                or "run_status.in_denominator" in source), (
            f"{name} imports the predicate but never calls it")
    assert inspected == ["tables.py", "stats.py", "ladder_from_results.py"]
