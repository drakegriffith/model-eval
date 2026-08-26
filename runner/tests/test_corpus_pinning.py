"""test_corpus_pinning.py -- issues #23 and #24.

THE INCIDENT (issue #23's own words). Three separate times during the
2026-08-25 gate-wiring wave, a demo or verification run using the DEFAULT
--results/--usage paths appended synthetic rows (e.g.
dose--glm-4.7-local--...) to the real runner/results/results.jsonl /
usage.jsonl -- once turning the suite red with five corpus-pinning failures.
Each was caught uncommitted and restored by hand: the guard was human
attention.

THE SECOND DEFECT (issue #24). --results redirects results.jsonl but
execute_run wrote every usage row to usage_ledger.paths_for_repo(ROOT).usage --
a module-level constant baked in at import time -- so a --mock probe pointed
at a scratch --results still appended to the live token-accounting ledger.
Observed twice in one session (1 row, then 16), each disclosed and reverted by
hand.

WHAT THIS FILE PROVES, per the prior-art gate's own disposition ("a guard must
be shown refusing on a positive control, not assumed from a missing row"):

  - test_mock_*_against_default_*_is_refused_and_leaves_corpus_untouched: the
    REAL run.py, invoked against the REAL repo's default paths, refuses and
    leaves the archived corpus's results.jsonl / usage.jsonl byte-identical.
    This is the positive control -- the thing being guarded against is
    actually attempted, from outside the process under test, at the literal
    default path, not simulated.
  - test_mock_with_only_usage_left_at_default_is_still_refused: redirecting
    --results is not enough by itself to clear the guard if --usage still
    resolves to the live ledger -- the second half of the OR.
  - test_mock_with_both_paths_redirected_is_not_refused: the guard is not a
    blanket "no --mock ever", so a caller who names scratch paths for both
    files is let through (asserted via an --only filter that matches no run,
    so nothing downstream needs a real task).
  - test_execute_run_writes_usage_row_to_explicit_path_not_the_module_default
    and the _falls_back_ test below: execute_run's new usage_path parameter
    plumbs a caller-supplied path through to the ledger, and its default
    (unset) still reads the module-level USAGE_PATH exactly as every existing
    caller (test_write_containment.py, test_pass_completeness_gate.py,
    test_acceptance_broker.py) already relies on via monkeypatch --
    positional callers written before this ticket keep working unchanged.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

REAL_RESULTS = os.path.join(RUNNER_DIR, "results", "results.jsonl")
REAL_USAGE = os.path.join(RUNNER_DIR, "results", "usage.jsonl")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture
def corpus_untouched():
    """Assert the REAL archived corpus is byte-identical before and after.

    Presence is asserted, not assumed (INDEX.md's absence-is-not-evidence
    disposition): a hash of a missing file would compare equal to any amount
    of prior damage just as readily as to none.
    """
    assert os.path.exists(REAL_RESULTS), f"no corpus at {REAL_RESULTS}"
    assert os.path.exists(REAL_USAGE), f"no ledger at {REAL_USAGE}"
    before = (_sha(REAL_RESULTS), _sha(REAL_USAGE))
    yield
    after = (_sha(REAL_RESULTS), _sha(REAL_USAGE))
    assert after == before, (
        "the real corpus/ledger changed -- exactly the corruption issue #23 "
        "and #24 are about")


def run_cli_subprocess(args):
    return subprocess.run(
        [sys.executable, "run.py"] + args,
        cwd=RUNNER_DIR, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# #23: refuse-by-default, proven against the real repo's real default paths --
# the positive control. Both guards fire BEFORE the config is even read, so
# --only need not match a real run for these three.
# --------------------------------------------------------------------------- #
def test_mock_against_default_paths_is_refused_and_leaves_corpus_untouched(
        corpus_untouched):
    proc = run_cli_subprocess(["--mock", "--only", "no-such-run-id-xyz"])
    assert proc.returncode != 0
    assert "refus" in proc.stderr.lower()
    assert REAL_RESULTS.split(os.sep)[-1] in proc.stderr or REAL_RESULTS in proc.stderr


def test_mock_fail_against_default_paths_is_refused_and_leaves_corpus_untouched(
        corpus_untouched):
    """--mock-fail is sugar for GAUNTLET_MOCK=fail, a non-empty string and
    therefore truthy -- the guard must not special-case the pass/fail flavor
    of mock, since #23's incident rows were freely either."""
    proc = run_cli_subprocess(["--mock-fail", "--only", "no-such-run-id-xyz"])
    assert proc.returncode != 0
    assert "refus" in proc.stderr.lower()


def test_mock_with_only_usage_left_at_default_is_still_refused(
        corpus_untouched, tmp_path):
    """Redirecting --results alone is not sufficient: this is issue #24's own
    incident, a --mock probe with --results pointed elsewhere still reaching
    the live ledger. Proven by explicitly repointing --usage BACK at the live
    file while --results is redirected, and confirming the OR's second half
    still catches it."""
    scratch_results = tmp_path / "scratch-results.jsonl"
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz",
        "--results", str(scratch_results), "--usage", REAL_USAGE,
    ])
    assert proc.returncode != 0
    assert "refus" in proc.stderr.lower()
    assert not scratch_results.exists()


def test_mock_with_both_paths_redirected_is_not_refused(corpus_untouched, tmp_path):
    """The guard is not a blanket ban on --mock, only on --mock reaching the
    live paths. --only matches no run_id so the matrix has zero pending runs
    and nothing downstream needs a real task -- this isolates the guard
    decision itself from whether a run can complete."""
    scratch_results = tmp_path / "scratch-results.jsonl"
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz", "--results", str(scratch_results),
    ])
    assert proc.returncode == 0, proc.stderr
    assert "refus" not in proc.stderr.lower()
    assert "pending=0" in proc.stdout


# --------------------------------------------------------------------------- #
# #24: execute_run's usage_path plumbing, at the unit level. A fabricated task
# with a trivial verify.sh (`exit 0`) so the assertion is about which FILE the
# usage row lands in, not about grading.
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_task_repo(tmp_path):
    """A stand-in repo with one task solvable by mock apply, mirroring the
    `repo` fixtures in test_write_containment.py / test_home_isolation.py but
    with a real solution.patch (apply_mock() shells out to `git apply`, so the
    patch must be genuine, not hand-typed unified-diff prose)."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-corpus-pin"
    base = task_dir / "base"
    base.mkdir(parents=True)
    (base / "README.md").write_text("solve it\n", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("append 42\n", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                        encoding="utf-8")
    os.chmod(task_dir / "verify.sh", 0o755)

    env = dict(os.environ)
    subprocess.run(["git", "init", "-q"], cwd=base, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=base, env=env, check=True)
    subprocess.run(["git", "-c", "user.email=g@g", "-c", "user.name=gauntlet",
                    "commit", "-q", "-m", "base"], cwd=base, env=env, check=True)
    (base / "README.md").write_text("solve it\n42\n", encoding="utf-8")
    diff = subprocess.run(["git", "diff"], cwd=base, env=env,
                          capture_output=True, text=True, check=True).stdout
    shutil.rmtree(base / ".git")
    (base / "README.md").write_text("solve it\n", encoding="utf-8")
    (task_dir / "solution.patch").write_text(diff, encoding="utf-8")

    scratch_root = root / ".scratch"
    scratch_root.mkdir()
    return {"root": str(root), "tasks_dir": str(root / "tasks"),
            "scratch_root": str(scratch_root)}


def _run_mock(mock_task_repo, monkeypatch, tmp_path, results_name, usage_path):
    monkeypatch.setattr(runner, "ROOT", mock_task_repo["root"])
    monkeypatch.setattr(runner, "RUNNER_DIR",
                        os.path.join(mock_task_repo["root"], "runner"))
    monkeypatch.setenv("GAUNTLET_MOCK", "1")
    run = {"run_id": "pin--probe--r1", "sweep": "pin", "model": "claude-haiku-4-5",
           "effort": None, "harness": False, "task": "t-corpus-pin", "rep": 1,
           "mode": "solve"}
    cfg = {"defaults": {"timeout_default_s": 60}}
    results_path = str(tmp_path / results_name)
    args = [run, cfg, mock_task_repo["tasks_dir"], mock_task_repo["scratch_root"],
            results_path]
    if usage_path is not None:
        args.append(usage_path)
    return runner.execute_run(*args)


def test_execute_run_writes_usage_row_to_explicit_path_not_the_module_default(
        mock_task_repo, monkeypatch, tmp_path):
    decoy_usage = tmp_path / "decoy-usage.jsonl"
    monkeypatch.setattr(runner, "USAGE_PATH", str(decoy_usage))
    real_usage = tmp_path / "explicit-usage.jsonl"

    row = _run_mock(mock_task_repo, monkeypatch, tmp_path, "results-a.jsonl",
                    str(real_usage))

    assert row["exit_reason"] == "mock"
    assert os.path.exists(real_usage), "explicit usage_path was not written"
    assert not decoy_usage.exists(), (
        "the module-level USAGE_PATH was written even though an explicit "
        "usage_path was passed -- the explicit argument must win")


def test_execute_run_without_usage_path_falls_back_to_module_default(
        mock_task_repo, monkeypatch, tmp_path):
    """Every caller written before this ticket -- including the three test
    files this PR's own verify command runs -- calls execute_run positionally
    with five arguments. This is the direct proof that call shape still works
    and still lands the row where USAGE_PATH (module-patchable) says to."""
    default_usage = tmp_path / "default-usage.jsonl"
    monkeypatch.setattr(runner, "USAGE_PATH", str(default_usage))

    row = _run_mock(mock_task_repo, monkeypatch, tmp_path, "results-b.jsonl", None)

    assert row["exit_reason"] == "mock"
    assert os.path.exists(default_usage), (
        "execute_run(usage_path unset) must fall back to the module-level "
        "USAGE_PATH, the same constant every existing test monkeypatches")
    rows = [json.loads(l) for l in open(default_usage, encoding="utf-8") if l.strip()]
    assert [r["run_id"] for r in rows] == ["pin--probe--r1"]
