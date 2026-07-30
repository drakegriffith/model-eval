"""test_live_vault_seal.py — proves the model under test cannot read the VAULT,
the global agent configs, the global MCP config, the skills tree or shell
history, through the seal run.py actually applies (ticket 04).

Why this file exists alongside test_sandbox_seal.py. That file proves the
*mechanism*: sandbox_seal.sealed_run() denies sensitive_paths() and a probe
launched through it reaches none of them. It has been green since 2026-07-27.
What it does not prove -- and what ticket 04 actually claims -- is that a real
benchmark run is sealed, because `sealed_run()` and `sensitive_paths()` were
never on the live path. run_cli() seals through the OTHER entry point,
sandbox_prefix(), whose deny list is `[ROOT]` and nothing else (ticket 16).
ROOT is the gauntlet repo. The vault is not under it.

So the green module test and the open ticket were both telling the truth about
different things, and the gap between them is exactly the condition every row in
results.jsonl was collected under: the answer key was sealed, the vault that may
contain the answers was not. A module that could seal something is not a seal.
This file asserts against runner.run_cli itself, so it cannot go green while the
live path stays open.

Per [[conclusive-ab-fixture]], the control arm must leak or the fixture proves
nothing, and it is written first. No model is invoked: whether a path opens is
decided by the kernel, not by which binary asks, so fixtures/seal_probe.py
stands in for the model at zero token cost.
"""
import json
import os
import sys
import uuid

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402
import sandbox_seal  # noqa: E402

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "seal_probe.py")
VAULT = sandbox_seal.VAULT


@pytest.fixture
def vault_canary():
    """A uniquely-tagged file inside the REAL vault, standing in for the answer
    a seeded task could be cheated with. Planted and removed per test, so a
    failing assertion cannot leave one behind."""
    path = os.path.join(VAULT, f".gauntlet-live-seal-canary-{uuid.uuid4().hex[:8]}.md")
    assert not os.path.exists(path), "canary path collided; rerun"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"GAUNTLET-LIVE-SEAL-CANARY-{uuid.uuid4().hex[:12]}")
    try:
        yield path
    finally:
        os.remove(path)


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature: scratch inside the repo root, a task
    dir beside it. Mirrors test_task_dir_seal.py's fixture because run_cli is
    the thing under test in both and it must be driven the same way."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-vault-seal"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")

    scratch = root / ".scratch" / "vaultseal--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=True)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def leak_vectors(canary_path):
    """The six vectors ticket 04 names, as concrete readable files.

    Deliberately files rather than directory roots: a directory that cannot be
    opened is a weaker claim than a known-secret file that cannot be read, and
    the ticket's threat is the bytes, not the listing.
    """
    return {
        "vault_canary": canary_path,
        "global_claude_config": os.path.expanduser("~/.claude/CLAUDE.md"),
        "global_codex_config": os.path.expanduser("~/.codex/AGENTS.md"),
        "global_mcp_config": os.path.expanduser("~/.claude.json"),
        "claude_skills": os.path.expanduser("~/.claude/skills/tdd/SKILL.md"),
        "shell_history": os.path.expanduser("~/.zsh_history"),
    }


def auth_vectors():
    """What must STAY readable or every subscription/metered run breaks.

    This is the conflict ticket 04 flagged in 2026-07-27 and deferred: the deny
    that closes `claude_skills` and `global_claude_config` is a deny on
    ~/.claude, and the credential file lives inside it. A seal that blocks these
    does not measure a sealed model, it measures a model that could not log in.
    """
    return {
        "claude_credentials": os.path.expanduser("~/.claude/.credentials.json"),
        "codex_auth": os.path.expanduser("~/.codex/auth.json"),
    }


def in_scratch_vectors(scratch):
    """The harness-on arm the seal must not break: run.py drops CLAUDE.md into
    scratch on purpose, and that copy is the treatment being measured."""
    return {
        "scratch_harness_claude_md": os.path.join(scratch, "CLAUDE.md"),
        "scratch_readme": os.path.join(scratch, "README.md"),
    }


def probe(vectors):
    return [sys.executable, PROBE, json.dumps(list(vectors.values()))]


def via_run_cli(repo, vectors, monkeypatch, sealed=True):
    """Launch the probe through the REAL run_cli: real env construction, real
    cwd, real seal. sealed=False is the live GAUNTLET_NO_SANDBOX=1 off-switch,
    which is what makes the control arm a genuine control rather than a
    reimplementation that could drift from the code it stands for."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    if sealed:
        monkeypatch.delenv("GAUNTLET_NO_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    out, reason, _wall = runner.run_cli(probe(vectors), repo["scratch"], 300,
                                        repo["task_dir"])
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    by_path = json.loads(out)
    return {name: by_path[path] for name, path in vectors.items()}


# --------------------------------------------------------------------------- #
# Control arm -- must leak, or the fixture is too weak to prove anything.
# --------------------------------------------------------------------------- #
def test_control_arm_leaks_every_named_vector(repo, vault_canary, monkeypatch):
    """GAUNTLET_NO_SANDBOX=1 through the real run_cli. All six vectors readable."""
    vectors = leak_vectors(vault_canary)
    readable = via_run_cli(repo, vectors, monkeypatch, sealed=False)

    shut = {name for name, ok in readable.items() if not ok}
    assert not shut, f"control arm failed to leak (fixture too weak): {shut}"


def test_control_arm_reads_the_cli_auth_files(repo, monkeypatch):
    """The capability the fix must preserve, measured before the fix touches it."""
    readable = via_run_cli(repo, auth_vectors(), monkeypatch, sealed=False)

    shut = {name for name, ok in readable.items() if not ok}
    assert not shut, f"auth files unreadable even unsealed -- fixture broken: {shut}"


# --------------------------------------------------------------------------- #
# Treatment -- the live seal. RED against run.py as it stands: the live deny
# list is [ROOT], and none of these paths are under ROOT.
# --------------------------------------------------------------------------- #
def test_live_run_cli_cannot_read_the_vault(repo, vault_canary, monkeypatch):
    """The headline claim of ticket 04. The vault is where the answers may
    literally be written down; a benchmark that can read it measures nothing."""
    got = via_run_cli(repo, {"vault_canary": vault_canary}, monkeypatch)

    assert not got["vault_canary"], (
        f"the vault is readable from inside a sealed run at {vault_canary} -- "
        "every results.jsonl row collected under this condition is open-book")


def test_live_run_cli_cannot_read_global_agent_config(repo, vault_canary, monkeypatch):
    """~/.claude/CLAUDE.md and ~/.codex/AGENTS.md load regardless of cwd, so the
    harness-off arm is not actually harness-off while these are reachable --
    which makes the A/B contrast the benchmark measures smaller than it reports."""
    vectors = leak_vectors(vault_canary)
    got = via_run_cli(repo, vectors, monkeypatch)

    leaked = {name for name in ("global_claude_config", "global_codex_config")
              if got[name]}
    assert not leaked, f"global agent config readable inside a sealed run: {leaked}"


def test_live_run_cli_cannot_read_mcp_config_skills_or_history(repo, vault_canary,
                                                               monkeypatch):
    """The remaining three named vectors: the global MCP config (which lists
    vault retrieval tools), the skills tree, and shell history."""
    vectors = leak_vectors(vault_canary)
    got = via_run_cli(repo, vectors, monkeypatch)

    leaked = {name for name in ("global_mcp_config", "claude_skills", "shell_history")
              if got[name]}
    assert not leaked, f"readable inside a sealed run: {leaked}"


# --------------------------------------------------------------------------- #
# The constraints that make this harder than widening the deny list.
# --------------------------------------------------------------------------- #
def test_seal_keeps_the_cli_auth_files_readable(repo, monkeypatch):
    """The auth carve-out. Denying ~/.claude also denies
    ~/.claude/.credentials.json, which is how every subscription run
    authenticates -- and denying ~/.codex takes ~/.codex/auth.json with it.
    A run that cannot log in produces a row that looks like a capability result
    and is an instrument fault, which is the failure mode this whole seal exists
    to prevent."""
    readable = via_run_cli(repo, auth_vectors(), monkeypatch)

    blocked = {name for name, ok in readable.items() if not ok}
    assert not blocked, f"seal blocked CLI auth -- runs cannot authenticate: {blocked}"


def test_auth_carve_out_does_not_reopen_the_directory(repo, vault_canary, monkeypatch):
    """The carve-out must be surgical. Re-allowing ~/.claude wholesale to fix
    auth would hand back CLAUDE.md and the skills tree in the same rule, which
    is the deny this ticket is for. Asserted separately from the deny tests
    because a too-wide fix passes those only by accident of ordering."""
    vectors = {**leak_vectors(vault_canary), **auth_vectors()}
    got = via_run_cli(repo, vectors, monkeypatch)

    assert got["claude_credentials"], "auth carve-out did not take effect"
    assert not got["global_claude_config"], (
        "the auth carve-out re-opened ~/.claude wholesale -- CLAUDE.md is "
        "readable again")
    assert not got["claude_skills"], (
        "the auth carve-out re-opened the skills tree")


def test_seal_does_not_break_the_harness_on_arm(repo, monkeypatch):
    """run.py drops CLAUDE.md into scratch deliberately -- that copy IS the
    harness-on treatment. Sealing the global one must not touch the local one."""
    readable = via_run_cli(repo, in_scratch_vectors(repo["scratch"]), monkeypatch)

    blocked = {name for name, ok in readable.items() if not ok}
    assert not blocked, f"seal over-blocked the scratch tree: {blocked}"
