#!/usr/bin/env python3
"""probe_endpoints.py — verify which models are drivable headlessly, and whether
their reasoning-effort knob actually does anything.

Answers two questions per candidate model, because the frontier study needs both:

  1. REACHABILITY — is there a working non-interactive invocation? Exact command,
     resolved model id, auth path, scaffold token floor.
  2. EFFORT — which effort tiers does it *really* expose? Verified by observed
     token-spend deltas between tiers, never by the flag merely being accepted.

Question 2 is the load-bearing one. A model whose effort knob is a silent no-op is
worse for this study than a model with no knob at all: it produces two frontier
points that differ only in their label, and the frontier claim quietly inherits a
duplicate. So a tier is only credited when the spend moves.

Two prompts, because one cannot answer both questions. FLOOR_PROMPT deliberately
induces no reasoning, which is what makes it a clean measure of scaffold overhead --
and exactly what makes it useless for question 2, since every tier would return the
same near-zero thinking spend and a working knob would be misread as dead. Question 2
therefore uses LADDER_PROMPT, whose cost scales with how hard the model thinks.

Stdlib only, matching run.py. Both CLIs run on Drake's subscriptions: this script
never sets ANTHROPIC_API_KEY / OPENAI_API_KEY for them. Kimi is the exception and is
metered for real, so it -- and only it -- gets an injected key and a hard spend cap.

Results append to runner/results/endpoint-probe-<date>.jsonl, fsync'd per row and
resumable: re-running skips probe ids already recorded, so an interrupted sweep or a
newly-shipped CLI costs only the missing cells.
"""

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "runner", "results")
KIMI_KEY_FILE = os.path.expanduser(
    os.environ.get("GAUNTLET_KIMI_KEY_FILE", "~/.gauntlet/secrets/kimi.env")
)
MOONSHOT_ANTHROPIC_URL = "https://api.moonshot.ai/anthropic"
CODEX_MODEL_CACHE = os.path.expanduser("~/.codex/models_cache.json")

# List-price $/1M tokens, charged at the conservative cache-miss rate (mirrors run.py).
KIMI_PRICE_IN = 3.0
KIMI_PRICE_OUT = 15.0

FLOOR_PROMPT = "reply with the single word ok"

# Deterministic, tool-free, and costly in proportion to how hard the model thinks --
# the three properties the ladder needs. Answers: 2x13 -> Fib(14) = 377; 3x8 -> 153.
# Correctness is a free side-signal here, NOT the study's metric (verify.sh owns that).
LADDER_PROMPT = (
    "Without using any tools, and reasoning it out yourself: compute the number of "
    "distinct ways to tile a 2x13 rectangle with 1x2 dominoes, then the number of "
    "distinct ways to tile a 3x8 rectangle with 1x2 dominoes. "
    "Reply with only the two numbers separated by a comma."
)
LADDER_EXPECTED = ("377", "153")

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Every id worth asking about. Invalid ids 404 with usage.input_tokens == 0 before any
# inference runs, so guessing wide here is free -- the sweep prices only what exists.
CLAUDE_CANDIDATES = [
    "claude-opus-5",
    "claude-opus-5[1m]",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
]

# Driven through Claude Code against Moonshot's Anthropic-compatible endpoint: Codex
# speaks only the Responses API, which Moonshot does not serve. This scaffold is also
# the source of Kimi's ~30k-token overhead tax and of the observed 200k context cap.
KIMI_CANDIDATES = ["kimi-k3", "kimi-k2.7"]

# studio/local-family: same Claude Code scaffold, pointed at an LM Studio server on
# loopback (MODEL_EVAL_LOCAL_BASE_URL, default http://localhost:1234) instead of a
# hosted endpoint. Unmetered -- there is no --max-usd concern for this family, unlike
# Kimi -- but reachability and the effort ladder are exactly as unverified, which is
# what this script exists to answer. Requires an LM Studio server actually running and
# these two models actually loaded; with neither, every cell here comes back
# unreachable rather than silently skipped, which is the correct floor-phase signal.
LOCAL_BASE_URL = os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:1234")
LOCAL_PLACEHOLDER_TOKEN = "sk-local-lmstudio-unused"
LOCAL_CANDIDATES = ["glm-4.7-local", "qwen3-coder-next-local"]


def load_kimi_key():
    """Return MOONSHOT_API_KEY from the gitignored secrets file, or None.

    Never logged or recorded; injected into a subprocess env only.
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


def codex_roster():
    """Declared Codex models and effort tiers, read from the CLI's own cache.

    Declared, not verified -- which is the whole reason the ladder exists. CLI-FACTS
    asserted low/medium/high as recently as 2026-07-10; the cache now lists six tiers
    including 'ultra'. Trusting either source over observed spend is the mistake this
    script is written to prevent.
    """
    try:
        with open(CODEX_MODEL_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    out = []
    for m in cache.get("models", []):
        efforts = [e["effort"] for e in m.get("supported_reasoning_levels", [])]
        out.append({
            "id": m["slug"],
            "efforts": efforts,
            "ctx_declared": m.get("context_window"),
            "visibility": m.get("visibility"),
            "supported_in_api": m.get("supported_in_api"),
        })
    return out


# ---------------------------------------------------------------- invocation

def build_cmd(family, model_id, effort, prompt, tool_free):
    """The exact non-interactive invocation under test.

    Whatever this returns is recorded verbatim in the JSONL, so the roster ships the
    command that was actually proven rather than a reconstruction of it.
    """
    if family in ("claude", "kimi", "local"):
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--model", model_id, "--dangerously-skip-permissions"]
        if effort:
            cmd += ["--effort", effort]
        if tool_free:
            # The ladder measures thinking, so deny the escape hatch: a model that
            # shells out to compute the answer spends near-identical tokens at every
            # tier and forges a flat (i.e. dead-looking) ladder.
            cmd += ["--disallowed-tools", "Bash", "Edit", "Write", "Read",
                    "Glob", "Grep", "WebSearch", "WebFetch", "Task"]
        return cmd
    if family == "codex":
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check"]
        cmd += ["--sandbox", "read-only"] if tool_free else \
               ["--dangerously-bypass-approvals-and-sandbox"]
        cmd += ["-m", model_id]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        cmd += [prompt]
        return cmd
    raise ValueError(f"unknown family {family}")


def parse_claude(out):
    """Pull usage out of `claude -p --output-format json`.

    Also surfaces the 404 that an invalid model id produces, which is the signal the
    free id-existence sweep is built on.
    """
    try:
        obj = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    usage = obj.get("usage", {}) or {}
    tin = (usage.get("input_tokens", 0)
           + usage.get("cache_creation_input_tokens", 0)
           + usage.get("cache_read_input_tokens", 0))
    return {
        "tokens_in": tin,
        "tokens_out": usage.get("output_tokens", 0),
        "turns": obj.get("num_turns"),
        "text": (obj.get("result") or "")[:400],
        "is_error": bool(obj.get("is_error")),
        "api_error_status": obj.get("api_error_status"),
        "cost_usd_reported": obj.get("total_cost_usd"),
    }


def parse_codex(out):
    """Sum usage across `turn.completed` events in the codex JSONL stream."""
    tin = tout = turns = 0
    text = ""
    err = None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn.completed" or "usage" in ev:
            u = ev.get("usage") or {}
            tin += u.get("input_tokens", 0)
            tout += u.get("output_tokens", 0)
            turns += 1
        item = ev.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            text = item["text"]
        if ev.get("type") == "error" or ev.get("error"):
            err = str(ev.get("error") or ev)[:300]
    if turns == 0 and not text and err is None:
        return None
    return {
        "tokens_in": tin, "tokens_out": tout, "turns": turns,
        "text": text[:400], "is_error": err is not None,
        "api_error_status": None, "cost_usd_reported": None,
        "error_detail": err,
    }


def probe(family, model_id, effort, phase, timeout_s, scratch):
    """Run one probe cell and return a fully self-describing result row."""
    prompt = FLOOR_PROMPT if phase == "floor" else LADDER_PROMPT
    tool_free = phase == "ladder"
    cmd = build_cmd(family, model_id, effort, prompt, tool_free)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)   # subscription auth for claude/codex
    env.pop("OPENAI_API_KEY", None)
    if family == "kimi":
        key = load_kimi_key()
        if not key:
            return {"family": family, "model_id": model_id, "effort": effort,
                    "phase": phase, "reachable": False,
                    "failure": "kimi_key_missing"}
        env["ANTHROPIC_BASE_URL"] = MOONSHOT_ANTHROPIC_URL
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key
    elif family == "local":
        # Unmetered, so unlike kimi there is no key file and no missing-key
        # failure mode -- the placeholder only satisfies the claude binary's own
        # auth precondition; LM Studio never checks it.
        env["ANTHROPIC_BASE_URL"] = LOCAL_BASE_URL
        env["ANTHROPIC_API_KEY"] = LOCAL_PLACEHOLDER_TOKEN
        env["ANTHROPIC_AUTH_TOKEN"] = LOCAL_PLACEHOLDER_TOKEN

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=scratch, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout_s)
        raw = proc.stdout.decode("utf-8", "replace")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        raw, exit_code = "", "timeout"
    wall_s = round(time.time() - t0, 1)

    parsed = parse_codex(raw) if family == "codex" else parse_claude(raw)

    row = {
        "family": family,
        "model_id": model_id,
        "effort": effort,
        "phase": phase,
        # Recorded verbatim so the roster ships a proven command, not a retyped one.
        # The Kimi key lives only in env, never in argv, so this is safe to publish.
        "cmd": cmd,
        "wall_s": wall_s,
        "exit_code": exit_code,
        "auth": ("moonshot_api_key:~/.secrets/kimi.env" if family == "kimi"
                 else "local_placeholder_token:no_account" if family == "local"
                 else "subscription_oauth"),
    }

    if parsed is None:
        row.update({"reachable": False, "failure": "unparseable_output",
                    "raw_head": raw[:300]})
        return row

    # A 404 on an unknown id burns zero tokens -- that is what makes sweeping a wide
    # candidate list free, and it is also how a hallucinated model id gets ruled out
    # of scope rather than quietly substituted with a neighbour.
    if parsed.get("api_error_status") == 404 or (
            parsed["is_error"] and parsed["tokens_in"] == 0):
        row.update({"reachable": False, "failure": "model_id_not_available",
                    "tokens_in": parsed["tokens_in"], "tokens_out": parsed["tokens_out"],
                    "detail": parsed.get("text", "")[:200]})
        return row

    if parsed["is_error"]:
        row.update({"reachable": False, "failure": "api_error",
                    "tokens_in": parsed["tokens_in"], "tokens_out": parsed["tokens_out"],
                    "detail": (parsed.get("error_detail") or parsed.get("text", ""))[:200]})
        return row

    row.update({
        "reachable": True,
        "tokens_in": parsed["tokens_in"],
        "tokens_out": parsed["tokens_out"],
        "turns": parsed["turns"],
        "text": parsed["text"],
        "cost_usd_reported": parsed.get("cost_usd_reported"),
        "cost_usd_metered": (kimi_dollars(parsed["tokens_in"], parsed["tokens_out"])
                             if family == "kimi" else 0.0),
    })
    if phase == "ladder":
        row["answer_correct"] = all(x in (parsed["text"] or "") for x in LADDER_EXPECTED)
    return row


# ---------------------------------------------------------------- driver

def load_done(path):
    """Probe ids already recorded, so a resumed sweep re-spends nothing."""
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(f"{r.get('family')}|{r.get('model_id')}|{r.get('effort')}|{r.get('phase')}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["floor", "ladder"], required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-usd", type=float, default=5.0,
                    help="hard cap on METERED (Kimi) spend; subscription calls are free")
    # local is in the default set (unlike a metered family, an unreachable LM
    # Studio server just fails its cells closed with model_id_not_available /
    # a connection error -- there is no spend cap to protect, so nothing is
    # gained by making it opt-in the way Kimi's --max-usd gate would argue for).
    ap.add_argument("--families", default="claude,codex,kimi,local")
    ap.add_argument("--models", default=None, help="comma-separated id filter")
    ap.add_argument("--efforts", default=None, help="comma-separated effort filter")
    args = ap.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = args.out or os.path.join(
        RESULTS_DIR, f"endpoint-probe-{time.strftime('%Y-%m-%d')}.jsonl")
    done = load_done(out_path)
    families = set(args.families.split(","))
    model_filter = set(args.models.split(",")) if args.models else None
    effort_filter = set(args.efforts.split(",")) if args.efforts else None

    cells = []
    if "claude" in families:
        for mid in CLAUDE_CANDIDATES:
            efforts = CLAUDE_EFFORTS if args.phase == "ladder" else ["low"]
            for e in efforts:
                cells.append(("claude", mid, e))
    if "codex" in families:
        for m in codex_roster():
            efforts = m["efforts"] if args.phase == "ladder" else [m["efforts"][0] if m["efforts"] else None]
            for e in efforts:
                cells.append(("codex", m["id"], e))
    if "kimi" in families:
        for mid in KIMI_CANDIDATES:
            # Whether these tiers do anything through the Claude Code path is exactly
            # what is unknown. If the ladder comes back flat, Kimi is a single-point
            # entry on the frontier and that asymmetry gets disclosed, not smoothed.
            efforts = CLAUDE_EFFORTS if args.phase == "ladder" else ["low"]
            for e in efforts:
                cells.append(("kimi", mid, e))
    if "local" in families:
        for mid in LOCAL_CANDIDATES:
            # Same open question as Kimi's tiers, minus the metering: whether
            # --effort moves anything through LM Studio's Anthropic-compatible
            # endpoint is unverified (registry.py: efforts_verified=False).
            efforts = CLAUDE_EFFORTS if args.phase == "ladder" else ["low"]
            for e in efforts:
                cells.append(("local", mid, e))

    if model_filter:
        cells = [c for c in cells if c[1] in model_filter]
    if effort_filter:
        cells = [c for c in cells if c[2] in effort_filter]
    cells = [c for c in cells
             if f"{c[0]}|{c[1]}|{c[2]}|{args.phase}" not in done]

    scratch = os.path.join("/tmp", f"probe-scratch-{os.getpid()}")
    os.makedirs(scratch, exist_ok=True)

    print(f"probe: phase={args.phase} cells={len(cells)} "
          f"(skipped {len(done)} already recorded) -> {out_path}", file=sys.stderr)

    spent = [0.0]
    fh = open(out_path, "a", encoding="utf-8")

    def emit(row):
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        spent[0] += row.get("cost_usd_metered", 0.0) or 0.0
        flag = "OK " if row.get("reachable") else "XX "
        print(f"{flag}{row['family']}/{row['model_id']}/{row.get('effort')} "
              f"in={row.get('tokens_in','-')} out={row.get('tokens_out','-')} "
              f"{row.get('wall_s','-')}s {row.get('failure','')}", file=sys.stderr)

    # Metered cells run last and serially-ish, so the cap is enforced against a known
    # running total rather than against whatever a pool of threads happens to finish.
    free_cells = [c for c in cells if c[0] != "kimi"]
    metered_cells = [c for c in cells if c[0] == "kimi"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, f, m, e, args.phase, args.timeout, scratch): (f, m, e)
                for f, m, e in free_cells}
        for fut in concurrent.futures.as_completed(futs):
            emit(fut.result())

    for f, m, e in metered_cells:
        if spent[0] >= args.max_usd:
            print(f"HALT: metered spend ${spent[0]:.2f} hit cap ${args.max_usd:.2f}; "
                  f"remaining Kimi cells not run", file=sys.stderr)
            break
        emit(probe(f, m, e, args.phase, args.timeout, scratch))

    fh.close()
    print(f"\nmetered spend this run: ${spent[0]:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
