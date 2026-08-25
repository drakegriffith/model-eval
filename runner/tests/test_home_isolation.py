"""test_home_isolation.py -- blocker 2 of studio-handoff findings.md (issue #8):
the "bare" arm was never bare.

run_cli built the model's environment as `dict(os.environ)` and never touched
HOME or CLAUDE_CONFIG_DIR, so the claude binary resolved its global config out
of the OPERATOR's home directory -- ~/.claude/CLAUDE.md, settings, skills,
agents, plugins. On this machine that file is roughly 25k tokens of personal
harness. Every harness=False row therefore measured a model carrying a large
harness nobody registered, and the harness/bare contrast measured the delta
between two harnesses rather than between a harness and none.

Why the sandbox seal is not the answer, and why this file exists anyway:
sensitive_paths() does deny ~/.claude, so a SEALED run could not read
CLAUDE.md. That is isolation as a side effect of a filesystem profile with a
documented opt-out (GAUNTLET_NO_SANDBOX=1, which the GLM seats use), on a
denylist that has been wrong before -- until 2026-07-30 the live deny list was
[ROOT] alone and every row collected before then was open-book against the
vault. Isolation that only exists while a second mechanism is switched on is
not a property of the arm, it is a coincidence. These tests assert it on the
constructed environment itself, unsealed, where it cannot depend on anything
else being enabled.

No model is invoked anywhere in this file: a stand-in probe prints its own
environ, per the precedent in test_local_family.py and test_task_dir_seal.py --
which env vars a subprocess inherits is decided by run_cli, not by which binary
asks.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

# The live-row assertions need execute_run against a real grader, which is the
# harness ticket 34 built. Reused rather than copied, per test_invocation_mode.
from test_pass_completeness_gate import execute  # noqa: E402
from test_pass_completeness_gate import repo as repo_gate  # noqa: E402,F401

ENV_PROBE = "import json, os, sys\njson.dump(dict(os.environ), sys.stdout)\n"


@pytest.fixture
def operator_home(tmp_path, monkeypatch):
    """A stand-in for the operator's real home, holding the exact file that
    contaminates the bare arm. Used as $HOME for the RUNNER process, so these
    tests assert against a fixture instead of against whatever happens to be in
    the home directory of whoever runs them -- and so a green run on CI (where
    ~/.claude does not exist) still inspects a real subject."""
    home = tmp_path / "operator-home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text(
        "# global harness\n" + ("x " * 5000), encoding="utf-8")
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # Positive control: the thing we are proving unreachable has to be there.
    assert (home / ".claude" / "CLAUDE.md").exists()
    return home


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature (mirrors test_local_family.py)."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-home-isolation"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "homeiso--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def probe_env(repo, monkeypatch, model=None, **kw):
    """The env of a subprocess launched through the REAL run_cli. Unsealed:
    the filesystem seal is proven separately (test_live_vault_seal.py) and is
    exactly the mechanism these tests refuse to lean on."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", ENV_PROBE], repo["scratch"], 30,
        repo["task_dir"], model=model, **kw)
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


# --------------------------------------------------------------------------- #
# The arm-level claim: no arm inherits the operator's home.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", [None, "claude-sonnet-5", "glm-4.7-local",
                                   "gpt-5.6-sol"])
def test_no_arm_inherits_the_operator_home(operator_home, repo, monkeypatch, model):
    """Stage-1's autonomy arms depend on this too, not just the bare arm: every
    one of them runs the same claude binary, which reads the same global config
    unless told otherwise. So the assertion is over families, not over one."""
    env = probe_env(repo, monkeypatch, model=model)

    for var in ("HOME", "CLAUDE_CONFIG_DIR"):
        value = env.get(var)
        assert value, f"{var} must be set explicitly, not left to inheritance"
        assert not value.startswith(str(operator_home)), (
            f"{var}={value} is inside the operator's home -- this arm loads "
            f"whatever global harness lives there")


def test_the_global_harness_file_is_not_reachable_through_the_isolated_home(
        operator_home, repo, monkeypatch):
    """The concrete instance, not just the path shape: ~25k tokens of
    CLAUDE.md sit one join away from $HOME, and that join is what the claude
    binary performs."""
    env = probe_env(repo, monkeypatch)

    assert not os.path.exists(os.path.join(env["HOME"], ".claude", "CLAUDE.md"))
    assert not os.path.exists(os.path.join(env["CLAUDE_CONFIG_DIR"], "CLAUDE.md"))
    assert not os.path.exists(os.path.join(env["CLAUDE_CONFIG_DIR"], "settings.json"))
    # Still true of the fixture -- an isolated run must not have deleted it.
    assert (operator_home / ".claude" / "CLAUDE.md").exists()


def test_each_run_gets_its_own_home(operator_home, repo, monkeypatch):
    """Per-run, like TMPDIR and CODEX_HOME (ticket 26/04). A shared scratch home
    would let one run's leftover config reach the next run's model."""
    first = probe_env(repo, monkeypatch)["HOME"]
    second = probe_env(repo, monkeypatch)["HOME"]

    assert first != second


def test_the_isolated_home_is_torn_down(operator_home, repo, monkeypatch):
    """It is a temp directory the run owns, so it must not outlive the run."""
    env = probe_env(repo, monkeypatch)

    assert not os.path.exists(env["HOME"])


def test_the_run_scoped_tmpdir_is_still_separate_from_the_home(
        operator_home, repo, monkeypatch):
    """Regression guard on ticket 26: TMPDIR names a directory on the WRITE
    allowlist by itself. Collapsing the two would silently widen or narrow that
    rule depending on which one the allowlist happens to name."""
    env = probe_env(repo, monkeypatch)

    assert env["TMPDIR"] != env["HOME"]


# --------------------------------------------------------------------------- #
# The seam: a harness level may inject a home ON PURPOSE.
# --------------------------------------------------------------------------- #
def test_an_explicitly_injected_home_is_used(operator_home, repo, monkeypatch,
                                             tmp_path):
    """Isolation is the default, not a wall. A harness level whose whole
    definition is "this agent has these skills and this CLAUDE.md" hands
    run_cli the directory holding them; it is then named on the row, which is
    the difference between a harness that was configured and one that leaked."""
    injected = tmp_path / "harness-level-2-home"
    (injected / ".claude").mkdir(parents=True)
    (injected / ".claude" / "CLAUDE.md").write_text("# level 2", encoding="utf-8")

    env = probe_env(repo, monkeypatch, home_dir=str(injected))

    assert env["HOME"] == str(injected)
    assert env["CLAUDE_CONFIG_DIR"] == str(injected / ".claude")


def test_an_injected_home_survives_the_run(operator_home, repo, monkeypatch,
                                           tmp_path):
    """A caller's directory is the caller's -- only the temp home run_cli
    created itself is deleted."""
    injected = tmp_path / "harness-level-2-home"
    (injected / ".claude").mkdir(parents=True)

    probe_env(repo, monkeypatch, home_dir=str(injected))

    assert injected.exists()


# --------------------------------------------------------------------------- #
# The opt-out, and the row that records which side of it a result came from.
# --------------------------------------------------------------------------- #
def test_the_opt_out_reproduces_the_old_condition_loudly(operator_home, repo,
                                                         monkeypatch, capsys):
    """GAUNTLET_INHERIT_HOME=1 is the control arm's switch, the same shape as
    GAUNTLET_NO_SANDBOX=1 and GAUNTLET_NO_BROKER=1. It exists because on a host
    whose scoped home has no credential provisioned it is the only way to run a
    claude-family arm at all -- and it warns, because a row carrying the
    operator's harness is not comparable with one that does not."""
    monkeypatch.setenv("GAUNTLET_INHERIT_HOME", "1")

    env = probe_env(repo, monkeypatch)

    assert env["HOME"] == str(operator_home)
    assert "WARNING: GAUNTLET_INHERIT_HOME=1" in capsys.readouterr().err


def test_an_injected_home_beats_the_opt_out(operator_home, repo, monkeypatch,
                                            tmp_path):
    """A named directory is an explicit instruction; the env var is an ambient
    one. Explicit beats ambient (resolve_repo_root's precedence, same rule)."""
    monkeypatch.setenv("GAUNTLET_INHERIT_HOME", "1")
    injected = tmp_path / "level-2-home"
    (injected / ".claude").mkdir(parents=True)

    env = probe_env(repo, monkeypatch, home_dir=str(injected))

    assert env["HOME"] == str(injected)


def test_a_live_row_records_whether_the_home_was_isolated(repo_gate, monkeypatch):
    row = execute(repo_gate, monkeypatch, solve=True, rc=0)
    assert row["home_isolated"] is True

    monkeypatch.setenv("GAUNTLET_INHERIT_HOME", "1")
    row = execute(repo_gate, monkeypatch, solve=True, rc=0)
    assert row["home_isolated"] is False, (
        "a row produced with the operator's global harness attached must say "
        "so -- absent-or-false is what every pre-2026-08-25 row means")


def test_a_mock_row_carries_the_field_as_none(repo_gate, monkeypatch):
    """No model was invoked, so the question is vacuous -- same shape as
    `sealed`. The KEY is present; the value is None."""
    monkeypatch.setenv("GAUNTLET_MOCK", "1")
    row = execute(repo_gate, monkeypatch, solve=True, rc=0)

    assert "home_isolated" in row
    assert row["home_isolated"] is None


# --------------------------------------------------------------------------- #
# Auth: isolation must not break the instrument (ticket 04's lesson).
# --------------------------------------------------------------------------- #
def test_the_isolated_home_carries_the_claude_credential(operator_home):
    """Denying ~/.claude outright "does not produce a sealed run, it produces a
    run that could not authenticate" (sandbox_seal.cli_auth_read_paths). Moving
    HOME has the identical failure mode, so the credential is symlinked into
    the scoped home exactly as scoped_codex_home does for codex -- the
    credential is never copied onto disk."""
    with runner.scoped_claude_home() as home:
        link = os.path.join(home, ".claude", ".credentials.json")
        assert os.path.islink(link)
        assert os.readlink(link) == runner.CLAUDE_AUTH_SOURCE
