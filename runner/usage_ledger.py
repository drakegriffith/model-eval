#!/usr/bin/env python3
"""usage_ledger.py -- the append-only token/dollar ledger (ticket 08).

runner/results/usage.jsonl is the permanent record of what a run actually
consumed: raw tokens are truth, dollars are derived and pricing-mutable,
joinable to results.jsonl by run_id. Separate from results.jsonl (which stays
the resettable per-run trace) for the same reason waku keeps its ledger
append-only and apart from resettable state.

This module owns the ONE correct token-parsing formula, shared by run.py's
live spend meter and this file's offline retrofit pass. See
parse_usage_detailed for the bug it fixes (ticket 08 session 2026-07-27):
run.py's old parse_usage summed only usage.input_tokens for the claude/kimi
branch, which is the LAST turn's fresh, uncached tokens -- it silently
dropped cache_creation_input_tokens / cache_read_input_tokens and undercounted
real consumption by 30x-400x on cached multi-turn sessions.
probe_endpoints.py already summed all three fields independently; this module
is now the single source both call through.

Stdlib only, and no longer imports run.py at all (ticket 30). retrofit() needs
the model registry to resolve a family per archived row; that registry now lives
in the leaf module runner/registry.py, imported normally at the top of this file.
Until ticket 30 it lived inside run.py's 1300-line worker, which run.py imports
this module from -- so the import back had to be local to dodge the cycle. The
cycle is resolved, not dodged: the direction is run -> usage_ledger -> registry.

    python3 runner/usage_ledger.py retrofit
"""
import argparse
import json
import os

import registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")
USAGE_PATH = os.path.join(RUNNER_DIR, "results", "usage.jsonl")
RESULTS_PATH = os.path.join(RUNNER_DIR, "results", "results.jsonl")
TRANSCRIPTS_DIR = os.path.join(RUNNER_DIR, "results", "transcripts")


# --------------------------------------------------------------------------- #
# Usage parsing -- single source of truth
# --------------------------------------------------------------------------- #
def parse_usage_detailed(family, out):
    """Parse token usage from raw CLI stdout. `family` is 'claude'/'kimi'/'codex'
    (already resolved by the caller -- this module never resolves a model id).

    Returns {"tokens_in", "tokens_out", "cache_read_tokens",
    "cache_creation_tokens", "turns"}. tokens_in is always the cache-inclusive
    total tokens processed -- comparable across families:

    - claude/kimi: `claude -p --output-format json`'s one "result" event holds
      cumulative usage for the whole session. Each of the three fields is a sum
      over the session's model requests, so tokens_in = input_tokens +
      cache_creation_input_tokens + cache_read_input_tokens.
    - codex: each `turn.completed` event's usage.input_tokens already folds
      cached_input_tokens in (it is a subset, not additional), so tokens_in is
      just the sum of input_tokens across events -- adding cached_input_tokens
      again would double-count. Every archived run had exactly one
      turn.completed event; summing is here for the (unverified) case codex
      ever emits more than one.

    Both families therefore report the SAME quantity -- the sum over model
    requests of the full context sent at each request. Ticket 20 measured this
    against both CLIs' per-request session logs (204/204 rows) and ruled the
    unit shared; see runner/token_units.py. Two readings recorded during ticket
    08 do not survive that measurement and are corrected here: codex's single
    turn.completed is a CUMULATIVE total, not one session context counted once
    (its `total_token_usage` is a running sum of `last_token_usage`), and
    claude's usage.input_tokens is the sum of fresh input across requests, not
    the freshest turn's alone. Neither correction changes this formula.
    """
    ti = to = turns = cache_read = cache_creation = 0
    if family in ("claude", "kimi"):
        obj = None
        for line in reversed([l for l in out.splitlines() if l.strip()]):
            try:
                cand = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(cand, dict) and cand.get("type") == "result":
                obj = cand
                break
        if obj is None:
            try:
                obj = json.loads(out)
            except json.JSONDecodeError:
                obj = None
        if isinstance(obj, dict):
            u = obj.get("usage", {}) or {}
            fresh = int(u.get("input_tokens", 0) or 0)
            cache_creation = int(u.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(u.get("cache_read_input_tokens", 0) or 0)
            ti = fresh + cache_creation + cache_read
            to = int(u.get("output_tokens", 0) or 0)
            turns = int(obj.get("num_turns", 0) or 0)
    else:  # codex JSONL
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
                cache_read += int(u.get("cached_input_tokens", 0) or 0)
                to += int(u.get("output_tokens", 0) or 0)
                turns += 1
    return {"tokens_in": ti, "tokens_out": to, "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation, "turns": turns}


# --------------------------------------------------------------------------- #
# Pricing -- dated, list-price, and ONLY for ids with a verified metered rate.
#
# Claude and Codex ids run on Drake's flat-rate subscriptions (runner/CLI-FACTS.md)
# with no per-token bill and no published per-token console price for these
# specific ids as of this ledger. runner/tables.py carries ad hoc placeholder
# numbers for a few aliases ("edit to taste") for illustrative $/task charting --
# those are NOT reused here and should not be read as verified prices. Ticket 08
# decision: do not fabricate a number dressed up as a dated constant. A future
# ticket can add a family/id here the day a real console price is confirmed.
# --------------------------------------------------------------------------- #
PRICING = {
    "kimi-k3": {
        "in_fresh": 3.0, "in_cache_read": 0.30, "in_cache_write": 3.0, "out": 15.0,
        "date": "2026-07-26",
        "source": "Moonshot API list price, as recorded in runner/run.py "
                   "KIMI_PRICE_IN/KIMI_PRICE_OUT (commit 7d5ba4d); cache-write rate "
                   "not published separately by Moonshot so it is charged at the "
                   "conservative fresh-input rate, mirroring run.py's existing "
                   "cache-miss-rate practice for the --max-usd cap.",
    },
}

# Scaffold overhead floor -- runner/CLI-FACTS.md, probed 2026-07-25 with a
# one-word prompt at lowest effort in an empty scratch dir. This is what a run
# costs before the task is even read: a harness property, not a model property.
# Recorded per row so it stays visible next to (never folded into) task-work
# tokens -- ticket 08's Kimi ~30k-tax finding generalizes to every model here.
SCAFFOLD_FLOOR_TOKENS = {
    "claude-opus-5": 28496,
    "claude-opus-5[1m]": 28509,
    "claude-opus-4-8": 27506,
    "claude-fable-5": 28822,
    "claude-sonnet-5": 36496,
    "claude-haiku-4-5": 26619,
    "claude-haiku-4-5-20251001": 26623,
    "gpt-5.6-sol": 20860,
    "gpt-5.6-terra": 17847,
    "gpt-5.6-luna": 16656,
    "gpt-5.5": 18136,
    "gpt-5.4": 16751,
    "gpt-5.4-mini": 16399,
    "gpt-5.3-codex-spark": 15399,
    "codex-auto-review": 16216,
    "kimi-k3": 32795,
}
SCAFFOLD_FLOOR_DATE = "2026-07-25"
SCAFFOLD_FLOOR_SOURCE = ("runner/CLI-FACTS.md 'Reachability and scaffold floor' table "
                         "(runner/probe_endpoints.py --phase floor)")


def usd_estimate(model_id, tokens_in, tokens_out, cache_read_tokens=0,
                 cache_creation_tokens=0):
    """List-price dollar estimate, or None when no verified price exists for
    this id. Never fabricated -- see PRICING's comment."""
    p = PRICING.get(model_id)
    if p is None:
        return None
    fresh = max(tokens_in - cache_read_tokens - cache_creation_tokens, 0)
    return (fresh / 1e6 * p["in_fresh"]
            + cache_read_tokens / 1e6 * p["in_cache_read"]
            + cache_creation_tokens / 1e6 * p["in_cache_write"]
            + tokens_out / 1e6 * p["out"])


# --------------------------------------------------------------------------- #
# Row construction
# --------------------------------------------------------------------------- #
def build_usage_row(row, family, usage_detail=None, model_id=None,
                    kind="worker", judged_run_id=None):
    """Build one usage.jsonl row from a results.jsonl-shaped `row`.

    `usage_detail` is parse_usage_detailed's output when tokens were parsed
    fresh from raw CLI output (a live run, or an archived row with a
    transcript to re-parse) -- retrofit_status = "measured". Without it we
    fall back to the row's own tokens_in/tokens_out:
      - codex family: never buggy, so the stored value already IS the true
        cache-inclusive total -- "measured" too.
      - claude/kimi family: the known undercount (see module docstring) means
        a stored value with no transcript to re-derive it from is NOT a true
        total -- "unfixable_floor_only", never silently trusted.
      - zero tokens_in and tokens_out (mock rows): "not_applicable_zero_tokens".

    `model_id` should be the CANONICAL id (the caller's job to resolve, e.g.
    via registry.resolve_model) -- pre-registry archived rows only carry an alias
    ("sol") in `model`, and scaffold/pricing lookups below are keyed by
    canonical id, so a caller that skips this silently loses those lookups.

    `kind` separates worker runs from judge-panel calls (ticket 20 item 3).
    Both shapes carry the SAME keys so a consumer never has to know which it is
    holding: a judge row sets kind="judge" and `judged_run_id` to the worker
    run whose diff it scored, and keeps its own unique `run_id`. Rows written
    before this field existed have no `kind` and are worker rows by definition
    -- read them with row.get("kind", "worker").
    """
    model_id = model_id or row.get("model_id") or row.get("model")
    stored_ti = row.get("tokens_in", 0) or 0
    stored_to = row.get("tokens_out", 0) or 0

    if usage_detail is not None:
        tokens_in = usage_detail["tokens_in"]
        tokens_out = usage_detail["tokens_out"]
        cache_read = usage_detail["cache_read_tokens"]
        cache_creation = usage_detail["cache_creation_tokens"]
        retrofit_status = "measured"
    elif stored_ti == 0 and stored_to == 0:
        tokens_in, tokens_out = stored_ti, stored_to
        cache_read = cache_creation = 0
        retrofit_status = "not_applicable_zero_tokens"
    else:
        tokens_in, tokens_out = stored_ti, stored_to
        cache_read = cache_creation = 0
        retrofit_status = "measured" if family == "codex" else "unfixable_floor_only"

    billing_mode = "metered" if family == "kimi" else "subscription"
    usd = usd_estimate(model_id, tokens_in, tokens_out, cache_read, cache_creation)
    scaffold = SCAFFOLD_FLOOR_TOKENS.get(model_id)
    price = PRICING.get(model_id)

    return {
        "run_id": row["run_id"],
        "ts": row.get("ts"),
        "kind": kind,
        "judged_run_id": judged_run_id,
        "model": row.get("model"),
        "model_id": model_id,
        "family": family,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "scaffold_overhead_tokens": scaffold,
        "scaffold_overhead_source": SCAFFOLD_FLOOR_SOURCE if scaffold is not None else None,
        "billing_mode": billing_mode,
        "usd_estimate": usd,
        "usd_estimate_kind": "list_price_estimate" if usd is not None else None,
        "pricing_date": price["date"] if price else None,
        "retrofit_status": retrofit_status,
    }


def append_usage_row(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(row) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# Retrofit -- offline pass over an existing results.jsonl + transcripts/
# --------------------------------------------------------------------------- #
def retrofit(results_path, transcripts_dir, usage_path):
    """Append one usage.jsonl row per results.jsonl row not already ledgered.
    Idempotent: run_ids already present in usage_path are skipped, so this is
    safe to re-run after new sweeps land without duplicating old rows.
    """
    already = set()
    if os.path.exists(usage_path):
        with open(usage_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    already.add(json.loads(line)["run_id"])
                except Exception:
                    continue

    written = skipped_existing = 0
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("run_id")
            if not rid:
                continue
            if rid in already:
                skipped_existing += 1
                continue
            try:
                model_id, spec = registry.resolve_model(row["model"])
                family = spec["family"]
            except ValueError:
                model_id, family = row.get("model"), "unknown"

            usage_detail = None
            tpath = os.path.join(transcripts_dir, rid + ".txt")
            if os.path.exists(tpath) and family in ("claude", "kimi"):
                with open(tpath, encoding="utf-8") as tf:
                    usage_detail = parse_usage_detailed(family, tf.read())

            urow = build_usage_row(row, family, usage_detail, model_id=model_id)
            append_usage_row(usage_path, urow)
            already.add(rid)
            written += 1

    return {"written": written, "skipped_existing": skipped_existing}


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet usage ledger")
    ap.add_argument("action", choices=["retrofit"])
    ap.add_argument("--results", default=RESULTS_PATH)
    ap.add_argument("--transcripts", default=TRANSCRIPTS_DIR)
    ap.add_argument("--usage", default=USAGE_PATH)
    args = ap.parse_args()

    summary = retrofit(args.results, args.transcripts, args.usage)
    print(f"wrote {summary['written']} row(s), "
          f"skipped {summary['skipped_existing']} already-ledgered -> {args.usage}")


if __name__ == "__main__":
    main()
