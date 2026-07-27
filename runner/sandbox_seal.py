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

Ticket 16 (2026-07-27) wires ONE narrow entry point of this module into
run.py -- sandbox_prefix(), below -- to seal the benchmark's own answer key
(tasks/*/solution.patch) from the model under test. sealed_run() and
sensitive_paths() remain UNWIRED and still belong to ticket 04, because
denying ~/.claude also denies ~/.claude/.credentials.json, which is how every
subscription run authenticates. sandbox_prefix() takes an explicit deny list
and deliberately does not call sensitive_paths(), so it cannot inherit that
conflict.
"""
import contextlib
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


def _rule(action, path):
    # sandbox-exec matches on the FULLY RESOLVED path. A rule naming an
    # unresolved symlink is accepted by the parser and then silently enforces
    # nothing -- e.g. (deny file-read* (subpath "/tmp/x")) never fires, because
    # the kernel sees /private/tmp/x. A trust control that no-ops quietly is
    # worse than none, so every path is realpath'd before it becomes a rule.
    # (Verified 2026-07-27, ticket 16: the /tmp form leaked, the /private/tmp
    # form blocked.) isdir is likewise checked post-resolution.
    real = os.path.realpath(path)
    keyword = "subpath" if os.path.isdir(real) else "literal"
    return f'({action} file-read* ({keyword} "{real}"))'


def _profile_text(deny_paths, allow_paths=()):
    # SBPL is last-match-wins, so allow rules are emitted AFTER the denies they
    # carve exceptions out of. Order here is load-bearing (verified ticket 16:
    # reversing it re-blocks the allowed path).
    lines = ["(version 1)", "(allow default)"]
    lines += [_rule("deny", p) for p in deny_paths]
    lines += [_rule("allow", p) for p in allow_paths]
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def sandbox_prefix(deny_paths, allow_paths=()):
    """Yield an argv prefix that runs any command under a deny-only profile.

    For callers that must keep their own process-control semantics -- run_cli
    needs Popen(start_new_session=True) plus killpg on timeout, which
    sealed_run's subprocess.run cannot express -- so this hands back the
    sandbox-exec argv to prepend rather than running the command itself.

    Unlike sealed_run, the deny list is entirely the caller's: nothing from
    sensitive_paths() is added, so this cannot silently take the ~/.claude auth
    conflict with it.

    The profile file lives only for the duration of the with-block; the caller
    must have started the process before leaving it.
    """
    if not os.path.exists("/usr/bin/sandbox-exec"):
        raise RuntimeError("sandbox-exec not found -- this seal is macOS-only")
    with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
        f.write(_profile_text(deny_paths, allow_paths))
        profile_path = f.name
    try:
        yield ["/usr/bin/sandbox-exec", "-f", profile_path]
    finally:
        os.remove(profile_path)


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
