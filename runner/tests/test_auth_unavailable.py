"""test_auth_unavailable.py -- an instrument that cannot log in must not report
a model that cannot code.

Found by measurement while fixing blocker 2 (issue #8). Isolating HOME and
CLAUDE_CONFIG_DIR moves the claude CLI's credential lookup: on macOS the
subscription credential lives in the login Keychain under a service name keyed
per config dir ("Claude Code-credentials-<hash>"), so a scoped config dir maps
to an entry that does not exist. Probed on this host, 2026-08-25, against a
loopback stub endpoint:

    real HOME, no CLAUDE_CONFIG_DIR   -> 1 request sent, 136094 chars
    CLAUDE_CONFIG_DIR=<tmp>/.claude   -> 0 requests, exit 1, "Not logged in"
    HOME=<tmp> + CLAUDE_CONFIG_DIR    -> 0 requests, exit 1, "Not logged in"

Symlinking ~/.claude/.credentials.json does not help: on this host that file
does not exist at all.

WHY THIS IS A SEPARATE FIELD AND NOT A COMMENT. `claude -p` exits 1 in that
state, run_cli labelled every nonzero exit "cli_error", and the pre-registration
scores any non-ok exit as a failed task. So an unprovisioned scoped home would
have written a full sweep of pass=false rows for a CLI that never sent a single
request -- the exact instrument-fault-as-capability-result confusion that
sandbox_seal.cli_auth_read_paths and cli_runtime_write_paths both exist to
prevent, arriving through the door blocker 2's own fix opened.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

from test_pass_completeness_gate import execute, repo as repo_gate  # noqa: E402,F401

# The real bytes, trimmed to the fields under test, captured from
# `claude -p --output-format json` with CLAUDE_CONFIG_DIR pointed at an empty
# scoped directory. Recorded rather than invented: a detector written against a
# guessed shape is a detector nobody has seen fire.
NOT_LOGGED_IN = json.dumps({
    "is_error": True, "duration_api_ms": 0, "num_turns": 1,
    "stop_reason": "stop_sequence", "session_id": "dbac26de",
    "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    "terminal_reason": "api_error", "subtype": "success",
    "result": "Not logged in · Please run /login", "type": "result",
})

# The negative control: a model that merely SAYS the phrase, in a run that
# worked. Without this, a detector that greps stdout for "Not logged in" scores
# a working run as an instrument fault and looks just as green.
MODEL_SAID_IT = json.dumps({
    "is_error": False, "num_turns": 3,
    "usage": {"input_tokens": 900, "output_tokens": 40,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    "result": "The service returns 'Not logged in - Please run /login' when the "
              "token is absent, so I added a test for that branch.",
    "type": "result",
})

OTHER_FAILURE = json.dumps({
    "is_error": True, "num_turns": 2,
    "usage": {"input_tokens": 500, "output_tokens": 20,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    "result": "API Error: Connection refused (ConnectionRefused)",
    "type": "result",
})


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-auth"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "auth--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def reason_for(repo, monkeypatch, stdout, rc):
    """The reason the REAL run_cli assigns to a CLI that printed `stdout` and
    exited `rc`. A stand-in binary, because the property under test is how
    run_cli labels an exit, not which binary produced it."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    body = f"import sys\nsys.stdout.write({stdout!r})\nsys.exit({rc})\n"
    _out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", body], repo["scratch"], 30, repo["task_dir"])
    return reason


# --------------------------------------------------------------------------- #
# The rule, as a pure predicate -- one place, read by run_cli.
# --------------------------------------------------------------------------- #
def test_the_not_logged_in_result_is_recognised():
    assert runner.cli_auth_failed(NOT_LOGGED_IN) is True


def test_a_model_merely_quoting_the_phrase_is_not_an_auth_failure():
    assert runner.cli_auth_failed(MODEL_SAID_IT) is False


def test_another_error_is_not_relabelled_as_an_auth_failure():
    assert runner.cli_auth_failed(OTHER_FAILURE) is False


def test_empty_output_is_not_an_auth_failure():
    """A CLI killed before it printed anything is a timeout or a crash. Naming
    it an auth failure would be a guess with a label on it."""
    assert runner.cli_auth_failed("") is False


# --------------------------------------------------------------------------- #
# The exit reason run_cli hands back.
# --------------------------------------------------------------------------- #
def test_run_cli_labels_an_unauthenticated_cli_distinctly(repo, monkeypatch):
    assert reason_for(repo, monkeypatch, NOT_LOGGED_IN, 1) == "auth_unavailable"


def test_run_cli_still_labels_other_nonzero_exits_cli_error(repo, monkeypatch):
    """The negative control on the relabelling itself: exactly one failure mode
    moves, and every other nonzero exit keeps the reason it always had."""
    assert reason_for(repo, monkeypatch, OTHER_FAILURE, 1) == "cli_error"


def test_run_cli_leaves_a_clean_exit_alone(repo, monkeypatch):
    assert reason_for(repo, monkeypatch, MODEL_SAID_IT, 0) == "ok"


# --------------------------------------------------------------------------- #
# What the corpus ends up holding.
# --------------------------------------------------------------------------- #
def test_the_row_is_not_a_model_failure(repo_gate, monkeypatch):
    """`pass` is still False -- the general non-ok gate owns that, and nothing
    here weakens it. What changes is that `exit_reason` names the instrument,
    so these rows are greppable and are not read as capability data."""
    import test_pass_completeness_gate as gate
    # The shared stub CLI prints whatever this module-level event says, so
    # redirecting it drives the REAL execute_run -> run_cli path rather than a
    # second stand-in for it.
    monkeypatch.setattr(gate, "RESULT_EVENT", NOT_LOGGED_IN)

    row = execute(repo_gate, monkeypatch, solve=False, rc=1)

    assert row["exit_reason"] == "auth_unavailable"
    assert row["pass"] is False


def test_an_unauthenticated_row_stays_pending_for_a_resumed_sweep(tmp_path):
    """It never ran, so it must retry rather than block behind its own fault --
    the same rule cli_error and timeout already get (existing_ids)."""
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({
        "run_id": "sweep--m--high--bare--t1-py-a--r1",
        "exit_reason": "auth_unavailable"}) + "\n", encoding="utf-8")

    assert runner.existing_ids(str(results)) == set()
