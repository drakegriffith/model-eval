"""test_report_acceptance.py -- issue #22's reporting requirement: the report
states max(acceptance_requests), its distribution, and the cap_exhausted
count beside the pass rate (A1, drakegriffith/model-eval@a0cef36).

The probe corpus below mixes every disposition the summary has to keep
distinct: two scored rows sharing one acceptance_requests value, one scored
row with no acceptance suite at all (None -- must be counted separately, not
folded into 0), one cap_exhausted row (SCORED, and the row this issue's count
exists to surface), and one excluded (timeout) row that must not enter any
of these numbers at all -- the summary shares run_status's SCORED
denominator with the pass rate it sits beside, not a second uncomparable
count over every row in the file.
"""
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import ladder_from_results as ladder  # noqa: E402
import report_acceptance  # noqa: E402
import run_status  # noqa: E402

MODEL = "glm-4.7-local"


def probe_rows():
    def row(rep, exit_reason, acc, passed):
        return {
            "run_id": f"acc--r{rep}", "sweep": "s", "model": MODEL,
            "model_id": MODEL, "effort": "high", "task": "t1-py-a",
            "rep": rep, "harness": False, "driver": "claude-code",
            "pass": passed, "exit_reason": exit_reason,
            "status_class": run_status.status_class(exit_reason),
            "acceptance_requests": acc,
            "tokens_in": 100, "tokens_out": 500, "wall_s": 1.0,
            "loc_changed": 5, "turns": 1, "tokens_in_status": "measured",
            "invocation_mode": "multi_turn",
        }
    return [
        row(0, "ok", 5, True),
        row(1, "ok", 5, True),
        row(2, "ok", None, False),          # scored, no acceptance suite
        row(3, "cap_exhausted", 20, False),  # scored, the count this issue wants
        row(4, "timeout", None, False),      # excluded -- must not enter any number
    ]


def test_acceptance_summary_over_the_scored_denominator():
    summary = report_acceptance.acceptance_summary(probe_rows())

    assert summary["n_scored"] == 4, "the timeout row must not enter the scored set"
    assert summary["max_acceptance_requests"] == 20
    assert summary["distribution"] == {5: 2, 20: 1}
    assert summary["no_acceptance_suite"] == 1
    assert summary["cap_exhausted_count"] == 1


def test_max_is_none_not_zero_when_nothing_ran_a_suite():
    """None means 'no acceptance suite ran', never a real 0 -- a 0 asserts the
    model made zero acceptance requests, which this corpus never measured."""
    rows = [{"exit_reason": "ok", "pass": True, "acceptance_requests": None}]

    summary = report_acceptance.acceptance_summary(rows)

    assert summary["max_acceptance_requests"] is None
    assert summary["no_acceptance_suite"] == 1
    assert summary["distribution"] == {}


def test_format_acceptance_summary_names_all_three_numbers():
    out = report_acceptance.format_acceptance_summary(
        report_acceptance.acceptance_summary(probe_rows()))

    assert "max: 20" in out, out
    assert "5=2" in out and "20=1" in out, out
    assert "no acceptance suite=1" in out, out
    assert "cap_exhausted: 1" in out, out


def test_wired_into_ladder_main_beside_the_pass_rate(tmp_path, capsys, monkeypatch):
    """Issue #22 says 'beside the pass rate' -- this checks it actually
    prints from ladder_from_results.py's main(), not just that the helper
    function exists and is never called."""
    import json
    path = tmp_path / "results.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in probe_rows():
            f.write(json.dumps(r) + "\n")

    monkeypatch.setattr(sys, "argv", ["ladder_from_results.py",
                                       "--results", str(path)])
    ladder.main()
    out = capsys.readouterr().out

    assert "acceptance_requests -- max: 20" in out, (
        f"the acceptance summary never reached main()'s output:\n{out}")
    assert "cap_exhausted: 1" in out, out
    assert "--passing-only" not in out, (
        f"the scope marker must not appear without the flag:\n{out}")


def test_passing_only_marks_the_acceptance_summarys_narrowed_scope(
        tmp_path, capsys, monkeypatch):
    """--passing-only makes load_rows() drop every non-passing row before
    `rows` reaches report_acceptance, so the printed max is a max over
    passing runs only -- not the corpus max a reader would assume without a
    flag they may not have typed themselves. The line must say so."""
    import json
    path = tmp_path / "results.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in probe_rows():
            f.write(json.dumps(r) + "\n")

    monkeypatch.setattr(sys, "argv", ["ladder_from_results.py",
                                       "--results", str(path), "--passing-only"])
    ladder.main()
    out = capsys.readouterr().out

    assert "acceptance_requests -- max:" in out, out
    assert "(scope: --passing-only rows)" in out, (
        f"the narrowed scope is not marked on the line:\n{out}")
    # The cap_exhausted row (pass=False) and the None-acceptance row
    # (pass=False) are both dropped by --passing-only, so this run's max is
    # 5 (the two passing rows), not 20 (the corpus max asserted above).
    assert "max: 5" in out, (
        f"expected the passing-only max (5), not the corpus max:\n{out}")
