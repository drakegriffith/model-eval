"""test_timeout_estimand.py -- issue #12 (d): a cap-terminated run is not a
model failure.

TWO DOCUMENTS SAID OPPOSITE THINGS, and the runner implemented the older one.

run.py's resolve_timeout_s docstring said:

    Cap-terminated runs score as FAILURES under the pre-registration's
    estimand: a mis-sized cap does not show up as a timeout in the analysis,
    it shows up as task difficulty.

The pre-registered bundle in ~/studio-handoff (PR #9, later-authored,
panel-reviewed) says the opposite, in prompt-2-run-experiment.md:43-44:

    Timeouts and infra errors are distinct statuses, excluded from the
    denominator and reported separately, never counted as model failures.

and findings.md auto-assert rule 7, which serving_registry implements:

    wall-clock timeouts log a distinct status, never a task fail.

WHY IT IS NOT COSMETIC ON THIS STACK. Prefill runs 57-71 tok/s; a 61k-token
prefill was clocked at 1077 s; under PARALLEL=4 a neighbour's prefill starved a
decode to 0.05 tok/s -- a 380x wall-clock swing on identical work. Under the
older reading that swing lands in the accuracy column, so THE SCHEDULER GRADES
THE MODEL. The high-harness arms carry the largest prompts, so the bias is not
even random across the dose ladder: it manufactures exactly the "L5 looks
worse" result the experiment is trying to measure.

WHAT THIS FILE PINS. A distinct status class on every row, an explicit
denominator predicate, and the fact that the readers' denominator really does
drop the excluded rows -- counted, not assumed.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import ladder_from_results as ladder  # noqa: E402
import run as runner  # noqa: E402

from test_pass_completeness_gate import execute  # noqa: E402
from test_pass_completeness_gate import repo  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #
def test_a_clean_run_is_scored():
    assert runner.termination_class("ok") == "scored"


def test_a_wall_clock_timeout_is_its_own_excluded_class():
    """Its own class, not merged with infra: a timeout says the cap was too
    small for this prompt on this serving config, and the remedy is a turn cap
    derived from the measured prefill rate (serving_registry.derive_turn_cap_s),
    not a re-run."""
    assert runner.termination_class("timeout") == "excluded_timeout"


@pytest.mark.parametrize("reason", ["cli_error", "broker_failed",
                                    "auth_unavailable", "kimi_key_missing",
                                    "no_completion"])
def test_an_instrument_fault_is_excluded_as_infra(reason):
    """An instrument that could not log in, a counter that faulted, a stream
    that stopped mid-tool-call: none of these is a model that tried the task
    and failed it."""
    assert runner.termination_class(reason) == "excluded_infra"


def test_the_acceptance_cap_is_still_scored_and_is_not_a_timeout():
    """The one termination the pre-registration DOES score. Section 7: a run
    that spent its K acceptance requests was ended by the protocol, not by the
    instrument, and re-running it would be retry-until-pass. Kept apart from
    the wall clock deliberately -- both are called "the cap" in prose and they
    are different experiments' caps."""
    assert runner.termination_class("cap_exhausted") == "scored"


@pytest.mark.parametrize("reason", ["mock", "mock_fail", "mock_patch_failed"])
def test_a_mock_run_is_excluded_under_its_own_name_not_as_infra(reason):
    """A mock run applies solution.patch and calls no model. Filing it under
    infra would put the runner's own smoke tests in the fault bucket and make
    that count unreadable."""
    assert runner.termination_class(reason) == "excluded_mock"


def test_an_unheard_of_reason_is_excluded_rather_than_scored():
    """By default, not by enlistment. A reason nobody has classified cannot be
    asserted to be a model failure, and the fail-closed direction here is to
    leave it out of the denominator and report it. This goes red the moment
    someone rewrites the rule as membership in a list of known-bad reasons."""
    assert runner.termination_class("gpu_fell_out_of_the_rack") == "excluded_infra"


def test_only_scored_rows_are_in_the_pass_denominator():
    assert runner.in_pass_denominator({"exit_reason": "ok"}) is True
    assert runner.in_pass_denominator({"exit_reason": "cap_exhausted"}) is True
    assert runner.in_pass_denominator({"exit_reason": "timeout"}) is False
    assert runner.in_pass_denominator({"exit_reason": "cli_error"}) is False


def test_a_row_with_no_exit_reason_is_not_silently_scored():
    """Silence is not evidence: an unstamped row is a row nobody
    dispositioned, which is not the same thing as a clean one."""
    assert runner.in_pass_denominator({}) is False


# --------------------------------------------------------------------------- #
# The status reaches the row, through the real execute_run
# --------------------------------------------------------------------------- #
def test_a_timed_out_run_carries_the_excluded_status_on_its_row(repo, monkeypatch):
    row = execute(repo, monkeypatch, solve=True, rc=0, force_reason="timeout")

    assert row["exit_reason"] == "timeout"
    assert row["status_class"] == "excluded_timeout"
    assert runner.in_pass_denominator(row) is False


def test_a_clean_run_carries_the_scored_status(repo, monkeypatch):
    """The negative control. A row-stamping change that wrote
    "excluded_timeout" everywhere would satisfy the assertion above."""
    row = execute(repo, monkeypatch, solve=True, rc=0)

    assert row["exit_reason"] == "ok"
    assert row["status_class"] == "scored"
    assert runner.in_pass_denominator(row) is True


def test_the_grader_verdict_on_a_timed_out_run_survives_as_pass_raw(
        repo, monkeypatch):
    """`pass` stays gated by ticket 34 -- an incomplete run may not claim
    success -- and the estimand is carried by status_class instead. Both facts
    are on the row, so neither reading of "did it pass" has to be reconstructed
    from the other."""
    row = execute(repo, monkeypatch, solve=True, rc=0, force_reason="timeout")

    assert row["pass"] is False
    assert row["pass_raw"] is True, (
        "the grader's own verdict must survive, or the exclusion is unauditable")


# --------------------------------------------------------------------------- #
# The denominator, counted rather than assumed
# --------------------------------------------------------------------------- #
def corpus(tmp_path):
    """Five runs of one cell: two passes, one honest fail, one timeout, one
    infra fault."""
    rows = [
        {"run_id": "s--m--high--bare--t2-py-a--r1", "sweep": "s", "model": "m",
         "effort": "high", "task": "t2-py-a", "tokens_out": 100,
         "pass": True, "exit_reason": "ok", "status_class": "scored"},
        {"run_id": "s--m--high--bare--t2-py-a--r2", "sweep": "s", "model": "m",
         "effort": "high", "task": "t2-py-a", "tokens_out": 110,
         "pass": True, "exit_reason": "ok", "status_class": "scored"},
        {"run_id": "s--m--high--bare--t2-py-a--r3", "sweep": "s", "model": "m",
         "effort": "high", "task": "t2-py-a", "tokens_out": 120,
         "pass": False, "exit_reason": "ok", "status_class": "scored"},
        {"run_id": "s--m--high--bare--t2-py-a--r4", "sweep": "s", "model": "m",
         "effort": "high", "task": "t2-py-a", "tokens_out": 5,
         "pass": False, "exit_reason": "timeout",
         "status_class": "excluded_timeout"},
        {"run_id": "s--m--high--bare--t2-py-a--r5", "sweep": "s", "model": "m",
         "effort": "high", "task": "t2-py-a", "tokens_out": 5,
         "pass": False, "exit_reason": "cli_error",
         "status_class": "excluded_infra"},
    ]
    path = tmp_path / "results.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return str(path), rows


def test_the_denominator_is_three_of_five_and_the_two_are_named(tmp_path):
    """The load-bearing arithmetic. Under the old reading the pass rate is
    2/5 = 40%; under the pre-registration it is 2/3 = 67%, and the two excluded
    runs are reported separately by class rather than dropped in silence."""
    _path, rows = corpus(tmp_path)

    scored = [r for r in rows if runner.in_pass_denominator(r)]
    excluded = runner.excluded_by_class(rows)

    assert len(scored) == 3
    assert sum(1 for r in scored if r["pass"]) == 2
    assert excluded == {"excluded_timeout": 1, "excluded_infra": 1}


def test_the_readers_denominator_agrees_with_the_predicate(tmp_path):
    """Independent confirmation from a reader that was written before this
    change: ladder_from_results keeps only complete runs and prints what it
    dropped. If the two ever disagreed, the number in a published table would
    not be the number this predicate describes."""
    path, _rows = corpus(tmp_path)

    kept, excluded = ladder.load_rows(path, sweep=None, model=None,
                                      passing_only=False)

    assert len(kept) == 3
    assert dict(excluded) == {"timeout": 1, "cli_error": 1}


def test_the_docstring_no_longer_asserts_the_discarded_reading():
    """The sentence that said the opposite is gone, and the replacement names
    the bundle it now follows -- so the next reader does not have to discover
    that two documents disagreed."""
    doc = runner.resolve_timeout_s.__doc__

    assert "score as FAILURES" not in doc
    assert "shows up as task difficulty" not in doc
    assert "denominator" in doc
