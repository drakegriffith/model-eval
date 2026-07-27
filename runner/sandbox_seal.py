"""sandbox_seal.py — proves and enforces filesystem containment for a benchmark
run subprocess (ticket 04), independent of runner/run.py.

Seal = a macOS sandbox-exec profile: allow everything by default, then deny
file-read* on the named absolute paths a model under test must never reach --
the vault, the global Claude/Codex configs (loaded regardless of cwd), Claude's
skills directory, the global MCP config, and shell history.

This is a denylist, not a general filesystem jail: an absolute path outside
these named roots is still readable, so "any absolute path the model chooses to
read" is not fully closed by this module alone. That gap, and the one-line
change runner/run.py would need to adopt this seal, are written up in ticket
04's session log rather than made here -- run.py is owned by a concurrent
session.
"""
import os
import subprocess
import tempfile

VAULT = os.path.expanduser("~/brain-actual-intelligence")


def sensitive_paths():
    """Named absolute paths/roots a sealed run must not be able to read."""
    return {
        "vault": VAULT,
        "claude_global_dir": os.path.expanduser("~/.claude"),
        "claude_global_mcp_config": os.path.expanduser("~/.claude.json"),
        "codex_global_dir": os.path.expanduser("~/.codex"),
        "zsh_history": os.path.expanduser("~/.zsh_history"),
        "bash_history": os.path.expanduser("~/.bash_history"),
    }


def _profile_text(deny_paths):
    lines = ["(version 1)", "(allow default)"]
    for p in deny_paths:
        keyword = "subpath" if os.path.isdir(p) else "literal"
        lines.append(f'(deny file-read* ({keyword} "{p}"))')
    return "\n".join(lines) + "\n"


def sealed_run(cmd, cwd, timeout=None, extra_deny=(), env=None):
    """Run cmd under sandbox-exec, denying sensitive_paths() plus extra_deny.

    env is passed straight through to subprocess.run (None = inherit this
    process's environment); run_cli's per-model env (Kimi key injection,
    GAUNTLET_TASK_DIR) would ride along unchanged if routed through here.

    Returns a subprocess.CompletedProcess (stdout/stderr captured as text).
    Raises RuntimeError off macOS -- sandbox-exec is Darwin-only and that is
    this ticket's scope.
    """
    if not os.path.exists("/usr/bin/sandbox-exec"):
        raise RuntimeError("sandbox-exec not found -- this seal is macOS-only")
    deny = list(sensitive_paths().values()) + list(extra_deny)
    with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
        f.write(_profile_text(deny))
        profile_path = f.name
    try:
        full_cmd = ["/usr/bin/sandbox-exec", "-f", profile_path] + list(cmd)
        return subprocess.run(full_cmd, cwd=cwd, timeout=timeout, env=env,
                               capture_output=True, text=True)
    finally:
        os.remove(profile_path)
