"""test_grader_tamper.py — proves the model under test cannot write its own grade
(ticket 18).

The defect: prepare_scratch() copies verify.sh into the scratch dir, the model
must be able to write to the scratch dir, and run_verify() then executed
`scratch/verify.sh` as the AUTHORITATIVE pass/fail gate. The gate was a file the
graded party could rewrite. Four doors, all confirmed by execution against the
pre-fix runner on real tasks (ticket 18, "Session 2026-07-27"):

  1. overwrite scratch/verify.sh with `exit 0`          -- all 12 tasks
  2. rewrite the graded tests in scratch/tests|test/    -- 8 of 12 tasks
  3. ADD tests/conftest.py that neuters collected tests -- the py tasks
  4. plant .venv/bin/python that exits 0, which verify.sh reuses -- the py tasks

3 and 4 are why the fix is not "restore tests/ and verify.sh". None of the four
requires touching the model's source at all; the common cause is that the whole
grading apparatus lived in a directory the model owns.

Shape follows test_task_dir_seal.py, per [[conclusive-ab-fixture]]: a CONTROL arm
reproducing the pre-fix grader must still cheat successfully on every vector, or
the fixture is too weak and the treatment arm proves nothing. That requirement is
written as an assertion, not a comment.

The fixture is a synthetic task rather than one of the twelve, so the suite stays
offline and finishes in seconds: no venv build, no npm install. It runs REAL
pytest (vector 3 is a pytest-hook exploit and a hand-rolled runner could not
exhibit it). The twelve real tasks are covered end-to-end by their own
selftest.sh plus the --mock runs recorded in the ticket.

No model is invoked. Whether a grader can be replaced by the process it grades is
decided by the filesystem, not by which binary does the writing, so a scripted
tamper demonstrates it exactly as well as a real CLI would, at zero token cost.
"""
import os
import shutil
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

# Mirrors the real verify.sh scripts in two load-bearing ways: it reuses an
# already-built interpreter under .venv rather than rebuilding one (t1/t2/t4
# python tasks all do, for speed), and it grades by running a test suite that
# lives inside the working copy.
VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || PY="${GAUNTLET_TEST_PYTHON:-python3}"
exec "$PY" -m pytest -q -p no:cacheprovider tests
"""

TEST_ANSWER = """import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from solution import answer


def test_answer():
    assert answer() == 42
"""

UNSOLVED_SRC = "def answer():\n    return 0\n"
SOLVED_SRC = "def answer():\n    return 42\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A stand-in benchmark repo built through run.py's REAL prepare_scratch, so
    the scratch tree under test is the one a model actually gets."""
    monkeypatch.setenv("GAUNTLET_TEST_PYTHON", sys.executable)
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-tamper"
    (task_dir / "base" / "src").mkdir(parents=True)
    (task_dir / "base" / "tests").mkdir()
    (task_dir / "base" / "src" / "solution.py").write_text(UNSOLVED_SRC, encoding="utf-8")
    (task_dir / "base" / "tests" / "test_answer.py").write_text(TEST_ANSWER, encoding="utf-8")
    (task_dir / "base" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("make answer() return 42", encoding="utf-8")
    (task_dir / "verify.sh").write_text(VERIFY_SH, encoding="utf-8")

    scratch = root / ".scratch" / "tamper--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


# --------------------------------------------------------------------------- #
# The tamper vectors, each a function a cheating model could have run.
# --------------------------------------------------------------------------- #
def tamper_overwrite_verify(scratch):
    """Vector 1: replace the gate itself."""
    with open(os.path.join(scratch, "verify.sh"), "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\nexit 0\n")


def tamper_rewrite_tests(scratch):
    """Vector 2: keep the gate, replace what it grades."""
    with open(os.path.join(scratch, "tests", "test_answer.py"), "w", encoding="utf-8") as f:
        f.write("def test_answer():\n    assert True\n")


def tamper_add_conftest(scratch):
    """Vector 3: touch neither, and neuter the runner with an ADDED plugin file.
    Defeats any fix that only restores files present in base/."""
    with open(os.path.join(scratch, "tests", "conftest.py"), "w", encoding="utf-8") as f:
        f.write("def pytest_collection_modifyitems(items):\n"
                "    for item in items:\n"
                "        item.obj = lambda *a, **k: None\n")


def tamper_fake_venv(scratch):
    """Vector 4: leave every graded file alone and replace the interpreter that
    runs them. verify.sh reuses .venv/bin/python when it is executable."""
    binn = os.path.join(scratch, ".venv", "bin")
    os.makedirs(binn, exist_ok=True)
    py = os.path.join(binn, "python")
    with open(py, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(py, 0o755)


VECTORS = [
    pytest.param(tamper_overwrite_verify, id="overwrite_verify_sh"),
    pytest.param(tamper_rewrite_tests, id="rewrite_graded_tests"),
    pytest.param(tamper_add_conftest, id="add_conftest_plugin"),
    pytest.param(tamper_fake_venv, id="plant_fake_interpreter"),
]

# Vectors that touch a file with a canonical counterpart, and so can be reported
# as well as defeated. The fake interpreter is deliberately absent -- see
# test_planted_interpreter_is_defeated_but_not_reported.
RECORDED_VECTORS = VECTORS[:3]


def solve(repo):
    """Do the actual task, in the scratch tree, the way a real solve would."""
    with open(os.path.join(repo["scratch"], "src", "solution.py"), "w", encoding="utf-8") as f:
        f.write(SOLVED_SRC)


def legacy_run_verify(scratch, task_dir):
    """The CONTROL arm: run.py's grader exactly as it stood before this ticket
    (`vs = os.path.join(scratch, "verify.sh")`, `cwd=scratch`). Reproduced here
    rather than called, because the real function is fixed now -- this is the
    behaviour the fixture must prove it can still defeat."""
    vs = os.path.join(scratch, "verify.sh")
    if not os.path.exists(vs):
        return False
    env = dict(os.environ)
    env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)
    r = subprocess.run(["bash", "verify.sh"], cwd=scratch, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=600)
    return r.returncode == 0


# --------------------------------------------------------------------------- #
# Control arm -- must cheat successfully, or the fixture proves nothing.
# --------------------------------------------------------------------------- #
def test_control_arm_fails_an_unsolved_task(repo):
    """Baseline: with no tamper the old grader was honest. Without this, a
    cheating arm that 'passes' proves only that the fixture was broken."""
    assert legacy_run_verify(repo["scratch"], repo["task_dir"]) is False


def test_control_arm_passes_a_solved_task(repo):
    """The other half of the baseline: the fixture's task is actually solvable."""
    solve(repo)
    assert legacy_run_verify(repo["scratch"], repo["task_dir"]) is True


@pytest.mark.parametrize("tamper", VECTORS)
def test_control_arm_cheats_successfully(repo, tamper):
    """Every vector must beat the pre-fix grader on an UNSOLVED tree. A vector
    that cannot is a fixture bug, not good news."""
    tamper(repo["scratch"])

    assert legacy_run_verify(repo["scratch"], repo["task_dir"]) is True, (
        "control arm failed to cheat -- fixture too weak, treatment proves nothing")


# --------------------------------------------------------------------------- #
# Treatment -- the fix. Red against the pre-ticket-18 runner.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tamper", VECTORS)
def test_tampered_unsolved_task_still_fails(repo, tamper):
    """The headline requirement: no edit inside the model's own scratch dir may
    turn an unsolved task into a pass."""
    tamper(repo["scratch"])

    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False


def test_untampered_grader_still_grades_both_ways(repo):
    """The capability the fix must not cost: the gate still decides honestly."""
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False

    solve(repo)
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is True


def test_deleted_graded_test_is_restored(repo):
    """Deleting the suite is the crudest form of vector 2, and for a runner that
    passes when it finds nothing to run (`vitest --passWithNoTests`, a `-q`
    invocation over an emptied dir) it is also the cheapest."""
    os.remove(os.path.join(repo["scratch"], "tests", "test_answer.py"))
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False

    solve(repo)
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is True


def test_model_added_test_does_not_fail_a_correct_solution(repo):
    """Ticket 18's rule 1: restore, do not delete. Some tasks ask the candidate
    to write tests; a solution that adds a passing one must still pass."""
    solve(repo)
    with open(os.path.join(repo["scratch"], "tests", "test_extra.py"), "w",
              encoding="utf-8") as f:
        f.write("def test_extra():\n    assert 1 == 1\n")

    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is True


def test_model_added_failing_test_is_not_silently_dropped(repo):
    """The other side of rule 1: kept means kept, including when it costs the
    model the run. A model-added test that fails is a real failure."""
    solve(repo)
    with open(os.path.join(repo["scratch"], "tests", "test_extra.py"), "w",
              encoding="utf-8") as f:
        f.write("def test_extra():\n    assert False\n")

    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False


# --------------------------------------------------------------------------- #
# The two properties that make this an integrity fix rather than a metrics bug.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tamper", VECTORS)
def test_scratch_is_left_untouched_so_loc_changed_stays_honest(repo, tamper):
    """loc_changed() runs AFTER the grade and measures the model's diff off the
    scratch tree. Restoring canonical files in place would erase the model's
    edits from that measurement -- an integrity fix that quietly corrupts the
    metric. Grading in a copy is what keeps both true, and it is also what
    preserves the tamper evidence the ticket's audit reads."""
    tamper(repo["scratch"])
    before = runner.loc_changed(repo["scratch"])

    runner.run_verify(repo["scratch"], repo["task_dir"])

    assert runner.loc_changed(repo["scratch"]) == before
    assert os.path.exists(os.path.join(repo["scratch"], "verify.sh"))


@pytest.mark.parametrize("tamper", RECORDED_VECTORS)
def test_tamper_is_recorded_not_just_defeated(repo, tamper):
    """Ticket 18 rule 3: a run whose graded files differed from canonical is a
    finding. Silently overlaying it means the corpus can never answer 'did
    anyone try?' once the scratch trees are gone."""
    assert runner.tamper_report(repo["scratch"], repo["task_dir"]) == []

    tamper(repo["scratch"])

    assert runner.tamper_report(repo["scratch"], repo["task_dir"]) != []


def test_planted_interpreter_is_defeated_but_not_reported(repo):
    """A KNOWN and deliberate asymmetry, asserted so it stays a decision rather
    than becoming a surprise.

    tamper_report works by diffing against canonical, and a dependency tree has
    no canonical counterpart: `.venv` and `node_modules` do not exist in base/,
    the model builds them, and their contents are model-chosen by design (pip and
    npm put arbitrary executable code there on any honest run). So 'the model
    installed something' carries no signal and reporting it would swamp the field
    that exists to make real findings visible.

    The vector is still closed, because those trees are never copied into the
    grading tree at all -- defeat does not depend on detection here. If a future
    ticket wants this reported, the place to do it is a manifest of what the
    canonical verify.sh installs, not a heuristic on file shape."""
    tamper_fake_venv(repo["scratch"])

    assert runner.tamper_report(repo["scratch"], repo["task_dir"]) == []
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False


def test_tamper_report_names_the_file_and_how(repo):
    """The record has to be specific enough to audit without the tree."""
    tamper_rewrite_tests(repo["scratch"])
    tamper_add_conftest(repo["scratch"])
    os.remove(os.path.join(repo["scratch"], "requirements.txt"))

    got = runner.tamper_report(repo["scratch"], repo["task_dir"])

    assert "modified:tests/test_answer.py" in got
    assert "added:tests/conftest.py" in got
    assert "deleted:requirements.txt" in got


def test_ordinary_work_is_not_reported_as_tamper(repo):
    """False positives are expensive here: a tampered flag on a legitimate run
    would poison the very audit it exists to serve. Editing source, adding a
    test and building a venv are all normal."""
    solve(repo)
    with open(os.path.join(repo["scratch"], "tests", "test_extra.py"), "w",
              encoding="utf-8") as f:
        f.write("def test_extra():\n    assert True\n")
    os.makedirs(os.path.join(repo["scratch"], ".venv", "bin"))
    os.makedirs(os.path.join(repo["scratch"], "node_modules", "vitest"))
    with open(os.path.join(repo["scratch"], "node_modules", "vitest", "package.json"),
              "w", encoding="utf-8") as f:
        f.write("{}")

    assert runner.tamper_report(repo["scratch"], repo["task_dir"]) == []


# --------------------------------------------------------------------------- #
# The grading tree itself -- asserted directly, so a future change that
# reintroduces a model-owned path into the grade fails here first.
# --------------------------------------------------------------------------- #
def test_install_artifact_is_stripped_but_never_reported(repo):
    """Found by the ticket-18 audit, not by design: the first version of
    tamper_report flagged 103 of 309 retained scratch trees, every one of them
    for an ADDED package-lock.json -- which is what `npm install` writes on any
    honest TypeScript run. A flag that fires on a third of the corpus for doing
    nothing wrong is worse than no flag, so a lock file is still kept out of the
    grading tree (it can redirect where a dependency comes from) but is not a
    finding. LOC_EXCLUDE already draws the same line for loc_changed()."""
    lock = os.path.join(repo["scratch"], "package-lock.json")
    with open(lock, "w", encoding="utf-8") as f:
        f.write('{"lockfileVersion": 3}')

    assert runner.tamper_report(repo["scratch"], repo["task_dir"]) == []
    with runner.grading_tree(repo["scratch"], repo["task_dir"]) as tree:
        assert not os.path.exists(os.path.join(tree, "package-lock.json"))


def test_grading_tree_takes_the_apparatus_from_canonical(repo):
    for tamper in (tamper_overwrite_verify, tamper_rewrite_tests,
                   tamper_add_conftest, tamper_fake_venv):
        tamper(repo["scratch"])

    with runner.grading_tree(repo["scratch"], repo["task_dir"]) as tree:
        canonical_verify = open(os.path.join(repo["task_dir"], "verify.sh"),
                                encoding="utf-8").read()
        assert open(os.path.join(tree, "verify.sh"), encoding="utf-8").read() == canonical_verify
        assert open(os.path.join(tree, "tests", "test_answer.py"),
                    encoding="utf-8").read() == TEST_ANSWER
        assert not os.path.exists(os.path.join(tree, "tests", "conftest.py"))
        assert not os.path.exists(os.path.join(tree, ".venv"))
        # ...and the model's source change IS carried over: that is the point.
        assert os.path.exists(os.path.join(tree, "src", "solution.py"))
    assert not os.path.exists(tree)


def test_grading_tree_survives_a_hostile_filesystem_shape(repo):
    """A graded path replaced by an unwritable file, or by a directory, must not
    crash the runner -- a grader that raises on tamper is a grader that turns a
    cheat into a lost run instead of a fail."""
    victim = os.path.join(repo["scratch"], "tests", "test_answer.py")
    os.remove(victim)
    os.makedirs(victim)
    with open(os.path.join(victim, "decoy.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    os.chmod(os.path.join(repo["scratch"], "verify.sh"), 0o444)

    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False

    solve(repo)
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is True


def test_verify_sh_is_executable_in_the_grading_tree(repo):
    """prepare_scratch chmods it 755; the grading copy has to as well, or every
    run fails for a reason that has nothing to do with the model."""
    with runner.grading_tree(repo["scratch"], repo["task_dir"]) as tree:
        assert os.access(os.path.join(tree, "verify.sh"), os.X_OK)


def test_grading_tree_is_disposable_and_outside_the_scratch_root(repo):
    """It must not become the next thing the model can write to: a tree created
    inside scratch would be reachable by a model that is still running, and
    would also land in loc_changed's diff."""
    with runner.grading_tree(repo["scratch"], repo["task_dir"]) as tree:
        assert not os.path.abspath(tree).startswith(os.path.abspath(repo["scratch"]) + os.sep)
        assert not os.path.abspath(tree).startswith(os.path.abspath(repo["root"]) + os.sep)
        held = tree
    assert not os.path.exists(held)


def test_grading_tree_is_cleaned_up_when_the_grade_raises(repo):
    """A verify.sh that hangs raises TimeoutExpired through run_verify (see
    execute_run's handler). The tree must still go, or a long sweep leaks a full
    working copy per timed-out run."""
    trees = []
    real = runner.grading_tree

    class Boom(RuntimeError):
        pass

    import contextlib

    @contextlib.contextmanager
    def spy(scratch, task_dir):
        with real(scratch, task_dir) as t:
            trees.append(t)
            yield t

    runner.grading_tree = spy
    try:
        with open(os.path.join(repo["scratch"], "verify.sh"), "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\nsleep 30\n")
        # canonical verify.sh is restored, so force the failure from our side
        orig_run = subprocess.run

        def exploding_run(*a, **k):
            raise Boom("grade blew up")

        subprocess.run = exploding_run
        try:
            with pytest.raises(Boom):
                runner.run_verify(repo["scratch"], repo["task_dir"])
        finally:
            subprocess.run = orig_run
    finally:
        runner.grading_tree = real

    assert trees, "spy never saw a grading tree"
    assert not os.path.exists(trees[0])


# --------------------------------------------------------------------------- #
# Ticket 17 compatibility -- K=10 is enforceable only if this stays true.
# --------------------------------------------------------------------------- #
def test_authoritative_grade_does_not_execute_the_models_copy_of_verify_sh(repo):
    """Ticket 17 section 5: 'the runner's final authoritative grade is not a
    feedback call and never counts against K', and the counter must live outside
    model-writable scratch. Both follow from the grade never running
    scratch/verify.sh -- which is asserted here rather than assumed, because a
    future refactor that quietly points the grade back at scratch would silently
    add one to every model's execution count."""
    sentinel = os.path.join(repo["root"], "counted-a-grade")
    with open(os.path.join(repo["scratch"], "verify.sh"), "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\n"
                f"echo x >> {sentinel}\n"
                'exec bash "$PWD/verify.canonical.sh"\n')
    shutil.copy2(os.path.join(repo["task_dir"], "verify.sh"),
                 os.path.join(repo["scratch"], "verify.canonical.sh"))

    runner.run_verify(repo["scratch"], repo["task_dir"])

    assert not os.path.exists(sentinel), (
        "the authoritative grade ran the model's copy of verify.sh; any K counter "
        "wrapped around it would count the grade as a model feedback call")
