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
from datetime import datetime, timezone

import broker
import registry
import sandbox_seal
import usage_ledger

# Import direction is one-way and now acyclic (ticket 30): run -> usage_ledger ->
# registry, with registry a leaf importing nothing. Until ticket 30 the model
# registry lived in this file, so usage_ledger had to import run back locally to
# resolve a model -- a leaf module paying for a god module to answer "what family
# is this". That local import is gone. Do not add an `import run` anywhere below
# this line's dependents.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/code/model-gauntlet
RUNNER_DIR = os.path.join(ROOT, "runner")

# --- Kimi K3 (Moonshot) ---------------------------------------------------- #
# K3 has no native agent CLI; we drive it through Codex's OpenAI-compatible
# provider path. Its API key lives in a gitignored secrets file in the vault and
# is read here at runtime only — never hard-coded, echoed, or committed.
KIMI_KEY_FILE = os.path.expanduser("~/brain-actual-intelligence/.secrets/kimi.env")
# Codex 0.144 only speaks the Responses API, which Moonshot doesn't serve; instead
# we drive K3 through Claude Code against Moonshot's Anthropic-compatible endpoint.
MOONSHOT_ANTHROPIC_URL = "https://api.moonshot.ai/anthropic"
# List-price $/1M tokens. Cache-miss input is $3; cache-hit is $0.30. We charge
# the cap at the conservative cache-miss rate.
KIMI_PRICE_IN = 3.0
KIMI_PRICE_OUT = 15.0


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
                        run_id = f"{name}--{model}--{effort}--{harness_tag}--{task}--r{rep}"
                        sweep_runs.append({
                            "run_id": run_id,
                            "sweep": name,
                            "model": model,
                            "effort": effort,
                            "harness": bool(harness),
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


def build_cli_cmd(model, effort, prompt):
    """The exact headless invocation for a model, per runner/CLI-FACTS.md.

    Dispatches on registry family, so a new model id needs no change here. Effort
    is validated against the model's declared tiers first.

    kimi rides the `claude` binary (base_url + key are injected by run_cli) but is
    a distinct family: it is metered, and it DOES take --effort. The previous
    version passed no --effort for Kimi while labelling every Kimi run "max",
    which is CLI-FACTS correction #3 -- the label was fiction and no Kimi ladder
    was measurable.
    """
    mid, spec = resolve_model(model)
    check_effort(mid, effort)
    family = spec["family"]

    if family in ("claude", "kimi"):
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--model", mid, "--dangerously-skip-permissions"]
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


def broker_enabled():
    """False only when a run explicitly opts out via GAUNTLET_NO_BROKER=1.

    Same shape as seal_enabled, and for the same reason: the opt-out has to
    exist (it is how the control arm reproduces the pre-broker condition) and
    it has to be loud and recorded, because a `brokered: false` row is protocol
    v1 and the pre-registration forbids pooling the two strata.
    """
    return os.environ.get("GAUNTLET_NO_BROKER") != "1"


def run_cli(cmd, scratch, timeout_s, task_dir, model=None, bk=None):
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

    `bk` is a live broker.Broker (ticket 17). Its socket is the model's only
    route to acceptance feedback; the seal is widened by exactly that one
    directory, and the mirror is emptied to match. Two exit reasons come out of
    it and both are terminal for the row: `cap_exhausted` (the model asked for
    request K+1 and the run was ended, scored a failure per the estimand) and
    `broker_failed` (the counter faulted, so the run is uncounted and unusable
    under the pre-registration).
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # subscription auth only
    env.pop("OPENAI_API_KEY", None)
    if model is not None and model_family(model) == "kimi":
        key = load_kimi_key()
        if not key:
            return "", "kimi_key_missing", 0.0
        # Point Claude Code at Moonshot's Anthropic-compatible endpoint for this run.
        env["ANTHROPIC_BASE_URL"] = MOONSHOT_ANTHROPIC_URL
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key   # some Claude Code versions read this

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

        if seal_enabled():
            # write_allow_paths names the run's own apparatus and nothing else;
            # sandbox_seal appends the CLI runtime tier itself. Everything
            # outside it -- results.jsonl, the canonical tasks/ tree, sibling
            # scratch trees -- is read-only to the model under test.
            prefix = stack.enter_context(sandbox_seal.sandbox_prefix(
                deny_paths=[ROOT], allow_paths=allow,
                write_allow_paths=allow + [run_tmp]))
            cmd = prefix + list(cmd)
        else:
            print("WARNING: GAUNTLET_NO_SANDBOX=1 -- model can read its own "
                  "answer key AND write to results.jsonl, the canonical tasks "
                  "and other runs' scratch trees; this row is marked "
                  "sealed=false write_contained=false", file=sys.stderr)
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
        env = dict(os.environ)
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
    ended and scored a failure (pre-registration section 7). Re-running it would
    be retry-until-pass, which is exactly the optional stopping section 8 rules
    out. `broker_failed` is a fault and does stay pending, like any CLI error.
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

    Resolution order for a tier-N task: `timeout_t{N}_s`, then the legacy key
    covering that tier (t1/t2 -> timeout_t1_t2_s, t3 -> timeout_t3_s), then an
    explicit `timeout_default_s`. Nothing after that -- a tier with no cap
    declared for it is a config bug and is raised as one.

    Fail-closed for the same reason check_effort() is (ticket 22 defect 2). The
    old expression keyed on the literal "t3" and sent everything else to the
    t1/t2 branch, so t4 and t5 tasks silently drew a cap sized for a 20-minute
    task. Cap-terminated runs score as FAILURES under the pre-registration's
    estimand: a mis-sized cap does not show up as a timeout in the analysis, it
    shows up as task difficulty. Adding a tier must therefore cost a config edit
    it cannot forget to make, not inherit the short cap by falling off the end
    of a boolean.
    """
    defaults = defaults or {}
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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def execute_run(run, cfg, tasks_dir, scratch_root, results_path):
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
    # ticket 26: whether the model ran with WRITES contained -- i.e. unable to
    # append to or truncate results.jsonl, poison the canonical tasks/ tree, or
    # write into a sibling run's scratch. Separate from `sealed` because it is a
    # separate property (integrity, not confidentiality) and because it has a
    # separate history: every row written before 2026-07-29 was produced with
    # reads sealed and writes wide open, and will lack this field entirely.
    # Absent-or-false means "not contained" for any corpus consumer; the
    # admissibility argument for those rows is in ticket 26.
    write_contained = None
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
            cmd = build_cli_cmd(run["model"], run["effort"], prompt)
            sealed = seal_enabled()
            # AND'd against the seal module's own capability flag rather than
            # hardcoded True: if write containment is ever removed from
            # sandbox_seal, the corpus says so instead of carrying rows that
            # claim a guarantee nothing is enforcing.
            write_contained = sealed and sandbox_seal.WRITE_CONTAINMENT
            out, exit_reason, wall_s = run_cli(cmd, scratch, timeout_s, task_dir,
                                               run["model"], bk=bk)
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

    # ticket 34. Pre-registration section 7 -- "Runs terminated by the cap are
    # scored as failures" -- generalized from the cap to every run that did not
    # finish. exit_reason == "ok" is this instrument's completeness gate
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
    # carries it and pre-registration section 7 names it. It is now exactly
    # pass_raw narrowed to the cap case; the general gate above is what forces
    # `pass` False, here as everywhere else.
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
        "task": run["task"], "rep": run["rep"], "pass": passed,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "wall_s": wall_s,
        "turns": turns, "loc_changed": loc, "exit_reason": exit_reason,
        "sealed": sealed, "write_contained": write_contained,
        # ticket 17. acceptance_requests is the design parameter K governs, and
        # it is counted by the broker rather than inferred from CLI telemetry --
        # which is what makes it comparable across families, unlike `turns`
        # (structurally 1 on all 148 Codex rows, and barred from citation).
        "brokered": brokered, "k_cap": k_cap,
        "acceptance_requests": acceptance_requests,
        "cap_exhausted": exit_reason == "cap_exhausted",
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
    usage_ledger.append_usage_row(usage_ledger.USAGE_PATH, urow)

    return row


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet runner")
    ap.add_argument("--config", default=os.path.join(RUNNER_DIR, "runs.yaml"))
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--scratch", default=os.path.join(ROOT, ".scratch"))
    ap.add_argument("--results", default=os.path.join(RUNNER_DIR, "results", "results.jsonl"))
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

    # CLI flags are sugar over the GAUNTLET_MOCK env var that execute_run reads.
    if args.mock_fail:
        os.environ["GAUNTLET_MOCK"] = "fail"
    elif args.mock:
        os.environ["GAUNTLET_MOCK"] = "1"

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = parse_yaml(f.read())

    runs = build_runs(cfg)
    if args.only:
        runs = [r for r in runs if args.only in r["run_id"]]

    # Validate the whole matrix before the first CLI call. An unknown model id or
    # an undeclared effort tier is a config bug, and finding it 40 runs into a
    # sweep means those 40 runs were spent on a matrix that was never going to
    # finish. Fail closed, up front, at zero cost.
    bad = []
    for r in runs:
        try:
            resolve_model(r["model"])
            check_effort(r["model"], r["effort"])
            # Same posture for the wall-clock cap: an undeclared tier is a config
            # bug, and the run it would spoil is the one that already cost money.
            resolve_timeout_s(r["task"], cfg.get("defaults", {}) or {})
        except ValueError as e:
            bad.append(f"  {r['run_id']}: {e}")
    if bad:
        print(f"config rejected -- {len(bad)} invalid run(s):")
        for b in dict.fromkeys(bad):
            print(b)
        sys.exit(2)

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

    kimi_spent = 0.0
    for i, r in enumerate(pending, 1):
        if is_metered(r["model"]) and args.max_usd is not None and kimi_spent >= args.max_usd:
            print(f"    [cap] kimi spend ${kimi_spent:.2f} >= --max-usd "
                  f"${args.max_usd:.2f}; skipping {r['run_id']}", flush=True)
            continue
        print(f"[{i}/{len(pending)}] {r['run_id']} ...", flush=True)
        row = execute_run(r, cfg, args.tasks_dir, args.scratch, args.results)
        cost_note = ""
        if is_metered(r["model"]):
            kimi_spent += kimi_dollars(row["tokens_in"], row["tokens_out"])
            cap = f"/{args.max_usd:.2f}" if args.max_usd is not None else ""
            cost_note = f" kimi_spend=${kimi_spent:.3f}{cap}"
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
