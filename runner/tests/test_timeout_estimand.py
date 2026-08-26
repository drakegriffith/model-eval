"""test_timeout_estimand.py -- issue #12 (d): a run killed by the wall-clock cap
is not a model failure, and must leave the pass-rate denominator.

THE TWO READINGS. run.py's resolve_timeout_s docstring stated one:

    Cap-terminated runs score as FAILURES under the pre-registration's estimand:
    a mis-sized cap does not show up as a timeout in the analysis, it shows up
    as task difficulty.

The pre-registered bundle in ~/studio-handoff states the other, and it is the
later-authored, panel-reviewed document (PR #9):

    Timeouts and infra errors are distinct statuses, excluded from the
    denominator and reported separately, never counted as model failures.

findings.md auto-assert rule 7 agrees ("wall-clock timeouts log a distinct
status, never a task fail"), and so does issue #8's surviving design. The bundle
wins; the docstring was the local reading and is corrected.

WHY IT IS NOT COSMETIC ON THIS STACK. Prefill runs 57-71 tok/s, a 61k-token
prefill was clocked at 1077 s, and under PARALLEL=4 a neighbour's prefill starved
a decode to 0.05 tok/s -- a 380x wall-clock swing on identical work. Under the
old reading that swing lands in the accuracy column, so the SCHEDULER grades the
model. And the bias is not random across the dose ladder: the high-harness arms
carry the largest prompts, so it manufactures exactly the "L5 looks worse" result
the experiment is trying to measure.

WHAT IS NOT CHANGED, deliberately: `cap_exhausted`. That is the BROKER's K
acceptance cap, and pre-registration amendment A1
(docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, registered
2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip at >= 10 requests) scores
it a failure on purpose -- the model spent its acceptance requests and did not
converge, which is a fact about the model. It is a different cap from the wall clock,
and merging the two would smuggle a protocol treatment out of the denominator.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run_status  # noqa: E402
import tables  # noqa: E402


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exit_reason,expected", [
    ("ok", run_status.SCORED),
    # The broker's K cap: a protocol treatment, scored, per pre-registration
    # amendment A1 (docs/studio-handoff/prompt-2-run-experiment.md at
    # a0cef36, registered 2026-08-25: K=20, cap_exhausted SCORED, stage-0
    # flip at >= 10 requests).
    ("cap_exhausted", run_status.SCORED),
    # The wall clock: the instrument ran out of patience, not the model.
    ("timeout", run_status.TIMEOUT),
    ("cli_error", run_status.INFRA),
    ("auth_unavailable", run_status.INFRA),
    ("broker_failed", run_status.INFRA),
    ("no_completion", run_status.INFRA),
    ("kimi_key_missing", run_status.INFRA),
    ("structurally_impossible", run_status.STRUCTURAL),
])
def test_each_exit_reason_lands_in_its_declared_class(exit_reason, expected):
    assert run_status.status_class(exit_reason) == expected


def test_a_composite_reason_takes_the_worse_of_its_parts():
    """run.py appends '+verify_timeout' to whatever reason it already had, so
    'ok+verify_timeout' is a real value in the corpus. A grader that timed out
    produced no verdict, which is an infra fact, not a model fact."""
    assert run_status.status_class("ok+verify_timeout") == run_status.INFRA
    assert run_status.status_class("timeout+verify_timeout") == run_status.TIMEOUT


def test_an_unknown_reason_is_unclassified_rather_than_assumed_either_way():
    """Fail-closed in BOTH directions. Defaulting an unknown reason to scored
    re-creates this bug for the next status somebody adds; defaulting it to
    excluded silently inflates the pass rate. Cannot-determine is its own answer
    and gets reported by name."""
    assert run_status.status_class("some_new_reason") == run_status.UNCLASSIFIED
    assert not run_status.in_denominator({"exit_reason": "some_new_reason"})


def test_a_row_with_no_exit_reason_is_unclassified():
    """An unstamped row is a row nobody dispositioned, which is not the same
    thing as a clean one."""
    assert run_status.status_class(None) == run_status.UNCLASSIFIED
    assert not run_status.in_denominator({})


@pytest.mark.parametrize("exit_reason,counts", [
    ("ok", True), ("cap_exhausted", True),
    ("timeout", False), ("cli_error", False),
    ("structurally_impossible", False), ("weird", False),
])
def test_the_denominator_predicate_matches_the_vocabulary(exit_reason, counts):
    assert run_status.in_denominator({"exit_reason": exit_reason}) is counts


# --------------------------------------------------------------------------- #
# The reader: pass_rate over the scored rows only
# --------------------------------------------------------------------------- #
def rows_for(reasons, model="glm-4.7-local", effort="high"):
    out = []
    for i, (reason, passed) in enumerate(reasons):
        out.append({"run_id": f"s--{model}--{effort}--bare--t1-py-a--r{i}",
                    "model": model, "effort": effort, "task": "t1-py-a",
                    "pass": passed, "exit_reason": reason,
                    "tokens_in": 10, "tokens_out": 10,
                    "tokens_in_status": "measured"})
    return out


def test_a_timed_out_run_leaves_the_pass_rate_denominator():
    """THE regression. Four runs, three clean and all passing, one killed by the
    wall clock. The old denominator says 75%; the estimand says 100% of the runs
    that produced a measurement passed, and one timed out."""
    rows = rows_for([("ok", True), ("ok", True), ("ok", True),
                     ("timeout", False)])

    out = tables.table1_effort_ladder(rows, {})

    assert "| 3 |" in out, f"denominator is not 3:\n{out}"
    assert "100" in out, f"pass_rate did not reach 100%:\n{out}"
    assert "75" not in out, f"the timeout is still in the denominator:\n{out}"


def test_the_excluded_runs_are_reported_rather_than_disappearing():
    """'Excluded from the denominator and reported separately'. Dropping them
    silently would turn a stack problem into a smaller, cleaner-looking table --
    the one outcome worse than counting them wrong."""
    rows = rows_for([("ok", True), ("timeout", False), ("cli_error", False)])

    out = tables.table1_effort_ladder(rows, {})

    assert "timeout" in out
    assert "infra" in out or "cli_error" in out


def test_a_cell_whose_every_run_timed_out_reports_no_rate_rather_than_zero():
    """Silence is not evidence, and neither is 0%. A cell with an empty
    denominator has not measured a 0% pass rate; it has measured nothing, and
    0% is the single most misleading thing it could print."""
    rows = rows_for([("timeout", False), ("timeout", False)])

    out = tables.table1_effort_ladder(rows, {})

    assert "0%" not in out, f"an empty denominator printed as 0%:\n{out}"
    assert "2" in out, "the excluded count is not shown"


def test_the_broker_cap_stays_in_the_denominator():
    """The control. cap_exhausted is the K acceptance cap, which pre-registration
    amendment A1 (docs/studio-handoff/prompt-2-run-experiment.md at a0cef36,
    registered 2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip at >= 10
    requests) scores as a failure -- the model spent its acceptance requests
    and did not converge. If the fix swept that out too, a real model failure
    would have been laundered into an infra note."""
    rows = rows_for([("ok", True), ("cap_exhausted", False)])

    out = tables.table1_effort_ladder(rows, {})

    assert "| 2 |" in out, f"cap_exhausted left the denominator:\n{out}"
    assert "50" in out


def test_a_failing_but_complete_run_stays_in_the_denominator():
    """The other control, and the one that matters most: this fix must not make
    failures vanish. A run that finished cleanly and did not pass IS a model
    failure and stays."""
    rows = rows_for([("ok", True), ("ok", False)])

    out = tables.table1_effort_ladder(rows, {})

    assert "| 2 |" in out
    assert "50" in out


# --------------------------------------------------------------------------- #
# The runner stamps the class on the row it writes
# --------------------------------------------------------------------------- #
def test_the_runner_records_the_status_class_on_every_row(tmp_path):
    """Stamped by the code that ran, so a reader never has to re-derive the
    disposition from a reason string it may not know."""
    sys.path.insert(0, RUNNER_DIR)
    import run as runner

    results = tmp_path / "results.jsonl"
    runner.record_structurally_impossible(
        {"run_id": "s--glm-4.7-local--high--bare--t1-py-a--r1", "sweep": "s",
         "model": "glm-4.7-local", "effort": "high", "harness": False,
         "harness_level": 5, "driver": "pi", "task": "t1-py-a", "rep": 1},
        "driver pi tops out at harness level 2", str(results))

    row = json.loads(results.read_text(encoding="utf-8").strip())
    assert row["status_class"] == run_status.STRUCTURAL
    assert row["pass"] is None
    assert not run_status.in_denominator(row)


def test_the_docstring_no_longer_asserts_the_discarded_reading():
    """run.py:resolve_timeout_s stated the opposite rule in prose. Prose that
    contradicts the estimand is how the next person re-introduces it."""
    sys.path.insert(0, RUNNER_DIR)
    import run as runner

    doc = runner.resolve_timeout_s.__doc__

    # The old sentence may still APPEAR -- naming the discarded option is how the
    # next reader learns it was considered and rejected, rather than overlooked.
    # What must not survive is it standing as the rule.
    if "shows up as task difficulty" in doc:
        assert "discarded reading" in doc, (
            "the old rule is quoted but not marked as discarded, so it still "
            "reads as this function's own statement of the estimand")
    # The rule that replaces it has to be stated, and stated as the bundle does.
    assert "excluded from the denominator" in doc
    assert "never counted as model failures" in doc
    # And it must still make the case for declaring a cap per tier -- that part
    # of the docstring was right and is why the function fails closed.
    assert "t4" in doc or "tier" in doc
