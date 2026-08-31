"""The claude-family credential path: injection, preflight, provenance.

WHY THIS FILE EXISTS. On 2026-08-28 a t4/t5 cross-family sweep produced 14 rows
(opus 4, sonnet 4, haiku 3, fable 3) with exit_reason=auth_unavailable,
tokens_in=0, tokens_out=0, turns=1 -- the whole claude arm, unmeasured. The
classification was right (run_status.py:111 calls it INFRA) but the runner
spent an attempt per row to learn something it could have learned once, before
the first dispatch.

Two independent review seats re-derived the cause and agreed the ORIGINAL fix
proposal was wrong. Linking $HOME/Library/Keychains into the scoped home does
NOT restore auth, because there are two independent severs, not one:

  (a) the login Keychain is unreachable from a relocated $HOME, and
  (b) run.py:1300 also scopes CLAUDE_CONFIG_DIR, and run.py:806-810 records that
      the macOS subscription credential is keyed PER CONFIG DIR
      ("Claude Code-credentials-<hash>"), so a scoped dir maps to an entry that
      cannot exist no matter what is linked.

Fixing (a) alone leaves (b). So the credential is injected as
CLAUDE_CODE_OAUTH_TOKEN instead -- the same per-family env lever the kimi arm
has used since run.py:1229 -- which restores the measurement while widening the
filesystem seal by exactly nothing.

The token is NOT added to CHILD_ENV_ALLOWLIST. That list's own contract
(run.py:925) is "nothing on this list can carry a credential", and this is a
credential; it is injected per-family after child_env(), where the kimi key is.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run as runner  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path):
    """A minimal task + scratch tree, same shape as test_auth_unavailable.py."""
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


def token_file(tmp_path, value="sk-ant-oat-TESTONLY"):
    p = tmp_path / "claude.env"
    p.write_text("CLAUDE_CODE_OAUTH_TOKEN=%s\n" % value, encoding="utf-8")
    return str(p)


def env_probe(tmp_path, marker):
    """A stand-in binary NAMED `claude` that dumps its env and touches `marker`.

    Touching the marker is what makes "the model never ran" an ASSERTION rather
    than an inference from a missing row: a preflight that refuses must leave no
    marker, and a test that only checked the reason string could not tell a
    refusal from a model that ran and failed.

    The basename matters. run_cli only consults the auth preflight when the
    command it is about to launch is actually the `claude` binary, so a probe
    called anything else would silently skip the very gate these tests exist to
    exercise -- passing for the wrong reason. Naming it `claude` keeps the probe
    inside the gate's scope while still being a script this repo controls.
    """
    d = tmp_path / ("bin-%s" % os.path.basename(marker))
    d.mkdir(parents=True, exist_ok=True)
    shim = d / "claude"
    shim.write_text(
        "#!%s\nimport json,os,sys\nopen(%r,'w').close()\n"
        "sys.stdout.write(json.dumps(dict(os.environ)))\n"
        % (sys.executable, marker), encoding="utf-8")
    shim.chmod(0o755)
    return [str(shim)]


# --------------------------------------------------------------------------- #
# load_claude_token -- the secret never becomes a literal or a log line
# --------------------------------------------------------------------------- #
def test_token_absent_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE",
                        str(tmp_path / "does-not-exist.env"))
    assert runner.load_claude_token() is None


def test_token_read_from_secrets_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", token_file(tmp_path))
    assert runner.load_claude_token() == "sk-ant-oat-TESTONLY"


def test_token_file_without_the_key_returns_none(tmp_path, monkeypatch):
    p = tmp_path / "claude.env"
    p.write_text("SOMETHING_ELSE=x\n", encoding="utf-8")
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", str(p))
    assert runner.load_claude_token() is None


# --------------------------------------------------------------------------- #
# Provenance -- a row that cannot name its auth path is not reproducible
# --------------------------------------------------------------------------- #
def test_auth_source_names_the_injected_path(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", token_file(tmp_path))
    assert runner.claude_auth_source() == "oauth_token_env"


def test_auth_source_names_the_inherited_path(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE",
                        str(tmp_path / "absent.env"))
    assert runner.claude_auth_source() == "inherited_login"


def test_cli_version_is_recorded_or_honestly_absent(monkeypatch):
    """Either a real version string, or None -- never a guess.

    The 14 outage rows carried a symlinked binary PATH and no version, so
    nothing in the corpus can say which build produced them. That is the gap
    this field closes; a fabricated default would reopen it.
    """
    runner._CLAUDE_CLI_VERSION_CACHE.clear()
    v = runner.claude_cli_version()
    assert v is None or (isinstance(v, str) and v[0].isdigit())


def test_cli_version_absent_binary_is_none(monkeypatch):
    runner._CLAUDE_CLI_VERSION_CACHE.clear()
    monkeypatch.setenv("PATH", "/nonexistent")
    assert runner.claude_cli_version() is None


# --------------------------------------------------------------------------- #
# Injection -- claude family only
# --------------------------------------------------------------------------- #
def child_env_via_run_cli(repo, model, monkeypatch, tmp_path, marker):
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    monkeypatch.setattr(runner, "claude_auth_preflight",
                        lambda env: (True, "stubbed-ok"))
    out, _reason, _wall = runner.run_cli(
        env_probe(tmp_path, marker), repo["scratch"], 30, repo["task_dir"],
        model=model)
    return json.loads(out)


def test_token_injected_for_claude_family(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", token_file(tmp_path))
    env = child_env_via_run_cli(repo, "claude-sonnet-5", monkeypatch, tmp_path,
                                str(tmp_path / "m1"))
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat-TESTONLY"


def test_token_not_injected_for_local_family(repo, tmp_path, monkeypatch):
    """The seal is per-family. A local model must not receive a subscription
    credential just because one happens to be on disk."""
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", token_file(tmp_path))
    env = child_env_via_run_cli(repo, "qwen3.8-27b-local", monkeypatch, tmp_path,
                                str(tmp_path / "m2"))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_no_token_file_means_no_variable(repo, tmp_path, monkeypatch):
    """Absent secret must leave the variable absent, not empty-string it --
    an empty credential is a different failure than no credential."""
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE",
                        str(tmp_path / "absent.env"))
    env = child_env_via_run_cli(repo, "claude-sonnet-5", monkeypatch, tmp_path,
                                str(tmp_path / "m3"))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


# --------------------------------------------------------------------------- #
# Preflight -- refuse BEFORE the dispatch, and only for the family it can judge
# --------------------------------------------------------------------------- #
def test_preflight_failure_refuses_before_the_model_runs(repo, tmp_path,
                                                         monkeypatch):
    marker = tmp_path / "ran"
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    monkeypatch.setattr(runner, "claude_auth_preflight",
                        lambda env: (False, "loggedIn=false"))
    _out, reason, _wall = runner.run_cli(
        env_probe(tmp_path, str(marker)), repo["scratch"], 30, repo["task_dir"],
        model="claude-sonnet-5")
    assert reason == "auth_unavailable"
    assert not marker.exists(), "preflight refused but the model still ran"


def test_preflight_pass_lets_the_model_run(repo, tmp_path, monkeypatch):
    """Negative control for the test above. Without this, a preflight that
    refused unconditionally would pass the refusal test."""
    marker = tmp_path / "ran"
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    monkeypatch.setattr(runner, "claude_auth_preflight",
                        lambda env: (True, "loggedIn=true"))
    _out, _reason, _wall = runner.run_cli(
        env_probe(tmp_path, str(marker)), repo["scratch"], 30, repo["task_dir"],
        model="claude-sonnet-5")
    assert marker.exists(), "preflight passed but the model never ran"


def test_preflight_does_not_gate_other_families(repo, tmp_path, monkeypatch):
    """Codex's finding: scoped_claude_home() is entered UNCONDITIONALLY across
    families (run.py:1296), so a check placed there would reject codex, kimi and
    local rows too. The gate belongs to the claude branch, and this asserts it
    stayed there -- a preflight that refused everything would still pass every
    claude-family test above."""
    marker = tmp_path / "ran"
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    monkeypatch.setattr(runner, "claude_auth_preflight",
                        lambda env: (False, "would refuse if consulted"))
    _out, reason, _wall = runner.run_cli(
        env_probe(tmp_path, str(marker)), repo["scratch"], 30, repo["task_dir"],
        model="qwen3.8-27b-local")
    assert reason != "auth_unavailable"
    assert marker.exists(), "a local row was gated by the claude preflight"


def test_preflight_reads_the_exact_child_env(repo, tmp_path, monkeypatch):
    """The preflight is worthless if it inspects the PARENT environment: the
    parent is logged in, which is precisely why the outage was invisible until
    a dispatch was spent. It must see the scoped HOME the model will get."""
    seen = {}
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")

    def spy(env):
        seen.update(env)
        return (True, "spied")

    monkeypatch.setattr(runner, "claude_auth_preflight", spy)
    runner.run_cli(env_probe(tmp_path, str(tmp_path / "m4")), repo["scratch"], 30,
                   repo["task_dir"], model="claude-sonnet-5")
    assert seen, "preflight was never consulted for a claude-family run"
    assert seen.get("HOME") != os.environ.get("HOME"), \
        "preflight saw the operator HOME, not the scoped one"
    assert seen.get("CLAUDE_CONFIG_DIR", "").startswith(seen.get("HOME", "\0"))


# --------------------------------------------------------------------------- #
# The real preflight, against the real CLI. Skipped where there is no binary.
# --------------------------------------------------------------------------- #
def _have_claude():
    try:
        subprocess.run(["claude", "--version"], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _have_claude(), reason="no claude CLI on this host")
def test_live_preflight_positive_control():
    """Rule 4: a gate that has never returned True on a known-good subject has
    not been shown to inspect anything. The operator's own environment is that
    subject -- `claude auth status --json` reports loggedIn there or the whole
    host is logged out, in which case this correctly fails rather than skips."""
    ok, detail = runner.claude_auth_preflight(dict(os.environ))
    assert ok, "positive control failed; host itself is not logged in: %s" % detail


@pytest.mark.skipif(not _have_claude(), reason="no claude CLI on this host")
def test_live_preflight_negative_control(tmp_path):
    """The outage, reproduced in one call instead of 14 rows: a scoped HOME with
    no credential must be judged NOT authenticated."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / ".claude")
    ok, _detail = runner.claude_auth_preflight(env)
    assert not ok, "scoped empty HOME was judged authenticated"


# --------------------------------------------------------------------------- #
# A rejected credential is an instrument fault, not a model failure.
#
# Found by running this PR's own fix end to end on 2026-08-31. The preflight
# passed (see the docstring limit below), the CLI then returned
#
#   api_error_status: 401
#   result: "Failed to authenticate. API Error: 401 OAuth access token is
#            invalid."
#
# and that string matches NONE of the four AUTH_FAILURE_MARKERS, so the row
# landed as cli_error -- the bucket the pre-registration reads as the model
# having failed the task. The model was never asked anything; tokens_in was 0.
#
# Matched on api_error_status, a field the CLI sets, rather than on the prose,
# for the reason the existing marker block already gives: a model writing about
# authentication can print any of these words in a run that worked.
# --------------------------------------------------------------------------- #
def _envelope(**kw):
    base = {"type": "result", "is_error": True, "num_turns": 1,
            "usage": {"input_tokens": 0, "output_tokens": 0}}
    base.update(kw)
    return json.dumps(base)


def test_401_is_an_auth_failure_not_a_cli_error():
    """The exact envelope observed from claude 2.1.252 with a rejected token."""
    out = _envelope(api_error_status=401,
                    result="Failed to authenticate. API Error: 401 OAuth "
                           "access token is invalid.")
    assert runner.cli_auth_failed(out) is True


def test_403_is_also_an_auth_failure():
    out = _envelope(api_error_status=403, result="Forbidden")
    assert runner.cli_auth_failed(out) is True


def test_500_is_not_an_auth_failure():
    """Negative control. Without this, `any nonzero api_error_status` would
    pass the test above while quietly relabelling every server fault as an
    auth problem -- which would hide real outages in the INFRA bucket."""
    out = _envelope(api_error_status=500, result="Internal server error")
    assert runner.cli_auth_failed(out) is False


def test_a_successful_run_is_never_an_auth_failure():
    """The other negative control: is_error false must short-circuit before
    any status code is consulted."""
    out = json.dumps({"type": "result", "is_error": False,
                      "api_error_status": 401, "result": "done"})
    assert runner.cli_auth_failed(out) is False


def test_prose_markers_still_work():
    """The original four markers are not regressed by the structural check."""
    out = _envelope(result="Not logged in - Please run /login")
    assert runner.cli_auth_failed(out) is True
