#!/usr/bin/env python3
"""run.py — headless benchmark runner for the model-gauntlet.

Copies each task's base/ into a throwaway git-inited scratch dir, optionally drops
the benchmark harness (CLAUDE.md/AGENTS.md) in, composes the prompt, invokes the
chosen CLI headlessly (per runner/CLI-FACTS.md) with a hard wall-clock timeout,
then grades OURSELVES as the authoritative pass/fail gate -- in a disposable copy
of the scratch tree with the task's canonical verify.sh and test suite overlaid,
so the graded party cannot rewrite its own grade (ticket 18). Appends one JSONL
row per run to runner/results/results.jsonl.

Stdlib only. Both CLIs run on subscriptions — we never set ANTHROPIC_API_KEY /
OPENAI_API_KEY.

MOCK mode (no tokens spent):
  GAUNTLET_MOCK=1     -> apply tasks/<id>/solution.patch instead of calling a CLI
  GAUNTLET_MOCK=fail  -> do nothing (leaves base unchanged, verify.sh should fail)
"""
import argparse
import contextlib
import fnmatch
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone

import broker
import corpus_guard
import local_endpoint
import registry
import run_status
import sandbox_seal
import serving_registry
import usage_ledger

# Import direction is one-way and now acyclic (ticket 30): run -> usage_ledger ->
# registry, with registry a leaf importing nothing. Until ticket 30 the model
# registry lived in this file, so usage_ledger had to import run back locally to
# resolve a model -- a leaf module paying for a god module to answer "what family
# is this". That local import is gone. Do not add an `import run` anywhere below
# this line's dependents.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/code/model-gauntlet
RUNNER_DIR = os.path.join(ROOT, "runner")

# Ticket 37: usage_ledger became a core module and stopped deriving any path from
# its own __file__, so the instrument now tells the ledger where its tree is
# rather than the other way round. This file is the instrument -- deriving ROOT
# from __file__ here is correct and always was; what was wrong was a core module
# doing it on the instrument's behalf.
USAGE_PATH = usage_ledger.paths_for_repo(ROOT).usage

# Issue #24: the DEFAULT --results path, named so main()'s --results argparse
# default and the issue #23 guard below both read it off one constant instead
# of two literals that could drift apart.
DEFAULT_RESULTS_PATH = os.path.join(RUNNER_DIR, "results", "results.jsonl")

# --- Kimi K3 (Moonshot) ---------------------------------------------------- #
# K3 has no native agent CLI; we drive it through Codex's OpenAI-compatible
# provider path. Its API key lives in a gitignored secrets file, whose location
# is read here at runtime only — never hard-coded, echoed, or committed. The
# default is a generic per-user path; override GAUNTLET_KIMI_KEY_FILE to point
# at wherever your own secrets actually live.
KIMI_KEY_FILE = os.path.expanduser(
    os.environ.get("GAUNTLET_KIMI_KEY_FILE", "~/.gauntlet/secrets/kimi.env")
)
# Codex 0.144 only speaks the Responses API, which Moonshot doesn't serve; instead
# we drive K3 through Claude Code against Moonshot's Anthropic-compatible endpoint.
MOONSHOT_ANTHROPIC_URL = "https://api.moonshot.ai/anthropic"
# The claude family's own credential, same shape and same rules as the kimi key
# above: read at runtime from a secrets file that lives OUTSIDE every repo,
# injected into a child env only, never hard-coded, echoed or committed.
#
# WHY A TOKEN AND NOT THE KEYCHAIN. run_cli scopes both HOME and
# CLAUDE_CONFIG_DIR (below), and the macOS subscription credential is keyed PER
# CONFIG DIR -- see the auth-availability block further down. So there are two
# independent severs: an unreachable login Keychain AND a per-config-dir service
# name that cannot exist for a fresh tempdir. Linking Library/Keychains into the
# scoped home fixes only the first, which is why that option was rejected. An
# injected token answers both and widens the filesystem seal by nothing.
CLAUDE_TOKEN_FILE = os.path.expanduser(
    os.environ.get("GAUNTLET_CLAUDE_TOKEN_FILE", "~/.secrets/claude.env")
)
# List-price $/1M tokens. Cache-miss input is $3; cache-hit is $0.30. We charge
# the cap at the conservative cache-miss rate.
KIMI_PRICE_IN = 3.0
KIMI_PRICE_OUT = 15.0

# studio/local-family: same claude binary, pointed at an LM Studio server serving
# an Anthropic-compatible endpoint on loopback instead of Moonshot's. Overridable
# because LM Studio's default port (1234) is a local dev convention, not a fact
# about the instrument -- a different port or a remote box shouldn't need a code
# change. No key file and no price constants here: unlike Kimi this family is
# unmetered, so there is nothing to load and nothing to charge.
#
# Single-sourced in local_endpoint.py (issue #5): this used to be a literal
# defined here AND, byte-identical, in probe_endpoints.py, with nothing pinning
# the two copies equal, so a probe could certify one endpoint while a run
# dispatched against a different one.
LOCAL_BASE_URL = local_endpoint.get_local_base_url()
LOCAL_PLACEHOLDER_TOKEN = local_endpoint.LOCAL_PLACEHOLDER_TOKEN

def load_kimi_key():
    """Return MOONSHOT_API_KEY from the gitignored secrets file, or None.

    The value is never logged; callers inject it into a subprocess env only.
    """
    try:
        with open(KIMI_KEY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MOONSHOT_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return None


def load_claude_token():
    """Return CLAUDE_CODE_OAUTH_TOKEN from the out-of-repo secrets file, or None.

    The value is never logged; callers inject it into a subprocess env only.
    Mint one with `claude setup-token`. Absent file and present-but-keyless file
    are both None: the caller must not set an empty variable, because an empty
    credential and no credential are different failures and the CLI reports them
    differently.
    """
    try:
        with open(CLAUDE_TOKEN_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val or None
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None
    return None


def load_claude_api_key():
    """Return ANTHROPIC_API_KEY from the out-of-repo secrets file, or None.

    First-party console key, distinct from the subscription setup-token above.
    Drake runs the claude arm on this deliberately: the API is more stable than
    a subscription session, which cannot be re-authenticated without a human.
    The value is never logged; callers inject it into a subprocess env only.
    """
    try:
        with open(CLAUDE_TOKEN_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val or None
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None
    return None


def claude_auth_source():
    """Name the credential path a claude-family row ran under.

    Precedence -- api_key > oauth_token_env > inherited_login -- is decided
    HERE rather than left to whichever variable the CLI happens to prefer. A
    secrets file holding both must produce exactly one credential, or which one
    authenticated the row is undecidable from the row itself.

    Recorded on every row. The 14 auth_unavailable rows of 2026-08-28 carried no
    such field, so nothing in the corpus can say which auth arrangement produced
    which row -- the rows are not invalidated by that, but they are not
    reproducible either, and a field is cheaper than the argument.
    """
    if load_claude_api_key():
        return "api_key"
    return "oauth_token_env" if load_claude_token() else "inherited_login"


_CLAUDE_CLI_VERSION_CACHE = {}


def claude_cli_version():
    """The `claude` binary's own version string, or None when it cannot be read.

    None, never a default. The outage rows recorded a symlinked binary PATH,
    which names a pointer rather than a build; a fabricated version would be the
    row asserting something nobody measured.
    """
    if "v" in _CLAUDE_CLI_VERSION_CACHE:
        return _CLAUDE_CLI_VERSION_CACHE["v"]
    v = None
    try:
        p = subprocess.run(["claude", "--version"], capture_output=True,
                           text=True, timeout=30)
        if p.returncode == 0:
            m = re.search(r"\d+\.\d+\.\d+", p.stdout or "")
            v = m.group(0) if m else None
    except (OSError, subprocess.SubprocessError):
        v = None
    _CLAUDE_CLI_VERSION_CACHE["v"] = v
    return v


def reported_cost_usd(out):
    """The CLI's own `total_cost_usd` from the LAST result event, or None.

    Read from the CLI rather than computed from a price table in this repo.
    registry.py's docstring points at a `pricing` module that does not exist,
    and there is no rate here for opus-5/sonnet-5/haiku-4.5/fable-5 -- inventing
    one would put a number with no enumerator behind it into the corpus.
    probe_endpoints.py already reads this same field.

    None, not 0.0, when absent or unparseable: "the CLI did not say" and "the
    run was free" are different facts and only one of them is measured.
    """
    for line in reversed([l for l in (out or "").splitlines() if l.strip()]):
        try:
            cand = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(cand, dict) and cand.get("type") == "result":
            v = cand.get("total_cost_usd")
            return v if isinstance(v, (int, float)) else None
    return None


def run_is_metered(model):
    """True when THIS run costs real money, auth path included.

    is_metered() answers from the registry, which is a property of the model id
    -- and every claude model declares metered=False because a subscription run
    is $0. Point the same model at the first-party API and each row bills.
    Metering is therefore a property of the credential, not of the model, and
    --max-usd was silently inert for the claude family until this existed.

    Widens the registry fact, never replaces it: kimi stays metered whatever the
    claude credential looks like.
    """
    if is_metered(model):
        return True
    return (model_family(model) == "claude"
            and claude_auth_source() == "api_key")


def row_dollars(model, tokens_in, tokens_out, cost_usd):
    """What this row contributes to cumulative spend.

    Prefers the figure the CLI reported. Falls back to the kimi price table,
    which is the one family this repo actually has rates for. Otherwise 0.0 --
    the row still counts as metered, so a missing cost shows up as
    cost_usd=None in the corpus rather than as a fabricated dollar amount.
    """
    if cost_usd is not None:
        return float(cost_usd)
    if model_family(model) == "kimi":
        return kimi_dollars(tokens_in, tokens_out)
    return 0.0


def claude_auth_preflight(env):
    """(ok, detail) for whether `claude` can authenticate under EXACTLY `env`.

    The parent process is logged in -- that is precisely why the 2026-08-28
    outage was invisible until a dispatch had been spent. So this is handed the
    child env the model will actually receive, scoped HOME and all, and asks the
    CLI itself rather than inferring from the presence of a file.

    WHAT THIS CANNOT TELL YOU. `claude auth status` reports on the credential it
    can SEE, not one it has exercised: measured 2026-08-31, it answers
    loggedIn=true method=oauth_token for a well-formed setup-token that the API
    then rejects with 401. So this gate catches the absent credential -- the
    2026-08-28 outage, where a scoped HOME had nothing at all -- and does not
    catch an expired or revoked one. That case still costs one row, and is
    caught after the fact by cli_auth_failed() reading api_error_status. Closing
    it here would mean spending a real request per sweep to prove the token
    works, which is a trade worth making only if revoked tokens become common.

    FAIL CLOSED, WITH ONE EXCEPTION. A CLI that answers "not logged in" is a
    refusal. A preflight that cannot RUN -- no binary, timeout, unparseable
    output -- returns True: this is an instrument check, and letting an
    unreadable check block the sweep would convert an apparatus fault into a
    fleet-wide outage. The run then fails the way it always did, on the CLI's
    own envelope via cli_auth_failed(), which is no worse than before this
    function existed.
    """
    try:
        p = subprocess.run(["claude", "auth", "status", "--json"],
                           capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return True, "preflight-could-not-run: %s" % type(e).__name__
    try:
        obj = json.loads(p.stdout or "")
    except json.JSONDecodeError:
        return True, "preflight-unparseable"
    if not isinstance(obj, dict) or "loggedIn" not in obj:
        return True, "preflight-no-verdict"
    if obj.get("loggedIn"):
        return True, "loggedIn=true method=%s" % (obj.get("authMethod") or "?")
    return False, "loggedIn=false"


def kimi_dollars(tokens_in, tokens_out):
    return tokens_in / 1e6 * KIMI_PRICE_IN + tokens_out / 1e6 * KIMI_PRICE_OUT


# --------------------------------------------------------------------------- #
# Model registry -- MOVED to runner/registry.py (ticket 30)
# --------------------------------------------------------------------------- #
# The roster, the alias table, the three tier ladders and the four resolution
# functions now live in registry.py, which owns their interface contract. They are
# re-exported here by name, unchanged and as the same objects, so every call site
# below and every external caller of `run.<symbol>` keeps working and the import
# switch stays atomic -- no intermediate commit has a broken tree.
#
# Add a model in registry.py, not here. This block is a forwarding seam, and if it
# ever grows a second definition of one of these names the two halves of the
# instrument will disagree about the roster; test_registry.py asserts identity
# (`run.MODELS is registry.MODELS`) to catch exactly that.
from registry import (  # noqa: E402
    ALIASES, CLAUDE_TIERS, CODEX_TIERS, CODEX_TIERS_6, MODELS,
    check_effort, is_metered, model_family, resolve_model,
)

# The run_id format, owned by one module and shared with its readers (judge.py)
# rather than restated as an f-string here and a comment there -- blocker 3.
# Imported under a name so the module object is what call sites use; `run_id` is
# already the name of the local variable holding one all over this file.
import run_id as run_id_mod  # noqa: E402


DONE_GATE_SENTENCE = (
    "\n\n---\nYour work is judged solely by running `bash verify.sh` from the "
    "repository root; it must exit 0. Run it yourself and confirm a clean exit "
    "before you finish. Do not edit verify.sh or the tests."
)

# Ticket 17 section 5: "K is disclosed to the model in the prompt. A budget the
# agent can plan against is the practitioner's situation and matches 03's
# estimand; a hidden budget instead measures how a model behaves when its tools
# begin failing for reasons it cannot see." So the cap is stated, and so is what
# happens at exhaustion -- a model that does not know the run ends cannot budget
# its last check.
BROKER_INSTRUCTION_TMPL = (
    "\n\n`verify.sh` is a metered feedback channel, not a local test run. It "
    "reports a PASS/FAIL verdict and test counts only -- no test names, no file "
    "paths, no assertion text -- and you may call it at most {k} times in this "
    "run. Request {k1} ends the run immediately and it is scored as a failure, "
    "so spend the budget deliberately. Any tests inside this working copy are "
    "yours and are unmetered: run them directly as often as you like."
)

HYBRID_INSTRUCTION = (
    "\n\n---\nYou are the ORCHESTRATOR, not the implementer. Do NOT write the "
    "solution code yourself. Delegate the actual code changes to the `codex` CLI. "
    "From the repository root run, as many times as needed:\n"
    "    codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "
    "\"<precise instructions plus the task text>\"\n"
    "After each codex run, inspect the changes, run `bash verify.sh`, and if it does "
    "not exit 0 call `codex exec` again with corrective instructions. Keep iterating "
    "until `bash verify.sh` exits 0."
)


# --------------------------------------------------------------------------- #
# Minimal YAML reader (subset: block maps/seqs, flow {..}/[..], scalars).
# --------------------------------------------------------------------------- #
def _coerce(v):
    v = v.strip()
    if v == "" or v == "~" or v == "null":
        return None
    if (v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'"):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _parse_flow(s):
    """Parse an inline flow scalar/list/map. Recursion depth is shallow here."""
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_flow(p) for p in _split_top(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        d = {}
        if inner:
            for p in _split_top(inner):
                k, _, val = p.partition(":")
                d[k.strip()] = _parse_flow(val)
        return d
    return _coerce(s)


def _split_top(s):
    """Split on commas that are not nested inside [] or {}."""
    parts, depth, buf = [], 0, ""
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def parse_yaml(text):
    """Parse the constrained YAML subset used by runs.yaml into Python objects."""
    lines = []
    for raw in text.splitlines():
        # strip full-line and trailing comments (naive: not inside quotes/flow)
        if "#" in raw:
            in_flow = 0
            out = []
            for ch in raw:
                if ch in "[{":
                    in_flow += 1
                elif ch in "]}":
                    in_flow -= 1
                if ch == "#" and in_flow == 0:
                    break
                out.append(ch)
            raw = "".join(out)
        if raw.strip() == "":
            continue
        lines.append(raw.rstrip())

    def build(idx, indent):
        # Returns (value, next_index)
        if idx >= len(lines):
            return None, idx
        first = lines[idx]
        is_seq = first.lstrip().startswith("- ")
        container = [] if is_seq else {}
        while idx < len(lines):
            line = lines[idx]
            ind = _indent(line)
            if ind < indent:
                break
            if ind > indent:  # shouldn't happen at this level
                break
            stripped = line.strip()
            if stripped.startswith("- "):
                item = stripped[2:].strip()
                if ":" in item and not item.startswith(("[", "{")):
                    # inline "- key: value" possibly starting a map that continues
                    submap = {}
                    k, _, v = item.partition(":")
                    v = v.strip()
                    if v == "":
                        val, idx = build(idx + 1, ind + 4)
                        submap[k.strip()] = val
                    else:
                        submap[k.strip()] = _parse_flow(v)
                        idx += 1
                    # continuation lines of the same map item (deeper indent)
                    while idx < len(lines) and _indent(lines[idx]) > ind:
                        ln = lines[idx].strip()
                        kk, _, vv = ln.partition(":")
                        vv = vv.strip()
                        if vv == "":
                            val, idx = build(idx + 1, _indent(lines[idx]) + 2)
                            submap[kk.strip()] = val
                        else:
                            submap[kk.strip()] = _parse_flow(vv)
                            idx += 1
                    container.append(submap)
                else:
                    container.append(_parse_flow(item))
                    idx += 1
            else:
                k, _, v = stripped.partition(":")
                v = v.strip()
                if v == "":
                    val, idx = build(idx + 1, ind + 2)
                    container[k.strip()] = val
                else:
                    container[k.strip()] = _parse_flow(v)
                    idx += 1
        return container, idx

    value, _ = build(0, 0)
    return value


# --------------------------------------------------------------------------- #
# Config -> run list
# --------------------------------------------------------------------------- #
def resolve_effort(effort, model, winning):
    """Expand the literal "WINNING" against winning_effort in the config.

    winning_effort may be keyed by alias (as the existing runs.yaml files do) or
    by canonical id; both are tried so old configs keep resolving. hybrid inherits
    fable's winning effort because hybrid IS fable orchestrating.
    """
    if isinstance(effort, str) and effort.upper() == "WINNING":
        if model == "hybrid":
            return winning.get("hybrid", winning.get("fable", "high"))
        if model in winning:
            return winning[model]
        try:
            mid = resolve_model(model)[0]
        except ValueError:
            return "high"
        if mid in winning:
            return winning[mid]
        # alias whose canonical id was used as the key, or vice versa
        for alias, canonical in ALIASES.items():
            if canonical == mid and alias in winning:
                return winning[alias]
        return "high"
    return effort


def serving_config_from(cfg):
    """The serving config the runs config DECLARES every run will use.

    The source is the `serving:` block, and it is deliberately a DECLARATION
    rather than a probe of the live server. Two reasons. Gating has to be
    deterministic and reviewable -- the thing a result is labelled with belongs
    in version control next to the matrix that produced it, not in whatever
    state a GUI happened to be in at dispatch time. And the pre-registration's
    "if LM Studio is not already in this config, stop and ask Drake" is a
    PRE-FLIGHT instruction to a human, which is a different mechanism with a
    different failure mode; it lives in `preflight` below.

    Returned as numbers, straight from the config. Not copied out of the registry
    row: a request built from the row it is about to be compared against agrees
    with it by construction, which is a gate that cannot fail.

    An absent block is `{}` here rather than an error, because whether that is
    an error depends on the run -- check_dispatch refuses it with
    UninspectedConfig for a model that has a row, and a model with no row was
    never making a claim about serving in the first place.
    """
    serving = cfg.get("serving") or {}
    if not isinstance(serving, dict):
        raise ValueError(
            f"the config's `serving:` block must be a map of field: number, "
            f"got {type(serving).__name__}")
    return dict(serving)


def build_runs(cfg):
    winning = cfg.get("winning_effort", {}) or {}
    seed = (cfg.get("defaults", {}) or {}).get("seed", 1337)
    all_runs = []
    for sweep in cfg.get("sweeps", []):
        name = sweep["name"]
        mode = sweep.get("mode", "solo")
        reps = sweep.get("reps", [1])
        tasks = list(sweep.get("tasks", []))
        configs = sweep.get("configs", [])
        if "harness_matrix" in sweep:
            harness_opts = sweep["harness_matrix"]
        else:
            harness_opts = [bool(sweep.get("harness", False))]

        # deterministic per-sweep task shuffle for interleaving
        rng = random.Random(f"{seed}:{name}")
        shuffled = tasks[:]
        rng.shuffle(shuffled)
        task_index = {t: i for i, t in enumerate(shuffled)}

        sweep_runs = []
        for rep in reps:
            for task in tasks:
                for harness in harness_opts:
                    for ci, conf in enumerate(configs):
                        model = conf["model"]
                        effort = resolve_effort(conf.get("effort", "high"), model, winning)
                        harness_tag = "harness" if harness else "bare"
                        # Built through run_id.build_run_id, never an f-string:
                        # it is the only place that knows where a new segment
                        # (agent, harness_level) goes, and it refuses a field
                        # holding the delimiter. See run_id.py for why an
                        # appended segment is silent damage (blocker 3).
                        run_id = run_id_mod.build_run_id(
                            sweep=name, model=model, effort=effort,
                            harness=harness_tag, task=task, rep=rep)
                        sweep_runs.append({
                            "run_id": run_id,
                            "sweep": name,
                            "model": model,
                            "effort": effort,
                            "harness": bool(harness),
                            # issue #12. WHICH CLI drives the model, per the
                            # serving registry's (model, driver) key. Read with
                            # no default and left as None when undeclared: the
                            # gate decides whether a missing driver is an error,
                            # and it is an error exactly when the model has a row
                            # (serving_registry.require_driver says why).
                            "driver": conf.get("driver", sweep.get("driver")),
                            # The RUNG of the L1-L5 dose ladder, when a config
                            # declares one. Distinct from `harness` above, which
                            # is the pre-ladder boolean and is not a level: the
                            # capability check needs a number to compare against
                            # a driver's ceiling, and False is not 0.
                            "harness_level": conf.get("harness_level",
                                                      sweep.get("harness_level")),
                            "task": task,
                            "rep": rep,
                            "mode": mode,
                            "_sort": (rep, task_index.get(task, 0), ci,
                                      0 if not harness else 1),
                        })
        # round-robin across configs within a task, tasks in shuffled order
        sweep_runs.sort(key=lambda r: r["_sort"])
        for r in sweep_runs:
            r.pop("_sort", None)
        all_runs.extend(sweep_runs)
    return all_runs


# --------------------------------------------------------------------------- #
# Execution helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_prompt(task_dir, harness, mode, k=None):
    parts = []
    for fname in ("PROMPT.md", "TICKET.md", "SPEC.md"):
        p = os.path.join(task_dir, fname)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                parts.append(f"# {fname}\n\n{f.read().strip()}")
    prompt = "\n\n".join(parts)
    prompt += DONE_GATE_SENTENCE
    if k is not None:
        prompt += BROKER_INSTRUCTION_TMPL.format(k=k, k1=k + 1)
    if mode == "hybrid":
        prompt += HYBRID_INSTRUCTION
    return prompt


def prepare_scratch(task_dir, scratch, harness, verify_text=None):
    """Build the model's working copy.

    verify_text replaces the canonical verify.sh with the broker client shim
    (ticket 17). It is written BEFORE the base commit, like the harness files
    above, so it never appears in loc_changed()'s diff as model-authored work.
    None keeps the pre-broker behaviour -- a straight copy of the canonical
    script -- which is what the v1 corpus was produced under and what the
    ticket-16 and ticket-18 tests exercise.
    """
    if os.path.exists(scratch):
        shutil.rmtree(scratch)
    base = os.path.join(task_dir, "base")
    shutil.copytree(base, scratch)
    # copy verify.sh into the working copy root (it runs from inside base copy)
    vsrc = os.path.join(task_dir, "verify.sh")
    if os.path.exists(vsrc):
        vdst = os.path.join(scratch, "verify.sh")
        if verify_text is None:
            shutil.copy2(vsrc, vdst)
        else:
            with open(vdst, "w", encoding="utf-8") as f:
                f.write(verify_text)
        os.chmod(vdst, 0o755)
    # drop harness BEFORE the base commit so it is not counted as model LOC
    if harness:
        for hf in ("CLAUDE.md", "AGENTS.md"):
            src = os.path.join(ROOT, "harness", hf)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(scratch, hf))
    env = dict(os.environ)
    for cmd in (["git", "init", "-q"],
                ["git", "add", "-A"],
                ["git", "-c", "user.email=g@g", "-c", "user.name=gauntlet",
                 "commit", "-q", "-m", "base"]):
        subprocess.run(cmd, cwd=scratch, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Ticket 04, MCP half. An empty server map plus --strict-mcp-config REPLACES the
# discovered MCP servers instead of merging with them, so the model under test is
# handed no mcp__* tools. Measured 2026-07-30 through the request the CLI really
# sends: 42 tools / 11 mcp__ without these, 28 / 0 with them, Read+Write+Bash
# intact either way. The eleven were the context-mode plugin -- ctx_search reads
# an index built over the vault, ctx_execute_file runs arbitrary code -- i.e. a
# retrieval channel the filesystem seal does not watch, because it arrives over a
# socket rather than a path.
#
# ORDER IS LOAD-BEARING. --mcp-config <configs...> is variadic: it swallows every
# following argument that does not start with '-'. --strict-mcp-config must
# follow the value, so the value is never in the last position where a later
# append would be absorbed into it. The failure mode is "MCP config file not
# found: <the swallowed argument>", and it only fires once something appends a
# positional, so it sits latent. Asserted in
# tests/test_live_mcp_seal.py::test_mcp_config_value_is_never_last.
MCP_SEAL_FLAGS = ["--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"]

# issue #25 verify pass. Drivers build_cli_cmd can actually turn into an argv.
# None means "undeclared", which every pre-#25 row and every ungated model
# (fable, sol, ...) carries -- treated as claude-code, the only binary this
# module execs. Single source for BOTH gates: main()'s config-time loop below
# calls this to classify a row BEFORE dispatch (so a pi row is refused at zero
# cost, never after prepare_scratch's git subprocesses have already run), and
# build_cli_cmd calls it again as the last line of defense for any caller that
# reaches it directly (a test, a future script) without going through main().
DISPATCHABLE_DRIVERS = (None, "claude-code")


def driver_has_dispatch_path(driver):
    return driver in DISPATCHABLE_DRIVERS


class DriverUnsupported(ValueError):
    """A row whose driver has no dispatch path in build_cli_cmd (issue #25).

    Distinct from serving_registry.StructurallyImpossible: that answers "can
    the driver express this cell" -- a capability-manifest fact the registry
    measures per (model, driver) row. This answers "does run.py itself have
    launch code for this driver at all" -- orthogonal, and a driver can carry
    a perfectly valid registry row (glm-4.7 x pi does) while run.py still has
    no dispatch path for it. Caught before the generic ValueError branch in
    main()'s gate loop for the same reason StructurallyImpossible is: one
    unsupported cell must drop that cell, not exit 2 for the whole sweep.
    """


def build_cli_cmd(model, effort, prompt, driver=None):
    """The exact headless invocation for a model, per runner/CLI-FACTS.md.

    Dispatches on registry family, so a new model id needs no change here. Effort
    is validated against the model's declared tiers first.

    kimi rides the `claude` binary (base_url + key are injected by run_cli) but is
    a distinct family: it is metered, and it DOES take --effort. The previous
    version passed no --effort for Kimi while labelling every Kimi run "max",
    which is CLI-FACTS correction #3 -- the label was fiction and no Kimi ladder
    was measurable.

    local (studio/local-family) rides the same `claude` binary the same way kimi
    does (base_url + placeholder token injected by run_cli), so it takes the
    identical invocation shape including --effort. --effort is passed through
    rather than suppressed: the flag is harmless to include even if LM Studio's
    server ignores it server-side (whether it does is exactly what
    effort_verdict.py's credited / not-yet-credited classification answers, from
    probe_endpoints.py's ladder data, not a hand-set flag on the registry row),
    and suppressing it here would make the argv depend on a fact -- whether the
    knob works -- that isn't known yet. Whether it moves spend at all is exactly
    what probe_endpoints.py's ladder phase is for, same as every other family.

    `driver` (issue #25). Trailing and optional, defaulting to None, so every
    caller that predates it (8+ test files call this positionally with three
    args) keeps working unchanged. The serving registry (serving_registry.py)
    already lets a config DECLARE a pi row -- it has its own capability
    manifest -- but nothing below actually launches pi; the argv this function
    builds always execs the `claude` binary. A None or "claude-code" driver is
    exactly what that binary implements, so it proceeds. Any other declared
    driver (pi today) is refused HERE, before a token is spent, rather than
    silently producing a row stamped with a driver the CLI that ran it does
    not match. This is the caec128 defect: glm-stage1-pi declared driver: pi
    and this function launched claude-code anyway, stamping 15 rows with a
    label the binary that produced them did not earn.
    """
    if not driver_has_dispatch_path(driver):
        raise ValueError(
            f"build_cli_cmd has no dispatch path for driver {driver!r}: the "
            f"claude binary this function invokes implements claude-code "
            f"only. Add a driver-specific branch here (see issue #25) before "
            f"any config may declare driver: {driver!r}.")
    mid, spec = resolve_model(model)
    check_effort(mid, effort)
    family = spec["family"]

    if family in ("claude", "kimi", "local"):
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--model", mid, "--dangerously-skip-permissions"] + MCP_SEAL_FLAGS
        if effort:
            cmd += ["--effort", effort]
        return cmd
    if family == "codex":
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "-m", mid]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        return cmd + [prompt]
    raise ValueError(f"unknown family {family} for model {mid}")


# Ticket 04, codex half. The host credential file the run-scoped CODEX_HOME
# symlinks onto. A module constant rather than an inline literal so the negative
# auth arm can point it at a path that does not exist without a test-only branch
# living in shipped code (tests/test_live_codex_seal.py). It must stay in
# sandbox_seal.cli_auth_read_paths(), or the symlink resolves onto a denied path.
CODEX_AUTH_SOURCE = os.path.expanduser("~/.codex/auth.json")

# The claude half of the same thing. Resolved at import, from the OPERATOR's
# home -- deliberately, and this is the one place that reads it: scoped_claude_home
# below moves $HOME out from under the model, so `~` inside a run no longer names
# the directory this credential lives in.
CLAUDE_AUTH_SOURCE = os.path.expanduser("~/.claude/.credentials.json")

# Deliberately comments only: no `mcp_servers` table means codex starts with an
# empty server list. An absent key is the seal here, so nothing may be added to
# this text that declares one.
SCOPED_CODEX_CONFIG = """\
# Run-scoped CODEX_HOME for the model-gauntlet (ticket 04).
# Intentionally declares nothing. The host ~/.codex/config.toml configures MCP
# servers -- node_repl, computer-use, an authenticated remote github, and more --
# and `codex exec` has no --strict-mcp-config equivalent, so the only way to hand
# the model under test an empty server list is to hand it a different home.
"""


@contextlib.contextmanager
def scoped_codex_home():
    """Yield a throwaway CODEX_HOME holding an empty config and the host's auth.

    Ticket 04, codex half, fixes two defects with one change.

    1. INSTRUMENT FAULT. sensitive_paths() denies ~/.codex and
       cli_auth_read_paths() carves back auth.json alone, so from 1a6b0d5 every
       codex-family run died at config load -- `Failed to read config file
       ~/.codex/config.toml: Operation not permitted` -- before any model call.
       It was verified with `codex --version`, which never loads the config: a
       binary that starts is not a binary that ran.

    2. MCP SURFACE. The host config declares six servers, several of which
       execute code or carry a bearer PAT.

    Pointing CODEX_HOME at this directory makes config load succeed with no
    servers configured, WITHOUT re-allowing ~/.codex/config.toml -- re-allowing
    it would reopen exactly the vector the ticket closes.

    auth.json is a symlink rather than a copy so the credential is never
    duplicated onto disk; it resolves onto CODEX_AUTH_SOURCE, which is already
    in the read carve-out.

    TEARDOWN IS NOT COSMETIC. codex replaces auth.json by writing a sibling and
    renaming over it, not by writing through the link, and its refresh tokens
    are single-use. A run that crosses a refresh therefore spends the host's
    token and leaves the replacement in a directory we are about to delete --
    breaking Drake's login, not just the run's. So a real file where the symlink
    was is copied back to the source; a symlink still being a symlink means
    nothing was refreshed and the source is left untouched, mtime included.
    """
    with tempfile.TemporaryDirectory(prefix="gauntlet-codexhome-") as home:
        with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as f:
            f.write(SCOPED_CODEX_CONFIG)
        link = os.path.join(home, "auth.json")
        os.symlink(CODEX_AUTH_SOURCE, link)
        try:
            yield home
        finally:
            # exists() follows the link, so a dangling symlink (the negative
            # auth arm) is False here and nothing is written back.
            if os.path.exists(link) and not os.path.islink(link):
                shutil.copyfile(link, CODEX_AUTH_SOURCE)
                os.chmod(CODEX_AUTH_SOURCE, 0o600)


@contextlib.contextmanager
def scoped_claude_home(home_dir=None):
    """Yield the directory the model's claude binary will see as $HOME.

    THE BARE ARM WAS NOT BARE. run_cli built its environment as
    `dict(os.environ)` and never touched HOME or CLAUDE_CONFIG_DIR, so the
    claude binary resolved its global config out of the operator's home:
    ~/.claude/CLAUDE.md, settings.json, the skills tree, agents, plugins. On
    this machine CLAUDE.md alone is roughly 25k tokens of personal harness that
    no sweep configured and no row records. Every harness=False row therefore
    measured a model carrying a large unregistered harness, and the
    harness/bare contrast measured one harness against another (studio-handoff
    findings.md blocker 2, issue #8).

    ISOLATION IS NOT A SIDE EFFECT OF THE SEAL. sensitive_paths() does deny
    ~/.claude, so a sealed run could not read CLAUDE.md -- but that is a
    property of a filesystem profile with a documented opt-out
    (GAUNTLET_NO_SANDBOX=1, which the local-family seats use) rather than a
    property of the arm. The same denylist was [ROOT] alone until 2026-07-30,
    and every row collected before then was open-book against the vault. So the
    environment states it directly, where it holds whether or not a second
    mechanism is switched on, and it is set for EVERY arm: the stage-1 autonomy
    arms run the same binary reading the same global config, so "only the bare
    arm needs this" was never true.

    Per-run and torn down, like TMPDIR and CODEX_HOME: a shared scratch home
    would carry one run's leftover config into the next run's model.

    AUTH, ticket 04's lesson restated. Denying ~/.claude outright "does not
    produce a sealed run, it produces a run that could not authenticate"
    (sandbox_seal.cli_auth_read_paths) -- and moving $HOME has the identical
    failure mode, since the credential is found under it. So .credentials.json
    is symlinked into the scoped home, exactly as scoped_codex_home does for
    codex: a link, never a copy, so the credential is not duplicated onto disk,
    and a real file where the link was is copied back on teardown because a
    refresh that lands in a directory about to be deleted breaks the operator's
    login, not just the run.

    `home_dir` is the seam a harness LEVEL uses: a level whose definition is
    "this agent has these skills and this CLAUDE.md" hands over the directory
    holding them, which is then a configured harness on a named path instead of
    an inherited one. A caller's directory is the caller's -- it is used as-is,
    nothing is written into it and it is not deleted.
    """
    if home_dir is not None:
        yield home_dir
        return
    with tempfile.TemporaryDirectory(prefix="gauntlet-claudehome-") as home:
        config_dir = os.path.join(home, ".claude")
        os.makedirs(config_dir, exist_ok=True)
        link = os.path.join(config_dir, ".credentials.json")
        os.symlink(CLAUDE_AUTH_SOURCE, link)
        try:
            yield home
        finally:
            # exists() follows the link, so a dangling symlink (no credential
            # file on this host, e.g. CI) is False here and nothing is written.
            if os.path.exists(link) and not os.path.islink(link):
                os.makedirs(os.path.dirname(CLAUDE_AUTH_SOURCE), exist_ok=True)
                shutil.copyfile(link, CLAUDE_AUTH_SOURCE)
                os.chmod(CLAUDE_AUTH_SOURCE, 0o600)


@contextlib.contextmanager
def staged_task_dir(task_dir, stage_acceptance=True):
    """Yield a sanitised mirror of task_dir for the model's GAUNTLET_TASK_DIR.

    GAUNTLET_TASK_DIR used to point the model under test at the canonical task
    directory, which holds solution.patch -- the reference answer -- alongside
    the hidden acceptance suite. The model runs in a clean copy of base/, so
    that variable was the signposted route to its own answer key, and one `cat`
    away (ticket 16).

    Ticket 16 could not empty the mirror: t3-a and all three t4-* tasks resolve
    acceptance/ through this variable, because prepare_scratch copied verify.sh
    into the scratch dir and orphaned it from its test assets. Mirroring the
    suite was what kept local self-check alive, at the price of leaving the
    suite readable AS SOURCE -- ticket 16 section 8's one open gap, recorded
    there as an executable assertion rather than a paragraph.

    Ticket 17 closed it by removing the reason: self-check is now an RPC to the
    broker, which needs no local copy of anything, so stage_acceptance=False is
    the brokered default and the mirror is EMPTY. The directory still exists
    and the variable is still set, so a script reaching through it fails to
    find a suite rather than silently falling back to $SCRIPT_DIR and finding
    the real one.

    stage_acceptance=True is the ticket-16 behaviour, kept for the v1 protocol
    (GAUNTLET_NO_BROKER=1) and for the tests that prove the gap was real.
    __pycache__ is excluded there -- a stale .pyc of the acceptance suite is
    the source in all but name.
    """
    tmp = tempfile.mkdtemp(prefix="gauntlet-taskdir-")
    try:
        src = os.path.join(task_dir, "acceptance")
        if stage_acceptance and os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, "acceptance"),
                            ignore=shutil.ignore_patterns("__pycache__"))
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def seal_enabled():
    """False only when a run explicitly opts out via GAUNTLET_NO_SANDBOX=1."""
    return os.environ.get("GAUNTLET_NO_SANDBOX") != "1"


# --------------------------------------------------------------------------- #
# Auth availability -- an instrument that cannot log in is not a model that
# cannot code.
#
# `claude -p` exits 1 when it has no usable credential, and every nonzero exit
# used to be "cli_error", which the pre-registration scores as a failed task.
# That was survivable while the CLI read the operator's own home; it stopped
# being survivable the moment run_cli started handing the binary a scoped
# CLAUDE_CONFIG_DIR, because on macOS the subscription credential lives in the
# login Keychain under a service name keyed per config dir
# ("Claude Code-credentials-<hash>"), so a scoped dir maps to an entry that does
# not exist. An unprovisioned host would then write a whole sweep of pass=false
# rows for a CLI that never sent one request.
#
# The phrase alone is not the test. A model writing about authentication can
# print those words in a run that worked, so the detector requires the CLI's own
# `result` envelope with is_error true -- a field the CLI sets, not the model.
# --------------------------------------------------------------------------- #
AUTH_FAILURE_MARKERS = ("not logged in", "please run /login",
                        "invalid api key", "oauth token has expired")


def cli_auth_failed(out):
    """True when the CLI's own result envelope says it had no usable credential.

    Reads the LAST `type == "result"` object, the same event
    usage_ledger.parse_usage_detailed reads, so both answer from the same bytes.
    Absent or unparseable output is False: a CLI killed before it printed
    anything is a timeout or a crash, and naming that an auth failure would be a
    guess wearing a label.
    """
    obj = None
    for line in reversed([l for l in (out or "").splitlines() if l.strip()]):
        try:
            cand = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(cand, dict) and cand.get("type") == "result":
            obj = cand
            break
    if not isinstance(obj, dict) or not obj.get("is_error"):
        return False
    # Structural signal first. Measured 2026-08-31 against claude 2.1.252 with
    # a rejected setup-token: the CLI returns api_error_status 401 and the prose
    # "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    # which matches none of the four markers below -- so the row landed as
    # cli_error, the bucket the pre-registration reads as the MODEL having
    # failed, for a run in which the model was never asked anything.
    #
    # Only 401 and 403. Not "any nonzero status": a 500 or a 529 is a real
    # server fault and relabelling those as auth would hide outages inside the
    # one bucket nobody re-runs.
    if obj.get("api_error_status") in (401, 403):
        return True
    text = str(obj.get("result") or "").lower()
    return any(m in text for m in AUTH_FAILURE_MARKERS)


def home_isolation_enabled():
    """False only when a run explicitly opts out via GAUNTLET_INHERIT_HOME=1.

    Same shape as seal_enabled and broker_enabled, and for the same two
    reasons: the opt-out has to EXIST, because it is the only way to reproduce
    the pre-2026-08-25 condition (and, on a host whose scoped home has no
    credential provisioned, the only way to run a claude-family arm at all),
    and it has to be LOUD and RECORDED, because a row produced with the
    operator's global harness attached is not comparable with one produced
    without it. See scoped_claude_home for what is being isolated and why.
    """
    return os.environ.get("GAUNTLET_INHERIT_HOME") != "1"


def broker_enabled():
    """False only when a run explicitly opts out via GAUNTLET_NO_BROKER=1.

    Same shape as seal_enabled, and for the same reason: the opt-out has to
    exist (it is how the control arm reproduces the pre-broker condition) and
    it has to be loud and recorded, because a `brokered: false` row is protocol
    v1 and the pre-registration forbids pooling the two strata.
    """
    return os.environ.get("GAUNTLET_NO_BROKER") != "1"


# --------------------------------------------------------------------------- #
# The model's environment (issue #15, finding F1)
# --------------------------------------------------------------------------- #
# Built by ALLOWLIST, never by copying os.environ and popping what looked
# dangerous. This is the shape product/gauntlet_playground/executor.py:82
# already uses, and it is here for the reason stated there: a subtractive env is
# a claim about EVERYTHING THAT EXISTS, an additive one is a claim about ten
# names, and only the second can be asserted positively -- which is what
# runner/tests/test_child_env_allowlist.py does.
#
# Until 2026-08-25 this was `dict(os.environ)` minus ANTHROPIC_API_KEY and
# OPENAI_API_KEY. Everything else rode in, and the list of what "everything
# else" contained on this machine is not hypothetical:
#
#   ANTHROPIC_BASE_URL      re-points any arm at another endpoint, so a row
#                           labelled claude-sonnet-5 could have been answered by
#                           LM Studio
#   ANTHROPIC_AUTH_TOKEN    the second name the binary reads a credential from;
#                           popping only ANTHROPIC_API_KEY left it behind
#   ANTHROPIC_MODEL         overrides which model actually answers, under the
#   ANTHROPIC_SMALL_FAST_MODEL   row's own label
#   CLAUDE_CODE_MAX_OUTPUT_TOKENS, MAX_THINKING_TOKENS
#                           change the SERVING CONFIG the row is reported under.
#                           serving_registry's gate cannot see this: the gate
#                           compares the DECLARED config against the row, and
#                           these change the actual one after it has passed.
#   XDG_CONFIG_HOME, XDG_DATA_HOME
#                           re-point config/state discovery even after blocker
#                           2 scoped HOME and CLAUDE_CONFIG_DIR
#   CLAUDECODE, CLAUDE_EFFORT, CLAUDE_CODE_*
#                           live whenever a sweep is launched from inside a
#                           Claude Code session, which is how every sweep on
#                           this machine has been launched
#   API_TIMEOUT_MS, CLAUDE_STREAM_IDLE_TIMEOUT_MS
#                           from the PARENT these are exactly the class above:
#                           an operator's shell env leaking into a claude-sonnet
#                           row would silently widen or shrink its timeout
#                           budget under a label that does not say so. Blocked
#                           here for that reason. run_cli sets BOTH of them
#                           deliberately, from a value it derives itself, on
#                           the local-family CHILD only (issue #40) -- that is
#                           not the same hazard, because the value is not
#                           inherited from an uninspected shell, it is either
#                           the serving row's own client_timeout_ms or this
#                           run's own resolved wall-clock cap, so the row
#                           is still labelled with a number this instrument
#                           chose and can name.
#
# CLAUDE_EFFORT is the one that ruins an experiment rather than merely
# threatening it. `CLAUDE_EFFORT=high` in the parent is `CLAUDE_EFFORT=high` in
# every arm, so an effort ladder measures one effort five times while its rows
# carry five different `effort` labels -- and the dose ladder stage 2 exists to
# fit would be fitted over a constant.
#
# Nothing on this list can carry a credential, an endpoint, a model choice or a
# harness. Adding a name is therefore a decision about that class, not a
# convenience: if a binary turns out to need something else, the missing name
# shows up as a clean startup failure, which is a better outcome than a row
# that ran under conditions nobody declared.
CHILD_ENV_ALLOWLIST = ("PATH", "HOME", "SHELL", "USER", "LOGNAME",
                       "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")


def is_loopback_endpoint(url):
    """True when `url`'s host is this machine.

    Fail-closed: a URL this cannot parse is not one it may vouch for. Matched on
    HOST only, never the port -- which port LM Studio listens on is a local
    convention, and which MACHINE answers is the thing a registry row can be
    wrong about.
    """
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    # `localhost` is the one NAME allowed through, because the platform pins it.
    # Everything else must be a literal address that ipaddress agrees is
    # loopback -- which covers all of 127/8 and ::1 without enumerating them.
    if host == "localhost":
        return True
    # ipaddress is STRICT and it stays strict: the abbreviated forms (`127.1`,
    # `0177.0.0.1`, `2130706433`) are refused even though a resolver would accept
    # them as loopback. That direction is deliberate. A false REFUSAL of an
    # unusual spelling costs an operator one edit; a false ACCEPT is the bug this
    # check exists to stop, and inet_aton's lax parsing is a wider accept surface
    # bought for no safety.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not a literal address at all, so it is a DNS name whose resolution is
        # not knowable here. Refused -- and this is the whole finding: the
        # original test was `host.startswith("127.")`, a STRING match on
        # something that is not a string quantity, so `127.0.0.1.evil.com` -- an
        # ordinary DNS name its owner points wherever they like -- walked
        # straight through a check named after the thing it was meant to stop.
        return False


def check_local_endpoint(model, endpoint):
    """Refuse a local-family run served from off this machine (finding 2, issue #7).

    The serving gate cannot catch this. It compares the DECLARED serving config
    against the row, and the declared config still matches -- the endpoint was
    never one of the pinned fields.

    But the row's numbers are measurements OF ONE MACHINE: parallel=1,
    context_length=131072 and the 57-71 tok/s prefill band were probed on the Mac
    Studio's LM Studio over loopback. Pointed at another host, every one of them
    becomes a claim about a box nobody probed, while the row goes on asserting
    them and the results are labelled with it.

    The override itself is NOT refused -- changing the port is how this family is
    meant to be used, and the pre-registration makes the serving stack a human's
    to set. What is refused is leaving the machine the row describes.

    THE REASON DEPENDS ON WHETHER A ROW EXISTS, and that is not decoration. Only
    glm-4.7 has a serving row. Told the same sentence, qwen3-coder-next-local --
    local family, no row -- was refused with "Its registry row pins parallel,
    context_length and a measured 57-71 tok/s prefill band", quoting another
    model's measurement at a model nobody has probed. Asserting a measurement
    that was never taken is the exact failure this branch exists to prevent, and
    a refusal that commits it while preventing it teaches the reader to distrust
    the message.

    Both are still refused. The row argument is the stronger one and applies only
    to gated models, but the weaker one applies to every local run: "local" names
    a family whose defining property is that the server is on this box, and an
    off-box endpoint ships the task tree and the model's prompts to a host the
    operator did not name in the config. Issue #7 is about the variable accepting
    a non-loopback URL at all, not about one model's row.
    """
    if model is None or model_family(model) != "local":
        return
    if is_loopback_endpoint(endpoint):
        return

    head = (f"run of {model!r} would be served from {endpoint!r}, which is not "
            f"this machine (set via MODEL_EVAL_LOCAL_BASE_URL). ")
    gated = (serving_registry.serving_model_name(resolve_model(model)[0])
             in serving_registry.models_with_rows(serving_registry.load_rows()))
    if gated:
        why = ("Its registry row pins parallel, context_length and a measured "
               "57-71 tok/s prefill band, all probed on the LOCAL LM Studio over "
               "loopback. None of them were measured on another host, and the "
               "results would be reported under them anyway.")
    else:
        # No row, so there is no measurement to be mislabelled -- say that, and
        # give the reason that does apply, rather than borrowing glm-4.7's.
        why = ("This model has no serving registry row, so nothing here records "
               "what it was served under and no measurement can be checked "
               "against the host answering. A local-family run is defined by "
               "serving from this box; an off-box endpoint sends the task tree "
               "and the model's prompts somewhere the config never names.")
    raise ValueError(
        head + why + " Point it back at loopback, or record a row for the "
        "machine you actually intend to serve from.")


def invocation_provenance(model):
    """What actually served this run: endpoint, key SOURCE, and binary.

    Verifier findings 2 and 4. Neither is a hole in the F1 allowlist -- both are
    facts the runner CHOOSES and then failed to write down.

    MODEL_EVAL_LOCAL_BASE_URL is read by this module and set on the child
    deliberately, so no allowlist stops it and none should: the local family
    exists because the endpoint is not fixed, and the pre-registration makes the
    serving stack a human's to set. What was wrong is that it was INVISIBLE. A
    row said `glm-4.7-local` and carried nothing about which server answered, so
    two rows served by two endpoints were indistinguishable in the corpus
    forever after.

    Same one level down for the binary: build_cli_cmd emits the bare name
    `claude`, and which file that names is decided by the parent shell's PATH.
    Two rows produced by two Claude Code versions, or by a shim earlier on PATH,
    looked identical.

    `key_source` is a PATH or a word, NEVER a value. A provenance field that
    fixed a visibility problem by writing a live credential into an append-only
    corpus would be a far worse bug than the one it closed.
    """
    family = model_family(model) if model is not None else None
    binary = "codex" if family == "codex" else "claude"

    if family == "local":
        endpoint = LOCAL_BASE_URL
        # Named from where the string CAME FROM, not by comparing it to the
        # default: a deliberate override that happens to equal the default is a
        # different fact from no override at all.
        source = ("MODEL_EVAL_LOCAL_BASE_URL"
                  if os.environ.get("MODEL_EVAL_LOCAL_BASE_URL") else "default")
        key_source = "placeholder"
    elif family == "kimi":
        endpoint, source = MOONSHOT_ANTHROPIC_URL, "moonshot"
        key_source = KIMI_KEY_FILE
    else:
        # The runner sets no base URL for claude/codex, and after F1 no ambient
        # one can reach them either. None is the honest value; a synthesised URL
        # would assert a fact this code does not have.
        endpoint, source = None, "vendor_default"
        # Was hardcoded "subscription", which was true while a subscription was
        # the only claude credential. Once a run can authenticate from the
        # secrets file, that constant contradicts auth_source on the same row --
        # observed live 2026-09-01: auth_source=api_key beside
        # key_source=subscription. Still a PATH or a WORD, never a value.
        if family == "claude" and (load_claude_api_key() or load_claude_token()):
            key_source = CLAUDE_TOKEN_FILE
        else:
            key_source = "subscription"

    return {
        "serving_endpoint": endpoint,
        "endpoint_source": source,
        "key_source": key_source,
        "cli_binary": binary,
        # None, never the bare name: recording `claude` as a path would look
        # resolved and be a guess.
        "cli_binary_path": shutil.which(binary),
    }


# The GRADER's environment (issue #14 F1, downstream half). Separate list from
# CHILD_ENV_ALLOWLIST because the two processes are different: the model needs a
# shell it can work in, the grader needs only enough to run bash, python and a
# venv. Kept separate rather than shared so that widening one cannot silently
# widen the other -- the grader's list is the one where a mistake changes a
# recorded verdict.
#
# Every shipped task's verify.sh was read before this list was written. The only
# environment names any of them reference are PYTHON_BIN (read with a
# `${PYTHON_BIN:-python3}` default), VENV_DIR / STAGE / SCRIPT_DIR /
# ACCEPT_STATUS / BASH_SOURCE (all assigned in-script before use), and
# GAUNTLET_TASK_DIR (which graded_run sets itself). So nothing a task reads
# arrives from the parent except by graded_run's own assignment.
#
# PYTHON_BIN is deliberately absent: inherited, it swaps the interpreter the
# whole acceptance suite runs under, and the in-script default resolves through
# PATH, which is honest.
GRADER_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                        "TZ")


# A task that genuinely needs one more name declares it, in its own directory,
# in version control. One NAME per line -- never a value, so the declaration says
# "this grade depends on the operator's FOO" without becoming a second place to
# configure FOO. The point is that the dependency is reviewable: a task asking
# for GIT_CONFIG_GLOBAL is a question somebody gets to ask in a diff, whereas
# ambient inheritance asked nobody.
GRADER_ENV_MANIFEST = "env_allowlist"


def task_env_additions(task_dir):
    """Extra environment names this task's grade declares it needs."""
    path = os.path.join(task_dir, GRADER_ENV_MANIFEST)
    if not os.path.isfile(path):
        return ()
    names = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
    return tuple(names)


def grader_env(source=None, task_dir=None):
    """The environment `bash verify.sh` is graded in: the allowlist, and nothing.

    `task_dir` adds the names that task declares in its own `env_allowlist`.
    """
    src = os.environ if source is None else source
    allowed = GRADER_ENV_ALLOWLIST + (task_env_additions(task_dir) if task_dir else ())
    return {name: src[name] for name in allowed if name in src}


def child_env(source=None):
    """The base environment for the model under test: the allowlist, and nothing.

    Copies the names that are PRESENT and does not invent the ones that are
    not -- handing a child a locale the parent never had is its own small lie
    about the conditions the row was produced under.
    """
    src = os.environ if source is None else source
    return {name: src[name] for name in CHILD_ENV_ALLOWLIST if name in src}


def local_family_client_timeout_ms(model, wall_clock_s):
    """The value for CLAUDE_STREAM_IDLE_TIMEOUT_MS and API_TIMEOUT_MS on a
    local-family child (issue #40).

    claude-code carries its own client-side stream-idle timer that aborts a
    turn the instant it sees no visible-content bytes for that long --
    regardless of whether tokens are still flowing server-side. Measured
    directly in the 2.1.246 binary: its first-party default is 180000 ms when
    CLAUDE_STREAM_IDLE_TIMEOUT_MS is unset, and whatever value this function
    returns is clamped by the CLI itself to Math.min(Math.max(value, 10000),
    1800000) -- a 30-minute ceiling no caller-supplied number can raise. A
    model that reasons silently before emitting visible content (GLM 4.7
    logged one turn reasoning for 1964s) can still trip this: a silent
    reasoning stretch longer than 30 minutes still aborts the turn as
    cli_error no matter what this function passes in. The wall clock
    (resolve_timeout_s) remains the actual hang backstop, since the
    subprocess is killed there regardless of how the client timer resolves.

    Resolution order: the serving row's own `client_timeout_ms` (models.yaml),
    a human-set value for a model whose reasoning latency has actually been
    measured, else `wall_clock_s` -- this run's own resolved wall-clock cap
    (resolve_timeout_s), converted to ms. The wall clock is the safe default
    because it is the one number in this instrument that is guaranteed to be
    at least as large: the subprocess is killed at wall_clock_s regardless, so
    the client timer can never be the thing that ends the run first.

    A single hardcoded constant was the other option and is rejected: it would
    have to be sized for whichever model reasons slowest, which is a fact that
    changes every time a new local model is added, and this instrument already
    has a per-task-tier bound (resolve_timeout_s) that does not need a second,
    parallel guess to go stale against it.
    """
    row_model = serving_registry.serving_model_name(resolve_model(model)[0])
    try:
        row = serving_registry.find_row(
            serving_registry.load_rows(), row_model, "claude-code")
    except serving_registry.RegistryError:
        row = None
    client_ms = row.get("client_timeout_ms") if row else None
    if client_ms is not None:
        return client_ms
    return int(wall_clock_s * 1000)


def run_cli(cmd, scratch, timeout_s, task_dir, model=None, bk=None, home_dir=None):
    """Run headlessly, killing the process group on timeout. Returns (out, reason, wall).

    The command runs sealed (ticket 16): reads of this repo are denied except
    the scratch dir it works in, so neither tasks/*/solution.patch nor the
    canonical acceptance suites nor other runs' transcripts under
    runner/results/ are reachable -- by GAUNTLET_TASK_DIR or by walking up from
    cwd, since scratch lives inside the repo by default.

    Writes are contained too (ticket 26): file-write* is denied outright and
    re-allowed only on this run's own apparatus -- its scratch tree, the staged
    mirror, the broker directory, a run-scoped TMPDIR -- plus the CLI runtime
    tier in sandbox_seal.cli_runtime_write_paths(). Before that ticket the seal
    emitted read rules only, so the model under test could append a forged row
    to results.jsonl, truncate it, poison a canonical task file or write into a
    concurrent run's working tree. Recorded on the row as `write_contained`.

    The seal is fail-closed: no sandbox-exec means no run, rather than a run
    that silently measures an open-book attempt. GAUNTLET_NO_SANDBOX=1 is the
    documented, loudly-warned opt-out, and it is recorded on the results row.

    The model's HOME and CLAUDE_CONFIG_DIR are this run's alone, never the
    operator's -- see scoped_claude_home for why that is stated in the
    environment rather than left to the filesystem seal. `home_dir` overrides
    it with a directory a harness level supplies on purpose.

    `bk` is a live broker.Broker (ticket 17). Its socket is the model's only
    route to acceptance feedback; the seal is widened by exactly that one
    directory, and the mirror is emptied to match. Two exit reasons come out of
    it and both are terminal for the row: `cap_exhausted` (the model asked for
    request K+1 and the run was ended, scored a failure per the estimand) and
    `broker_failed` (the counter faulted, so the run is uncounted and unusable
    under the pre-registration).
    """
    # Wall clock for the whole call, not just the child: a preflight
    # refusal is a real elapsed cost and a row that reports 0.0s for it
    # would understate what the sweep spent.
    t0_outer = time.time()
    # Whether this call actually launches the `claude` binary. The auth
    # preflight below asks "can `claude` log in", which is a question only worth
    # asking when `claude` is what runs: most of this repo's tests drive
    # run_cli() with a stand-in binary precisely so they can test env scoping,
    # home isolation and exit labelling WITHOUT a credential, and gating those
    # on a real login would make the instrument's own test suite depend on the
    # operator being signed in. Read before the sandbox prefix is prepended.
    cmd_is_claude_cli = bool(cmd) and os.path.basename(str(cmd[0])) == "claude"
    env = child_env()
    if model is not None and model_family(model) == "kimi":
        key = load_kimi_key()
        if not key:
            return "", "kimi_key_missing", 0.0
        # Point Claude Code at Moonshot's Anthropic-compatible endpoint for this run.
        env["ANTHROPIC_BASE_URL"] = MOONSHOT_ANTHROPIC_URL
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key   # some Claude Code versions read this
    elif model is not None and model_family(model) == "claude":
        # The subscription credential, injected rather than inherited. See
        # CLAUDE_TOKEN_FILE for why the keychain cannot be reached from a scoped
        # HOME + CLAUDE_CONFIG_DIR no matter what is linked into it.
        #
        # NOT added to CHILD_ENV_ALLOWLIST. That list's stated contract is
        # "nothing on this list can carry a credential"; this is a credential,
        # so it is injected here per-family, exactly where the kimi key is, and
        # only for the family it belongs to. A local or codex row must not
        # receive a claude subscription token because one happens to be on disk.
        #
        # Absent token: set NOTHING. An empty-string variable is a different and
        # worse failure than a missing one -- the CLI reports "invalid" instead
        # of falling through to whatever credential it can still find.
        # Exactly one credential reaches the child, chosen by the precedence
        # claude_auth_source() declares, so the row's auth_source field and the
        # variable actually set can never disagree.
        _api_key = load_claude_api_key()
        if _api_key:
            env["ANTHROPIC_API_KEY"] = _api_key
        else:
            _claude_token = load_claude_token()
            if _claude_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = _claude_token
    elif model is not None and model_family(model) == "local":
        # studio/local-family: same lever as the kimi arm above, pointed at an
        # LM Studio server on loopback instead of Moonshot. No key file and no
        # "key missing" failure mode -- unlike kimi there is no account behind
        # this, so nothing to load and nothing that can be absent. The
        # placeholder token exists only to satisfy the claude binary's own
        # precondition (see LOCAL_PLACEHOLDER_TOKEN); LM Studio never checks it.
        env["ANTHROPIC_BASE_URL"] = LOCAL_BASE_URL
        env["ANTHROPIC_API_KEY"] = LOCAL_PLACEHOLDER_TOKEN
        env["ANTHROPIC_AUTH_TOKEN"] = LOCAL_PLACEHOLDER_TOKEN
        # issue #40: without this, every local-family child runs on
        # claude-code's own client-side stream-idle default (~200s) instead of
        # this run's resolved wall-clock cap, and a model that reasons for
        # minutes before emitting visible content trips the client timer long
        # before failing the task. See local_family_client_timeout_ms's
        # docstring for the resolution order.
        client_ms = local_family_client_timeout_ms(model, timeout_s)
        env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(client_ms)
        env["API_TIMEOUT_MS"] = str(client_ms)

    with contextlib.ExitStack() as stack:
        # verify.sh is copied into scratch; tasks whose test assets live beside
        # the canonical script (t3, t4) resolved them through this. Brokered,
        # the mirror is empty and the variable exists only so a stray reference
        # fails closed rather than falling back to the real task dir.
        mirror = stack.enter_context(
            staged_task_dir(task_dir, stage_acceptance=bk is None))
        env["GAUNTLET_TASK_DIR"] = mirror
        allow = [scratch, mirror]
        if bk is not None:
            env["GAUNTLET_BROKER_SOCK"] = bk.sock_path
            allow.append(bk.dir)
        # ticket 26: the model's TMPDIR is redirected to a directory created for
        # this run alone, and that directory -- not the shared /var/folders root
        # it lives in -- is what the write allowlist names. Allowing the shared
        # root instead would hand every run write access to every concurrent
        # run's staged mirror and broker directory, i.e. reopen one level up the
        # contamination channel this ticket closes.
        run_tmp = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="gauntlet-tmp-"))
        env["TMPDIR"] = env["TMP"] = env["TEMP"] = run_tmp

        # ticket 04, codex half: every run gets its own CODEX_HOME, holding an
        # empty config and a symlink to the host credential. Unconditional, not
        # branched on family -- a claude-family run that ignores CODEX_HOME
        # costs one temp directory, whereas a family branch is one more place
        # the seal can be absent without anything failing. It joins the WRITE
        # allowlist only: codex writes session state under its home, but reads
        # are denylist-shaped and /var/folders is already readable, so adding it
        # to `allow` would widen the read carve-out for nothing.
        codex_home = stack.enter_context(scoped_codex_home())
        env["CODEX_HOME"] = codex_home

        # blocker 2: the claude half of the same rule, and unconditional across
        # families for the same reason -- a family branch is one more place the
        # isolation can be absent without anything failing. Both variables are
        # set: HOME is what the binary joins ".claude" onto, CLAUDE_CONFIG_DIR
        # is the override that wins over that join, so setting only one leaves
        # the other pointing at the operator's home.
        claude_home = None
        if home_isolation_enabled() or home_dir is not None:
            claude_home = stack.enter_context(scoped_claude_home(home_dir))
            env["HOME"] = claude_home
            env["CLAUDE_CONFIG_DIR"] = os.path.join(claude_home, ".claude")
        else:
            print("WARNING: GAUNTLET_INHERIT_HOME=1 -- the model under test "
                  "loads the operator's global CLAUDE.md, settings, skills and "
                  "agents from $HOME, so a harness=False row is NOT a bare "
                  "arm; this row is marked home_isolated=false",
                  file=sys.stderr)

        if seal_enabled():
            # ROOT is ticket 16's deny (the benchmark's own answer key).
            # sensitive_paths() is ticket 04's: the vault, the global Claude and
            # Codex configs, the global MCP config, the skills tree and shell
            # history. Until 2026-07-30 the live list was [ROOT] alone, so every
            # row collected before then was sealed against the answer key and
            # open-book against the vault -- the module could seal those paths
            # and simply was never asked to.
            #
            # cli_auth_read_paths() is carved back out because the ~/.claude and
            # ~/.codex denies would otherwise take the credential files with
            # them and break subscription auth. It is appended to allow_paths
            # rather than removed from the deny list so the exception stays two
            # named files instead of two directories.
            #
            # write_allow_paths names the run's own apparatus and nothing else;
            # sandbox_seal appends the CLI runtime tier itself. Everything
            # outside it -- results.jsonl, the canonical tasks/ tree, sibling
            # scratch trees -- is read-only to the model under test.
            prefix = stack.enter_context(sandbox_seal.sandbox_prefix(
                deny_paths=[ROOT] + list(sandbox_seal.sensitive_paths().values()),
                allow_paths=allow + sandbox_seal.cli_auth_read_paths(),
                write_allow_paths=(allow + [run_tmp, codex_home]
                                   + ([claude_home] if claude_home else []))))
            cmd = prefix + list(cmd)
        else:
            print("WARNING: GAUNTLET_NO_SANDBOX=1 -- model can read its own "
                  "answer key AND the vault, the global agent configs and shell "
                  "history, AND write to results.jsonl, the canonical tasks "
                  "and other runs' scratch trees; this row is marked "
                  "sealed=false write_contained=false", file=sys.stderr)
        # ------------------------------------------------------------------ #
        # Auth preflight -- ask once, before the dispatch, under the EXACT env
        # the model is about to get.
        #
        # On 2026-08-28 a cross-family sweep spent one attempt per row to
        # discover the same fact 14 times: opus 4, sonnet 4, haiku 3, fable 3,
        # all exit_reason=auth_unavailable, all tokens_in=0/tokens_out=0, no
        # request ever sent. cli_auth_failed() below still catches this after
        # the fact and still labels it correctly; what it cannot do is decline
        # to spend the attempt.
        #
        # SCOPED TO THE CLAUDE FAMILY ON PURPOSE. scoped_claude_home() is
        # entered unconditionally for every family, so a check placed there
        # would reject codex, kimi and local rows -- families whose credentials
        # this preflight knows nothing about and has no standing to judge.
        # ------------------------------------------------------------------ #
        if (model is not None and model_family(model) == "claude"
                and cmd_is_claude_cli):
            _auth_ok, _auth_detail = claude_auth_preflight(env)
            if not _auth_ok:
                print("PREFLIGHT: claude auth unavailable under the scoped "
                      "child environment (%s); refusing before dispatch. "
                      "Mint a token with `claude setup-token` and store it as "
                      "CLAUDE_CODE_OAUTH_TOKEN in %s."
                      % (_auth_detail, CLAUDE_TOKEN_FILE), file=sys.stderr)
                return "", "auth_unavailable", round(time.time() - t0_outer, 2)

        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=scratch, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, env=env)

        def kill_group():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

        if bk is not None:
            # The broker can now end the run. Attaching after Popen is the
            # whole reason attach() is separate from start(): the socket has to
            # exist before the model launches, the pid only after.
            bk.attach(kill_group)
        try:
            out, _err = proc.communicate(timeout=timeout_s)
            reason = "ok" if proc.returncode == 0 else "cli_error"
            # Exactly one failure mode moves out of cli_error, and only from a
            # nonzero exit: an instrument that never authenticated produced no
            # measurement of the model at all. It is still not "ok", so the
            # general gate in execute_run keeps `pass` False and existing_ids
            # keeps the run pending for a resumed sweep -- what changes is that
            # the row names the instrument instead of implicating the model.
            if reason == "cli_error" and cli_auth_failed(out):
                reason = "auth_unavailable"
        except subprocess.TimeoutExpired:
            kill_group()
            try:
                out, _err = proc.communicate(timeout=30)
            except Exception:
                out = ""
            reason = "timeout"
        # A broker verdict overrides the CLI's exit status, because the CLI's
        # status is downstream of it: a killed model reports cli_error, and
        # filing that would hide a cap termination inside the fault bucket the
        # pre-registration re-runs from the spare pool.
        if bk is not None:
            if bk.failed:
                reason = "broker_failed"
            elif bk.exhausted:
                reason = "cap_exhausted"
    return out or "", reason, round(time.time() - t0, 2)


def parse_usage(model, out):
    """Return (tokens_in, tokens_out, turns) parsed from CLI JSON output.

    Routed by registry family, not by alias: the two output shapes are properties
    of the CLI binary (claude's single `result` object vs codex's JSONL event
    stream), so every current and future id of a family parses the same way.

    Delegates to usage_ledger.parse_usage_detailed, the single source of truth
    for this formula (ticket 08). Fixed bug, corrected here 2026-07-27: this
    used to sum only usage.input_tokens for the claude/kimi branch, which is the
    LAST turn's fresh, uncached tokens -- cache_creation_input_tokens and
    cache_read_input_tokens were dropped entirely, undercounting real
    consumption by 30x-400x on cached multi-turn sessions (a real transcript
    showed input_tokens=57 against cache_read_input_tokens=221097). Codex's
    single turn.completed event already folds cached tokens into input_tokens,
    so that branch was never buggy and is unchanged.
    """
    d = usage_ledger.parse_usage_detailed(model_family(model), out)
    return d["tokens_in"], d["tokens_out"], d["turns"]


def apply_mock(task_dir, scratch):
    """MOCK pass: apply solution.patch. Returns True on success."""
    patch = os.path.join(task_dir, "solution.patch")
    if not os.path.exists(patch):
        return False
    for cmd in (["git", "apply", "--recount", patch],
                ["git", "apply", "--recount", "--3way", patch],
                ["patch", "-p1", "-i", patch]):
        r = subprocess.run(cmd, cwd=scratch,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return True
    return False


# --------------------------------------------------------------------------- #
# Grading (ticket 18) -- the model writes the code, the task dir writes the grade
# --------------------------------------------------------------------------- #
# run_verify used to execute scratch/verify.sh, in scratch, as the authoritative
# gate. The model must be able to write to scratch -- that is its workspace -- so
# the gate was a file the graded party could rewrite. Four doors were confirmed by
# execution (ticket 18): overwrite verify.sh with `exit 0`; rewrite the graded
# tests; ADD a tests/conftest.py that neuters what pytest collects; plant a
# .venv/bin/python that exits 0, which verify.sh reuses. None of them touches the
# model's source, which is the tell: the whole grading apparatus lived in a
# directory the model owns.
#
# The rule that replaces it, and the only one worth stating: the grade takes the
# MODEL'S SOURCE from scratch and everything else from the task dir. So grading
# happens in a disposable copy of scratch with the apparatus overlaid from
# canonical -- not in scratch itself, because loc_changed() runs afterwards and
# restoring files in place would erase the model's real diff from the metric and
# destroy the tamper evidence at the same time.
#
# "Apparatus" is three things: verify.sh, the graded test directories, and the
# files that decide how the test runner behaves (its config, its dependency
# manifests, its plugin hooks). Test directories are RESTORED, not replaced
# wholesale, so a model-added test still runs -- some tasks ask the candidate to
# write tests, and dropping those would change the task. Apparatus files are
# different: they come from canonical or they do not exist, because an ADDED
# pytest.ini or vitest.config.ts is a rewrite of the gate wearing a new filename.
GRADED_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})

APPARATUS_NAMES = frozenset({
    "verify.sh",
    # python: what pytest loads before it runs a single test
    "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "setup.py",
    "tox.ini", "sitecustomize.py", "usercustomize.py", "requirements.txt",
    # node: what `npm test` resolves to, and what it installs first
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "tsconfig.json",
    # registry/index redirection -- a dependency source is a code source
    ".npmrc", "pip.conf", ".pydistutils.cfg",
})

APPARATUS_GLOBS = ("vitest.config.*", "vite.config.*", "jest.config.*",
                   "babel.config.*", "tsconfig.*.json", "*.pth")

# Apparatus the canonical verify.sh CREATES on an honest run: `npm install` writes
# a lock file into every working copy, none of the twelve tasks ships one, and
# 103 of the 309 retained scratch trees therefore contain one. They are stripped
# from the grading tree like any other model-owned apparatus -- a lock file can
# redirect where a dependency comes from -- but they are never REPORTED, because
# a flag that fires on every TypeScript run tells the corpus nothing. Same
# distinction LOC_EXCLUDE already draws for loc_changed(): an install artifact is
# not model-authored.
GENERATED_APPARATUS = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml"})

# Not part of the grade at any price: the model's own dependency trees are
# executables it installed and can replace (door 4), .git is prepare_scratch's
# bookkeeping, and the caches are derived bytes that can shadow a restored source.
NOT_GRADED_DIRS = frozenset({".git", ".venv", "venv", "node_modules",
                             "__pycache__", ".pytest_cache", ".mypy_cache",
                             ".ruff_cache", ".tox"})


def _is_apparatus(name):
    return (name in APPARATUS_NAMES
            or any(fnmatch.fnmatch(name, g) for g in APPARATUS_GLOBS))


def _graded_files(root):
    """Yield (relpath, abspath) for files under root, skipping NOT_GRADED_DIRS."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in NOT_GRADED_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            yield os.path.relpath(p, root), p


def canonical_grading_files(task_dir):
    """relpath in the working copy -> the canonical file the grade must use.

    Single definition of "apparatus", shared by tamper_report (which reports on
    it) and grading_tree (which enforces it), so the two can never disagree about
    what is being protected.
    """
    out = {}
    vs = os.path.join(task_dir, "verify.sh")
    if os.path.exists(vs):
        out["verify.sh"] = vs
    for rel, src in _graded_files(os.path.join(task_dir, "base")):
        parts = rel.split(os.sep)
        if any(p in GRADED_TEST_DIRS for p in parts[:-1]) or _is_apparatus(parts[-1]):
            out[rel] = src
    return out


def _same_bytes(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def tamper_report(scratch, task_dir, installed=None):
    """Sorted "<how>:<path>" strings for every apparatus file the model altered.

    Reported, not punished: the overlay already makes the grade honest, so this
    exists to make the question "did anyone try?" answerable from results.jsonl
    once the scratch trees are gone. Byte comparison, deliberately -- a
    reformatted graded test is still a rewritten graded test, and no reference
    solution in the suite touches one.

    `installed` maps a relpath to the TEXT the runner itself put there, for
    files that legitimately differ from canonical: the broker shim is written
    over verify.sh on every brokered run, and diffing it against the canonical
    script would flag `modified:verify.sh` on every honest run in the sweep --
    the same false-positive failure the ticket-18 audit hit with
    package-lock.json. The model rewriting the shim is still a finding, because
    the comparison moves to the shim rather than being switched off.
    """
    found = []
    installed = installed or {}
    canon = canonical_grading_files(task_dir)
    for rel, src in canon.items():
        dst = os.path.join(scratch, rel)
        if not os.path.isfile(dst):
            found.append("deleted:" + rel)
        elif rel in installed:
            with open(dst, "r", encoding="utf-8", errors="replace") as f:
                if f.read() != installed[rel]:
                    found.append("modified:" + rel)
        elif not _same_bytes(src, dst):
            found.append("modified:" + rel)
    for rel, _path in _graded_files(scratch):
        name = os.path.basename(rel)
        if rel not in canon and _is_apparatus(name) and name not in GENERATED_APPARATUS:
            found.append("added:" + rel)
    return sorted(f.replace(os.sep, "/") for f in found)


def _install_canonical(dst, src):
    """Put src at dst, defeating whatever shape the model left there.

    A graded path replaced by a directory, or by a file with its write bit
    cleared, must not raise: a grader that crashes on tamper converts a cheat
    into a lost run instead of a fail, which is a worse outcome than the bug.
    """
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o755)
    if os.path.islink(dst) or os.path.isfile(dst):
        os.remove(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755 if dst.endswith("verify.sh") else 0o644)


@contextlib.contextmanager
def grading_tree(scratch, task_dir):
    """Yield a throwaway working copy in which the grade is authoritative.

    Built as: the model's tree minus its dependency trees, minus any apparatus
    file it added, plus every canonical apparatus file overlaid on top. Lives
    outside the repo, so it is unreachable from a model that is somehow still
    running and invisible to loc_changed().
    """
    tmp = tempfile.mkdtemp(prefix="gauntlet-grade-")
    tree = os.path.join(tmp, "work")
    try:
        try:
            shutil.copytree(scratch, tree,
                            ignore=shutil.ignore_patterns(*sorted(NOT_GRADED_DIRS)),
                            ignore_dangling_symlinks=True)
        except shutil.Error:
            # Unreadable files are the model's own; the ones that matter are
            # overlaid from canonical below and the rest fail the grade honestly.
            pass
        canon = canonical_grading_files(task_dir)
        for rel, path in list(_graded_files(tree)):
            if rel not in canon and _is_apparatus(os.path.basename(rel)):
                with contextlib.suppress(OSError):
                    os.remove(path)
        for rel, src in canon.items():
            _install_canonical(os.path.join(tree, rel), src)
        yield tree
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def graded_run(scratch, task_dir):
    """Run the canonical grade over the model's source. Returns (rc, output).

    One definition of "the grade", shared by the authoritative gate below and
    by the broker, so brokered feedback can never diverge from what the run is
    finally scored on -- the model is told about the same measurement it will
    be judged by, at lower resolution, K times.

    GAUNTLET_TASK_DIR points at the real task dir here, not at the mirror
    run_cli hands the model (ticket 16): both callers are the runner's own
    subprocess, outside the model's sandbox, and t3/t4 resolve their hidden
    acceptance suites through it. Output is captured rather than discarded
    because the broker needs counts out of it; nothing forwards the text (see
    broker.parse_counts).
    """
    with grading_tree(scratch, task_dir) as tree:
        # Built by allowlist, same as the model's environment and for a stronger
        # reason (issue #14 F1, one hop downstream). This function produces the
        # VERDICT every row is scored on, so a name inherited here does not bias
        # what the model did -- it changes what the grade says the model did.
        # PYTHONPATH shadows the package under test, GIT_CONFIG_GLOBAL changes
        # what `git apply` does, NODE_OPTIONS preloads a module into every node
        # the suite spawns, PIP_INDEX_URL changes what requirements.txt fetches.
        # None of those are the model's doing and all of them land in `pass`.
        #
        # GAUNTLET_BROKER_SOCK no longer needs its explicit pop -- the allowlist
        # excludes it by construction -- but the pop stays because the rule it
        # enforces (this grade is never brokered) is worth stating where a reader
        # of this function will see it.
        env = grader_env(task_dir=task_dir)
        env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)
        env.pop("GAUNTLET_BROKER_SOCK", None)
        r = subprocess.run(["bash", "verify.sh"], cwd=tree, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=600)
        return r.returncode, r.stdout or ""


def run_verify(scratch, task_dir):
    """The authoritative gate. Runs the TASK's verify.sh, never the model's copy.

    Not a brokered call and never counted against K (ticket 17 section 5): it
    goes straight to graded_run, after the model is gone and the broker is
    closed.
    """
    if not os.path.exists(os.path.join(task_dir, "verify.sh")):
        return False
    return graded_run(scratch, task_dir)[0] == 0


# install artifacts written during the run are not model-authored code
LOC_EXCLUDE = [":(exclude)package-lock.json", ":(exclude)**/package-lock.json",
               ":(exclude)yarn.lock", ":(exclude)pnpm-lock.yaml"]


def base_commit(scratch):
    """The base commit of a scratch tree, or None if it cannot be resolved.

    prepare_scratch() git-inits and makes exactly one commit, so the base is
    always the root commit. Resolving it that way rather than off a tag or a
    threaded-through SHA means the anchor needs no extra state and is equally
    available when replaying an archived scratch tree after the fact.
    """
    r = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=scratch,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    roots = r.stdout.split()
    return roots[0] if r.returncode == 0 and len(roots) == 1 else None


def loc_changed(scratch):
    """Lines the model changed, measured against the base commit.

    Ticket 22 defect 1: this used to diff the index against HEAD, so a model
    that committed its own work left index == HEAD and the field recorded 0.
    That measures a git habit, not the work. calib-d2 caught it intermittently
    within one cell -- sol r2 and r3 recorded 0 against 57 and 58 real changed
    lines because they committed, while r1, same model and task, recorded its
    60 correctly only because it happened to leave the work in the tree.

    `git add -A` still runs first so untracked files count, and the diff is
    taken from the base commit to the index, so committed, uncommitted and
    untracked work all land in the same number.
    """
    subprocess.run(["git", "add", "-A"], cwd=scratch,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = base_commit(scratch)
    if base is None:
        # Never silently: a 0 here is what the defect looked like, so say so
        # rather than emitting an unremarkable-looking number.
        print(f"  ! loc_changed: no base commit in {scratch}, field is unanchored",
              file=sys.stderr)
        return 0
    r = subprocess.run(["git", "diff", "--cached", base, "--shortstat", "--", "."] + LOC_EXCLUDE,
                       cwd=scratch,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ins = dele = 0
    m = re.search(r"(\d+) insertion", r.stdout)
    if m:
        ins = int(m.group(1))
    m = re.search(r"(\d+) deletion", r.stdout)
    if m:
        dele = int(m.group(1))
    return ins + dele


def append_row(results_path, row):
    os.makedirs(os.path.dirname(results_path), exist_ok=True)
    line = json.dumps(row) + "\n"
    fd = os.open(results_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def existing_ids(results_path):
    """run_ids that count as done -- i.e. resumed sweeps should skip them.

    Gate is exit_reason == "ok", the same completeness gate ladder_from_results.py
    uses for analysis. A cli_error/timeout/etc. row means the CLI invocation itself
    never finished, so its run_id must stay pending and retry on the next sweep
    instead of silently blocking behind its own failed attempt.

    `cap_exhausted` is the exception and belongs with "ok": it is not a fault,
    it is the protocol working -- the model spent its K requests and the run was
    ended and scored a failure (pre-registration amendment A1,
    docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, registered
    2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip at >= 10 requests).
    Re-running it would be retry-until-pass, which is exactly the optional
    stopping section 8 rules out. `broker_failed` is a fault and does stay
    pending, like any CLI error.
    """
    ids = set()
    if not os.path.exists(results_path):
        return ids
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("exit_reason") in ("ok", "cap_exhausted"):
                    ids.add(row["run_id"])
            except Exception:
                continue
    return ids


# --------------------------------------------------------------------------- #
# Wall-clock cap
# --------------------------------------------------------------------------- #
# Tiers 1 and 2 shared one key and tier 3 had its own, which is the whole cap
# vocabulary the first sweeps needed. t4 and t5 tasks exist now.
_TIER_RE = re.compile(r"^t(\d+)(?:[-_]|$)")
_LEGACY_TIMEOUT_KEYS = {1: "timeout_t1_t2_s", 2: "timeout_t1_t2_s", 3: "timeout_t3_s"}


def resolve_timeout_s(task, defaults):
    """The wall-clock cap `task` runs under, or ValueError naming what to declare.

    Amendment A3: once `defaults.turn_cap_n` is registered (not null), it
    supersedes everything below -- the wall clock becomes a pure hang backstop
    derived from N via serving_registry.derive_wall_clock_s(N)
    (N x 157 s x 1.5, rounded up to the next 600 s), the same value for every
    task in the config, because N is a single registered number and the tiered
    timeout_t*_s constants are what it replaces. `turn_cap_n` absent or null
    (its state until the conductor fills it after stage 0, and permanently in
    runs-glm-stage0.yaml, which carries no turn cap by design) leaves this
    function's behaviour exactly as it was before A3.

    Resolution order for a tier-N task otherwise: `timeout_t{N}_s`, then the
    legacy key covering that tier (t1/t2 -> timeout_t1_t2_s, t3 ->
    timeout_t3_s), then an explicit `timeout_default_s`. Nothing after that --
    a tier with no cap declared for it is a config bug and is raised as one.

    Fail-closed for the same reason check_effort() is (ticket 22 defect 2). The
    old expression keyed on the literal "t3" and sent everything else to the
    t1/t2 branch, so t4 and t5 tasks silently drew a cap sized for a 20-minute
    task. Adding a tier must therefore cost a config edit it cannot forget to
    make, not inherit the short cap by falling off the end of a boolean.

    A mis-sized cap is still a real cost, but not the cost this docstring used to
    claim (issue #12 d). It read: "Cap-terminated runs score as FAILURES under
    the pre-registration's estimand: a mis-sized cap does not show up as a
    timeout in the analysis, it shows up as task difficulty." That is the
    discarded reading. The pre-registered bundle says the opposite -- "Timeouts
    and infra errors are distinct statuses, excluded from the denominator and
    reported separately, never counted as model failures" -- and findings.md
    rule 7 and issue #8 agree. A wall-clock kill now carries exit_reason
    "timeout", which run_status classes out of the pass-rate denominator.

    So the cost of a mis-sized cap is LOST RUNS, not fake failures: the cell's
    denominator shrinks and the excluded count says so out loud. That is a
    better failure -- visible, and it does not put the scheduler's behaviour in
    the accuracy column.
    """
    defaults = defaults or {}
    turn_cap_n = defaults.get("turn_cap_n")
    if turn_cap_n is not None:
        return serving_registry.derive_wall_clock_s(turn_cap_n)
    tried = []
    m = _TIER_RE.match(task or "")
    if m:
        tier = int(m.group(1))
        for key in (f"timeout_t{tier}_s", _LEGACY_TIMEOUT_KEYS.get(tier)):
            if key and key not in tried:
                tried.append(key)
                if key in defaults:
                    return defaults[key]
    tried.append("timeout_default_s")
    if "timeout_default_s" in defaults:
        return defaults["timeout_default_s"]
    raise ValueError(
        f"no wall-clock cap declared for task {task}; "
        f"add one of {', '.join(tried)} to the config's defaults")


# Amendment A3, round 2 (issue #19). ladder_from_results.py, tables.py and
# stats.py each compute a rate but load no sweep config of their own, so N
# used to arrive ONLY via a --turn-cap-n flag -- a second source of truth
# that silently diverged from this file's own defaults.turn_cap_n the moment
# an operator forgot the flag: a config carrying turn_cap_n: 20 and an
# unflagged reader printed a rate as if turn caps did not exist, with no
# warning that the two had disagreed. These two functions are the one place
# that precedence is decided, so every reader states it the same way.
#
# ladder_from_results.py and tables.py import this module and call
# turn_cap_n_from_config/resolve_turn_cap_n directly, reusing parse_yaml
# rather than inventing a third YAML reader. stats.py cannot: it is
# CORE_MODULE (runner/import_gate.py rule A: a core module may import only
# the stdlib and other core modules), and this module is neither, so it
# carries its own minimal, duplicated scalar reader instead -- same posture
# as stats.py's own multi_driver_models/model_key, kept in lockstep with
# tables.py by intent rather than DRY'd across the core boundary.
DEFAULT_TURN_CAP_CONFIG = os.path.join(RUNNER_DIR, "runs-glm-stage1.yaml")


def turn_cap_n_from_config(config_path):
    """`defaults.turn_cap_n` out of a sweep config, for a caller that has no
    config of its own already open.

    Tolerant the way a reader-side default has to be about ABSENCE: a
    missing file, a file with no `defaults:` block, and `turn_cap_n` absent
    or explicitly `null` all return None ("unset"), never an error.

    NOT tolerant about TYPE (round 3, issue #19). `turn_cap_n: "20"`,
    `20.0`, `"twenty"` and `true` all parse without error under parse_yaml --
    a quoted string, a float, an unparseable string, and a bool are all
    valid YAML scalars -- so a value check has to happen here, not rely on
    the parser to have refused them. Left unchecked, a quoted "20" reaches
    apply_turn_cap's `turns > n` and raises TypeError deep inside the
    classification loop instead of at the config boundary where the bad
    value actually lives, and stats.py's independent reader used to swallow
    the same bad value as a caught ValueError and print turn_cap_n=unset --
    publishing an UNCAPPED table with no warning that the config's N was
    ignored. `bool` is explicitly excluded even though it subclasses `int` in
    Python (`isinstance(True, int)` is True), because a stray `turn_cap_n:
    true` must not silently register a cap of N=1.
    """
    if not config_path or not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = parse_yaml(f.read()) or {}
    n = (cfg.get("defaults") or {}).get("turn_cap_n")
    if n is None:
        return None
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(
            f"{config_path}: defaults.turn_cap_n must be an int or null, "
            f"got {n!r}")
    return n


def resolve_turn_cap_n(config_path, flag_value):
    """N with an explicit --turn-cap-n beating the config, always -- exactly
    one of the two sources of truth wins, and the caller learns which.

    Returns (n, source) where source is "flag", "yaml", or "unset". Every
    reader prints both, in the same line, so a published table never has to
    be reverse-engineered to learn what N it was rendered under.
    """
    if flag_value is not None:
        return flag_value, "flag"
    n = turn_cap_n_from_config(config_path)
    if n is not None:
        return n, "yaml"
    return None, "unset"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def execute_run(run, cfg, tasks_dir, scratch_root, results_path, usage_path=None):
    # issue #25 verify pass. Checked BEFORE task_dir/scratch/prepare_scratch --
    # main()'s config-time gate already drops a driver_unsupported row from
    # `runs` before it ever reaches here, but --mock skips straight past
    # build_cli_cmd (the mock branch below never calls it), so this is the
    # backstop for any caller -- a --mock invocation, a test, a future script
    # -- that reaches execute_run directly without going through that gate.
    # Placed ahead of prepare_scratch deliberately: that function runs three
    # git subprocesses (init/add/commit) before a token would be spent, and a
    # row this function is about to refuse must not pay that cost first.
    if not driver_has_dispatch_path(run.get("driver")):
        return record_driver_unsupported(
            run,
            f"driver {run.get('driver')!r} has no dispatch path in "
            f"build_cli_cmd (issue #25); only claude-code is implemented",
            results_path)

    # absolute: mock patch / verify run with cwd=scratch, so relative paths break
    task_dir = os.path.abspath(os.path.join(tasks_dir, run["task"]))
    scratch = os.path.abspath(os.path.join(scratch_root, run["run_id"]))
    defaults = cfg.get("defaults", {}) or {}
    timeout_s = resolve_timeout_s(run["task"], defaults)

    mock = os.environ.get("GAUNTLET_MOCK")
    tokens_in = tokens_out = turns = 0
    wall_s = 0.0
    usage_detail = None  # ticket 08: full cache-token breakdown for the ledger row
    # ticket 16: whether the model ran sealed off from its own answer key. None
    # for mock runs, where no model was invoked and the question is vacuous.
    # Recorded per row so "was this result open-book?" is answerable from the
    # corpus instead of reconstructed from commit dates.
    sealed = None
    # ticket 32: HOW the CLI was driven -- single_shot (codex exec) vs
    # multi_turn (claude -p agentic session). None for mock runs, where no
    # model was invoked and the question is vacuous, same as `sealed`.
    # Derived through usage_ledger's one family rule, never from `turns`.
    invocation_mode = None
    # ticket 26: whether the model ran with WRITES contained -- i.e. unable to
    # append to or truncate results.jsonl, poison the canonical tasks/ tree, or
    # write into a sibling run's scratch. Separate from `sealed` because it is a
    # separate property (integrity, not confidentiality) and because it has a
    # separate history: every row written before 2026-07-29 was produced with
    # reads sealed and writes wide open, and will lack this field entirely.
    # Absent-or-false means "not contained" for any corpus consumer; the
    # admissibility argument for those rows is in ticket 26.
    write_contained = None
    # blocker 2: whether the model ran with its own HOME/CLAUDE_CONFIG_DIR
    # instead of the operator's. A separate property from `sealed` for the same
    # reason write_contained is: the seal is a filesystem profile with an
    # opt-out, this is the environment the binary reads its global config from,
    # and the two can disagree. Every row written before 2026-08-25 lacks the
    # field entirely -- absent means NOT isolated, i.e. a harness=False row
    # that carried whatever global harness lived in the operator's home.
    home_isolated = None
    # None, not 0.0, and initialised beside home_isolated for the same reason:
    # the mock path never launches a CLI, so there is no envelope to read a cost
    # from. "The CLI did not report one" and "the run was free" are different
    # facts, and a mock row must not claim the second.
    cost_usd = None
    # ticket 17: the same three questions for the acceptance cap. brokered=false
    # is a protocol-v1 row and the pre-registration forbids pooling it with v2,
    # so it is a field rather than an assumption about commit dates.
    brokered = None
    k_cap = None
    acceptance_requests = None
    installed = None

    with contextlib.ExitStack() as stack:
        bk = None
        if not mock and broker_enabled():
            brokered = True
            k_cap = broker.resolve_k(defaults.get("k_acceptance"))
            # Started before prepare_scratch so the shim can carry the socket
            # path and still land in the base commit -- outside loc_changed()'s
            # diff, like the harness files. A broker that will not start raises
            # here, before a token is spent: no counter, no run.
            bk = stack.enter_context(broker.acceptance_broker(
                scratch, task_dir, k_cap, graded_run))
            installed = {"verify.sh": broker.shim_text(bk.sock_path, k_cap,
                                                       sys.executable)}
        elif not mock:
            brokered = False
            print("WARNING: GAUNTLET_NO_BROKER=1 -- acceptance feedback is "
                  "uncapped and the canonical suite is readable as source; "
                  "this row is marked brokered=false (protocol v1)",
                  file=sys.stderr)

        prepare_scratch(task_dir, scratch, run["harness"],
                        verify_text=(installed or {}).get("verify.sh"))

        if mock == "fail":
            exit_reason = "mock_fail"
        elif mock:  # "1" or any truthy -> mock pass
            ok = apply_mock(task_dir, scratch)
            exit_reason = "mock" if ok else "mock_patch_failed"
        else:
            prompt = compose_prompt(task_dir, run["harness"], run["mode"],
                                    k=k_cap)
            cmd = build_cli_cmd(run["model"], run["effort"], prompt,
                               driver=run.get("driver"))
            invocation_mode = usage_ledger.invocation_mode(model_family(run["model"]))
            sealed = seal_enabled()
            # AND'd against the seal module's own capability flag rather than
            # hardcoded True: if write containment is ever removed from
            # sandbox_seal, the corpus says so instead of carrying rows that
            # claim a guarantee nothing is enforcing.
            write_contained = sealed and sandbox_seal.WRITE_CONTAINMENT
            # Read off the same predicate run_cli consults, never restated as a
            # literal here: a row asserting its own isolation is exactly what
            # the pre-2026-08-25 rows could not be trusted about.
            home_isolated = home_isolation_enabled()
            out, exit_reason, wall_s = run_cli(cmd, scratch, timeout_s, task_dir,
                                               run["model"], bk=bk)
            cost_usd = reported_cost_usd(out)
            usage_detail = usage_ledger.parse_usage_detailed(model_family(run["model"]), out)
            tokens_in = usage_detail["tokens_in"]
            tokens_out = usage_detail["tokens_out"]
            turns = usage_detail["turns"]
            # A CLI can exit 0 having never emitted a completed turn -- the
            # stream simply stops mid-tool-call. run_cli only sees returncode 0
            # and calls that "ok", so the run lands in results.jsonl as a
            # successful zero-token run. That row is poison for any spend
            # measurement: it is not a model choosing to spend nothing, it is a
            # run whose generation never finished, and averaging it into a tier
            # cell drags the mean toward zero and inflates within-tier CV. Same
            # class of truncation as a timeout, so it gets the same treatment --
            # a non-"ok" reason, which is the flag every analysis already gates
            # on (ladder_from_results.py excludes it; the excluded count is
            # printed, never silently dropped).
            if exit_reason == "ok" and turns == 0:
                exit_reason = "no_completion"
            tdir = os.path.join(RUNNER_DIR, "results", "transcripts")
            os.makedirs(tdir, exist_ok=True)
            with open(os.path.join(tdir, run["run_id"] + ".txt"), "w") as tf:
                tf.write(out)
        if bk is not None:
            acceptance_requests = bk.requests

    # ticket 18: read the scratch tree BEFORE grading and record what the model
    # did to the grading apparatus. The overlay in run_verify already makes the
    # grade honest; this is so the corpus can answer "did anyone try?" without
    # the scratch trees, which are not retained forever.
    tamper_files = tamper_report(scratch, task_dir, installed=installed)

    # pass_raw is what the grader itself returned, captured before any protocol
    # rule rewrites `passed`. None means the grader never produced a verdict at
    # all (it timed out below) -- which is a different fact from "the grader said
    # False", and collapsing the two is what makes a gate unauditable.
    pass_raw = None
    try:
        passed = run_verify(scratch, task_dir)
        pass_raw = passed
    except subprocess.TimeoutExpired:
        passed = False
        exit_reason = exit_reason + "+verify_timeout"

    # ticket 34. Pre-registration amendment A1 (docs/studio-handoff/prompt-2-
    # run-experiment.md at a0cef36, registered 2026-08-25: K=20, cap_exhausted
    # SCORED, stage-0 flip at >= 10 requests) -- "a run that exhausts K ...
    # is SCORED as an autonomy failure" -- rules the cap case; ticket 34
    # generalizes it here, from the cap to every run that did not finish.
    # exit_reason == "ok" is this instrument's completeness gate
    # (existing_ids() above; ladder_from_results.py excludes non-"ok" rows and
    # prints the excluded count), so `pass` -- the field that reads most like a
    # verdict -- may not claim success for an incomplete run. Before this, only
    # cap_exhausted and verify_timeout were handled and cli_error got neither,
    # which is how sweep2b--fable--medium--bare--t3-a--r1 landed cli_error with
    # pass=true.
    #
    # The test is `!= "ok"`, deliberately NOT membership in a list of known-bad
    # reasons: a reason added later must inherit this gate because it is not
    # "ok", never because someone remembered to enlist it. An enumerated
    # denylist would reintroduce the same silent default one reason further on.
    # Scoring the run a failure is a protocol rule; discarding what the grader
    # said would make the rule unauditable, so the verdict survives as
    # pass_raw. Nothing downstream may read pass_raw or pass_at_cap as pi.
    if exit_reason != "ok":
        passed = False

    # Kept under its original name because every row written before ticket 34
    # carries it -- `pass_at_cap` is this runner's field, introduced at
    # ticket 34, not named by pre-registration amendment A1
    # (docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, registered
    # 2026-08-25), which says only that cap_exhausted is SCORED and K=20. It
    # is now exactly pass_raw narrowed to the cap case; the general gate
    # above is what forces `pass` False, here as everywhere else.
    pass_at_cap = pass_raw if exit_reason == "cap_exhausted" else None

    loc = loc_changed(scratch)
    # `model` stays exactly as the config wrote it so existing rows, run_ids and
    # the alias-keyed analysis in stats.py/tables.py keep working; `model_id`
    # records which CLI id actually ran, which an alias row cannot otherwise tell
    # you and which is what makes alias and id rows groupable as one model.
    row = {
        "run_id": run["run_id"], "ts": now_iso(), "sweep": run["sweep"],
        "model": run["model"], "model_id": resolve_model(run["model"])[0],
        "effort": run["effort"], "harness": run["harness"],
        # WHICH CLI DROVE THE MODEL, and which rung of the dose ladder this cell
        # is. build_runs carried both and the gate required the driver, but this
        # row dict wrote neither -- only record_structurally_impossible did, so
        # every actually-EXECUTED row carried no driver at all.
        #
        # findings.md reports pi as a separately-reported vehicle contrast: pi
        # has no hooks and no subagents, so the driver is part of the TREATMENT.
        # Without this field a corpus of 3/3 claude-code and 0/3 pi renders as
        # one model row at 50%, and the stage-1 config's own "group by driver"
        # instruction names a column that does not exist.
        #
        # harness_level is None, never 0, when no rung is declared: `harness`
        # above is the pre-ladder boolean and False is not level 0.
        "driver": run.get("driver"),
        "harness_level": run.get("harness_level"),
        "task": run["task"], "rep": run["rep"], "pass": passed,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "wall_s": wall_s,
        # ticket 31: which parse formula produced tokens_in, recorded by the
        # code that ran it. Read off the module rather than written as a literal
        # here -- a hardcoded "measured" would be the row asserting its own
        # correctness, which is exactly what the 268 pre-f11be7e rows could not
        # do. The status is derived through the one shared rule, never restated.
        "usage_parser_version": usage_ledger.USAGE_PARSER_VERSION,
        "tokens_in_status": usage_ledger.tokens_in_status(
            model_family(run["model"]), usage_ledger.USAGE_PARSER_VERSION),
        "turns": turns, "loc_changed": loc, "exit_reason": exit_reason,
        "sealed": sealed, "write_contained": write_contained,
        # blocker 2: whether the "bare" arm was actually bare.
        "home_isolated": home_isolated,
        # ticket 32: which instrument produced this row's session shape.
        "invocation_mode": invocation_mode,
        # 2026-08-31: which credential path, and which CLI build, produced this
        # row. The 14 auth_unavailable rows of 2026-08-28 carried neither, so
        # the corpus cannot say what auth arrangement any earlier claude row ran
        # under, nor which binary ran it -- the rows record a symlinked PATH,
        # which names a pointer and not a build. Those rows are not invalidated
        # by the gap; they are simply not reproducible, and two fields are
        # cheaper than that argument.
        #
        # None for every non-claude family, never a default string: kimi gets a
        # Moonshot key and local gets a loopback endpoint, and labelling either
        # with a claude auth path would be this row asserting something nobody
        # measured.
        "auth_source": (claude_auth_source()
                        if model_family(run["model"]) == "claude" else None),
        # The CLI's own figure, never a rate table in this repo. None means the
        # CLI did not report one -- distinct from 0.0, which means free.
        "cost_usd": cost_usd,
        "metered": run_is_metered(run["model"]),
        "claude_cli_version": (claude_cli_version()
                               if model_family(run["model"]) == "claude"
                               else None),
        # ticket 17. acceptance_requests is the design parameter K governs, and
        # it is counted by the broker rather than inferred from CLI telemetry --
        # which is what makes it comparable across families, unlike `turns`
        # (structurally 1 on all 148 Codex rows, and barred from citation).
        "brokered": brokered, "k_cap": k_cap,
        "acceptance_requests": acceptance_requests,
        "cap_exhausted": exit_reason == "cap_exhausted",
        # issue #12 (d): the estimand disposition of this row, stamped by the
        # code that ran it rather than re-derived by each reader from a reason
        # string it may not recognise. Derived through run_status's one table,
        # never restated as a literal here.
        "status_class": run_status.status_class(exit_reason),
        # Verifier findings 2 and 4: WHAT SERVED THIS ROW. The endpoint, where
        # that endpoint came from, which key source was consulted (a path or a
        # word, never a value) and the resolved binary. Without these a row
        # names a model and says nothing about the server or the build that
        # answered for it.
        **invocation_provenance(run["model"]),
        # ticket 34: the grader's own verdict, on every row, so `pass` being a
        # gated field costs no information. Not pi -- see the gate above.
        "pass_raw": pass_raw,
        "pass_at_cap": pass_at_cap,
        "tampered": bool(tamper_files), "tamper_files": tamper_files,
    }
    append_row(results_path, row)

    # ticket 08: append-only usage.jsonl, joinable to results.jsonl by run_id.
    # Prospective only -- does not touch or retrofit any prior row.
    urow = usage_ledger.build_usage_row(row, model_family(run["model"]), usage_detail,
                                        model_id=row["model_id"])
    # Issue #24: usage_path is None for every caller written before this ticket,
    # so it falls back to the module-level USAGE_PATH exactly like before --
    # existing positional callers (tests and any external script) keep working
    # unchanged. main() below is the only caller that now passes usage_path
    # explicitly, derived from --results/--usage.
    if usage_path is None:
        usage_path = USAGE_PATH
    usage_ledger.append_usage_row(usage_path, urow)

    return row


def recorded_impossible_ids(results_path):
    """run_ids already written as structurally-impossible.

    Ported from PR #16. The runner is resume-friendly by design -- main() is
    re-invoked to pick up pending work and re-walks the whole matrix each time --
    and the impossible cells are still impossible on every pass, so without this
    each invocation appended the same run_id again.

    existing_ids() does not cover it: that set is built from rows whose
    exit_reason is "ok" or "cap_exhausted", and a structurally_impossible row is
    deliberately neither, since it must never look like a completed run.

    Left unguarded, one re-invocation per day adds one duplicate status row per
    day, so `structurally_impossible=N` in any report counts INVOCATIONS rather
    than cells -- a number that grows while nothing runs.

    A corrupt line is skipped rather than ending the scan: a half-written row
    from a killed run must not make the guard forget every id after it, which
    would silently restore the duplication.
    """
    ids = set()
    if not os.path.exists(results_path):
        return ids
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("exit_reason") == "structurally_impossible":
                ids.add(row.get("run_id"))
    return ids


def record_structurally_impossible(run, reason, results_path):
    """Write the row for a cell the driver cannot express (issue #12 c).

    A cell that cannot exist must leave a trace. Dropping it silently turns
    "this driver has no hooks, so this rung does not exist for it" into "nobody
    ran it", and those are different facts -- only the first one is reportable,
    and only if something wrote it down.

    `pass` is None, never False. False is a measurement: it says the model
    attempted the task and did not complete it, which puts a cell that never
    existed into the denominator and drags the vehicle contrast toward the
    pooled mean. exit_reason is its own status for the same reason -- it is not
    "ok", so every existing reader gate (existing_ids, corpus_gates.summarizable,
    ladder_from_results) already excludes it without being taught anything new.
    """
    row = {
        "run_id": run["run_id"], "ts": now_iso(), "sweep": run["sweep"],
        "model": run["model"], "model_id": resolve_model(run["model"])[0],
        "effort": run["effort"], "harness": run["harness"],
        "harness_level": run.get("harness_level"), "driver": run.get("driver"),
        "task": run["task"], "rep": run["rep"],
        "pass": None, "pass_raw": None, "pass_at_cap": None,
        "exit_reason": "structurally_impossible",
        "status_class": run_status.status_class("structurally_impossible"),
        **invocation_provenance(run["model"]),
        "structurally_impossible_reason": reason,
        "tokens_in": None, "tokens_out": None, "turns": None, "wall_s": None,
        "loc_changed": None, "sealed": None, "write_contained": None,
        "home_isolated": None, "invocation_mode": None,
        "brokered": None, "k_cap": None, "acceptance_requests": None,
        "cap_exhausted": False, "tampered": False, "tamper_files": [],
    }
    append_row(results_path, row)
    return row


def recorded_driver_unsupported_ids(results_path):
    """run_ids already written as driver_unsupported (issue #25).

    Same resume-friendly guard recorded_impossible_ids provides for
    structurally-impossible rows: main() re-walks the whole matrix on every
    invocation, and a cell that is unsupported today is still unsupported on
    the next pass, so without this each invocation would append a duplicate
    status row for the same run_id.
    """
    ids = set()
    if not os.path.exists(results_path):
        return ids
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("exit_reason") == "driver_unsupported":
                ids.add(row.get("run_id"))
    return ids


def record_driver_unsupported(run, reason, results_path):
    """Write the row for a driver run.py has no dispatch path for (issue #25).

    Distinct from record_structurally_impossible: that answers "can the
    driver express this cell", a capability-manifest fact serving_registry
    measures per (model, driver) row. This answers "does run.py itself know
    how to launch this driver at all" -- orthogonal, and a driver can carry a
    perfectly valid registry row (glm-4.7 x pi does; check_dispatch passes
    it) while build_cli_cmd still has no argv for it. Called from two places:
    main()'s config-time gate, before any row of this shape is ever passed to
    execute_run, and execute_run itself as a defense-in-depth backstop for
    any caller (a --mock run, a test, a future script) that reaches it
    without going through main()'s loop.

    `pass` is None, never False, and exit_reason is a distinct string never
    "ok" -- same reasoning as record_structurally_impossible: this cell was
    never dispatched, so False would assert an attempt that never happened,
    and run_status.py classes "driver_unsupported" INFRA so every existing
    reader gate already excludes it from a pass-rate denominator.
    """
    row = {
        "run_id": run["run_id"], "ts": now_iso(), "sweep": run["sweep"],
        "model": run["model"], "model_id": resolve_model(run["model"])[0],
        "effort": run["effort"], "harness": run["harness"],
        "harness_level": run.get("harness_level"), "driver": run.get("driver"),
        "task": run["task"], "rep": run["rep"],
        "pass": None, "pass_raw": None, "pass_at_cap": None,
        "exit_reason": "driver_unsupported",
        "status_class": run_status.status_class("driver_unsupported"),
        **invocation_provenance(run["model"]),
        "driver_unsupported_reason": reason,
        "tokens_in": None, "tokens_out": None, "turns": None, "wall_s": None,
        "loc_changed": None, "sealed": None, "write_contained": None,
        "home_isolated": None, "invocation_mode": None,
        "brokered": None, "k_cap": None, "acceptance_requests": None,
        "cap_exhausted": False, "tampered": False, "tamper_files": [],
    }
    append_row(results_path, row)
    return row


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet runner")
    ap.add_argument("--config", default=os.path.join(RUNNER_DIR, "runs.yaml"))
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--scratch", default=os.path.join(ROOT, ".scratch"))
    ap.add_argument("--results", default=DEFAULT_RESULTS_PATH)
    ap.add_argument("--usage", default=None,
                    help="override the usage-ledger path (issue #24); default "
                         "is usage.jsonl beside --results, so redirecting "
                         "--results redirects both files together")
    ap.add_argument("--only", default=None, help="substring filter on run_id")
    ap.add_argument("--limit", type=int, default=None,
                    help="execute at most N pending runs this invocation (resume-friendly)")
    ap.add_argument("--max-usd", type=float, default=None,
                    help="hard cap on cumulative Kimi (real-money) spend; skip further "
                         "kimi runs once reached. Fable/Sol are subscription ($0).")
    ap.add_argument("--mock", action="store_true",
                    help="mock pass: apply solution.patch instead of calling a CLI (no tokens)")
    ap.add_argument("--mock-fail", action="store_true",
                    help="mock fail: leave base unchanged so verify.sh fails (no tokens)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Issue #28: a --scratch that resolves inside (or equal to) the live
    # results directory leaves run checkouts sitting among corpus files even
    # though a checkout is not itself a corpus write -- the guard below only
    # ever looks at --results/--usage, never at --scratch, so it reports the
    # corpus untouched while runner/results/ fills up with git checkouts.
    # Checked here, before the config is even parsed and long before the
    # first prepare_scratch() call, and unconditionally (not gated on
    # --mock/--dry-run like the guard below): a live dispatch with a bad
    # --scratch pollutes the same directory just as surely as a mock one.
    scratch_msg = corpus_guard.refuse_scratch_inside_results(
        args.scratch, os.path.dirname(DEFAULT_RESULTS_PATH))
    if scratch_msg:
        print(scratch_msg, file=sys.stderr)
        sys.exit(corpus_guard.REFUSE_EXIT)

    # CLI flags are sugar over the GAUNTLET_MOCK env var that execute_run reads.
    if args.mock_fail:
        os.environ["GAUNTLET_MOCK"] = "fail"
    elif args.mock:
        os.environ["GAUNTLET_MOCK"] = "1"

    # Issue #24: usage.jsonl derives from --results' own directory unless
    # --usage names a different file explicitly -- so a caller who moves
    # --results to a scratch tree moves the ledger with it, without having to
    # know the ledger exists.
    usage_path = (os.path.abspath(args.usage) if args.usage is not None
                  else os.path.join(os.path.dirname(os.path.abspath(args.results)),
                                    "usage.jsonl"))

    # Issue #23: this is the guard, not the corpus-pinning tests -- those prove
    # it fires. A demo/mock/dry-run invocation that (after the derivation
    # above) still resolves to either LIVE corpus path is refused outright
    # rather than silently redirected: three times in one wave a demo run
    # appended synthetic rows to the real results.jsonl/usage.jsonl because
    # the default WAS the live path and nothing said so. Redirect-on-detect
    # was considered and rejected -- it would create an unnamed scratch
    # corpus the caller does not know to distrust or clean up, and a caller
    # who genuinely wants the live paths mocked (there is no such legitimate
    # case) gets no way to say so explicitly, same posture as this. Refusing
    # forces one flag, once, in the open, the same place the config-rejected
    # checks below already fail closed.
    #
    # --dry-run is in scope alongside GAUNTLET_MOCK, not just an alias for it:
    # a dry-run whose matrix contains a structurally-impossible cell still
    # calls record_structurally_impossible() (below, before the dry-run
    # return) and that write goes through args.results same as any other row.
    # Issue #23's title says "demo/dry runs" -- dry-run is named, not implied.
    #
    # The path check itself is corpus_guard.is_live_path, not a string
    # compare: a symlinked directory, a case-only variant (APFS is
    # case-insensitive by default) and a doubled leading slash all name the
    # live file while comparing unequal as strings, and a verifier
    # reproduced all three against the first cut of this guard.
    if os.environ.get("GAUNTLET_MOCK") or args.dry_run:
        msg = corpus_guard.refusal_message(
            [(args.results, DEFAULT_RESULTS_PATH, "results corpus"),
             (usage_path, USAGE_PATH, "usage ledger")],
            "--results (and, if needed, --usage) at a scratch path, e.g. "
            "--results /tmp/scratch/results.jsonl")
        if msg:
            print(msg, file=sys.stderr)
            sys.exit(corpus_guard.REFUSE_EXIT)

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = parse_yaml(f.read())

    runs = build_runs(cfg)
    if args.only:
        runs = [r for r in runs if args.only in r["run_id"]]

    # Validate the whole matrix before the first CLI call. An unknown model id or
    # an undeclared effort tier is a config bug, and finding it 40 runs into a
    # sweep means those 40 runs were spent on a matrix that was never going to
    # finish. Fail closed, up front, at zero cost.
    #
    # issue #12: the serving gate runs HERE, in this same loop, because this is
    # the last point at which a refusal costs nothing. serving_registry answers
    # "is the config this run declares the one its registry row was produced
    # under, and can this driver express this cell at all".
    #
    # Gated = the model has a row. The registry holds glm-4.7 today; fable, sol
    # and everything predating it have no row and are not gated, because
    # inventing rows for them would manufacture a serving config nobody measured.
    # Both counts are printed below: the defect this closes was a gate with zero
    # invokers, and the only durable defence against its return is that the
    # runner says out loud how many runs it actually inspected.
    rows = serving_registry.load_rows()
    gated_models = serving_registry.models_with_rows(rows)
    requested_serving = serving_config_from(cfg)

    bad = []
    impossible = []
    driver_unsupported = []
    gated = ungated = 0
    for r in runs:
        try:
            model_id = resolve_model(r["model"])[0]
            check_effort(r["model"], r["effort"])
            # Same posture for the wall-clock cap: an undeclared tier is a config
            # bug, and the run it would spoil is the one that already cost money.
            resolve_timeout_s(r["task"], cfg.get("defaults", {}) or {})
            # Finding 2 / issue #7: the row's numbers were measured on THIS
            # machine, so a run served from another host is reported under
            # measurements nobody took there. The serving gate cannot see this --
            # the declared config still matches the row, because the endpoint is
            # not a pinned field.
            check_local_endpoint(r["model"], LOCAL_BASE_URL)

            row_model = serving_registry.serving_model_name(model_id)
            if row_model in gated_models:
                serving_registry.check_dispatch(
                    rows, row_model,
                    serving_registry.require_driver(r.get("driver"), row_model),
                    requested_serving,
                    harness_level=r.get("harness_level"))
                # issue #25 verify pass. Checked AFTER check_dispatch,
                # deliberately, not before: registry capability is necessary
                # but not sufficient. check_dispatch above can bless a cell
                # (glm-4.7 x pi passes it -- pi has its own registry row,
                # require_driver above already confirmed the NAME is one the
                # registry recognizes) while run.py itself still has no
                # launch code for the driver. Checking here means a name the
                # registry does not recognize at all (a config typo) still
                # fails through require_driver's own refusal above -- a
                # config bug, exit 2 -- rather than being reclassified as
                # this soft, per-cell skip; and a cell the registry already
                # refused as StructurallyImpossible (pi x L5) keeps that
                # exit_reason rather than this one, because check_dispatch
                # raises before this line is ever reached.
                if not driver_has_dispatch_path(r.get("driver")):
                    raise DriverUnsupported(
                        f"driver {r.get('driver')!r} has no dispatch path "
                        f"in build_cli_cmd (issue #25); only claude-code is "
                        f"implemented")
                gated += 1
            else:
                if not driver_has_dispatch_path(r.get("driver")):
                    raise DriverUnsupported(
                        f"driver {r.get('driver')!r} has no dispatch path "
                        f"in build_cli_cmd (issue #25); only claude-code is "
                        f"implemented")
                ungated += 1
        except serving_registry.StructurallyImpossible as e:
            # CAUGHT BEFORE ValueError, and the order is the whole point.
            # StructurallyImpossible subclasses RegistryError subclasses
            # ValueError, so appending it to `bad` would make ONE inexpressible
            # cell exit 2 for the entire sweep. A matrix containing pi x L5 is
            # not an invalid config; it is a valid matrix containing cells that
            # do not exist. They are dropped from the dispatch list and recorded
            # with their own status -- never scored 0, which would assert that
            # the model attempted the task and failed it.
            impossible.append((r, str(e)))
        except DriverUnsupported as e:
            # CAUGHT BEFORE ValueError for the identical reason: DriverUnsupported
            # subclasses ValueError, and one row with a driver run.py cannot yet
            # launch is a valid matrix containing a cell this INSTRUMENT cannot
            # dispatch -- not a config bug that should exit 2 for the whole sweep.
            driver_unsupported.append((r, str(e)))
        except ValueError as e:
            bad.append(f"  {r['run_id']}: {e}")
    if bad:
        print(f"config rejected -- {len(bad)} invalid run(s):")
        for b in dict.fromkeys(bad):
            print(b)
        sys.exit(2)

    print(f"serving gate: gated={gated} ungated={ungated} "
          f"structurally_impossible={len(impossible)} "
          f"driver_unsupported={len(driver_unsupported)} "
          f"declared={sorted(requested_serving) or 'none'}")
    if impossible:
        dropped = {r["run_id"] for r, _ in impossible}
        already = recorded_impossible_ids(args.results)
        for r, why in impossible:
            print(f"  [structurally-impossible] {r['run_id']}: {why}")
            if r["run_id"] not in already:
                record_structurally_impossible(r, why, args.results)
        runs = [r for r in runs if r["run_id"] not in dropped]
    if driver_unsupported:
        dropped = {r["run_id"] for r, _ in driver_unsupported}
        already = recorded_driver_unsupported_ids(args.results)
        for r, why in driver_unsupported:
            print(f"  [driver-unsupported] {r['run_id']}: {why}")
            if r["run_id"] not in already:
                record_driver_unsupported(r, why, args.results)
        runs = [r for r in runs if r["run_id"] not in dropped]

    # Same posture, same reason: a K outside the pre-registered range makes every
    # row of the sweep unreportable, and finding that at analysis time is finding
    # it after the spend.
    k_cap = None
    if not os.environ.get("GAUNTLET_MOCK") and broker_enabled():
        try:
            k_cap = broker.resolve_k((cfg.get("defaults", {}) or {}).get("k_acceptance"))
        except ValueError as e:
            print(f"config rejected -- {e}")
            sys.exit(2)

    done = existing_ids(args.results)
    pending = [r for r in runs if r["run_id"] not in done]
    skipped = len(runs) - len(pending)
    if args.limit is not None:
        pending = pending[:args.limit]

    print(f"total={len(runs)} pending={len(pending)} already_done={skipped} "
          f"mock={os.environ.get('GAUNTLET_MOCK', '0')} "
          f"K={k_cap if k_cap is not None else 'off'}")

    if args.dry_run:
        for r in runs:
            mark = "SKIP" if r["run_id"] in done else "RUN "
            print(f"  [{mark}] {r['run_id']}  (mode={r['mode']})")
        return

    # Renamed from kimi_spent: the claude family bills too once it runs on an
    # API key, and a variable named for one family is how --max-usd came to be
    # inert for every other one.
    spent = 0.0
    for i, r in enumerate(pending, 1):
        if run_is_metered(r["model"]) and args.max_usd is not None and spent >= args.max_usd:
            print(f"    [cap] metered spend ${spent:.2f} >= --max-usd "
                  f"${args.max_usd:.2f}; skipping {r['run_id']}", flush=True)
            continue
        print(f"[{i}/{len(pending)}] {r['run_id']} ...", flush=True)
        row = execute_run(r, cfg, args.tasks_dir, args.scratch, args.results,
                          usage_path)
        cost_note = ""
        if run_is_metered(r["model"]):
            spent += row_dollars(r["model"], row["tokens_in"],
                                 row["tokens_out"], row.get("cost_usd"))
            cap = f"/{args.max_usd:.2f}" if args.max_usd is not None else ""
            unknown = "" if row.get("cost_usd") is not None else " (cost unreported)"
            cost_note = f" spend=${spent:.3f}{cap}{unknown}"
        acc = ""
        if row["acceptance_requests"] is not None:
            acc = f" acc={row['acceptance_requests']}/{row['k_cap']}"
        print(f"    pass={row['pass']} reason={row['exit_reason']} "
              f"tok_in={row['tokens_in']} tok_out={row['tokens_out']} "
              f"wall={row['wall_s']}s turns={row['turns']} "
              f"loc={row['loc_changed']}{acc}{cost_note}",
              flush=True)


if __name__ == "__main__":
    main()
