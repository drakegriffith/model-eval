"""test_local_family.py — studio/local-family: an LM Studio server on loopback
(http://localhost:1234, Anthropic-compatible endpoint), riding the same claude
binary the kimi arm already rides against Moonshot (run.py:637).

Two claims, cheap to prove:
  1. build_cli_cmd(model, ...) for a "local" family model emits the identical
     claude-CLI invocation shape as claude/kimi, including --effort when asked
     — build_cli_cmd is a pure function, so this needs no subprocess.
  2. run_cli() injects ANTHROPIC_BASE_URL (+ a placeholder auth token) for a
     local-family model, the same lever it already pulls for kimi. Proven
     through the REAL run_cli, launching a stand-in probe rather than the
     claude binary — per the precedent in test_task_dir_seal.py and
     test_live_vault_seal.py, which env vars a subprocess inherits is decided
     by run_cli itself, not by which binary asks, so a probe proves it at zero
     token cost and without requiring an LM Studio server to actually be up.

No model is invoked anywhere in this file.
"""
import importlib
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

PROMPT = "reply with the single word ok"


# --------------------------------------------------------------------------- #
# build_cli_cmd — pure, no subprocess
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", ["glm-4.7-local", "qwen3-coder-next-local", "qwen3.6-35b-a3b-local"])
def test_build_cli_cmd_local_matches_claude_kimi_shape(model):
    """local rides the identical invocation shape as claude/kimi (run.py:467):
    same binary, same flags, only --model differs."""
    claude_cmd = runner.build_cli_cmd("claude-sonnet-5", None, PROMPT)
    local_cmd = runner.build_cli_cmd(model, None, PROMPT)

    assert local_cmd[:2] == ["claude", "-p"]
    assert "--dangerously-skip-permissions" in local_cmd
    assert "--model" in local_cmd
    assert local_cmd[local_cmd.index("--model") + 1] == model

    def without_model_value(cmd, mid):
        return [c for i, c in enumerate(cmd) if not (c == mid and cmd[i - 1] == "--model")]

    assert sorted(without_model_value(local_cmd, model)) == sorted(
        without_model_value(claude_cmd, "claude-sonnet-5")), (
        "local's flag set drifted from claude/kimi's shape")


@pytest.mark.parametrize("model", ["glm-4.7-local", "qwen3-coder-next-local", "qwen3.6-35b-a3b-local"])
def test_build_cli_cmd_local_passes_effort_through(model):
    """--effort passes through for local, same decision as kimi (documented in
    build_cli_cmd's own docstring): unverified whether LM Studio's server
    honours it, but harmless to send, and the verification question belongs to
    probe_endpoints.py's ladder phase, not to command construction."""
    cmd = runner.build_cli_cmd(model, "high", PROMPT)

    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_build_cli_cmd_local_omits_effort_when_none_given():
    cmd = runner.build_cli_cmd("glm-4.7-local", None, PROMPT)
    assert "--effort" not in cmd


def test_build_cli_cmd_local_rejects_an_undeclared_effort_before_building_anything():
    """local declares CLAUDE_TIERS (no 'ultra' — that's codex-6 only); check_effort
    must fire before any command is assembled, same fail-closed contract as
    every other family."""
    with pytest.raises(ValueError):
        runner.build_cli_cmd("glm-4.7-local", "ultra", PROMPT)


# --------------------------------------------------------------------------- #
# build_cli_cmd — driver dispatch (issue #25)
# --------------------------------------------------------------------------- #
def test_build_cli_cmd_no_driver_is_still_the_default_shape():
    """The trailing `driver` kwarg must not break any of the 8+ call sites
    above that predate it and call this positionally with three args."""
    cmd = runner.build_cli_cmd("glm-4.7-local", None, PROMPT)
    assert cmd[:2] == ["claude", "-p"]


def test_build_cli_cmd_claude_code_driver_is_a_no_op():
    """The one driver the claude binary actually implements must not raise."""
    cmd = runner.build_cli_cmd("glm-4.7-local", None, PROMPT, driver="claude-code")
    assert cmd[:2] == ["claude", "-p"]


def test_build_cli_cmd_pi_driver_raises():
    """caec128's defect: glm-stage1-pi declared driver: pi and build_cli_cmd
    launched the claude binary anyway, stamping 15 rows with a driver label
    the binary that ran them did not earn. It must refuse instead."""
    with pytest.raises(ValueError, match="pi"):
        runner.build_cli_cmd("glm-4.7-local", None, PROMPT, driver="pi")


# --------------------------------------------------------------------------- #
# run_cli — the real env-injection path, no model invoked
# --------------------------------------------------------------------------- #
ENV_PROBE = "import json, os, sys\njson.dump(dict(os.environ), sys.stdout)\n"


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature (mirrors test_task_dir_seal.py's `repo`
    fixture): scratch and task dir share a root run_cli can resolve."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-local-family"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")

    scratch = root / ".scratch" / "localfamily--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def via_run_cli(repo, model, monkeypatch):
    """Launch the env probe through the REAL run_cli. GAUNTLET_NO_SANDBOX=1
    because env injection -- the thing under test here -- is orthogonal to the
    filesystem seal, which is already proven separately in
    test_live_vault_seal.py / test_task_dir_seal.py; running unsealed avoids
    coupling this test to sandbox-exec's availability."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", ENV_PROBE], repo["scratch"], 30,
        repo["task_dir"], model=model)
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


def test_run_cli_local_family_injects_base_url_and_placeholder_token(repo, monkeypatch):
    env = via_run_cli(repo, "glm-4.7-local", monkeypatch)

    assert env.get("ANTHROPIC_BASE_URL") == runner.LOCAL_BASE_URL
    assert env.get("ANTHROPIC_API_KEY") == runner.LOCAL_PLACEHOLDER_TOKEN
    assert env.get("ANTHROPIC_AUTH_TOKEN") == runner.LOCAL_PLACEHOLDER_TOKEN
    # Not just present -- non-empty, since an empty value is what makes the
    # claude binary refuse to start against a custom ANTHROPIC_BASE_URL.
    assert env["ANTHROPIC_API_KEY"]
    assert env["ANTHROPIC_AUTH_TOKEN"]


def test_run_cli_local_family_default_base_url_is_localhost_1234(repo, monkeypatch):
    monkeypatch.delenv("MODEL_EVAL_LOCAL_BASE_URL", raising=False)
    importlib.reload(runner)
    try:
        env = via_run_cli(repo, "qwen3-coder-next-local", monkeypatch)
        assert env.get("ANTHROPIC_BASE_URL") == "http://localhost:1234"
    finally:
        importlib.reload(runner)  # restore module state for tests that follow


def test_run_cli_local_family_base_url_is_overridable(repo, monkeypatch):
    monkeypatch.setenv("MODEL_EVAL_LOCAL_BASE_URL", "http://127.0.0.1:9999")
    importlib.reload(runner)
    try:
        env = via_run_cli(repo, "glm-4.7-local", monkeypatch)
        assert env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:9999"
    finally:
        monkeypatch.delenv("MODEL_EVAL_LOCAL_BASE_URL", raising=False)
        importlib.reload(runner)  # restore module state for tests that follow


def test_run_cli_claude_family_does_not_gain_local_env_vars(repo, monkeypatch):
    """Defensive regression: the new elif branch must not leak into the
    unrelated claude arm it sits beside."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    env = via_run_cli(repo, "claude-sonnet-5", monkeypatch)

    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env
