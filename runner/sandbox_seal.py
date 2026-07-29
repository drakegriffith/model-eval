"""sandbox_seal.py — proves and enforces filesystem containment for a benchmark
run subprocess (ticket 04), independent of runner/run.py.

Seal = a macOS sandbox-exec profile with two halves, shaped differently on
purpose.

READS are denylist-shaped: allow everything by default, then deny file-read* on
the named absolute paths a model under test must never reach -- the vault, the
global Claude/Codex configs (loaded regardless of cwd), Claude's skills
directory, the global MCP config, and shell history. This is not a general
filesystem jail: an absolute path outside these named roots is still readable,
so "any absolute path the model chooses to read" is not fully closed by this
module alone. That gap belongs to ticket 04.

WRITES are allowlist-shaped (ticket 26, 2026-07-29): file-write* is denied
outright and then re-allowed on an enumerated set -- the run's own scratch tree,
its own apparatus (staged task mirror, broker directory, a run-scoped TMPDIR),
and the CLI runtime state in cli_runtime_write_paths(). Nothing else in the
filesystem is writable, including runner/results/results.jsonl, the canonical
tasks/ tree, and every other run's scratch directory.

Until ticket 26 this module emitted file-read* rules and nothing else, so the
model under test was denied READING the answer key and the results corpus while
remaining free to append a forged row to results.jsonl, truncate it, poison a
canonical task file, or write into a concurrent run's working tree. run.py's
O_APPEND+fsync writer is the runner's own discipline, not containment: it
constrains how the runner writes, not what anything else may do to the file.
Proven and closed in tests/test_write_containment.py against the live run_cli.
The full allowlist, what it deliberately excludes, and the ruling on rows already
collected without containment are in runner/WRITE-CONTAINMENT.md.

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

# Ticket 26. Read by run.py to stamp `write_contained` on every results row.
# A constant rather than a literal True at the call site so that deleting write
# containment from this module turns the corpus field false instead of leaving
# rows that claim a guarantee nothing is enforcing -- a green that never ran
# reads as success, which is the failure this benchmark exists to avoid.
WRITE_CONTAINMENT = True


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


def cli_runtime_write_paths():
    """Paths the model's CLI must be able to WRITE or the measurement breaks.

    Ticket 26. Write containment is allowlist-shaped (see _profile_text), so
    this tier has to be enumerated rather than assumed. Every entry here was
    established empirically, by running the real `claude` and `codex` binaries
    under a candidate profile with macOS sandbox denial logging on
    (`log stream --predicate 'eventMessage CONTAINS "deny(1) file-write"'`) and
    adding back only what a denial actually broke. Guessing this list is not
    safe: the failure mode is not a crash, it is a model that quietly loses a
    tool and scores worse, which lands in results.jsonl as a capability
    difference rather than as an instrument fault.

    The concrete case that makes the point -- with /private/tmp denied, Claude
    Code's Bash tool still ran the command but its wrapper could not write
    /tmp/claude-<id>-cwd, so the wrapper returned 1 for a suite that had passed,
    and the model under test reported "exit code 1" for a green pytest run. A
    seal that silently corrupts what the model observes about its own work is a
    worse instrument than no seal.

    None of these hold benchmark state: ROOT (the corpus, the canonical tasks,
    every scratch tree) is not reachable from any of them, which is what lets
    this tier be broad without reopening the defect.
    """
    home = os.path.expanduser("~")
    return [
        # Claude Code / Codex session state, transcripts, shell snapshots.
        os.path.join(home, ".claude"),
        os.path.join(home, ".codex"),
        os.path.join(home, "Library", "Caches", "claude-cli-nodejs"),
        # Hardcoded /tmp users that do NOT honour TMPDIR: Claude Code's bash
        # wrapper (/tmp/claude-<id>-cwd) and zsh's own temp files
        # (/tmp/zshXXXXXX). The model's TMPDIR is redirected to a run-scoped
        # directory by run_cli; this covers only what ignores it.
        "/private/tmp",
        "/dev",
    ]


# ~/.claude.json is rewritten atomically as sibling temp files and guarded by a
# lock file (~/.claude.json.lock, ~/.claude.json.tmp.<pid>.<hash>), so a literal
# rule on the json path alone leaves the CLI unable to commit config. Matched by
# prefix rather than by naming a directory, because the siblings live directly
# in $HOME and allowing $HOME would allow everything under it.
def _home_claude_json_rule():
    real = os.path.realpath(os.path.expanduser("~")).replace("\\", "\\\\")
    return f'(allow file-write* (regex #"^{real}/\\.claude\\.json"))'


def _rule(action, op, path, kind=None):
    # sandbox-exec matches on the FULLY RESOLVED path. A rule naming an
    # unresolved symlink is accepted by the parser and then silently enforces
    # nothing -- e.g. (deny file-read* (subpath "/tmp/x")) never fires, because
    # the kernel sees /private/tmp/x. A trust control that no-ops quietly is
    # worse than none, so every path is realpath'd before it becomes a rule.
    # (Verified 2026-07-27, ticket 16: the /tmp form leaked, the /private/tmp
    # form blocked.) isdir is likewise checked post-resolution.
    #
    # `kind` overrides that probe. Write-allow roots pass kind="subpath"
    # because they must stay writable as DIRECTORIES even when they do not
    # exist yet at profile-build time -- probing isdir on a not-yet-created
    # cache dir yields "literal", which allows the path itself and denies
    # everything the CLI then tries to put inside it.
    real = os.path.realpath(path)
    keyword = kind or ("subpath" if os.path.isdir(real) else "literal")
    return f'({action} {op} ({keyword} "{real}"))'


def _profile_text(deny_paths, allow_paths=(), write_allow_paths=None):
    """Build the SBPL profile.

    Reads are DENYLIST-shaped (ticket 04/16): allow by default, name the roots
    that must not be read. Writes are ALLOWLIST-shaped (ticket 26): deny
    file-write* outright, then name the only places anything may be written.

    The asymmetry is deliberate and is the whole fix. A write denylist would
    have to enumerate every path worth protecting and would silently miss the
    next one added to the repo; the allowlist inverts the default, so a path
    nobody thought about is contained rather than exposed. It is affordable on
    the write side and not on the read side because the set of things a run
    legitimately writes is small and knowable (its own scratch tree, its own
    apparatus, its CLI's state) while the set it legitimately reads is
    effectively the whole toolchain.

    write_allow_paths=None emits no write rules at all -- the pre-ticket-26
    behaviour, kept only so read-side callers can still express it.

    SBPL is last-match-wins, so allow rules are emitted AFTER the denies they
    carve exceptions out of. Order here is load-bearing (verified ticket 16:
    reversing it re-blocks the allowed path).
    """
    lines = ["(version 1)", "(allow default)"]
    lines += [_rule("deny", "file-read*", p) for p in deny_paths]
    lines += [_rule("allow", "file-read*", p) for p in allow_paths]
    if write_allow_paths is not None:
        lines.append("(deny file-write*)")
        lines += [_rule("allow", "file-write*", p, kind="subpath")
                  for p in list(write_allow_paths) + cli_runtime_write_paths()]
        lines.append(_home_claude_json_rule())
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def sandbox_prefix(deny_paths, allow_paths=(), write_allow_paths=None):
    """Yield an argv prefix that runs any command under the run's profile.

    For callers that must keep their own process-control semantics -- run_cli
    needs Popen(start_new_session=True) plus killpg on timeout, which
    sealed_run's subprocess.run cannot express -- so this hands back the
    sandbox-exec argv to prepend rather than running the command itself.

    Unlike sealed_run, the deny list is entirely the caller's: nothing from
    sensitive_paths() is added, so this cannot silently take the ~/.claude auth
    conflict with it.

    write_allow_paths (ticket 26) is the run-scoped half of the write
    allowlist -- the scratch tree, the staged task mirror, the broker directory,
    the run's private TMPDIR. cli_runtime_write_paths() is appended to it
    automatically, so callers name only what is theirs. Passing None leaves
    writes uncontained, which is the pre-ticket-26 behaviour and is not what the
    live path does.

    The profile file lives only for the duration of the with-block; the caller
    must have started the process before leaving it.
    """
    if not os.path.exists("/usr/bin/sandbox-exec"):
        raise RuntimeError("sandbox-exec not found -- this seal is macOS-only")
    with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
        f.write(_profile_text(deny_paths, allow_paths, write_allow_paths))
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
