#!/usr/bin/env python3
"""run.py — headless benchmark runner for the model-gauntlet.

Copies each task's base/ into a throwaway git-inited scratch dir, optionally drops
the benchmark harness (CLAUDE.md/AGENTS.md) in, composes the prompt, invokes the
chosen CLI headlessly (per runner/CLI-FACTS.md) with a hard wall-clock timeout,
then runs verify.sh OURSELVES as the authoritative pass/fail gate. Appends one
JSONL row per run to runner/results/results.jsonl.

Stdlib only. Both CLIs run on subscriptions — we never set ANTHROPIC_API_KEY /
OPENAI_API_KEY.

MOCK mode (no tokens spent):
  GAUNTLET_MOCK=1     -> apply tasks/<id>/solution.patch instead of calling a CLI
  GAUNTLET_MOCK=fail  -> do nothing (leaves base unchanged, verify.sh should fail)
"""
import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

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


def run_cli(cmd, scratch, timeout_s, task_dir, model=None):
    """Run headlessly, killing the process group on timeout. Returns (out, reason, wall)."""
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
    # verify.sh is copied into scratch; tasks whose test assets live beside the
    # canonical script (t3) resolve them through this instead of dirname $0
    env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)
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

    Known limitation, deliberately left alone: this does not add
    cache_read_input_tokens for the claude families, so claude/kimi tokens_in is
    a FLOOR while codex's cumulative figure is conservative-correct. Cross-family
    per-run token comparisons are not apples-to-apples until that is fixed, which
    is the token-ledger ticket's call, not this one's.
    """
    ti = to = turns = 0
    if model_family(model) in ("claude", "kimi"):  # `claude -p --output-format json`
        obj = None
        for line in reversed([l for l in out.splitlines() if l.strip()]):
            try:
                cand = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(cand, dict) and cand.get("type") == "result":
                obj = cand
                break
        if obj is None:  # maybe whole blob is one JSON object
            try:
                obj = json.loads(out)
            except json.JSONDecodeError:
                obj = None
        if isinstance(obj, dict):
            u = obj.get("usage", {}) or {}
            ti = int(u.get("input_tokens", 0) or 0)
            to = int(u.get("output_tokens", 0) or 0)
            turns = int(obj.get("num_turns", 0) or 0)
    else:  # sol / codex JSONL
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "turn.completed":
                u = ev.get("usage", {}) or {}
                ti += int(u.get("input_tokens", 0) or 0)
                to += int(u.get("output_tokens", 0) or 0)
                turns += 1
    return ti, to, turns


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


def run_verify(scratch, task_dir):
    vs = os.path.join(scratch, "verify.sh")
    if not os.path.exists(vs):
        return False
    env = dict(os.environ)
    env["GAUNTLET_TASK_DIR"] = os.path.abspath(task_dir)
    r = subprocess.run(["bash", "verify.sh"], cwd=scratch, env=env,
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
    ids = set()
    if not os.path.exists(results_path):
        return ids
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["run_id"])
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
    if mock == "fail":
        exit_reason = "mock_fail"
    elif mock:  # "1" or any truthy -> mock pass
        ok = apply_mock(task_dir, scratch)
        exit_reason = "mock" if ok else "mock_patch_failed"
    else:
        prompt = compose_prompt(task_dir, run["harness"], run["mode"])
        cmd = build_cli_cmd(run["model"], run["effort"], prompt)
        out, exit_reason, wall_s = run_cli(cmd, scratch, timeout_s, task_dir, run["model"])
        tokens_in, tokens_out, turns = parse_usage(run["model"], out)
        tdir = os.path.join(RUNNER_DIR, "results", "transcripts")
        os.makedirs(tdir, exist_ok=True)
        with open(os.path.join(tdir, run["run_id"] + ".txt"), "w") as tf:
            tf.write(out)

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
    }
    append_row(results_path, row)
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
