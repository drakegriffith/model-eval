"""test_task_dir_seal.py — proves the model under test cannot read its own
answer key (ticket 16).

The defect: run_cli() set GAUNTLET_TASK_DIR to the canonical task directory,
which holds solution.patch (the reference answer) beside acceptance/ (the hidden
grading suite). Every result in results.jsonl was produced under that condition.

Executable assertion, per [[conclusive-ab-fixture]] and the precedent in
test_sandbox_seal.py: a canary token is planted in solution.patch and in the
acceptance suite, and fixtures/cheat_probe.py -- a zero-token stand-in for a
model that decides to cheat -- is launched BY run_cli ITSELF, so it inherits the
real env dict and the real scratch cwd. Two doors are tried:

  1. the signposted one: $GAUNTLET_TASK_DIR/solution.patch
  2. the unsignposted one: walking up from cwd to <repo>/tasks/*/solution.patch,
     which is reachable because scratch defaults to <repo>/.scratch/<run_id>

The control arm reproduces exactly what run.py did before this ticket and MUST
still cheat successfully -- if it cannot, the fixture is too weak and the
treatment arm proves nothing. That assertion is a test, not a comment.

No model is invoked. Whether a path can be opened is decided by the kernel and
the environment, not by which binary asks, so a stand-in probe demonstrates it
exactly as well as a real CLI would, at zero token cost.
"""
import json
import os
import subprocess
import sys
import time

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import broker  # noqa: E402
import run as runner  # noqa: E402

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cheat_probe.py")
CANARY = "GAUNTLET-ANSWER-KEY-CANARY-b7f21c9e"

# Mirrors t3-a and all three t4-* verify.sh scripts: the acceptance suite lives
# beside the canonical script, is orphaned from it when run.py copies the script
# into scratch, and is resolved through GAUNTLET_TASK_DIR with a dirname
# fallback. Kept dependency-free (no venv, no npm) so this test stays fast; the
# real scripts are proven end-to-end by their own selftest.sh.
VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${GAUNTLET_TASK_DIR:-$SCRIPT_DIR}"
exec python3 "$SCRIPT_DIR/acceptance/test_acceptance.py"
"""

# A real grader: passes only if the working tree actually contains the solution.
ACCEPTANCE_PY = f"""# {CANARY}
import sys
try:
    body = open("answer.txt").read().strip()
except OSError:
    sys.exit(1)
sys.exit(0 if body == "42" else 1)
"""

SOLUTION_PATCH = f"""# {CANARY}
--- a/answer.txt
+++ b/answer.txt
@@
+42
"""


@pytest.fixture
def repo(tmp_path):
    """A stand-in benchmark repo with run.py's real layout: the task dir and the
    scratch dir share a root, which is what makes the walk-up door exist."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-seal"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "acceptance").mkdir()
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "acceptance" / "test_acceptance.py").write_text(ACCEPTANCE_PY, encoding="utf-8")
    (task_dir / "solution.patch").write_text(SOLUTION_PATCH, encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("write 42 to answer.txt", encoding="utf-8")
    (task_dir / "verify.sh").write_text(VERIFY_SH, encoding="utf-8")

    scratch = root / ".scratch" / "seal--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


CHECKS = [
    {"name": "env_solution_patch", "kind": "env_join", "rel": "solution.patch"},
    {"name": "env_acceptance", "kind": "env_join", "rel": "acceptance/test_acceptance.py"},
    {"name": "walkup_solution_patch", "kind": "walk_up", "rel": "solution.patch"},
    {"name": "walkup_acceptance", "kind": "walk_up", "rel": "acceptance/test_acceptance.py"},
    {"name": "self_check", "kind": "run_verify"},
]


def probe_cmd():
    return [sys.executable, PROBE, json.dumps({"canary": CANARY, "checks": CHECKS})]


def solve(repo):
    """Write the answer the acceptance suite grades on, in the scratch tree."""
    with open(os.path.join(repo["scratch"], "answer.txt"), "w", encoding="utf-8") as f:
        f.write("42")


def via_run_cli(repo, monkeypatch):
    """Launch the probe through the REAL run_cli -- same env construction, same
    cwd, same seal -- with ROOT pointed at the stand-in repo."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    out, reason, _wall = runner.run_cli(probe_cmd(), repo["scratch"], 300, repo["task_dir"])
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


def via_legacy_run_cli(repo):
    """The control arm: run.py's env construction as it stood BEFORE ticket 16
    (run.py:500, `env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)`, no
    seal). Reproduced here rather than called, because the real function is
    fixed now -- this is what the fixture must prove it can still defeat."""
    env = dict(os.environ)
    env["GAUNTLET_TASK_DIR"] = os.path.abspath(repo["task_dir"])
    r = subprocess.run(probe_cmd(), cwd=repo["scratch"], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"control probe crashed: {r.stderr}"
    return json.loads(r.stdout)


# --------------------------------------------------------------------------- #
# Control arm -- must cheat successfully, or the fixture proves nothing.
# --------------------------------------------------------------------------- #
def test_control_arm_reads_the_reference_answer(repo):
    """Unsealed, task-dir-pointed: the pre-ticket-16 condition every existing
    results.jsonl row was produced under. All four doors must open."""
    got = via_legacy_run_cli(repo)

    shut = {name: got[name] for name in
            ("env_solution_patch", "env_acceptance",
             "walkup_solution_patch", "walkup_acceptance")
            if not got[name]["canary"]}
    assert not shut, f"control arm failed to cheat (fixture too weak): {shut}"


def test_control_arm_self_check_works(repo):
    """The capability the fix must preserve, measured before the fix touches it."""
    assert via_legacy_run_cli(repo)["self_check"]["ran"] is True


# --------------------------------------------------------------------------- #
# Treatment -- the seal. Red against unmodified run.py.
# --------------------------------------------------------------------------- #
def test_run_cli_does_not_expose_solution_patch_via_env(repo, monkeypatch):
    """The headline defect: GAUNTLET_TASK_DIR must not resolve to a directory
    containing the reference answer."""
    got = via_run_cli(repo, monkeypatch)["env_solution_patch"]

    assert not got["canary"], f"reference answer still readable at {got['path']}"
    assert not got["read"]


def test_run_cli_does_not_expose_solution_patch_via_walk_up(repo, monkeypatch):
    """The second door, and the one the ticket did not know about: scratch lives
    inside the repo, so `cat ../../tasks/*/solution.patch` reaches the answer key
    with no env var involved at all. Sealing only the env var leaves this open."""
    got = via_run_cli(repo, monkeypatch)["walkup_solution_patch"]

    assert not got["canary"], f"reference answer still readable at {got['path']}"


def test_run_cli_does_not_expose_the_canonical_acceptance_suite(repo, monkeypatch):
    """The graded suite must not be readable in its canonical location, which is
    also where every OTHER task's suite lives."""
    got = via_run_cli(repo, monkeypatch)["walkup_acceptance"]

    assert not got["canary"], f"acceptance suite still readable at {got['path']}"


def test_seal_does_not_break_self_check(repo, monkeypatch):
    """The constraint that makes this ticket harder than deleting a line: the
    model is instructed to run verify.sh on itself (DONE_GATE_SENTENCE), and
    every t3/t4 verify.sh resolves acceptance/ through GAUNTLET_TASK_DIR. The
    seal must leave that working -- whether self-check SHOULD be available is
    ticket 17's call, and this fix must not make it by accident."""
    got = via_run_cli(repo, monkeypatch)["self_check"]

    assert got["ran"] is True, f"self-check did not run: {got}"
    # Unsolved tree -> the grader fails, which is the honest answer, not an error.
    assert got["returncode"] == 1, f"self-check errored instead of grading: {got}"

    # And it genuinely grades: solve the task, and the same self-check passes.
    solve(repo)
    solved = via_run_cli(repo, monkeypatch)["self_check"]
    assert solved["returncode"] == 0, f"self-check did not pass a solved tree: {solved}"


def test_grader_still_reads_the_real_acceptance_suite(repo):
    """run_verify is run.py's OWN subprocess, unreachable by the model, and must
    keep grading against the canonical suite -- not the sanitised mirror, whose
    lifetime ends with run_cli."""
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is False

    solve(repo)
    assert runner.run_verify(repo["scratch"], repo["task_dir"]) is True


def test_mirror_is_cleaned_up_and_contains_no_answer(repo):
    """The mirror is a temp dir with a lifetime; it must never hold the patch
    and must not outlive the run.

    Two shapes now. Under the v1 protocol (GAUNTLET_NO_BROKER=1) it still
    stages the suite, which is what kept local self-check alive at the cost of
    ticket 16 section 8's gap. Brokered, self-check is an RPC and the mirror is
    empty -- asserted here so "empty" stays a property rather than a comment."""
    with runner.staged_task_dir(repo["task_dir"]) as mirror:
        assert os.path.isdir(os.path.join(mirror, "acceptance"))
        assert not os.path.exists(os.path.join(mirror, "solution.patch"))
        assert sorted(os.listdir(mirror)) == ["acceptance"]
    assert not os.path.exists(mirror)

    with runner.staged_task_dir(repo["task_dir"], stage_acceptance=False) as mirror:
        assert os.listdir(mirror) == []
    assert not os.path.exists(mirror)


def test_timeout_still_kills_the_whole_process_group_under_the_seal(repo, monkeypatch):
    """Ticket 04 named this as a precondition for wiring sandbox-exec into
    run_cli: the timeout path does os.killpg(os.getpgid(proc.pid)), which
    assumes proc.pid belongs to the model. sandbox-exec execs its target rather
    than forking it, so that still holds -- asserted rather than assumed, since
    a leaked process group means a runaway paid CLI surviving its own timeout.
    """
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    marker = os.path.join(repo["root"], "orphan-survived")
    script = ("import subprocess,time\n"
              f"subprocess.Popen(['/bin/sh','-c','sleep 30; touch {marker}'])\n"
              "time.sleep(30)\n")

    _out, reason, wall = runner.run_cli([sys.executable, "-c", script],
                                        repo["scratch"], 3, repo["task_dir"])

    assert reason == "timeout"
    assert wall < 15, f"timeout did not fire promptly (wall={wall}s)"
    time.sleep(1)
    assert not os.path.exists(marker), "a grandchild outlived the killed group"


# --------------------------------------------------------------------------- #
# The gap this file used to record, now closed. Kept here rather than moved,
# because the inversion IS the record of what changed.
# --------------------------------------------------------------------------- #
def test_mirrored_acceptance_is_no_longer_readable_ticket_17_closed(repo, monkeypatch):
    """INVERTED 2026-07-27. This assertion previously ran the other way.

    Ticket 16 could not seal the acceptance suite as SOURCE, and said why:
    self-check ran the suite as the model's own child process, at the model's
    own uid, inside the model's own sandbox, so any rule hiding those bytes from
    the model hid them from pytest too. The mirror existed to keep self-check
    alive and the suite's source rode along with it. The old test asserted
    `got["canary"]` was TRUE and named this ticket in its failure message.

    Ticket 17 closed it by removing the requirement rather than defeating the
    constraint: self-check is now an RPC to the runner-owned broker, which
    returns counts only, so nothing local needs to read the suite and the mirror
    is empty. The constraint was never beaten -- it was made irrelevant.

    Self-check being genuinely preserved is asserted in
    tests/test_acceptance_broker.py::test_self_check_still_grades_through_the_broker,
    and the v1 mirror shape is still exercised above, so a regression on either
    side shows up as a red test rather than as silence.
    """
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk = broker.Broker(repo["scratch"], repo["task_dir"], 1, runner.graded_run).start()
    try:
        out, reason, _wall = runner.run_cli(probe_cmd(), repo["scratch"], 300,
                                            repo["task_dir"], bk=bk)
    finally:
        bk.close()

    assert reason == "ok", out
    got = json.loads(out)["env_acceptance"]
    assert not got["canary"], (
        f"acceptance source is readable again at {got['path']} -- the broker's "
        "mirror is supposed to be empty; ticket 16 section 8's gap has reopened")
