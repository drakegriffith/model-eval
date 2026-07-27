"""test_sandbox_seal.py — proves the benchmark sandbox is sealed (ticket 04).

Executable assertion for [[conclusive-ab-fixture]]: a canary is planted at a path
standing in for "the vault" (the seeded task's answer), and a zero-token stand-in
probe (fixtures/seal_probe.py) tries to read it plus the other vectors the ticket
names -- global Claude config, global Codex config, global MCP config, Claude
skills, shell history -- once unsealed (mirrors runner/run.py's actual
subprocess.Popen(cwd=scratch, env=os.environ) invocation) and once through
runner/sandbox_seal.sealed_run. A stand-in probe is deterministic and free
because the property under test -- can this process read this absolute path --
is enforced by the kernel via sandbox-exec, identically regardless of which
binary is asking.

Per the conclusive-ab-fixture rule, the control arm MUST leak or the fixture is
too weak to prove anything; that assertion is written and run first.
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import sandbox_seal  # noqa: E402

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "seal_probe.py")
VAULT = os.path.expanduser("~/brain-actual-intelligence")


@pytest.fixture
def vault_canary():
    """Plant a uniquely-tagged file inside the real vault -- standing in for a
    seeded task's answer -- and guarantee its removal even on assertion failure."""
    token = f"GAUNTLET-SEAL-CANARY-{uuid.uuid4().hex[:12]}"
    path = os.path.join(VAULT, f".gauntlet-seal-canary-{uuid.uuid4().hex[:8]}.md")
    assert not os.path.exists(path), "canary path collided; pick a new token"
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        yield path
    finally:
        os.remove(path)


@pytest.fixture
def scratch(tmp_path):
    """Stand-in for run.py's scratch dir: task text + the harness-dropped
    CLAUDE.md the sealed arm is SUPPOSED to still be able to read."""
    (tmp_path / "TICKET.md").write_text("do the task", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("harness text dropped intentionally", encoding="utf-8")
    return tmp_path


def leak_vectors(canary_path):
    return {
        "vault_canary": canary_path,
        "global_claude_config": os.path.expanduser("~/.claude/CLAUDE.md"),
        "global_codex_config": os.path.expanduser("~/.codex/AGENTS.md"),
        "global_mcp_config": os.path.expanduser("~/.claude.json"),
        "claude_skills": os.path.expanduser("~/.claude/skills/tdd/SKILL.md"),
        "shell_history": os.path.expanduser("~/.zsh_history"),
    }


def in_scratch_vectors(scratch):
    return {
        "scratch_ticket": str(scratch / "TICKET.md"),
        "scratch_harness_claude_md": str(scratch / "CLAUDE.md"),
    }


def run_probe(paths_by_name, cwd, sealed):
    paths = list(paths_by_name.values())
    cmd = [sys.executable, PROBE, json.dumps(paths)]
    if sealed:
        out = sandbox_seal.sealed_run(cmd, cwd=cwd, timeout=30)
    else:
        out = subprocess.run(cmd, cwd=cwd, env=dict(os.environ),
                              capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"probe crashed: {out.stderr}"
    by_path = json.loads(out.stdout)
    return {name: by_path[path] for name, path in paths_by_name.items()}


def test_control_arm_leaks_every_named_vector(vault_canary, scratch):
    """Unsealed control: today's actual run.py invocation shape. Every named
    vector must be readable, or this fixture is too weak to prove the seal."""
    vectors = {**leak_vectors(vault_canary), **in_scratch_vectors(scratch)}
    readable = run_probe(vectors, cwd=scratch, sealed=False)
    leaked = {name: ok for name, ok in readable.items() if not ok}
    assert not leaked, f"control arm failed to leak (fixture too weak): {leaked}"


def test_sealed_arm_blocks_named_vectors_but_not_scratch(vault_canary, scratch):
    """Treatment: same probe, same cwd, routed through sandbox_seal.sealed_run.
    Every named leak vector must now be blocked, while the in-scratch harness
    files stay readable -- the seal must not break the intended harness-on arm."""
    named = leak_vectors(vault_canary)
    in_scratch = in_scratch_vectors(scratch)
    readable = run_probe({**named, **in_scratch}, cwd=scratch, sealed=True)

    leaked = {name: ok for name, ok in readable.items() if name in named and ok}
    assert not leaked, f"sealed arm still leaked: {leaked}"

    blocked_scratch = {name: ok for name, ok in readable.items()
                       if name in in_scratch and not ok}
    assert not blocked_scratch, f"seal over-blocked intended scratch files: {blocked_scratch}"
