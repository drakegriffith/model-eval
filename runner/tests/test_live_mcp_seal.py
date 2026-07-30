"""test_live_mcp_seal.py — proves the model under test is handed no MCP tools by
the invocation run.py actually builds (ticket 04, MCP half).

The filesystem seal closed in test_live_vault_seal.py denies the vault on disk.
It says nothing about MCP servers, which hand the model tools over a socket
rather than a path. On this machine that surface is the context-mode plugin:
eleven tools including `ctx_search` (queries an FTS5 index built over the vault)
and `ctx_execute_file` / `ctx_batch_execute` (run arbitrary code). A benchmark
run holding those is open-book over a channel the filesystem seal does not
watch.

WHY THE OBSERVABLE IS THE REQUEST, NOT `claude mcp list`. `mcp list` answers
"what does this account have"; this file needs "what can the model under test
call". They are different properties and they disagree -- the flags below empty
the model's tool surface while `mcp list` goes on reporting the server
Connected. An instrument reading a neighbouring property is not a reading of
the property.

So this file reads the `tools` array of the request the CLI really sends, by
pointing ANTHROPIC_BASE_URL at a stub /v1/messages endpoint in-process. `claude`
honours the override while still authenticating with the subscription OAuth
session, so the request carries the real, fully-initialised tool list. No model
is called and no tokens are billed. It is the same lever run_cli already pulls
for the Kimi arm, so it drives the live path rather than a model of it.

ANTHROPIC_API_KEY IS DELIBERATELY NOT SET. A key makes the CLI print "claude.ai
connectors are disabled because ANTHROPIC_API_KEY or another auth source is set"
and ship zero MCP tools -- i.e. it measures the metered Kimi arm, which is not
the arm the servers attach to, and greens on the wrong arm.

WHY THE CONTROL ARM IS UNSEALED. The filesystem seal denies ~/.claude and
~/.claude.json, which is where the plugin's config and code live, so a sealed
run already ships zero MCP tools today -- by accident of a read deny aimed at
something else, not by any decision about MCP. Using the sealed arm as the
control would compare 0 against 0 and green on a fixture that can detect
nothing. The control is therefore GAUNTLET_NO_SANDBOX=1 with the two flags
stripped, which is the only arm where the leak is observable, and the flags are
then shown to close it there. The sealed live arm is asserted separately, as a
regression guard rather than as evidence the flags work.

Measured 2026-07-30 through this fixture, `claude -p --model claude-sonnet-5`:

    arm                                 tools   mcp__
    unsealed, no flags (control)          42      11
    unsealed + the two flags              28       0
    live sealed path as shipped           28       0

This corrects the table recorded in ticket 04's 2026-07-30 section (163 tools /
131 `mcp__`, six claude.ai account connectors). Those numbers do not reproduce
on this path: no account connector appears in the request, `claude mcp list`
reports one server (the plugin) from $HOME, from the vault and from the repo,
and setting ANTHROPIC_BASE_URL does not change what it reports. Whatever
produced 131 was not `build_cli_cmd` + `run_cli` headless. The eleven measured
here are a real leak and are what this file closes.

Per [[conclusive-ab-fixture]] the control arm must LEAK, or the fixture proves
nothing. It is derived from build_cli_cmd's own output with exactly the two
treatment flags removed -- never hand-built, per this ticket's standing lesson
that a reimplementation of the live path is not the live path.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

# The arm under test. claude-family, subscription auth, no metering.
MODEL = "claude-sonnet-5"
PROMPT = "Reply with the single word: ok"
TIMEOUT_S = 240


# --------------------------------------------------------------------------- #
# Stub Anthropic endpoint. Captures every request body; answers end_turn.
# --------------------------------------------------------------------------- #
def _sse(events):
    return "".join(f"event: {n}\ndata: {json.dumps(p)}\n\n"
                   for n, p in events).encode("utf-8")


def _end_turn_events(model):
    msg = {"id": "msg_stub", "type": "message", "role": "assistant",
           "model": model, "content": [], "stop_reason": None,
           "stop_sequence": None,
           "usage": {"input_tokens": 1, "output_tokens": 1}}
    return [
        ("message_start", {"type": "message_start", "message": msg}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "ok"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn",
                                     "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def _end_turn_message(model):
    return {"id": "msg_stub", "type": "message", "role": "assistant",
            "model": model, "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


class _Handler(BaseHTTPRequestHandler):
    captures = None          # rebound per server instance

    def log_message(self, *_a):
        pass                  # pytest output stays readable

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        self.captures.append({"path": self.path, "body": body})
        model = body.get("model") or MODEL
        if "count_tokens" in self.path:
            self._send(200, "application/json",
                       json.dumps({"input_tokens": 1}).encode("utf-8"))
        elif body.get("stream"):
            self._send(200, "text/event-stream", _sse(_end_turn_events(model)))
        else:
            self._send(200, "application/json",
                       json.dumps(_end_turn_message(model)).encode("utf-8"))

    def do_GET(self):
        self.captures.append({"path": self.path, "body": None})
        self._send(200, "application/json", b"{}")


class _Stub:
    """A real HTTP server on loopback, so the subprocess reaches it the way it
    reaches the real API -- over the network, not through a monkeypatch that
    only exists inside this process."""

    def __init__(self):
        self.captures = []
        handler = type("_BoundHandler", (_Handler,), {"captures": self.captures})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Driving the live path
# --------------------------------------------------------------------------- #
MCP_FLAG = "--mcp-config"
STRICT_FLAG = "--strict-mcp-config"


def strip_mcp_flags(cmd):
    """The control arm: the LIVE argv with exactly the treatment removed.

    Not a hand-written command line. If build_cli_cmd stops emitting the flags
    this raises instead of quietly making both arms identical -- a control that
    has silently become the treatment reports the same green as a working seal.
    """
    out, found, i = [], 0, 0
    while i < len(cmd):
        if cmd[i] == MCP_FLAG:
            found += 1
            i += 2          # the flag and its config value
            continue
        if cmd[i] == STRICT_FLAG:
            found += 1
            i += 1
            continue
        out.append(cmd[i])
        i += 1
    assert found == 2, (
        f"control arm found {found} of the 2 MCP flags in the live argv -- "
        "build_cli_cmd no longer emits them, so this control is not a control")
    return out


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """run.py's real layout in miniature, same shape as test_live_vault_seal's
    fixture, because run_cli is the thing under test in both."""
    root = tmp_path_factory.mktemp("mcpseal") / "model-gauntlet"
    task_dir = root / "tasks" / "t-mcp-seal"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "mcpseal--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=True)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def _capture(repo, cmd, sealed):
    """Run `cmd` through the REAL run_cli against the stub endpoint and return
    every request body the CLI sent. sealed=False is the live
    GAUNTLET_NO_SANDBOX=1 off-switch, which is what makes the control a control
    rather than a reimplementation that can drift from the code it stands for."""
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(runner, "ROOT", repo["root"])
        if sealed:
            mp.delenv("GAUNTLET_NO_SANDBOX", raising=False)
        else:
            mp.setenv("GAUNTLET_NO_SANDBOX", "1")
        # Not even a stub key: a key of any kind disables the servers and would
        # green the wrong arm. run_cli pops it too; belt and braces.
        mp.delenv("ANTHROPIC_API_KEY", raising=False)
        with _Stub() as stub:
            mp.setenv("ANTHROPIC_BASE_URL", stub.base_url)
            out, reason, _wall = runner.run_cli(
                cmd, repo["scratch"], TIMEOUT_S, repo["task_dir"])
            captures = list(stub.captures)
    finally:
        mp.undo()
    return {"captures": captures, "out": out, "reason": reason}


def tool_names(arm):
    """Every tool name the CLI offered the model, across all its requests."""
    return [t["name"] for c in arm["captures"]
            for t in ((c["body"] or {}).get("tools") or []) if t.get("name")]


def mcp_tools(arm):
    return sorted({n for n in tool_names(arm) if n.startswith("mcp__")})


def messages_requests(arm):
    return [c for c in arm["captures"]
            if "/v1/messages" in c["path"] and "count_tokens" not in c["path"]]


def assert_measured(arm, label):
    """Assert presence before asserting absence. A CLI that never reached the
    stub -- wrong base URL, auth failure, a crash -- produces zero captures,
    and zero captures satisfy every "no mcp tools" assertion in this file.
    Silence is not evidence."""
    reqs = messages_requests(arm)
    assert reqs, (
        f"{label}: the CLI sent no /v1/messages request (reason={arm['reason']}); "
        f"nothing was measured. stdout: {arm['out'][:2000]!r}")
    assert any("tools" in (r["body"] or {}) for r in reqs), (
        f"{label}: no request carried a `tools` array at all -- the observable "
        "this file reads does not exist in what was captured")
    return reqs


@pytest.fixture(scope="module")
def control(repo):
    """Unsealed, live argv minus the two flags. Must leak."""
    return _capture(repo, strip_mcp_flags(runner.build_cli_cmd(MODEL, None, PROMPT)),
                    sealed=False)


@pytest.fixture(scope="module")
def treatment(repo):
    """Unsealed, live argv exactly as run.py builds it. Isolates the flags."""
    return _capture(repo, runner.build_cli_cmd(MODEL, None, PROMPT), sealed=False)


@pytest.fixture(scope="module")
def sealed_live(repo):
    """The real thing: live argv, live seal, nothing switched off."""
    return _capture(repo, runner.build_cli_cmd(MODEL, None, PROMPT), sealed=True)


# --------------------------------------------------------------------------- #
# Control arm -- must leak, or the fixture is too weak to prove anything.
# --------------------------------------------------------------------------- #
def test_control_arm_hands_the_model_mcp_tools(control):
    """Measured 2026-07-30: 11 of 42 tools were `mcp__*`, the context-mode
    plugin. If this ever goes green-by-emptiness the fixture has stopped being
    able to see the thing it exists to see, and every assertion below it becomes
    vacuous."""
    assert_measured(control, "control")
    assert mcp_tools(control), (
        "control arm exposed no mcp__ tools, so this fixture cannot detect the "
        "leak it exists to detect -- an ANTHROPIC_API_KEY in the environment, a "
        "signed-out session, or every MCP server having been uninstalled will "
        f"all do this. reason={control['reason']} "
        f"tools={sorted(set(tool_names(control)))}")


# --------------------------------------------------------------------------- #
# Treatment -- RED until build_cli_cmd emits the flags.
# --------------------------------------------------------------------------- #
def test_the_flags_close_the_mcp_surface(control, treatment):
    """The A/B, both arms unsealed so the only difference is the two flags.
    Asserted as a contrast rather than as an absolute, because an absolute
    passes just as well when the arm never ran."""
    assert_measured(treatment, "treatment")
    assert mcp_tools(control), "control arm did not leak; see its own test"
    assert not mcp_tools(treatment), (
        f"MCP tools reached the model under test: {mcp_tools(treatment)}")


def test_treatment_keeps_the_models_own_tools(treatment):
    """Over-blocking is the other instrument fault: --strict-mcp-config must
    drop the servers without taking Read/Write/Bash with them, or the arm
    measures a model that could not work rather than one that could not cheat."""
    assert_measured(treatment, "treatment")
    missing = {"Read", "Write", "Bash"} - set(tool_names(treatment))
    assert not missing, (
        f"the MCP seal removed the model's own built-in tools: {missing}")


def test_the_live_sealed_run_hands_the_model_no_mcp_tools(sealed_live):
    """What an actual benchmark row is collected under. Note this arm ships
    zero MCP tools with OR without the flags today -- the filesystem seal denies
    ~/.claude, where the plugin lives -- so it is a regression guard, not
    evidence the flags work. That evidence is the unsealed A/B above."""
    assert_measured(sealed_live, "sealed live")
    assert not mcp_tools(sealed_live), (
        f"MCP tools reached the model in a fully sealed run: "
        f"{mcp_tools(sealed_live)}")


def test_the_live_sealed_run_still_has_its_own_tools(sealed_live):
    """The seal plus the flags together must still leave a working model."""
    assert_measured(sealed_live, "sealed live")
    missing = {"Read", "Write", "Bash"} - set(tool_names(sealed_live))
    assert not missing, f"a sealed run lost its built-in tools: {missing}"


# --------------------------------------------------------------------------- #
# The flag-order invariant. `--mcp-config <configs...>` is VARIADIC: it swallows
# every following argument that does not start with '-'. Getting the order wrong
# fails with "MCP config file not found: <...>" -- but only when a positional
# happens to follow it, so it sits latent until something appends one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", ["claude-sonnet-5", "kimi-k3"])
@pytest.mark.parametrize("effort", [None, "high"])
def test_mcp_config_value_is_never_last(model, effort):
    cmd = runner.build_cli_cmd(model, effort, PROMPT)
    i = cmd.index(MCP_FLAG)
    assert cmd[i + 2] == STRICT_FLAG, (
        "--mcp-config is variadic; the argument after its value must be an "
        f"option or the value list eats it. got {cmd[i:i + 3]!r}")
    assert i + 1 != len(cmd) - 1, (
        "--mcp-config's value is in the last position, where any argument "
        "appended later is silently absorbed into it")


@pytest.mark.parametrize("model", ["claude-sonnet-5", "kimi-k3"])
def test_the_empty_config_declares_no_servers(model):
    cmd = runner.build_cli_cmd(model, None, PROMPT)
    cfg = json.loads(cmd[cmd.index(MCP_FLAG) + 1])
    assert cfg == {"mcpServers": {}}, (
        f"the suppression config must declare zero servers, got {cfg!r}")
    assert STRICT_FLAG in cmd, (
        "an empty --mcp-config without --strict-mcp-config merges with the "
        "discovered servers instead of replacing them")


def test_codex_family_does_not_take_the_claude_flags():
    """codex exec has no --strict-mcp-config equivalent; its MCP surface is
    file-configured and is closed by a run-scoped CODEX_HOME instead. Passing
    these flags to it would be an unknown-argument failure."""
    cmd = runner.build_cli_cmd("gpt-5.6-sol", None, PROMPT)
    assert MCP_FLAG not in cmd and STRICT_FLAG not in cmd
