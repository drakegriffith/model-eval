"""test_live_codex_seal.py — proves the codex-family arm both RUNS and is sealed
under the live path (ticket 04, codex half).

Two defects, one fix.

1. INSTRUMENT FAULT, shipped 2026-07-30 in `1a6b0d5`. sensitive_paths() denies
   ~/.codex; cli_auth_read_paths() carves back only auth.json; config.toml is
   where codex keeps everything it needs to start. So every codex-family run
   since that commit dies at config load, before any model call:

       $ codex mcp list
       Error: failed to load configuration
       Caused by: Failed to read config file ~/.codex/config.toml: Operation not permitted
       exit 1

   It was missed because the verification was `codex --version`, exit 0 — and
   --version never loads the config. A binary that starts is not a binary that
   ran.

2. MCP SURFACE. Unsealed, this host's ~/.codex/config.toml configures six
   servers — node_repl (executes node), sites-design-picker, computer-use, and
   remote github (bearer PAT), linear, openaiDeveloperDocs. `codex exec` has no
   --strict-mcp-config equivalent, so the claude/kimi fix in
   test_live_mcp_seal.py does not apply here: codex's whole MCP surface is
   file-configured.

THE FIX IS ONE CHANGE. run_cli points CODEX_HOME at a run-scoped temp directory
holding a config.toml that declares no mcp_servers plus an auth.json symlink onto
the host's, which resolves onto the already-allowed literal in
cli_auth_read_paths(). That makes config load succeed (defect 1) with an empty
server list (defect 2), without re-allowing ~/.codex/config.toml — which is
asserted here, because re-allowing it would reopen exactly the vector this ticket
closes.

WHY THE CONTROL ARM IS UNSEALED, AND WHY IT PATCHES SHIPPED CODE. Sealed, the
host ~/.codex cannot be read at all, so a sealed "host home" arm reports no
servers because it crashed — an arm that greens by dying is not a control. The
control is therefore GAUNTLET_NO_SANDBOX=1 with runner.scoped_codex_home
monkeypatched to yield the host ~/.codex. Same self-invalidating shape as
test_live_mcp_seal.strip_mcp_flags: the fake records that it was called and the
control asserts it was, so if run_cli ever stops scoping CODEX_HOME the control
raises instead of quietly becoming the treatment.

WHY THE ARGV IS HAND-BUILT HERE, AND ONLY HERE. This file's observable is what
`codex` does with the environment and profile run_cli constructs, so every arm
drives the real runner.run_cli — never a reimplementation of the seal. The argvs
are probe subcommands (`mcp list --json`, `login status`, `exec --help`) chosen
because they exercise the dependency that was sealed, which is precisely what
`--version` failed to do. The shipped model-invocation argv is a different
object and is pinned elsewhere:
test_live_mcp_seal.py::test_codex_family_does_not_take_the_claude_flags.

Zero tokens: `mcp list`, `login status` and `exec --help` all return without
contacting a model. run_cli returns stdout only (stderr is dropped), so every
assertion here reads an exit reason or stdout, never a stderr message.

Measured 2026-07-30 against codex 0.144.0 under the exact profile run_cli
generates:

    arm                                   mcp list        servers
    unsealed, host CODEX_HOME (control)   exit 0          6
    unsealed, run-scoped CODEX_HOME       exit 0          0
    sealed, run-scoped CODEX_HOME         exit 0          0
    sealed, host CODEX_HOME               exit 1 (config load denied)
"""
import contextlib
import json
import os
import stat
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402
import sandbox_seal  # noqa: E402

HOST_CODEX_HOME = os.path.expanduser("~/.codex")
TIMEOUT_S = 120

# Probe argvs. Each one loads the config that `--version` does not.
MCP_LIST = ["codex", "mcp", "list", "--json"]
LOGIN_STATUS = ["codex", "login", "status"]
EXEC_HELP = ["codex", "exec", "--help"]

# What run_cli's own sealed environment looks like from inside, read back through
# a shell so the assertions are about the profile as the child process meets it.
ENV_PROBE = ["/bin/sh", "-c", r"""
printf 'CODEX_HOME=%s\n' "$CODEX_HOME"
if echo probe > "$CODEX_HOME/probe.txt" 2>/dev/null; then echo WRITE=ok
else echo WRITE=denied; fi
if cat "$CODEX_HOME/config.toml" >/dev/null 2>&1; then echo SCOPED_CFG=readable
else echo SCOPED_CFG=denied; fi
if cat "$HOME/.codex/config.toml" >/dev/null 2>&1; then echo HOST_CFG=readable
else echo HOST_CFG=denied; fi
if cat "$CODEX_HOME/auth.json" >/dev/null 2>&1; then echo AUTH=readable
else echo AUTH=denied; fi
printf 'ENTRIES=%s\n' "$(ls -A "$CODEX_HOME" | tr '\n' ',')"
"""]


# --------------------------------------------------------------------------- #
# Driving the live path
# --------------------------------------------------------------------------- #
def _fake_scoped_home(path, calls):
    """Stand-in for runner.scoped_codex_home that yields `path` and records the
    call. The recording is the self-invalidation: an arm built on this asserts
    `calls` is non-empty, so a run_cli that no longer scopes CODEX_HOME fails
    the control loudly instead of silently making control == treatment."""
    @contextlib.contextmanager
    def _cm():
        calls.append(path)
        yield path
    return _cm


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """run.py's real layout in miniature — same shape as test_live_mcp_seal's
    fixture, because run_cli is the thing under test in both."""
    root = tmp_path_factory.mktemp("codexseal") / "model-gauntlet"
    task_dir = root / "tasks" / "t-codex-seal"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "codexseal--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=True)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def _run(repo, cmd, sealed=True, codex_home=None, auth_source=None):
    """Run `cmd` through the REAL run_cli and return its result.

    sealed=False is the live GAUNTLET_NO_SANDBOX=1 off-switch. codex_home
    replaces runner.scoped_codex_home (the control lever). auth_source replaces
    runner.CODEX_AUTH_SOURCE — a module constant precisely so the negative auth
    arm needs no test-only branch inside the shipped code.
    """
    calls = []
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(runner, "ROOT", repo["root"])
        if sealed:
            mp.delenv("GAUNTLET_NO_SANDBOX", raising=False)
        else:
            mp.setenv("GAUNTLET_NO_SANDBOX", "1")
        if codex_home is not None:
            mp.setattr(runner, "scoped_codex_home",
                       _fake_scoped_home(codex_home, calls))
        if auth_source is not None:
            mp.setattr(runner, "CODEX_AUTH_SOURCE", auth_source)
        out, reason, _wall = runner.run_cli(
            cmd, repo["scratch"], TIMEOUT_S, repo["task_dir"])
    finally:
        mp.undo()
    return {"out": out, "reason": reason, "calls": calls}


def servers(arm, label):
    """The `tools`-array equivalent for codex: the JSON server list the CLI
    itself reports. Asserts the arm actually produced a reading first — a run
    that died at config load emits no JSON, and "no JSON" satisfies every
    "no servers" assertion in this file by accident. Silence is not evidence."""
    assert arm["reason"] == "ok", (
        f"{label}: `codex mcp list` did not exit 0 (reason={arm['reason']}); "
        f"nothing was measured. stdout: {arm['out'][:2000]!r}")
    try:
        parsed = json.loads(arm["out"])
    except ValueError:
        pytest.fail(f"{label}: stdout is not the JSON server list: "
                    f"{arm['out'][:2000]!r}")
    assert isinstance(parsed, list), f"{label}: expected a list, got {parsed!r}"
    return parsed


def probe_fields(arm, label):
    assert arm["reason"] == "ok", (
        f"{label}: the env probe did not run (reason={arm['reason']}); "
        f"stdout: {arm['out'][:2000]!r}")
    fields = dict(line.split("=", 1) for line in arm["out"].splitlines()
                  if "=" in line)
    assert "CODEX_HOME" in fields, (
        f"{label}: run_cli set no CODEX_HOME in the child environment; "
        f"stdout: {arm['out'][:2000]!r}")
    return fields


@pytest.fixture(scope="module")
def control(repo):
    """Unsealed, CODEX_HOME on the host's real ~/.codex. Must leak."""
    return _run(repo, MCP_LIST, sealed=False, codex_home=HOST_CODEX_HOME)


@pytest.fixture(scope="module")
def treatment(repo):
    """Unsealed, run-scoped CODEX_HOME. Isolates the scoping from the seal."""
    return _run(repo, MCP_LIST, sealed=False)


@pytest.fixture(scope="module")
def sealed_live(repo):
    """The real thing: live seal, live scoping, nothing switched off."""
    return _run(repo, MCP_LIST, sealed=True)


@pytest.fixture(scope="module")
def sealed_host_home(repo):
    """Sealed, but pointed back at the host ~/.codex — i.e. the state shipped at
    1a6b0d5. Must still fail config load, or the fix re-allowed the file."""
    return _run(repo, MCP_LIST, sealed=True, codex_home=HOST_CODEX_HOME)


@pytest.fixture(scope="module")
def env_probe(repo):
    return _run(repo, ENV_PROBE, sealed=True)


# --------------------------------------------------------------------------- #
# Control arm — must leak, or the fixture proves nothing.
# --------------------------------------------------------------------------- #
def test_control_arm_leaves_the_codex_mcp_servers_configured(control):
    """Measured 2026-07-30: six servers, including node_repl (executes node) and
    github (authenticated with a bearer PAT). If this ever goes green-by-
    emptiness the fixture has stopped being able to see the leak it exists to
    see, and every assertion below it becomes vacuous."""
    found = servers(control, "control")
    assert found, (
        "control arm reported zero MCP servers from the host ~/.codex, so this "
        "fixture cannot detect the leak it exists to detect -- an emptied host "
        f"config will do this. stdout: {control['out'][:2000]!r}")
    assert control["calls"] == [HOST_CODEX_HOME], (
        "run_cli did not call scoped_codex_home, so the control lever is not "
        "attached to anything and this arm is not a control")


# --------------------------------------------------------------------------- #
# Treatment — RED until run_cli scopes CODEX_HOME.
# --------------------------------------------------------------------------- #
def test_the_scoped_home_closes_the_codex_mcp_surface(control, treatment):
    """The A/B, both arms unsealed so the only difference is which CODEX_HOME
    the live path handed the CLI. Asserted as a contrast rather than as an
    absolute, because an absolute passes just as well when the arm never ran."""
    assert servers(control, "control"), "control arm did not leak; see its own test"
    assert servers(treatment, "treatment") == [], (
        f"MCP servers reached the codex arm: {servers(treatment, 'treatment')}")


def test_the_live_sealed_codex_run_loads_its_config(sealed_live):
    """Defect 1, the instrument fault. Every codex-family run since 1a6b0d5
    exits 1 here, at config load, before any model call."""
    servers(sealed_live, "sealed live")


def test_the_live_sealed_codex_run_has_no_mcp_servers(sealed_live):
    """Defect 2, on the arm a real benchmark row is actually collected under."""
    assert servers(sealed_live, "sealed live") == [], (
        f"MCP servers reached a fully sealed codex run: "
        f"{servers(sealed_live, 'sealed live')}")


def test_the_sealed_arm_can_run_the_subcommand_the_benchmark_uses(repo):
    """`mcp list` is the observable; `exec` is what a run actually invokes.
    Asserting only the observable would leave a fix that happens to satisfy the
    instrument and still breaks the measurement."""
    arm = _run(repo, EXEC_HELP, sealed=True)
    assert arm["reason"] == "ok", (
        f"`codex exec --help` failed under the live seal (reason={arm['reason']}); "
        f"stdout: {arm['out'][:2000]!r}")
    assert "exec" in arm["out"], (
        f"`codex exec --help` produced no usage text: {arm['out'][:2000]!r}")


# --------------------------------------------------------------------------- #
# The fix must NOT be a re-allow of ~/.codex/config.toml — that file is where
# the servers are configured, so reading it back reopens the vector.
# --------------------------------------------------------------------------- #
def test_the_host_codex_config_stays_denied_under_the_seal(sealed_host_home):
    """Behavioural: sealed and pointed at the host home, codex must still die at
    config load. If this starts passing, the deny was lifted and the scoped home
    is cosmetic."""
    assert sealed_host_home["reason"] != "ok", (
        "sealed codex read the host ~/.codex/config.toml -- the deny was "
        f"lifted. stdout: {sealed_host_home['out'][:2000]!r}")
    assert sealed_host_home["calls"] == [HOST_CODEX_HOME], (
        "run_cli did not call scoped_codex_home; this arm proves nothing")


def test_the_host_codex_config_is_unreadable_from_inside_a_sealed_run(env_probe):
    """The same claim read directly out of the child process, which is the only
    place the profile is actually enforced. Paired with AUTH=readable so a probe
    that cannot read anything at all cannot pass it."""
    fields = probe_fields(env_probe, "env probe")
    assert fields["HOST_CFG"] == "denied", (
        "~/.codex/config.toml is readable inside a sealed run -- the file that "
        "configures codex's MCP servers is exactly the file this ticket denies")
    assert fields["AUTH"] == "readable", (
        "the auth symlink does not resolve through the carve-out, so the probe "
        "above may be reporting a blanket failure rather than a targeted deny")


def test_the_auth_carve_out_does_not_name_the_codex_config():
    """Structural twin of the behavioural test: the read allowlist names two
    literal credential files and must never grow config.toml."""
    allowed = sandbox_seal.cli_auth_read_paths()
    assert not any(p.endswith("config.toml") for p in allowed), (
        f"config.toml is in the read carve-out: {allowed}")
    assert HOST_CODEX_HOME in sandbox_seal.sensitive_paths().values(), (
        "~/.codex is no longer in the deny list at all")


# --------------------------------------------------------------------------- #
# Auth. The scoped home must not cost the run its login — and the assertion has
# to be sensitive, i.e. it must fail when the auth is genuinely absent.
# --------------------------------------------------------------------------- #
def test_a_sealed_scoped_run_is_still_logged_in(repo):
    """Corrected 2026-07-30, fourth session. This asserted `"Logged in" in
    arm["out"]`, which cannot pass: measured against codex 0.144.0,
    `login status` writes "Logged in using ChatGPT" to STDERR and leaves stdout
    empty, and run_cli returns stdout only. The original verification read it off
    a terminal, where both streams land on the same screen -- the same shape of
    error as verifying a config-loading binary with `--version`.

    So the exit status is the whole observable here, and what makes it a
    sensitive one is test_login_status_fails_when_the_auth_source_is_absent
    below: it drives the same argv through the same live path with the auth
    source pointed at nothing and requires a non-ok reason. Delete that test and
    this one goes vacuous."""
    arm = _run(repo, LOGIN_STATUS, sealed=True)
    assert arm["reason"] == "ok", (
        f"`codex login status` failed under the scoped home (reason="
        f"{arm['reason']}); the run-scoped CODEX_HOME cost the arm its "
        f"subscription auth. stdout: {arm['out'][:2000]!r}")


def test_login_status_fails_when_the_auth_source_is_absent(repo, tmp_path):
    """The negative arm. Without this, the test above passes just as well
    against a `login status` that reports success unconditionally."""
    bogus = str(tmp_path / "no-such-auth.json")
    arm = _run(repo, LOGIN_STATUS, sealed=True, auth_source=bogus)
    assert arm["reason"] != "ok", (
        "`codex login status` exited 0 with no auth file at all, so it is not "
        f"a sensitive assertion. stdout: {arm['out'][:2000]!r}")


# --------------------------------------------------------------------------- #
# The scoped home itself: what is in it, and that it does not outlive the run.
# --------------------------------------------------------------------------- #
def test_the_scoped_config_declares_no_mcp_servers():
    with runner.scoped_codex_home() as home:
        text = open(os.path.join(home, "config.toml"), encoding="utf-8").read()
    live = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    assert not any("mcp_servers" in ln for ln in live), (
        f"the run-scoped config declares MCP servers: {live}")


def test_the_scoped_home_carries_no_global_agent_instructions():
    """~/.codex/AGENTS.md is loaded from CODEX_HOME regardless of cwd. The
    runner drops the harness text it intends into the scratch dir; anything else
    arriving from a home directory is contamination, and the scoped home is the
    one place it could be reintroduced by accident."""
    with runner.scoped_codex_home() as home:
        entries = sorted(os.listdir(home))
    assert "AGENTS.md" not in entries, (
        f"the run-scoped CODEX_HOME carries global agent instructions: {entries}")
    assert entries == ["auth.json", "config.toml"], (
        f"unexpected contents in the run-scoped CODEX_HOME: {entries}")


def test_the_scoped_home_is_writable_from_inside_the_run(env_probe):
    """codex writes session state under CODEX_HOME. Writes are allowlist-shaped
    (ticket 26), so a scoped home that is not named in the allowlist is a
    read-only home, and the failure mode is a model that quietly loses state
    rather than a crash."""
    fields = probe_fields(env_probe, "env probe")
    assert fields["WRITE"] == "ok", (
        "the run-scoped CODEX_HOME is not writable from inside the seal")
    assert fields["SCOPED_CFG"] == "readable", (
        "the run-scoped config.toml is not readable from inside the seal")


def test_the_scoped_home_is_not_the_models_tmpdir(env_probe):
    """It is apparatus, not workspace. Putting it inside the model's TMPDIR
    would hand the model under test its own CODEX_HOME as a working directory."""
    fields = probe_fields(env_probe, "env probe")
    home = fields["CODEX_HOME"]
    assert home not in ("", HOST_CODEX_HOME), (
        f"CODEX_HOME was not scoped for the run: {home!r}")
    assert "gauntlet-tmp-" not in home, (
        f"the scoped CODEX_HOME lives inside the model's TMPDIR: {home}")


def test_the_scoped_home_does_not_outlive_the_run(env_probe):
    fields = probe_fields(env_probe, "env probe")
    assert not os.path.exists(fields["CODEX_HOME"]), (
        f"the run-scoped CODEX_HOME survived the run: {fields['CODEX_HOME']}")


# --------------------------------------------------------------------------- #
# Teardown. codex replaces auth.json by rename rather than writing through the
# symlink (its binary carries "failed to atomically replace secrets file at"),
# and its refresh tokens are single-use ("your refresh token was already used").
# A run that crosses a refresh therefore consumes the host's token and writes
# the replacement into a directory that is deleted at run end, leaving
# ~/.codex/auth.json holding a spent token -- which breaks the HOST's login, not
# just the run's. The permission verified for ticket 26 (writes through a
# symlink into ~/.codex are allowed) is not the permission codex uses.
# --------------------------------------------------------------------------- #
def test_a_replaced_auth_file_is_copied_back_to_the_source(tmp_path):
    src = tmp_path / "auth.json"
    src.write_text('{"tokens": "original"}', encoding="utf-8")
    os.chmod(src, 0o600)
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(runner, "CODEX_AUTH_SOURCE", str(src))
        with runner.scoped_codex_home() as home:
            link = os.path.join(home, "auth.json")
            assert os.path.islink(link), "auth.json is not a symlink"
            assert os.path.realpath(link) == os.path.realpath(str(src)), (
                "the auth symlink does not point at CODEX_AUTH_SOURCE")
            # Exactly what codex does: write a sibling, rename it over the
            # symlink. The symlink is replaced, not followed.
            fresh = os.path.join(home, "auth.json.tmp")
            with open(fresh, "w", encoding="utf-8") as f:
                f.write('{"tokens": "refreshed"}')
            os.replace(fresh, link)
            assert not os.path.islink(link), "the simulated rename did not fire"
    finally:
        mp.undo()
    assert json.loads(src.read_text(encoding="utf-8"))["tokens"] == "refreshed", (
        "a refreshed auth.json was discarded with the scoped home, leaving the "
        "host holding a spent single-use refresh token")
    assert stat.S_IMODE(os.stat(src).st_mode) == 0o600, (
        "the copied-back credential file is not 0600")


def test_an_untouched_auth_symlink_leaves_the_source_alone(tmp_path):
    """The other half: the common case must not rewrite the host's credentials
    at all. A teardown that copies unconditionally would follow the symlink back
    onto its own target, and a bug there costs Drake his login."""
    src = tmp_path / "auth.json"
    src.write_text('{"tokens": "original"}', encoding="utf-8")
    os.chmod(src, 0o600)
    before = os.stat(src).st_mtime_ns
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(runner, "CODEX_AUTH_SOURCE", str(src))
        with runner.scoped_codex_home() as home:
            assert os.path.islink(os.path.join(home, "auth.json"))
    finally:
        mp.undo()
    assert src.read_text(encoding="utf-8") == '{"tokens": "original"}'
    assert os.stat(src).st_mtime_ns == before, (
        "an untouched run rewrote the host's auth.json")


def test_the_auth_source_is_the_host_credential_file():
    """The constant exists so the negative arm above can point it elsewhere; it
    must still name the real file by default, or the scoped home authenticates
    against nothing in production."""
    assert runner.CODEX_AUTH_SOURCE == os.path.expanduser("~/.codex/auth.json")
    assert runner.CODEX_AUTH_SOURCE in sandbox_seal.cli_auth_read_paths(), (
        "the auth source is not in the read carve-out, so the symlink resolves "
        "onto a denied path")
