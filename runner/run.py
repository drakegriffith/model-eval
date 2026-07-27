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

import sandbox_seal
import usage_ledger

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
# Model registry
# --------------------------------------------------------------------------- #
# Every model the runner can invoke, keyed by the canonical CLI id from
# runner/CLI-FACTS.md. Three behaviours vary across the roster and nothing else
# does: which CLI binary drives it (`family`), which effort tiers it will accept
# (`efforts`), and whether a run costs real money (`metered`). Everything
# downstream -- command construction, usage parsing, key injection, spend
# metering -- dispatches on `family`, so adding a model is a row here rather than
# a new branch in four functions.
#
# `efforts` is the DECLARED tier set (source: ~/.codex/models_cache.json for
# codex, `claude --help` for claude). Declared is not the same as real -- whether
# a tier moves spend is what effort_verdict.py decides -- but declared is enough
# to reject a typo before a 4M-token sweep launches.
CLAUDE_TIERS = ["low", "medium", "high", "xhigh", "max"]
CODEX_TIERS = ["low", "medium", "high", "xhigh"]
CODEX_TIERS_6 = ["low", "medium", "high", "xhigh", "max", "ultra"]

MODELS = {
    # claude family -- driven by `claude -p`, subscription auth
    "claude-opus-5":             {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-opus-5[1m]":         {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-opus-4-8":           {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-fable-5":            {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-sonnet-5":           {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-haiku-4-5":          {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-haiku-4-5-20251001": {"family": "claude", "efforts": CLAUDE_TIERS},
    # codex family -- driven by `codex exec`, subscription auth. sol and terra are
    # the only two ids declaring six tiers; the rest stop at xhigh.
    "gpt-5.6-sol":               {"family": "codex", "efforts": CODEX_TIERS_6},
    "gpt-5.6-terra":             {"family": "codex", "efforts": CODEX_TIERS_6},
    "gpt-5.6-luna":              {"family": "codex", "efforts": CLAUDE_TIERS},
    "gpt-5.5":                   {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.4":                   {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.4-mini":              {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.3-codex-spark":       {"family": "codex", "efforts": CODEX_TIERS},
    "codex-auto-review":         {"family": "codex", "efforts": CODEX_TIERS},
    # kimi family -- Claude Code binary pointed at Moonshot's Anthropic-compatible
    # endpoint, injected API key, real money. Separate family from `claude`
    # precisely because those last two facts differ.
    "kimi-k3":                   {"family": "kimi", "efforts": CLAUDE_TIERS,
                                  "metered": True},
}

# Short names used by the existing runs.yaml files and already written into
# results.jsonl. Kept so old configs and old rows stay readable; new configs may
# name either an alias or a canonical id. `hybrid` is a MODE, not a model -- it
# runs Fable as an orchestrator (see HYBRID_INSTRUCTION), and pointing it here
# states that plainly instead of hiding it in a build_cli_cmd branch.
ALIASES = {
    "fable": "claude-fable-5",
    "hybrid": "claude-fable-5",
    "sol": "gpt-5.6-sol",
    "kimi": "kimi-k3",
}


def resolve_model(model):
    """Return (canonical_id, spec) for an alias or canonical CLI id.

    Single resolution point: build_cli_cmd, parse_usage, run_cli and the spend
    meter all come through here, so an alias is expanded once and by one rule.
    Raises ValueError on an unknown name -- a typo must not reach a paid CLI.
    """
    mid = ALIASES.get(model, model)
    spec = MODELS.get(mid)
    if spec is None:
        raise ValueError(
            f"unknown model {model!r}; known ids: {', '.join(sorted(MODELS))}; "
            f"aliases: {', '.join(sorted(ALIASES))}")
    return mid, spec


def model_family(model):
    return resolve_model(model)[1]["family"]


def is_metered(model):
    """True when a run of this model costs real money (Kimi only today)."""
    return bool(resolve_model(model)[1].get("metered"))


def check_effort(model, effort):
    """Reject an effort tier the model does not declare, before anything is spent.

    Fail-closed on purpose: a misspelled tier that the CLI silently ignores would
    produce a sweep of identical runs wearing different labels, which is exactly
    the artefact the effort ladder exists to rule out.
    """
    mid, spec = resolve_model(model)
    if effort and effort not in spec["efforts"]:
        raise ValueError(
            f"model {mid} does not declare effort {effort!r}; "
            f"declared: {', '.join(spec['efforts'])}")


DONE_GATE_SENTENCE = (
    "\n\n---\nYour work is judged solely by running `bash verify.sh` from the "
    "repository root; it must exit 0. Run it yourself and confirm a clean exit "
    "before you finish. Do not edit verify.sh or the tests."
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


def compose_prompt(task_dir, harness, mode):
    parts = []
    for fname in ("PROMPT.md", "TICKET.md", "SPEC.md"):
        p = os.path.join(task_dir, fname)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                parts.append(f"# {fname}\n\n{f.read().strip()}")
    prompt = "\n\n".join(parts)
    prompt += DONE_GATE_SENTENCE
    if mode == "hybrid":
        prompt += HYBRID_INSTRUCTION
    return prompt


def prepare_scratch(task_dir, scratch, harness):
    if os.path.exists(scratch):
        shutil.rmtree(scratch)
    base = os.path.join(task_dir, "base")
    shutil.copytree(base, scratch)
    # copy verify.sh into the working copy root (it runs from inside base copy)
    vsrc = os.path.join(task_dir, "verify.sh")
    if os.path.exists(vsrc):
        vdst = os.path.join(scratch, "verify.sh")
        shutil.copy2(vsrc, vdst)
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
def staged_task_dir(task_dir):
    """Yield a sanitised mirror of task_dir: acceptance/ and nothing else.

    GAUNTLET_TASK_DIR used to point the model under test at the canonical task
    directory, which holds solution.patch -- the reference answer -- alongside
    the hidden acceptance suite. The model runs in a clean copy of base/, so
    that variable was the signposted route to its own answer key, and one `cat`
    away (ticket 16).

    The variable cannot simply be dropped: t3-a and all three t4-* tasks
    resolve acceptance/ through it, because prepare_scratch copies verify.sh
    into the scratch dir and orphans it from its test assets. Removing it would
    take away the model's ability to self-check, which is ticket 17's decision
    to make, not this function's. So the model gets a directory that satisfies
    verify.sh and contains no answer: the suite it is graded on, and none of
    the patch that solves it.

    __pycache__ is excluded -- a stale .pyc of the acceptance suite is the
    source in all but name.
    """
    tmp = tempfile.mkdtemp(prefix="gauntlet-taskdir-")
    try:
        src = os.path.join(task_dir, "acceptance")
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, "acceptance"),
                            ignore=shutil.ignore_patterns("__pycache__"))
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def seal_enabled():
    """False only when a run explicitly opts out via GAUNTLET_NO_SANDBOX=1."""
    return os.environ.get("GAUNTLET_NO_SANDBOX") != "1"


def run_cli(cmd, scratch, timeout_s, task_dir, model=None):
    """Run headlessly, killing the process group on timeout. Returns (out, reason, wall).

    The command runs sealed (ticket 16): reads of this repo are denied except
    the scratch dir it works in, so neither tasks/*/solution.patch nor the
    canonical acceptance suites nor other runs' transcripts under
    runner/results/ are reachable -- by GAUNTLET_TASK_DIR or by walking up from
    cwd, since scratch lives inside the repo by default.

    The seal is fail-closed: no sandbox-exec means no run, rather than a run
    that silently measures an open-book attempt. GAUNTLET_NO_SANDBOX=1 is the
    documented, loudly-warned opt-out, and it is recorded on the results row.
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
        # the canonical script (t3, t4) resolve them through this. It points at
        # a sanitised mirror, never at the real task dir -- see staged_task_dir.
        mirror = stack.enter_context(staged_task_dir(task_dir))
        env["GAUNTLET_TASK_DIR"] = mirror
        if seal_enabled():
            prefix = stack.enter_context(sandbox_seal.sandbox_prefix(
                deny_paths=[ROOT], allow_paths=[scratch, mirror]))
            cmd = prefix + list(cmd)
        else:
            print("WARNING: GAUNTLET_NO_SANDBOX=1 -- model can read its own "
                  "answer key; this row is marked sealed=false", file=sys.stderr)
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=scratch, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, env=env)
        try:
            out, _err = proc.communicate(timeout=timeout_s)
            reason = "ok" if proc.returncode == 0 else "cli_error"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                out, _err = proc.communicate(timeout=30)
            except Exception:
                out = ""
            reason = "timeout"
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


def tamper_report(scratch, task_dir):
    """Sorted "<how>:<path>" strings for every apparatus file the model altered.

    Reported, not punished: the overlay already makes the grade honest, so this
    exists to make the question "did anyone try?" answerable from results.jsonl
    once the scratch trees are gone. Byte comparison, deliberately -- a
    reformatted graded test is still a rewritten graded test, and no reference
    solution in the suite touches one.
    """
    found = []
    canon = canonical_grading_files(task_dir)
    for rel, src in canon.items():
        dst = os.path.join(scratch, rel)
        if not os.path.isfile(dst):
            found.append("deleted:" + rel)
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


def run_verify(scratch, task_dir):
    """The authoritative gate. Runs the TASK's verify.sh, never the model's copy.

    GAUNTLET_TASK_DIR points at the real task dir here, not at the sanitised
    mirror run_cli hands the model (ticket 16): this is the runner's own
    subprocess, after the model is gone, and t3/t4 resolve their hidden
    acceptance suites through it.
    """
    if not os.path.exists(os.path.join(task_dir, "verify.sh")):
        return False
    with grading_tree(scratch, task_dir) as tree:
        env = dict(os.environ)
        env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)
        r = subprocess.run(["bash", "verify.sh"], cwd=tree, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600)
        return r.returncode == 0


# install artifacts written during the run are not model-authored code
LOC_EXCLUDE = [":(exclude)package-lock.json", ":(exclude)**/package-lock.json",
               ":(exclude)yarn.lock", ":(exclude)pnpm-lock.yaml"]


def loc_changed(scratch):
    subprocess.run(["git", "add", "-A"], cwd=scratch,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = subprocess.run(["git", "diff", "--cached", "--shortstat", "--", "."] + LOC_EXCLUDE,
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
                if row.get("exit_reason") == "ok":
                    ids.add(row["run_id"])
            except Exception:
                continue
    return ids


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def execute_run(run, cfg, tasks_dir, scratch_root, results_path):
    # absolute: mock patch / verify run with cwd=scratch, so relative paths break
    task_dir = os.path.abspath(os.path.join(tasks_dir, run["task"]))
    scratch = os.path.abspath(os.path.join(scratch_root, run["run_id"]))
    defaults = cfg.get("defaults", {}) or {}
    tier3 = run["task"].startswith("t3")
    timeout_s = defaults.get("timeout_t3_s", 3600) if tier3 else defaults.get("timeout_t1_t2_s", 1200)

    prepare_scratch(task_dir, scratch, run["harness"])

    mock = os.environ.get("GAUNTLET_MOCK")
    tokens_in = tokens_out = turns = 0
    wall_s = 0.0
    usage_detail = None  # ticket 08: full cache-token breakdown for the ledger row
    # ticket 16: whether the model ran sealed off from its own answer key. None
    # for mock runs, where no model was invoked and the question is vacuous.
    # Recorded per row so "was this result open-book?" is answerable from the
    # corpus instead of reconstructed from commit dates.
    sealed = None
    if mock == "fail":
        exit_reason = "mock_fail"
    elif mock:  # "1" or any truthy -> mock pass
        ok = apply_mock(task_dir, scratch)
        exit_reason = "mock" if ok else "mock_patch_failed"
    else:
        prompt = compose_prompt(task_dir, run["harness"], run["mode"])
        cmd = build_cli_cmd(run["model"], run["effort"], prompt)
        sealed = seal_enabled()
        out, exit_reason, wall_s = run_cli(cmd, scratch, timeout_s, task_dir, run["model"])
        usage_detail = usage_ledger.parse_usage_detailed(model_family(run["model"]), out)
        tokens_in = usage_detail["tokens_in"]
        tokens_out = usage_detail["tokens_out"]
        turns = usage_detail["turns"]
        # A CLI can exit 0 having never emitted a completed turn -- the stream
        # simply stops mid-tool-call. run_cli only sees returncode 0 and calls
        # that "ok", so the run lands in results.jsonl as a successful zero-token
        # run. That row is poison for any spend measurement: it is not a model
        # choosing to spend nothing, it is a run whose generation never finished,
        # and averaging it into a tier cell drags the mean toward zero and
        # inflates within-tier CV. Same class of truncation as a timeout, so it
        # gets the same treatment -- a non-"ok" reason, which is the flag every
        # analysis already gates on (ladder_from_results.py excludes it; the
        # excluded count is printed, never silently dropped).
        if exit_reason == "ok" and turns == 0:
            exit_reason = "no_completion"
        tdir = os.path.join(RUNNER_DIR, "results", "transcripts")
        os.makedirs(tdir, exist_ok=True)
        with open(os.path.join(tdir, run["run_id"] + ".txt"), "w") as tf:
            tf.write(out)

    # ticket 18: read the scratch tree BEFORE grading and record what the model
    # did to the grading apparatus. The overlay in run_verify already makes the
    # grade honest; this is so the corpus can answer "did anyone try?" without
    # the scratch trees, which are not retained forever.
    tamper_files = tamper_report(scratch, task_dir)

    try:
        passed = run_verify(scratch, task_dir)
    except subprocess.TimeoutExpired:
        passed = False
        exit_reason = exit_reason + "+verify_timeout"

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
        "sealed": sealed,
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
        except ValueError as e:
            bad.append(f"  {r['run_id']}: {e}")
    if bad:
        print(f"config rejected -- {len(bad)} invalid run(s):")
        for b in dict.fromkeys(bad):
            print(b)
        sys.exit(2)

    done = existing_ids(args.results)
    pending = [r for r in runs if r["run_id"] not in done]
    skipped = len(runs) - len(pending)
    if args.limit is not None:
        pending = pending[:args.limit]

    print(f"total={len(runs)} pending={len(pending)} already_done={skipped} "
          f"mock={os.environ.get('GAUNTLET_MOCK', '0')}")

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
        print(f"    pass={row['pass']} reason={row['exit_reason']} "
              f"tok_in={row['tokens_in']} tok_out={row['tokens_out']} "
              f"wall={row['wall_s']}s turns={row['turns']} loc={row['loc_changed']}{cost_note}",
              flush=True)


if __name__ == "__main__":
    main()
