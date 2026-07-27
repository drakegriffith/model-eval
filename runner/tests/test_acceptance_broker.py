"""test_acceptance_broker.py — proves K=10 is enforced and the acceptance suite
is no longer readable as source (ticket 17 sections 5 and 6, closing ticket 16
section 8).

WHAT WAS OPEN. Ticket 16 sealed solution.patch and the canonical acceptance
directories, but had to keep mirroring the suite into GAUNTLET_TASK_DIR so
local self-check would still run -- leaving the suite readable AS SOURCE, and
leaving feedback uncapped. Ticket 17 then measured the corpus and found the
enforcement point that does not exist: 37 of 60 Codex runs reached the suite
without going through verify.sh at all. A counter inside verify.sh is bypassed
by a majority of honest runs.

WHAT IS ASSERTED HERE. The control arm reproduces the shipped pre-broker
condition and must CHEAT SUCCESSFULLY on both counts -- read the suite's source
with the canary in hand, and take twelve consecutive uncounted grades -- or the
treatment arm proves nothing. That is a test, not a comment; it is the same bar
ticket 16 set with [[conclusive-ab-fixture]] and it is reproduced rather than
imagined, since GAUNTLET_NO_BROKER=1 keeps that path alive.

No model is invoked anywhere in this file. Whether a path opens, and how many
times a socket answers, are decided by the kernel and by the broker, not by
which binary asks.
"""
import json
import os
import shutil
import socket
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import broker  # noqa: E402
import run as runner  # noqa: E402
import usage_ledger  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CHEAT_PROBE = os.path.join(FIXTURES, "cheat_probe.py")
BROKER_PROBE = os.path.join(FIXTURES, "broker_probe.py")

# One token, planted in two places that must never reach the model: the
# acceptance suite's source, and the failure detail the suite prints. The second
# is what makes "counts only" a real assertion rather than a hopeful one.
CANARY = "GAUNTLET-ACCEPTANCE-CANARY-4d19ae70"

# Mirrors the real t3-a / t4-* shape: the graded suite lives beside the
# canonical verify.sh, outside base/, and is resolved through GAUNTLET_TASK_DIR
# because run.py copies the script into scratch and orphans it from its assets.
# Dependency-free (no venv, no npm) so the file stays fast; the real scripts are
# proven end to end by their own selftest.sh.
VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${GAUNTLET_TASK_DIR:-$SCRIPT_DIR}"
python3 tests/visible.py
echo "--- acceptance ---"
python3 "$SCRIPT_DIR/acceptance/test_acceptance.py"
"""

VISIBLE_PY = """import sys
print("2 passed in 0.01s")
sys.exit(0)
"""

# Prints exactly what a real -q --tb=no run prints on the summary line, and --
# on failure -- the test id and assertion text a real traceback would carry.
# The canary rides in that detail, so any code path that forwards raw output
# instead of counts fails test_broker_reports_counts_and_nothing_else.
ACCEPTANCE_PY = f"""# {CANARY}
import sys
try:
    body = open("answer.txt").read().strip()
except OSError:
    body = ""
if body == "42":
    print("2 passed in 0.01s")
    sys.exit(0)
print("FAILED acceptance/test_acceptance.py::test_answer_is_42 - "
      "assert %r == '42'  # {CANARY}" % body)
print("1 failed, 1 passed in 0.01s")
sys.exit(1)
"""

SOLUTION_PATCH = f"""# {CANARY}
--- a/answer.txt
+++ b/answer.txt
@@
+42
"""


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout: task dir and scratch share a root."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-broker"
    (task_dir / "base" / "tests").mkdir(parents=True)
    (task_dir / "acceptance").mkdir()
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "base" / "tests" / "visible.py").write_text(VISIBLE_PY, encoding="utf-8")
    (task_dir / "acceptance" / "test_acceptance.py").write_text(ACCEPTANCE_PY, encoding="utf-8")
    (task_dir / "solution.patch").write_text(SOLUTION_PATCH, encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("write 42 to answer.txt", encoding="utf-8")
    (task_dir / "verify.sh").write_text(VERIFY_SH, encoding="utf-8")

    scratch = root / ".scratch" / "broker--probe--r1"
    scratch.parent.mkdir(parents=True)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch),
            "tmp": str(tmp_path)}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def cheat_cmd(checks):
    return [sys.executable, CHEAT_PROBE, json.dumps({"canary": CANARY, "checks": checks})]


def probe_cmd(log, **spec):
    spec["log"] = log
    return [sys.executable, BROKER_PROBE, json.dumps(spec)]


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def solve(repo):
    with open(os.path.join(repo["scratch"], "answer.txt"), "w", encoding="utf-8") as f:
        f.write("42")


def ask(sock_path):
    """A raw client, bypassing the shim entirely -- the model could write this."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(60)
    s.connect(sock_path)
    s.sendall(b'{"cmd":"check"}\n')
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8"))


def brokered_scratch(repo, k=3):
    """Set the scratch tree up exactly as execute_run does for a brokered run."""
    bk = broker.Broker(repo["scratch"], repo["task_dir"], k, runner.graded_run).start()
    shim = broker.shim_text(bk.sock_path, k, sys.executable)
    runner.prepare_scratch(repo["task_dir"], repo["scratch"], harness=False,
                           verify_text=shim)
    return bk, shim


def legacy_scratch(repo):
    """The shipped pre-broker tree: canonical verify.sh, staged suite, no cap."""
    runner.prepare_scratch(repo["task_dir"], repo["scratch"], harness=False)


# --------------------------------------------------------------------------- #
# Control arm -- must cheat successfully, or nothing below means anything.
# --------------------------------------------------------------------------- #
def test_control_arm_reads_the_acceptance_suite_as_source(repo, monkeypatch):
    """Ticket 16 section 8's documented gap, exercised rather than described:
    with the suite mirrored into GAUNTLET_TASK_DIR, the model reads the bytes it
    is graded on and can over-fit to them -- which is the whole thing the t4-*
    count-only authoring exists to prevent."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    legacy_scratch(repo)

    out, reason, _w = runner.run_cli(
        cheat_cmd([{"name": "acc", "kind": "env_join",
                    "rel": "acceptance/test_acceptance.py"}]),
        repo["scratch"], 300, repo["task_dir"], bk=None)

    assert reason == "ok", out
    got = json.loads(out)["acc"]
    assert got["canary"], f"control arm failed to read the suite (fixture too weak): {got}"


def test_control_arm_takes_twelve_uncounted_grades(repo, monkeypatch):
    """The other half of the cheat, and the one K exists for: the pre-broker
    harness answers as often as asked and counts nothing, so 'at most K
    acceptance-feedback requests' is unfalsifiable on every row in the corpus."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    legacy_scratch(repo)
    log = os.path.join(repo["tmp"], "control.jsonl")

    _out, reason, _w = runner.run_cli(probe_cmd(log, calls=12), repo["scratch"],
                                      300, repo["task_dir"], bk=None)

    calls = read_log(log)
    assert reason == "ok"
    assert len(calls) == 12, f"control arm was cut short: {calls}"
    assert all(c["rc"] == 1 for c in calls), "grades were not actually served"
    assert not any("budget" in (c.get("out") or "") for c in calls)


# --------------------------------------------------------------------------- #
# Treatment -- the broker. Red against the pre-broker runner.
# --------------------------------------------------------------------------- #
def test_broker_seals_the_acceptance_suite_as_source(repo, monkeypatch):
    """The gap closes: self-check no longer needs a local copy of the suite, so
    there is no local copy. Both doors ticket 16 named are checked, because the
    mirror going empty must not quietly re-open the walk-up."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo)
    try:
        out, reason, _w = runner.run_cli(
            cheat_cmd([{"name": "env", "kind": "env_join",
                        "rel": "acceptance/test_acceptance.py"},
                       {"name": "walk", "kind": "walk_up",
                        "rel": "acceptance/test_acceptance.py"}]),
            repo["scratch"], 300, repo["task_dir"], bk=bk)
    finally:
        bk.close()

    assert reason == "ok", out
    got = json.loads(out)
    assert not got["env"]["canary"], f"suite still readable via the mirror: {got['env']}"
    assert not got["walk"]["canary"], f"suite still readable by walk-up: {got['walk']}"


def test_self_check_still_grades_through_the_broker(repo, monkeypatch):
    """Option A without pure A: the model keeps the capability to know when it
    is done -- which ticket 17 refused to delete -- it just buys it K times and
    gets counts back instead of the suite."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo, k=4)
    log = os.path.join(repo["tmp"], "selfcheck.jsonl")
    try:
        runner.run_cli(probe_cmd(log, calls=2, solve_before=1), repo["scratch"],
                       300, repo["task_dir"], bk=bk)
    finally:
        bk.close()

    calls = read_log(log)
    assert len(calls) == 2
    assert calls[0]["rc"] == 1 and "verdict: fail" in calls[0]["out"]
    assert calls[1]["rc"] == 0 and "verdict: pass" in calls[1]["out"]
    # And it is genuinely the grade, not a guess: the counts move with the tree.
    assert "failed=1" in calls[0]["out"]
    assert "failed" not in calls[1]["out"]


def test_broker_reports_counts_and_nothing_else(repo, monkeypatch):
    """No test names, no paths, no assertion text (ticket 17 section 5).

    Asserted against a suite whose failure detail CARRIES the canary, so a
    version that forwarded raw output -- or forwarded a scrubbed tail of it --
    goes red here. The response is built from integers, which is why this holds
    structurally rather than by filtering."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo)
    log = os.path.join(repo["tmp"], "counts.jsonl")
    try:
        # The raw grade really does contain the secret -- proven, not assumed.
        rc, raw = runner.graded_run(repo["scratch"], repo["task_dir"])
        assert rc != 0 and CANARY in raw

        runner.run_cli(probe_cmd(log, calls=1), repo["scratch"], 300,
                       repo["task_dir"], bk=bk)
    finally:
        bk.close()

    seen = read_log(log)[0]["out"]
    assert CANARY not in seen
    assert "test_answer_is_42" not in seen
    assert "acceptance/" not in seen
    assert "assert" not in seen
    assert "failed=1  passed=3" in seen


def test_broker_caps_requests_at_k(repo, monkeypatch):
    """K binds. The K+1th request is refused rather than answered."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo, k=3)
    try:
        served = [ask(bk.sock_path) for _ in range(3)]
        refused = ask(bk.sock_path)
    finally:
        bk.close()

    assert [r["request"] for r in served] == [1, 2, 3]
    assert all(r["ok"] and r["counts"] for r in served)
    assert [r["remaining"] for r in served] == [2, 1, 0]
    assert refused["ok"] is False and refused["reason"] == "cap_exhausted"
    assert bk.exhausted is True


def test_exhausting_k_terminates_the_run_and_scores_it_a_failure(repo, monkeypatch):
    """Hard termination at exhaustion, not post-hoc disqualification -- post-hoc
    burns the tokens the cap exists to stop (ticket 17 section 5). And the row
    is a failure, per the pre-registration's estimand sentence, with the
    verifier's own answer kept beside it rather than discarded."""
    row, log = execute(repo, monkeypatch, calls=9, k=3, solve_before=0)

    calls = read_log(log)
    assert row["exit_reason"] == "cap_exhausted"
    assert row["cap_exhausted"] is True
    assert row["acceptance_requests"] == 4, "the refused request must still count"
    assert row["pass"] is False, "a cap-terminated run is scored as a failure"
    assert row["pass_at_cap"] is True, "the real verifier outcome is not discarded"
    # The probe wanted nine grades and the run ended on the fourth request.
    assert len(calls) <= 4, f"the model outlived its own cap: {calls}"


def test_the_final_authoritative_grade_never_counts_against_k(repo, monkeypatch):
    """Ticket 17 section 5: conflating the two silently inflates every count by
    one. run_verify goes straight to graded_run and never touches the socket."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo, k=3)
    try:
        ask(bk.sock_path)
        before = bk.requests

        runner.run_verify(repo["scratch"], repo["task_dir"])
        solve(repo)
        runner.run_verify(repo["scratch"], repo["task_dir"])

        assert bk.requests == before == 1
    finally:
        bk.close()


def test_the_counter_is_not_in_the_tree_the_model_owns(repo, monkeypatch):
    """The failure mode ticket 17 section 5 named: 'a counter placed there is
    mutable by the thing it is counting.' Deleting the entire workspace -- a
    strictly stronger move than any edit -- does not reset it."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo, k=5)
    try:
        ask(bk.sock_path)
        ask(bk.sock_path)

        shutil.rmtree(repo["scratch"])
        os.makedirs(repo["scratch"])

        assert ask(bk.sock_path)["request"] == 3
        assert not os.path.abspath(bk.dir).startswith(
            os.path.abspath(repo["scratch"]) + os.sep)
        assert not os.path.abspath(bk.dir).startswith(
            os.path.abspath(repo["root"]) + os.sep)
    finally:
        bk.close()


def test_one_budget_is_shared_across_every_process_in_the_workspace(repo, monkeypatch):
    """Ticket 17 section 5 left this open: 'whether K governs the outer
    orchestrator, nested workers, or the shared workspace's total requests.'
    Answered here -- the workspace. The hybrid arm runs a Fable orchestrator
    that shells out to codex in the same scratch dir, and both reach the same
    socket, so one run has one budget however many agents are inside it."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    bk, _shim = brokered_scratch(repo, k=4)
    try:
        env = dict(os.environ)
        outs = [subprocess.run(["bash", "verify.sh"], cwd=repo["scratch"], env=env,
                               capture_output=True, text=True, timeout=120).stdout
                for _ in range(3)]
    finally:
        bk.close()

    assert [o.splitlines()[0] for o in outs] == [
        "acceptance feedback 1/4 (3 remaining)",
        "acceptance feedback 2/4 (2 remaining)",
        "acceptance feedback 3/4 (1 remaining)",
    ]


# --------------------------------------------------------------------------- #
# Fail-closed, both ends. An uncounted run is unusable, so it is never produced.
# --------------------------------------------------------------------------- #
def test_a_broker_fault_mid_run_fails_the_run_closed(repo, monkeypatch):
    """The run is ended and marked, not allowed to proceed uncounted. Matches
    the seal's posture (ticket 16: no sandbox-exec, no run) and lands the row in
    the fault bucket the pre-registration re-runs from its spare pool."""
    real = runner.graded_run

    def boom_once(scratch, task_dir):
        # Only the brokered call faults. The authoritative grade that follows is
        # left working on purpose: the row has to show a fault reason even when
        # the model's tree would otherwise have graded fine.
        if not getattr(boom_once, "fired", False):
            boom_once.fired = True
            raise RuntimeError("counter is gone")
        return real(scratch, task_dir)

    monkeypatch.setattr(runner, "graded_run", boom_once)
    row, _log = execute(repo, monkeypatch, calls=6, k=5)

    assert row["exit_reason"] == "broker_failed"
    assert row["cap_exhausted"] is False


def test_a_broker_that_cannot_start_stops_the_run_before_a_token_is_spent(repo, monkeypatch):
    """Fail closed at the top too: the CLI is never built and never launched."""
    launched = []
    monkeypatch.setattr(runner, "build_cli_cmd",
                        lambda *a, **k: launched.append(a) or ["true"])
    monkeypatch.setattr(broker, "_TMP_ROOT", os.path.join(repo["tmp"], "nope"))

    with pytest.raises(OSError):
        execute(repo, monkeypatch, calls=1, k=3, patch_cmd=False)

    assert launched == [], "a token could have been spent with no counter running"


# --------------------------------------------------------------------------- #
# The protocol, recorded on the row and disclosed in the prompt.
# --------------------------------------------------------------------------- #
def test_the_row_records_the_protocol_it_ran_under(repo, monkeypatch):
    """v1 and v2 rows never pool (pre-registration section 8), so which protocol
    a row ran under has to be readable off the row rather than reconstructed
    from commit dates -- the same argument that put `sealed` there."""
    row, _log = execute(repo, monkeypatch, calls=2, k=6)

    assert row["brokered"] is True
    assert row["k_cap"] == 6
    assert row["acceptance_requests"] == 2
    assert row["sealed"] is True


def test_k_is_disclosed_to_the_model(repo):
    """Ticket 17 section 5: a hidden budget measures how a model behaves when
    its tools start failing for reasons it cannot see, which is a different
    study. The undisclosed form must stay reachable and unchanged for the v1
    protocol."""
    disclosed = runner.compose_prompt(repo["task_dir"], False, "solo", k=10)
    v1 = runner.compose_prompt(repo["task_dir"], False, "solo")

    assert "at most 10 times" in disclosed
    assert "Request 11 ends the run" in disclosed
    assert "scored as a failure" in disclosed
    assert "verify.sh" in v1 and "at most" not in v1


def test_replacing_verify_sh_with_the_shim_is_not_a_tamper_finding(repo):
    """The runner installs the shim on every brokered run. Diffing it against
    canonical would flag `modified:verify.sh` on all 72 rows of the sweep --
    the same false-positive that made the first tamper_report useless (ticket
    18's package-lock.json). The model rewriting the shim is still a finding."""
    bk, shim = brokered_scratch(repo)
    bk.close()
    installed = {"verify.sh": shim}

    assert runner.tamper_report(repo["scratch"], repo["task_dir"],
                                installed=installed) == []

    with open(os.path.join(repo["scratch"], "verify.sh"), "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\nexit 0\n")

    assert runner.tamper_report(repo["scratch"], repo["task_dir"],
                                installed=installed) == ["modified:verify.sh"]


# --------------------------------------------------------------------------- #
# K itself. Unit-level, because the number is pre-registered.
# --------------------------------------------------------------------------- #
def test_k_defaults_to_ten_and_cannot_exceed_the_pre_registered_ceiling():
    """K = 10 (ticket 17 section 6), revisable once to K' = min(20, 2M). A
    config above 20 is not a tuning choice, it is a protocol violation, and
    finding it at analysis time is finding it too late."""
    assert broker.resolve_k(None) == 10
    assert broker.resolve_k(20) == 20
    with pytest.raises(ValueError):
        broker.resolve_k(21)
    with pytest.raises(ValueError):
        broker.resolve_k(0)


@pytest.mark.parametrize("text,expect", [
    ("1 failed, 7 passed in 0.31s", {"passed": 7, "failed": 1}),
    ("9 passed in 0.20s", {"passed": 9}),
    ("2 failed, 1 error, 3 passed, 1 skipped in 1.2s",
     {"passed": 3, "failed": 2, "errors": 1, "skipped": 1}),
    (" Tests  2 failed | 7 passed (9)", {"passed": 7, "failed": 2}),
    ("2 passed in 0.01s\n--- acceptance ---\n1 failed, 1 passed in 0.01s",
     {"passed": 3, "failed": 1}),
    ("bash: line 1: pytest: command not found", {}),
])
def test_counts_are_read_from_real_runner_summaries(text, expect):
    """Verbatim summary lines from pytest and vitest, plus the two-suite shape a
    t4 verify.sh produces (summed, because reporting the blocks separately tells
    the model which one failed and structure is information too)."""
    assert broker.parse_counts(text) == expect


# --------------------------------------------------------------------------- #
# The end-to-end driver: the REAL execute_run, with the probe standing in for a
# CLI. Everything the row asserts above went through prepare_scratch, the
# broker, run_cli, the seal and run_verify exactly as a sweep would.
# --------------------------------------------------------------------------- #
def execute(repo, monkeypatch, calls, k, solve_before=-1, patch_cmd=True):
    log = os.path.join(repo["tmp"], "run.jsonl")
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setattr(runner, "RUNNER_DIR", os.path.join(repo["tmp"], "runner"))
    monkeypatch.setattr(usage_ledger, "USAGE_PATH",
                        os.path.join(repo["tmp"], "usage.jsonl"))
    if patch_cmd:
        monkeypatch.setattr(runner, "build_cli_cmd",
                            lambda *a, **kw: probe_cmd(log, calls=calls,
                                                       solve_before=solve_before))
    run = {"run_id": "broker--probe--r1", "sweep": "broker", "model": "claude-haiku-4-5",
           "effort": "low", "harness": False, "task": "t-broker", "rep": 1,
           "mode": "solo"}
    cfg = {"defaults": {"k_acceptance": k, "timeout_t1_t2_s": 300}}
    row = runner.execute_run(run, cfg, os.path.join(repo["root"], "tasks"),
                             os.path.join(repo["root"], ".scratch"),
                             os.path.join(repo["tmp"], "results.jsonl"))
    return row, log
