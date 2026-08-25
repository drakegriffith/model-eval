"""test_grader_env_allowlist.py -- the GRADER's environment, one hop downstream
of the model's.

WHAT WAS OPEN. issue #14's F1 fixed `run_cli`, which builds the environment the
model under test runs in. `graded_run` -- the function that runs `bash verify.sh`
and produces the pass/fail verdict every row is scored on -- still built its
environment as `dict(os.environ)`.

That is the same defect one hop downstream, and downstream is the worse place
for it: the model's environment can bias what the model DOES, but the grader's
environment can change the VERDICT. `PYTHONPATH` puts a different package ahead
of the one under test; `GIT_CONFIG_GLOBAL` changes what `git apply` does;
`NODE_OPTIONS` can preload a module into every node process the suite spawns;
`PIP_INDEX_URL` changes what `pip install -r requirements.txt` fetches. None of
those are the model's doing, and all of them land in the `pass` column.

WHY THE ALLOWLIST IS SMALL ENOUGH TO BE SAFE. Every task's verify.sh was read
before this list was written. Across the 15 shipped tasks the only environment
names they reference are:

    PYTHON_BIN         read with a `${PYTHON_BIN:-python3}` default
    VENV_DIR, STAGE, SCRIPT_DIR, ACCEPT_STATUS, BASH_SOURCE
                       all assigned inside the script before use
    GAUNTLET_TASK_DIR  set by graded_run itself, not inherited

So nothing a task actually reads comes from the parent except through
graded_run's own assignment. PYTHON_BIN is deliberately NOT on the allowlist:
inherited, it silently swaps the interpreter the whole suite runs under, and its
in-script default (`python3`, resolved through PATH) is the honest behaviour.

No model is invoked anywhere in this file.
"""
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

# Restated independently of run.py (harness rule #5).
EXPECTED_GRADER_ALLOWLIST = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
                             "TMPDIR", "TZ"}

# Names that can change a verdict without touching the model's work.
VERDICT_CHANGING = {
    "PYTHONPATH": "/tmp/shadow-packages",
    "PYTHONSTARTUP": "/tmp/evil.py",
    "PYTHON_BIN": "/tmp/not-python",
    "NODE_OPTIONS": "--require /tmp/preload.js",
    "GIT_CONFIG_GLOBAL": "/tmp/evil-gitconfig",
    "PIP_INDEX_URL": "https://pypi.evil.example/simple",
    "CLAUDE_EFFORT": "high",
    "ANTHROPIC_API_KEY": "host-subscription-token",
    "GAUNTLET_BROKER_SOCK": "/tmp/stale.sock",
}

ENV_DUMP_VERIFY = """#!/usr/bin/env bash
set -euo pipefail
env
exit 0
"""


@pytest.fixture
def graded(tmp_path, monkeypatch):
    """A task whose verify.sh prints its own environment, run through the REAL
    graded_run -- the same function the authoritative gate and the broker call."""
    task_dir = tmp_path / "tasks" / "t-grader-env"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("x", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do it", encoding="utf-8")
    (task_dir / "verify.sh").write_text(ENV_DUMP_VERIFY, encoding="utf-8")

    scratch = tmp_path / "scratch" / "grader--env--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)

    for name, value in VERDICT_CHANGING.items():
        monkeypatch.setenv(name, value)
    # Positive control: the contaminants must be present in the PARENT, or every
    # absence assertion below proves only that they were never set.
    assert os.environ["PYTHONPATH"] == "/tmp/shadow-packages"

    rc, out = runner.graded_run(str(scratch), str(task_dir))
    assert rc == 0, out
    env = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


def test_the_grader_does_not_inherit_a_verdict_changing_variable(graded):
    """The finding. Each of these can flip a pass to a fail, or a fail to a
    pass, without the model's work changing at all."""
    leaked = sorted(n for n in VERDICT_CHANGING if n in graded)

    assert leaked == [], f"the grader inherited {leaked} from the operator's shell"


def test_the_grader_environment_is_exactly_the_allowlist_plus_what_it_sets(graded):
    """Exact-set, for the reason the model-side test gives: a subset assertion
    can only catch a leak somebody already thought to name."""
    permitted = EXPECTED_GRADER_ALLOWLIST | {"GAUNTLET_TASK_DIR", "PWD", "SHLVL",
                                             "_", "__CF_USER_TEXT_ENCODING"}

    unexpected = sorted(set(graded) - permitted)

    assert unexpected == [], f"undeclared names in the grader env: {unexpected}"


def test_the_grader_still_gets_what_verify_sh_needs(graded):
    """Positive control. Every absence assertion above is satisfied by an empty
    environment, which would fail every grade in the corpus."""
    assert graded["PATH"]
    assert graded["HOME"]
    assert graded["GAUNTLET_TASK_DIR"]


def test_the_task_dir_points_at_the_real_task_not_the_mirror(graded):
    """Unchanged behaviour, pinned because it is the one name graded_run sets on
    purpose: t3/t4 resolve their hidden acceptance suites through it, and both
    callers here are the runner's own subprocess, outside the model's sandbox."""
    assert graded["GAUNTLET_TASK_DIR"].endswith("t-grader-env")


def test_a_stale_broker_socket_cannot_reach_the_authoritative_grade(graded):
    """Already handled by an explicit pop before this change; kept because the
    allowlist is now what enforces it, and a rule enforced by a different
    mechanism than before needs its test to survive the move."""
    assert "GAUNTLET_BROKER_SOCK" not in graded


def test_python_bin_is_not_inherited_so_the_suite_runs_on_its_own_default(graded):
    """Named separately from the bulk assertion because it is the one plausible
    argument for widening the list. An inherited PYTHON_BIN swaps the interpreter
    the entire acceptance suite runs under; the in-script `${PYTHON_BIN:-python3}`
    default is the honest behaviour and it resolves through PATH."""
    assert "PYTHON_BIN" not in graded


def test_the_grader_allowlist_carries_nothing_that_can_change_a_verdict():
    inspected = 0
    for name in runner.GRADER_ENV_ALLOWLIST:
        inspected += 1
        assert not name.startswith(("PYTHON", "NODE", "GIT_", "PIP_", "NPM_",
                                    "ANTHROPIC", "CLAUDE", "GAUNTLET")), name
    assert inspected == len(EXPECTED_GRADER_ALLOWLIST)
    assert set(runner.GRADER_ENV_ALLOWLIST) == EXPECTED_GRADER_ALLOWLIST
