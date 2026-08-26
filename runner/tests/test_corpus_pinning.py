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
  - test_dry_run_without_mock_against_default_paths_is_refused: --dry-run is
    named in issue #23's own title, and is unguarded by GAUNTLET_MOCK checks
    alone because a structurally-impossible cell writes before the dry-run
    return.
  - test_mock_with_symlinked_default_dir_is_refused and
    test_mock_with_case_variant_default_path_is_refused: a verifier
    reproduced both as live bypasses of a naive os.path.abspath string
    compare (plus a doubled-leading-slash variant realpath already
    normalizes without special-casing). Path identity is inode identity --
    see corpus_guard.py.
  - test_mock_with_both_paths_redirected_is_not_refused: the guard is not a
    blanket "no --mock ever", so a caller who names scratch paths for both
    files is let through (asserted via an --only filter that matches no run,
    so nothing downstream needs a real task).
  - test_mock_with_both_paths_redirected_writes_exactly_one_row_only_to_scratch:
    the previous test proves the guard doesn't fire; this one proves a run
    that actually executes writes ONLY to the redirected scratch files, via a
    real (fabricated) task driven end to end through main().
  - test_execute_run_writes_usage_row_to_explicit_path_not_the_module_default
    and the _falls_back_ test below: execute_run's new usage_path parameter
    plumbs a caller-supplied path through to the ledger, and its default
    (unset) still reads the module-level USAGE_PATH exactly as every existing
    caller (test_write_containment.py, test_pass_completeness_gate.py,
    test_acceptance_broker.py) already relies on via monkeypatch --
    positional callers written before this ticket keep working unchanged.
  - test_judge_mock_against_default_paths_is_refused_and_leaves_corpus_untouched:
    the write-set extension issue #24 asks for ("any --mock or demo run") --
    judge.py calls the same corpus_guard.refusal_message as run.py.
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
import corpus_guard  # noqa: E402
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
    # Pinned to corpus_guard.REFUSE_EXIT (3), not just "nonzero": this repo's
    # own exit 2 already means "config rejected" elsewhere in run.py, and the
    # wave's harness separately reads exit 2 as could-not-inspect. A refusal
    # that regressed to exit 2 would look like one of those to the harness,
    # not like a decision this guard made on purpose.
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()
    assert REAL_RESULTS.split(os.sep)[-1] in proc.stderr or REAL_RESULTS in proc.stderr


def test_mock_fail_against_default_paths_is_refused_and_leaves_corpus_untouched(
        corpus_untouched):
    """--mock-fail is sugar for GAUNTLET_MOCK=fail, a non-empty string and
    therefore truthy -- the guard must not special-case the pass/fail flavor
    of mock, since #23's incident rows were freely either."""
    proc = run_cli_subprocess(["--mock-fail", "--only", "no-such-run-id-xyz"])
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()


def test_dry_run_without_mock_against_default_paths_is_refused(corpus_untouched):
    """Issue #23's title names 'demo/dry runs', not just --mock: a --dry-run
    invocation whose matrix has a structurally-impossible cell still calls
    record_structurally_impossible(), which writes to args.results BEFORE the
    dry-run early return. GAUNTLET_MOCK is never set here -- this is the half
    of the guard's OR condition that fires on --dry-run alone."""
    assert not os.environ.get("GAUNTLET_MOCK")
    proc = run_cli_subprocess(["--dry-run"])
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
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
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()
    assert not scratch_results.exists()


def test_mock_with_symlinked_default_dir_is_refused(corpus_untouched, tmp_path):
    """Positive control for a verifier-reproduced bypass: os.path.abspath
    string comparison does not see through a symlink, so a --results pointed
    through a symlinked copy of the live results/ directory sailed past the
    first cut of this guard and appended a real row (268 -> 269). The fix
    (corpus_guard.is_live_path) resolves both sides with os.path.realpath
    before comparing, which follows the symlink to the same live file."""
    link = tmp_path / "results-link"
    link.symlink_to(os.path.join(RUNNER_DIR, "results"), target_is_directory=True)
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz",
        "--results", str(link / "results.jsonl"),
    ])
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()


def test_mock_with_case_variant_default_path_is_refused(corpus_untouched, tmp_path):
    """Positive control for a second verifier-reproduced bypass: APFS (this
    machine's default filesystem) is case-insensitive but case-preserving, so
    'Results/results.jsonl' opens the SAME file as 'results/results.jsonl'
    while comparing unequal as a string. Skipped on a case-sensitive
    filesystem, where the premise (the candidate path even resolves to the
    live file) does not hold."""
    marker = tmp_path / "case-marker.txt"
    marker.write_text("x", encoding="utf-8")
    if not os.path.exists(str(marker).upper()):
        pytest.skip("filesystem is case-sensitive; this bypass does not apply")

    case_variant = os.path.join(RUNNER_DIR, "Results", "results.jsonl")
    assert os.path.exists(case_variant), "case-insensitive premise did not hold"
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz", "--results", case_variant,
    ])
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()


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


@pytest.fixture
def fabricated_matrix(tmp_path):
    """A complete, minimal matrix (runs.yaml + one task) so main() itself --
    not execute_run directly -- can be driven end to end through a real
    --mock run without touching the real tasks/ or runs.yaml. The task is
    solvable by `git apply` and graded by a one-line `exit 0`, so the run
    completes in well under a second: nothing here needs a venv or npm
    install the way the real tasks/*/verify.sh scripts do.
    """
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "t-corpus-pin"
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

    config = tmp_path / "runs.yaml"
    config.write_text(
        "defaults:\n"
        "  timeout_default_s: 60\n"
        "sweeps:\n"
        "  - name: pin\n"
        "    harness: false\n"
        "    reps: [1]\n"
        "    tasks: [t-corpus-pin]\n"
        "    configs:\n"
        "      - {model: claude-haiku-4-5, effort: low}\n",
        encoding="utf-8")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {"config": str(config), "tasks_dir": str(tasks_dir), "scratch": str(scratch)}


def test_mock_with_both_paths_redirected_writes_exactly_one_row_only_to_scratch(
        corpus_untouched, fabricated_matrix, tmp_path):
    """The pass-through test above proves the guard does not fire; it proves
    nothing about where a run that actually executes writes to, since --only
    matched zero runs there. This one drives a REAL mock run (real
    prepare_scratch, real git-apply, real verify.sh, real append_row/
    append_usage_row) through main() end to end, with --results/--usage both
    redirected, and asserts two things together: the live corpus gained
    nothing (corpus_untouched, byte-hash) and the scratch corpus gained
    exactly one row in each file -- "wrote the right file" and "wrote the
    right file AND NOTHING ELSE" are different claims, same distinction
    test_usage_ledger_portability.py's repo_untouched fixture draws.
    """
    scratch_results = tmp_path / "scratch-results.jsonl"
    scratch_usage = tmp_path / "scratch-usage.jsonl"
    proc = run_cli_subprocess([
        "--mock", "--config", fabricated_matrix["config"],
        "--tasks-dir", fabricated_matrix["tasks_dir"],
        "--scratch", fabricated_matrix["scratch"],
        "--results", str(scratch_results), "--usage", str(scratch_usage),
        "--only", "t-corpus-pin",
    ])
    assert proc.returncode == 0, proc.stderr
    assert "refus" not in proc.stderr.lower()

    for path in (scratch_results, scratch_usage):
        assert path.exists(), f"{path} was never written"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        assert len(rows) == 1, f"{path} has {len(rows)} row(s), expected exactly 1"
    assert json.loads(scratch_results.read_text().splitlines()[0])["exit_reason"] == "mock"


# --------------------------------------------------------------------------- #
# #28: a --scratch that resolves inside (or equal to) the live results
# directory. Distinct from #23/#24 above -- those guard --results/--usage
# reaching a live FILE; this guards --scratch reaching the live results
# DIRECTORY, since a run checkout left there is not itself a corpus write
# and so passes the #23/#24 guards untouched while still polluting
# runner/results/. The reproduction is issue #28's own words: a relative
# --scratch from cwd runner/ created a checkout inside the live results
# directory while results.jsonl/usage.jsonl were reported untouched.
# --------------------------------------------------------------------------- #
def test_relative_scratch_from_runner_cwd_into_results_is_refused_and_creates_no_checkout(
        corpus_untouched, tmp_path):
    """Issue #28's own reproduction, verbatim: cwd=runner/, `--scratch
    results/work` (relative). --results is redirected to a /tmp path so the
    pre-existing #23 guard (default --results resolving to the live corpus)
    cannot fire first and mask which guard actually refused -- round 1's
    verifier caught exactly this gap: this test (and the symlink test
    below) passed on a tree carrying no #28 guard at all, because #23's own
    "refusing: a mock/demo/dry-run invocation would write to the live
    results corpus" message fired first on the unredirected default.
    Absence is asserted, not assumed -- a directory that was never going to
    be created either way would make this assertion pass for the wrong
    reason; the companion control test below proves the same checkout DOES
    appear when --scratch is left outside results/, so this absence
    assertion is capable of catching a regression, not vacuously true."""
    checkout_root = os.path.join(RUNNER_DIR, "results", "work")
    assert not os.path.exists(checkout_root), "stale checkout from a prior failed run"
    try:
        proc = run_cli_subprocess([
            "--mock", "--only", "no-such-run-id-xyz", "--scratch", "results/work",
            "--results", str(tmp_path / "results.jsonl"),
        ])
        assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
        assert "refus" in proc.stderr.lower()
        assert "--scratch" in proc.stderr, (
            "refusal message must name --scratch -- otherwise this could "
            "be the #23 --results/--usage guard firing instead of #28's")
        assert not os.path.exists(checkout_root), (
            "run.py created a checkout inside the live results directory")
    finally:
        if os.path.exists(checkout_root):
            shutil.rmtree(checkout_root)


def test_scratch_outside_results_still_creates_a_checkout_control(
        fabricated_matrix, tmp_path):
    """Control for the absence assertion above (INDEX.md: a gate that
    inspected zero subjects failed). Identical fabricated matrix, but
    --scratch stays at its own tmp_path location, outside results/: the run
    proceeds (exit 0) and a checkout DOES land under --scratch, proving
    prepare_scratch really does create a directory here under --mock, so
    the previous test's "no checkout dir" assertion is a claim that could
    have failed."""
    scratch_results = tmp_path / "control-results.jsonl"
    proc = run_cli_subprocess([
        "--mock", "--config", fabricated_matrix["config"],
        "--tasks-dir", fabricated_matrix["tasks_dir"],
        "--scratch", fabricated_matrix["scratch"],
        "--results", str(scratch_results),
        "--only", "t-corpus-pin",
    ])
    assert proc.returncode == 0, proc.stderr
    entries = os.listdir(fabricated_matrix["scratch"])
    assert entries, (
        "no checkout directory appeared under --scratch -- the fixture's "
        "own assumption (prepare_scratch creates a dir per run) is broken, "
        "which would silently defeat the absence assertion above")


def test_symlinked_scratch_pointing_into_results_dir_is_refused(
        corpus_untouched, tmp_path):
    """Same bypass class test_mock_with_symlinked_default_dir_is_refused
    above proves for --results: a --scratch that is ITSELF a symlink whose
    target resolves inside the live results directory must not sail past
    an unresolved-path or string-prefix compare. The symlink target is a
    path nested one level inside results/ (not results/ itself), so this
    exercises the containment branch (os.path.commonpath), not just the
    equality branch. --results is redirected to a /tmp path for the same
    reason as the reproduction test above -- round 1's verifier caught
    this test passing with no #28 guard present at all, because the
    unredirected default --results tripped the pre-existing #23 guard
    first."""
    link = tmp_path / "scratch-into-results"
    link.symlink_to(os.path.join(RUNNER_DIR, "results", "nested-via-symlink"),
                    target_is_directory=True)
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz", "--scratch", str(link),
        "--results", str(tmp_path / "results.jsonl"),
    ])
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()
    assert "--scratch" in proc.stderr, (
        "refusal message must name --scratch -- otherwise this could be "
        "the #23 --results/--usage guard firing instead of #28's")


def _fs_is_case_insensitive(tmp_path):
    """Detect case-(in)sensitivity the way the coordinator's brief asks:
    create a file, then stat its case-flipped variant. True on this
    machine's default APFS volume; a caller must SKIP (not xfail) the
    case-variant tests below on a case-sensitive filesystem, where the
    bypass this guards against cannot even be constructed."""
    marker = tmp_path / "case-fs-marker.txt"
    marker.write_text("x", encoding="utf-8")
    return os.path.exists(str(marker).upper())


def test_case_variant_scratch_dir_name_from_runner_cwd_is_refused(
        corpus_untouched, tmp_path):
    """A verifier's reproduction (round 2): `is_inside_or_same`'s first cut
    carried only the resolved-string/commonpath check, which is a no-op
    fold on POSIX (see corpus_guard's module docstring) -- so
    `--scratch Results/work` or `--scratch RESULTS/WORK` from cwd runner/
    resolved to a string that shared neither identity nor path components
    with 'results', even though APFS's own directory lookup treats
    'RESULTS' and 'results' as the same inode.  --results is redirected so
    only the #28 guard can produce the refusal."""
    if not _fs_is_case_insensitive(tmp_path):
        pytest.skip("filesystem is case-sensitive; this bypass does not apply")

    for variant in ("Results/work", "RESULTS/WORK"):
        checkout_root = os.path.join(RUNNER_DIR, variant.split("/")[0])
        stray = os.path.join(RUNNER_DIR, variant)
        assert not os.path.exists(stray), f"stale checkout at {stray}"
        proc = run_cli_subprocess([
            "--mock", "--only", "no-such-run-id-xyz", "--scratch", variant,
            "--results", str(tmp_path / f"results-{variant.replace('/', '-')}.jsonl"),
        ])
        assert proc.returncode == corpus_guard.REFUSE_EXIT, (variant, proc.stderr)
        assert "refus" in proc.stderr.lower(), variant
        assert "--scratch" in proc.stderr, variant
        assert not os.path.exists(stray), (
            f"run.py created a checkout at the case-variant path {stray!r}")


def test_scratch_resolving_to_a_results_sibling_directory_is_not_refused(
        corpus_untouched, tmp_path):
    """The discarded alternative this fix's commit names: a string-prefix
    compare would wrongly refuse this, since "results2" starts with
    "results" as a string. os.path.commonpath compares resolved PATH
    COMPONENTS, correctly treats results2 as a sibling (not a descendant)
    of results/, and lets it through. --only matches no run_id, so nothing
    beyond the guard itself is exercised. --results is redirected to a
    scratch file so the pre-existing #23 guard (default --results resolving
    to the live corpus) does not fire first and mask which guard this test
    is actually about."""
    sibling = os.path.join(RUNNER_DIR, "results2")
    proc = run_cli_subprocess([
        "--mock", "--only", "no-such-run-id-xyz", "--scratch", sibling,
        "--results", str(tmp_path / "sibling-results.jsonl"),
    ])
    assert proc.returncode == 0, proc.stderr
    assert "refus" not in proc.stderr.lower()


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


# --------------------------------------------------------------------------- #
# The write-set extension: judge.py has its own --mock and its own --out/
# --usage defaults pointed at the same live results/ directory (issue #24
# says "any --mock or demo run", not just run.py's). Same guard, same
# corpus_guard helper, same positive-control shape as the run.py tests above.
# --------------------------------------------------------------------------- #
def test_judge_mock_against_default_paths_is_refused_and_leaves_corpus_untouched(
        corpus_untouched):
    """judge.py's run_id is a required positional argument in the non-mock
    path (ap.error below the guard) -- passing none at all and still getting
    refused BEFORE that error is itself part of the proof: the guard runs
    ahead of argument validation that has nothing to do with it."""
    proc = subprocess.run(
        [sys.executable, "judge.py", "--mock"],
        cwd=RUNNER_DIR, capture_output=True, text=True)
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()
    assert "provide a run_id" not in proc.stderr
