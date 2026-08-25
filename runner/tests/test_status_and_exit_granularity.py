"""test_status_and_exit_granularity.py -- two counts that were readable as one
thing while meaning another.

1. MOCK RUNS were classed INFRA. Both are excluded from the pass denominator, so
   the denominators were right, but the reported number was not: a `--mock`
   sweep filed 75 "infra faults", and "infra=75" is what a badly broken serving
   stack looks like. A mock run is not a fault at all -- it applies
   solution.patch and never calls a model. Giving it its own class keeps the
   infra count meaning what an operator reads it to mean, which is the only
   reason the separate report exists.

2. THE PRE-FLIGHT collapsed two different operator actions into exit 4.
   "glm-4.7 is not loaded" is fixed by loading the model; "I could not read
   `lms ps` at all" is fixed by starting LM Studio or fixing the path. A script
   gating stage 1 has to tell those apart to say anything useful, and a human
   reading one exit code cannot.

Neither changes a denominator. Both change what a reader is told about why.
"""
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
sys.path.insert(0, RUNNER_DIR)
import run_status  # noqa: E402
import serving_registry as sr  # noqa: E402

LIVE_MISMATCH = os.path.join(FIXTURES, "lms-ps-live-mismatch.txt")


# --------------------------------------------------------------------------- #
# 1. mock is its own class
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reason", ["mock", "mock_fail", "mock_patch_failed"])
def test_a_mock_run_is_not_filed_as_an_infra_fault(reason):
    assert run_status.status_class(reason) == run_status.MOCK
    assert run_status.status_class(reason) != run_status.INFRA


@pytest.mark.parametrize("reason", ["mock", "mock_fail", "mock_patch_failed"])
def test_a_mock_run_is_still_excluded_from_the_denominator(reason):
    """The control. Separating the COUNT must not quietly admit mock rows to a
    pass rate -- a mock pass is solution.patch applying, not a model succeeding,
    and it would read as a 100% autonomy rate."""
    assert not run_status.in_denominator({"exit_reason": reason})


def test_a_mock_sweep_does_not_report_itself_as_an_infra_outage():
    """The motivating case, in the number an operator actually reads."""
    rows = [{"exit_reason": "mock"} for _ in range(75)]

    _kept, excluded = run_status.partition_for_rate(rows)

    assert excluded == {run_status.MOCK: 75}
    assert run_status.INFRA not in excluded
    assert "infra" not in run_status.format_excluded(excluded)


def test_real_infra_faults_still_read_as_infra():
    """The other control: pulling mock out must not empty the class that exists
    to make a genuine outage visible."""
    rows = [{"exit_reason": "cli_error"}, {"exit_reason": "broker_failed"},
            {"exit_reason": "mock"}]

    _kept, excluded = run_status.partition_for_rate(rows)

    assert excluded[run_status.INFRA] == 2
    assert excluded[run_status.MOCK] == 1


def test_every_declared_class_is_reachable_from_some_exit_reason():
    """Silence is not evidence: a class nobody can reach is a label, not a
    disposition."""
    reachable = {run_status.status_class(r) for r in run_status.EXIT_REASON_CLASS}
    declared = {run_status.SCORED, run_status.TIMEOUT, run_status.INFRA,
                run_status.STRUCTURAL, run_status.MOCK}

    assert declared <= reachable, f"unreachable: {declared - reachable}"


# --------------------------------------------------------------------------- #
# 2. the pre-flight separates "not loaded" from "could not look"
# --------------------------------------------------------------------------- #
def test_the_exit_codes_are_all_distinct():
    codes = [0, sr.EXIT_PREFLIGHT_MISMATCH, sr.EXIT_PREFLIGHT_NOT_LOADED,
             sr.EXIT_PREFLIGHT_UNINSPECTABLE]

    assert len(set(codes)) == 4, f"two operator actions share an exit code: {codes}"


def test_a_model_that_is_not_loaded_has_its_own_code(tmp_path):
    """LM Studio is up and answering; this model simply is not in it. The fix is
    'load the model', which is not the same instruction as 'start LM Studio'."""
    header = open(LIVE_MISMATCH, encoding="utf-8").read().splitlines()[1]
    empty = tmp_path / "nothing-loaded.txt"
    empty.write_text("\n" + header + "\n\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "serving_registry.py"),
         "preflight", "--lms-output", str(empty)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)

    assert proc.returncode == sr.EXIT_PREFLIGHT_NOT_LOADED
    assert proc.returncode == 5
    assert "not loaded" in proc.stdout


def test_unreadable_output_keeps_the_uninspectable_code(tmp_path):
    """The other side of the split: `lms ps` that cannot be parsed at all is
    still 4, and is still not a pass."""
    junk = tmp_path / "junk.txt"
    junk.write_text("lms: command not found\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "serving_registry.py"),
         "preflight", "--lms-output", str(junk)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)

    assert proc.returncode == sr.EXIT_PREFLIGHT_UNINSPECTABLE
    assert proc.returncode == 4


def test_neither_new_code_is_a_pass():
    """The rule that made these codes distinct in the first place:
    could-not-determine is a result requiring a decision, never a quiet
    success."""
    for code in (sr.EXIT_PREFLIGHT_MISMATCH, sr.EXIT_PREFLIGHT_NOT_LOADED,
                 sr.EXIT_PREFLIGHT_UNINSPECTABLE):
        assert code != 0


def test_the_mismatch_code_is_unchanged_for_todays_state():
    """The regression guard: splitting 4 must not renumber 3, which is the code
    the stage-1 runbook already refers to."""
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "serving_registry.py"),
         "preflight", "--lms-output", LIVE_MISMATCH],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)

    assert proc.returncode == 3
