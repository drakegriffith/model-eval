"""test_child_env_allowlist.py -- issue #15 finding F1: the model's environment
was SUBTRACTIVE, so the parent session leaked into every arm.

WHAT WAS OPEN. `run_cli` built the child environment as

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

which is a claim about *everything that exists*: every name the author did not
think of rode into the child. Probed leaks included ANTHROPIC_BASE_URL (which
re-points a claude arm at another endpoint entirely), ANTHROPIC_MODEL and
ANTHROPIC_SMALL_FAST_MODEL (which change which model actually answers, under
the row's label), CLAUDE_CODE_MAX_OUTPUT_TOKENS and MAX_THINKING_TOKENS (which
change the serving config the row is reported under, walking straight past
serving_registry's gate because the gate reads the DECLARED config and these
change the ACTUAL one), XDG_CONFIG_HOME / XDG_DATA_HOME (which re-point config
discovery even after blocker 2 scoped HOME), and -- because every sweep on this
machine is launched from inside a Claude Code session -- CLAUDECODE and
CLAUDE_EFFORT.

CLAUDE_EFFORT is the one that ruins the experiment rather than merely
threatening it: `CLAUDE_EFFORT=high` live in the parent is live in every arm,
so an effort ladder measures one effort five times while its rows carry five
different `effort` labels.

WHAT IS ASSERTED HERE. The env is built by ALLOWLIST -- the shape this repo
already uses at product/gauntlet_playground/executor.py:82 and asserts at
runner/tests/test_product_executor.py:271. The expected name set is restated
here as a literal rather than imported from run.py: a checker that learns the
answer from the module it checks cannot fail when that module is wrong.

No model is invoked anywhere in this file. A stand-in probe prints its own
environ, per the precedent in test_home_isolation.py and test_local_family.py:
which variables a subprocess inherits is decided by run_cli, not by which
binary asks.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

ENV_PROBE = "import json, os, sys\njson.dump(dict(os.environ), sys.stdout)\n"

# The allowlist, restated independently of run.py (harness #5). These are the
# names a child process needs to find its interpreter, its home and its locale;
# nothing here can carry a credential, an endpoint, a model choice or a harness.
EXPECTED_ALLOWLIST = {"PATH", "HOME", "SHELL", "USER", "LOGNAME",
                      "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR"}

# What run_cli sets on purpose. Anything in the child's environment outside
# ALLOWLIST | SET_BY_RUN_CLI | the family extras below is a leak.
SET_BY_RUN_CLI = {"GAUNTLET_TASK_DIR", "TMPDIR", "TMP", "TEMP",
                  "CODEX_HOME", "HOME", "CLAUDE_CONFIG_DIR"}

ANTHROPIC_EXTRAS = {"ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}

# Injected by macOS CoreFoundation into every process it starts, after execve
# and outside any launcher's control. Named here rather than quietly widened
# into the allowlist, so the exception stays one OS-owned variable that carries
# a text encoding and cannot carry a credential, an endpoint or a harness.
OS_INJECTED = {"__CF_USER_TEXT_ENCODING"}

# The contaminants, injected into the RUNNER's environment by the fixture below
# so that a green run inspects a real subject instead of depending on whatever
# happened to be exported in the shell that launched pytest.
CONTAMINANTS = {
    "ANTHROPIC_BASE_URL": "http://leaked-endpoint:9999",
    "ANTHROPIC_AUTH_TOKEN": "leaked-subscription-token",
    "ANTHROPIC_API_KEY": "leaked-api-key",
    "ANTHROPIC_MODEL": "some-other-model",
    "ANTHROPIC_SMALL_FAST_MODEL": "some-other-small-model",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "512",
    "MAX_THINKING_TOKENS": "31999",
    "XDG_CONFIG_HOME": "/leaked/xdg/config",
    "XDG_DATA_HOME": "/leaked/xdg/data",
    "CLAUDECODE": "1",
    "CLAUDE_EFFORT": "high",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "OPENAI_API_KEY": "leaked-openai-key",
    "AWS_SECRET_ACCESS_KEY": "should-not-travel",
    "GH_TOKEN": "should-not-travel-either",
}

ARMS = [None, "claude-sonnet-5", "glm-4.7-local", "gpt-5.6-sol", "kimi-k3"]


@pytest.fixture
def contaminated_parent(monkeypatch):
    """The parent session an operator really launches a sweep from."""
    for name, value in CONTAMINANTS.items():
        monkeypatch.setenv(name, value)
    # Positive control: the leak vector has to be present in the parent, or a
    # green assertion below would only prove the variable was never set.
    assert os.environ["CLAUDE_EFFORT"] == "high"
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://leaked-endpoint:9999"
    return CONTAMINANTS


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature (mirrors test_home_isolation.py)."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-child-env"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "childenv--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def probe_env(repo, monkeypatch, model=None, **kw):
    """The env of a subprocess launched through the REAL run_cli. Unsealed: the
    filesystem seal is a separate mechanism with a documented opt-out, and an
    isolation that only holds while a second mechanism is on is a coincidence,
    not a property of the arm (test_home_isolation.py's argument, same here)."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    # kimi is the one family that reads a key file; stub it so the arm can be
    # probed on a machine without the key rather than silently skipped.
    monkeypatch.setattr(runner, "load_kimi_key", lambda: "sk-test-kimi-key")
    out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", ENV_PROBE], repo["scratch"], 30,
        repo["task_dir"], model=model, **kw)
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


# --------------------------------------------------------------------------- #
# The negative control: the exact leak that motivated the fix.
# --------------------------------------------------------------------------- #
def test_the_parent_sessions_effort_does_not_reach_the_arm(
        contaminated_parent, repo, monkeypatch):
    """THE motivating leak (issue #15 F1). A sweep launched from a Claude Code
    session exports CLAUDE_EFFORT; inherited, every rung of an effort ladder
    runs at the parent's effort while wearing its own label, so the ladder is
    fitted over a constant."""
    env = probe_env(repo, monkeypatch, model="glm-4.7-local")

    assert "CLAUDE_EFFORT" not in env, (
        f"CLAUDE_EFFORT={env.get('CLAUDE_EFFORT')!r} reached the model under "
        f"test; every arm of an effort ladder would run at that effort")
    assert "CLAUDECODE" not in env


@pytest.mark.parametrize("model", ARMS)
def test_no_arm_inherits_a_claude_or_xdg_variable(
        contaminated_parent, repo, monkeypatch, model):
    """Asserted over families, not over one: every family here rides a binary
    that reads these names."""
    env = probe_env(repo, monkeypatch, model=model)

    leaked = sorted(k for k in env
                    if k.startswith(("CLAUDE_", "XDG_", "CLAUDECODE"))
                    and k != "CLAUDE_CONFIG_DIR")
    assert leaked == [], f"{model}: leaked {leaked} from the parent session"


@pytest.mark.parametrize("model", ARMS)
def test_no_arm_inherits_an_anthropic_variable_its_branch_did_not_set(
        contaminated_parent, repo, monkeypatch, model):
    """ANTHROPIC_* is either set by the family branch from a known value, or it
    is absent. Never inherited: an inherited base URL means a row labelled
    `claude-sonnet-5` could have been answered by whatever the parent shell
    pointed at."""
    env = probe_env(repo, monkeypatch, model=model)

    anthropic = {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")}
    if model in ("glm-4.7-local", "kimi-k3"):
        assert set(anthropic) == ANTHROPIC_EXTRAS
        assert anthropic["ANTHROPIC_BASE_URL"] != CONTAMINANTS["ANTHROPIC_BASE_URL"]
        assert anthropic["ANTHROPIC_AUTH_TOKEN"] != CONTAMINANTS["ANTHROPIC_AUTH_TOKEN"]
    else:
        assert anthropic == {}, f"{model}: inherited {sorted(anthropic)}"
    # ANTHROPIC_MODEL overrides which model the binary calls, so it is a leak
    # for every family including the two that set a base URL.
    assert "ANTHROPIC_MODEL" not in env
    assert "ANTHROPIC_SMALL_FAST_MODEL" not in env


@pytest.mark.parametrize("model", ARMS)
def test_the_child_environment_is_exactly_the_allowlist_plus_what_run_cli_sets(
        contaminated_parent, repo, monkeypatch, model):
    """The positive assertion, per executor.py's "show the list" spirit: an
    additive env is a claim about ten names, and a claim about ten names can be
    checked by naming them."""
    env = probe_env(repo, monkeypatch, model=model)

    permitted = EXPECTED_ALLOWLIST | SET_BY_RUN_CLI | OS_INJECTED
    if model in ("glm-4.7-local", "kimi-k3"):
        permitted |= ANTHROPIC_EXTRAS
    assert set(env) <= permitted, (
        f"{model}: unexpected names in the child environment: "
        f"{sorted(set(env) - permitted)}")


@pytest.mark.parametrize("model", ARMS)
def test_the_names_a_child_process_needs_survive(
        contaminated_parent, repo, monkeypatch, model):
    """The other half of the A/B: a suite that only asserted absence would go
    green against an empty environment, which is not a fix, it is a broken
    launcher."""
    env = probe_env(repo, monkeypatch, model=model)

    for name in ("PATH", "HOME", "TMPDIR"):
        assert env.get(name), f"{model}: {name} is missing or empty"
    assert env["PATH"] == os.environ["PATH"]
    # TMPDIR is the run-scoped one (ticket 26), not the ambient one. Asserted by
    # name rather than by existence: the directory is a context manager run_cli
    # has already torn down by the time the probe's output is read here.
    assert env["TMPDIR"] != os.environ.get("TMPDIR")
    assert os.path.basename(env["TMPDIR"]).startswith("gauntlet-tmp-")
    assert env["TMP"] == env["TEMP"] == env["TMPDIR"]


def test_a_locale_that_is_set_travels_and_one_that_is_not_does_not(
        contaminated_parent, repo, monkeypatch):
    """Allowlisted names are copied when present and not invented when absent --
    the child must not be handed a locale the parent never had."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)

    env = probe_env(repo, monkeypatch, model="claude-sonnet-5")

    assert env.get("LANG") == "en_US.UTF-8"
    assert "LC_ALL" not in env


# --------------------------------------------------------------------------- #
# The opt-out must stay loud, and must stay possible.
# --------------------------------------------------------------------------- #
def test_the_inherit_home_opt_out_still_hands_over_the_operators_home(
        contaminated_parent, repo, tmp_path, monkeypatch, capsys):
    """GAUNTLET_INHERIT_HOME=1 is how the control arm reproduces the
    pre-blocker-2 condition, and it stamps home_isolated=false on the row. An
    allowlist that dropped HOME would silently disable it, turning a declared
    control arm into an undeclared third condition."""
    operator_home = tmp_path / "operator-home"
    (operator_home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("GAUNTLET_INHERIT_HOME", "1")
    assert runner.home_isolation_enabled() is False

    env = probe_env(repo, monkeypatch, model="claude-sonnet-5")

    assert env["HOME"] == str(operator_home)
    assert "CLAUDE_CONFIG_DIR" not in env
    assert "GAUNTLET_INHERIT_HOME" in capsys.readouterr().err
