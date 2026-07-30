"""test_pass_completeness_gate.py — ticket 34. `pass` may not claim success for
a run that did not finish.

WHAT WAS OPEN. `run.py` graded every run unconditionally and then special-cased
exactly two exit reasons: `cap_exhausted` (forced False, raw verdict kept as
`pass_at_cap`) and `verify_timeout` (forced False). `cli_error` got neither, so
the corpus carries `sweep2b--fable--medium--bare--t3-a--r1` with
`exit_reason=cli_error` and `pass=true`. The doctrine was never in question --
`exit_reason == "ok"` is the completeness gate (`existing_ids`,
`ladder_from_results.py`) -- the defect was that the field which reads most like
a verdict was the one exempt from it.

WHAT IS ASSERTED HERE. Every test below runs the real `execute_run` against a
stand-in task; nothing stubs the grader. Each gate assertion is paired with a
negative control on the SAME fixture differing only in exit code, per the
conclusive-A/B rule: a suite where the gate is asserted but the pass case is not
would go green against a `pass = False` constant.

The by-default half (AC#2) is the load-bearing one. `test_an_unheard_of_exit_
reason_is_gated_without_being_enlisted` uses a reason no source file in this
repo has ever mentioned. It goes red the moment someone rewrites the rule as
membership in a list of known-bad reasons, which is the same silent default one
reason further on.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import judge  # noqa: E402
import run as runner  # noqa: E402

# num_turns is non-zero on purpose. run.py:1116 reclassifies an "ok" run that
# completed zero turns to `no_completion`, which is itself non-"ok" -- so a
# zero-turn stub would drag the negative control into the gated branch and the
# control would prove nothing.
RESULT_EVENT = json.dumps({
    "type": "result", "num_turns": 3,
    "usage": {"input_tokens": 100, "output_tokens": 50,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
})

VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail
test "$(cat answer.txt 2>/dev/null)" = "42"
echo "1 passed in 0.01s"
"""


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout, minimal: the grader passes iff answer.txt is 42."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-gate"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("write 42 to answer.txt", encoding="utf-8")
    (task_dir / "verify.sh").write_text(VERIFY_SH, encoding="utf-8")
    (root / ".scratch").mkdir(parents=True)
    return {"root": str(root), "task_dir": str(task_dir), "tmp": str(tmp_path)}


def stub_cli(solve, rc):
    """A stand-in CLI that optionally solves the task, then exits `rc`.

    Writing answer.txt inside the scratch tree is the model's own working copy,
    which is what write containment (ticket 26) allows.
    """
    body = (
        "import sys\n"
        f"solve = {solve!r}\n"
        "if solve:\n"
        "    open('answer.txt', 'w').write('42')\n"
        f"print({RESULT_EVENT!r})\n"
        f"sys.exit({rc})\n"
    )
    return [sys.executable, "-c", body]


def execute(repo, monkeypatch, solve=True, rc=0, force_reason=None):
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setattr(runner, "RUNNER_DIR", os.path.join(repo["tmp"], "runner"))
    # Ticket 37 moved this constant out of usage_ledger (a core module may not
    # know this repo's layout) and into run.py, which is the caller that owns it.
    # The redirect targets the instrument now, not the ledger.
    monkeypatch.setattr(runner, "USAGE_PATH",
                        os.path.join(repo["tmp"], "usage.jsonl"))
    monkeypatch.setattr(runner, "build_cli_cmd",
                        lambda *a, **kw: stub_cli(solve, rc))
    if force_reason is not None:
        real = runner.run_cli

        def relabel(*a, **kw):
            out, _reason, wall = real(*a, **kw)
            return out, force_reason, wall

        monkeypatch.setattr(runner, "run_cli", relabel)
    run = {"run_id": "gate--probe--r1", "sweep": "gate", "model": "claude-haiku-4-5",
           "effort": "low", "harness": False, "task": "t-gate", "rep": 1,
           "mode": "solo"}
    cfg = {"defaults": {"k_acceptance": 10, "timeout_t1_t2_s": 300,
                        "timeout_default_s": 300}}
    return runner.execute_run(run, cfg, os.path.join(repo["root"], "tasks"),
                              os.path.join(repo["root"], ".scratch"),
                              os.path.join(repo["tmp"], "results.jsonl"))


# --------------------------------------------------------------------------- #
# Negative control. Without this the gate assertions below are satisfied by a
# runner that scores every run a failure.
# --------------------------------------------------------------------------- #
def test_a_clean_run_whose_grader_passes_lands_pass_true(repo, monkeypatch):
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert row["exit_reason"] == "ok"
    assert row["pass"] is True, "the gate is scoring clean runs as failures"
    assert row["pass_raw"] is True


def test_a_clean_run_whose_grader_fails_lands_pass_false(repo, monkeypatch):
    """The other direction of the control: `pass` still tracks the grader when
    the run is complete. A gate that forced True would pass the test above."""
    row = execute(repo, monkeypatch, solve=False, rc=0)
    assert row["exit_reason"] == "ok"
    assert row["pass"] is False
    assert row["pass_raw"] is False


# --------------------------------------------------------------------------- #
# The gate.
# --------------------------------------------------------------------------- #
def test_cli_error_with_a_passing_grader_is_scored_a_failure(repo, monkeypatch):
    """The exact shape of the corpus row that prompted the ticket: the CLI died,
    the grader was still run and still said pass, and the row claimed success."""
    row = execute(repo, monkeypatch, solve=True, rc=3)
    assert row["exit_reason"] == "cli_error"
    assert row["pass"] is False, "an incomplete run may not claim success"
    assert row["pass_raw"] is True, "the grader's own verdict was discarded"


def test_an_unheard_of_exit_reason_is_gated_without_being_enlisted(repo, monkeypatch):
    """AC#2, and the reason this file exists rather than one more cli_error case.

    `reactor_scrammed` appears in no list, table or branch anywhere in this repo.
    It is gated because it is not "ok". Rewrite the rule as membership in a set
    of known-bad reasons and this goes red while every other test here stays
    green -- which is the whole point: the enumerated version is indistinguishable
    from the correct one until the next reason is added, and then it is a silent
    default again.
    """
    row = execute(repo, monkeypatch, solve=True, rc=0,
                  force_reason="reactor_scrammed")
    assert row["exit_reason"] == "reactor_scrammed"
    assert row["pass"] is False
    assert row["pass_raw"] is True


def test_the_cap_case_keeps_its_own_field_and_its_own_name(repo, monkeypatch):
    """Generalizing the rule must not quietly retire `pass_at_cap`: every row
    written before this ticket carries it and pre-registration section 7 names
    it. On a cap row it now equals pass_raw; everywhere else it stays None, so
    nothing that reads it starts seeing cap semantics on non-cap rows."""
    capped = execute(repo, monkeypatch, solve=True, rc=0,
                     force_reason="cap_exhausted")
    assert capped["pass"] is False
    assert capped["pass_at_cap"] is True
    assert capped["pass_raw"] is True


def test_pass_at_cap_stays_none_on_a_non_cap_failure(repo, monkeypatch):
    row = execute(repo, monkeypatch, solve=True, rc=3)
    assert row["exit_reason"] == "cli_error"
    assert row["pass_at_cap"] is None, "a cli_error row is not a capped row"


def test_pass_raw_is_none_when_the_grader_never_returned(repo, monkeypatch):
    """A grader that timed out did not say False -- it said nothing. Recording
    that as False would make `pass_raw` unable to answer the question it exists
    to answer, which is what the grader actually observed."""
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="verify.sh", timeout=1)

    monkeypatch.setattr(runner, "run_verify", boom)
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert row["exit_reason"].endswith("+verify_timeout")
    assert row["pass"] is False
    assert row["pass_raw"] is None


# --------------------------------------------------------------------------- #
# The consumer side (AC#3). Only the selector is gated in code; the summarizing
# readers inherit the write-time gate. See runner/PASS-FIELD-AUDIT.md.
# --------------------------------------------------------------------------- #
def test_the_judge_selector_skips_incomplete_runs_it_used_to_pick_up(tmp_path):
    """Written against the historical row shape, not the post-fix one: a
    pre-ticket-34 row carries cli_error AND pass=true, and AC#5 leaves it that
    way. The gate has to hold on the bytes that are actually on disk."""
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"run_id": "clean--r1", "exit_reason": "ok", "pass": True},
        {"run_id": "graded-fail--r1", "exit_reason": "ok", "pass": False},
        {"run_id": "legacy-cli-error--r1", "exit_reason": "cli_error", "pass": True},
        {"run_id": "capped--r1", "exit_reason": "cap_exhausted",
         "pass": False, "pass_at_cap": True},
    ]), encoding="utf-8")

    ids = judge.passing_run_ids(str(p))

    assert "legacy-cli-error--r1" not in ids, "an incomplete run is not a subject"
    assert "capped--r1" not in ids, "pass_at_cap is not pi and is not a ticket to judge"
    assert "graded-fail--r1" not in ids
    assert ids == ["clean--r1"], "the selector stopped selecting anything at all"
