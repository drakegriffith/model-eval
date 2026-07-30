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

Core module since ticket 37, which is why no path below is derived from this
file's own location on disk: a core module is consumed from another tree, and a
module that computes its ROOT from where its own source happens to sit hands
every such consumer THIS repo's directory layout (14b defect F3). Paths come
from the caller instead. The CLI still works unchanged from a repo checkout --

    python3 runner/usage_ledger.py retrofit

-- because main() resolves the repo root from --repo-root, else
$MODEL_GAUNTLET_ROOT, else the working directory, and fails loudly naming the
flag when no corpus is there.
"""
import argparse
import json
import os
from typing import NamedTuple

import registry

CORE_MODULE = True


class LedgerPaths(NamedTuple):
    """The three files the ledger reads and writes, resolved for one tree."""
    results: str
    transcripts: str
    usage: str


def paths_for_repo(repo_root):
    """model-gauntlet's own ledger layout, rooted at a path the CALLER supplies.

    This is the one place the layout `<root>/runner/results/{results,usage}.jsonl`
    plus `transcripts/` is written down, and it is a convenience for callers who
    happen to share that layout -- NOTHING IN THIS MODULE CALLS IT IMPLICITLY.
    Every function here takes its paths as arguments; a product with a different
    layout passes its own three paths and never touches this function. The old
    module-level constants (ROOT/RUNNER_DIR/USAGE_PATH/RESULTS_PATH/
    TRANSCRIPTS_DIR) are deleted rather than moved behind a default, so a caller
    cannot silently inherit a tree it did not name.
    """
    results_dir = os.path.join(repo_root, "runner", "results")
    return LedgerPaths(results=os.path.join(results_dir, "results.jsonl"),
                       transcripts=os.path.join(results_dir, "transcripts"),
                       usage=os.path.join(results_dir, "usage.jsonl"))


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
# Token-input provenance (ticket 31)
#
# The formula above is correct now and was not always. Rows written before
# f11be7e carry a tokens_in from the buggy claude/kimi branch, and nothing on
# those rows says so -- which is the whole defect: a reader averaging tokens_in
# across models cannot distinguish a real total from a 30x-400x undercount.
#
# USAGE_PARSER_VERSION is the version of the formula in parse_usage_detailed.
# Bump it whenever a change alters the VALUE that formula returns for the same
# input bytes (a refactor that cannot move a number is not a new version).
# Rows record the version that produced them, so no consumer ever has to date a
# row to know what it is holding.
# --------------------------------------------------------------------------- #
PARSER_VERSION_PRE_CACHE_FIX = 1
USAGE_PARSER_VERSION = 2
TOKENS_IN_FIX_COMMIT = "f11be7e"
TOKENS_IN_FIX_TS = "2026-07-27T15:38:36Z"

# Branches PROVEN unaffected by the v1 bug, so their v1 numbers are true totals.
# An allowlist by deliberate choice: a family absent here is quarantined under
# the old parser because nobody checked it, not because someone forgot to list
# it as broken. The inverse (a denylist of known-buggy families) reads the same
# until the next family is added, and then it silently trusts an unchecked
# number.
UNAFFECTED_PARSE_BRANCHES = ("codex",)

TOKENS_IN_STATUSES = ("measured", "recovered_in_ledger", "quarantined")


def tokens_in_status(family, parser_version, ledger_status=None):
    """Disposition of one row's `tokens_in`. THE rule -- stated once here, read
    by run.py's write path and by stamp_provenance; never restated at a reader.

    - "measured": the number on the row is the true cache-inclusive total.
    - "recovered_in_ledger": the row's number is wrong, but the run's transcript
      survived and usage.jsonl holds the re-parsed truth (join by run_id). 56 of
      the 120 buggy-branch rows; verified 56/56 against an independent re-parse
      on 2026-07-30.
    - "quarantined": no copy of the true value exists anywhere. fable's 64 rows.

    `ledger_status` is the joined usage.jsonl `retrofit_status`, or None when no
    ledger row exists. Only "measured" there means recovered --
    "unfixable_floor_only" is the ledger saying it could not recover it either.
    """
    if parser_version >= USAGE_PARSER_VERSION:
        return "measured"
    if family in UNAFFECTED_PARSE_BRANCHES:
        return "measured"
    if ledger_status == "measured":
        return "recovered_in_ledger"
    return "quarantined"


def parser_version_at(ts):
    """Which parser produced a row stamped `ts`. Used ONCE, by the backfill, to
    label rows written before the field existed; the label is then recorded and
    read from the row forever after. Nothing downstream may date a row itself.
    """
    if ts and ts >= TOKENS_IN_FIX_TS:
        return USAGE_PARSER_VERSION
    return PARSER_VERSION_PRE_CACHE_FIX


def stamp_provenance(results_path, usage_path, apply=False):
    """Backfill `usage_parser_version` + `tokens_in_status` onto results rows.

    Labelling, never recomputation (AC#2): no recorded `tokens_in` is rewritten.
    The wrong numbers stay exactly as recorded -- they are evidence of what the
    instrument did, and overwriting them with the ledger's recovered value would
    erase the distinction between a row that was always right and a row that was
    fixed afterwards. The recovered value stays in usage.jsonl and is joined by
    run_id when a reader wants it.

    Returns the count report; writes only when `apply` is true.
    """
    rows = _read_jsonl(results_path)
    ledger = {u["run_id"]: u for u in _read_jsonl(usage_path)
              if u.get("kind", "worker") == "worker"}
    report = {"inspected": len(rows), "path": results_path, "applied": bool(apply)}
    report.update({s: 0 for s in TOKENS_IN_STATUSES})
    report["by_arm"] = {}

    for row in rows:
        version = row.get("usage_parser_version")
        if version is None:
            version = parser_version_at(row.get("ts"))
        led = ledger.get(row.get("run_id"))
        status = tokens_in_status(registry.model_family(row.get("model")), version,
                                  led.get("retrofit_status") if led else None)
        row["usage_parser_version"] = version
        row["tokens_in_status"] = status
        report[status] += 1
        arm = report["by_arm"].setdefault(row.get("model"),
                                          {s: 0 for s in TOKENS_IN_STATUSES})
        arm[status] += 1

    if apply:
        tmp = results_path + ".stamped"
        with open(tmp, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        os.replace(tmp, results_path)
    return report


def format_provenance_report(report):
    """Count the subjects out loud (AC#4). Every disposition is printed even
    when it is zero: a corpus with nothing to quarantine and a corpus whose rows
    were never inspected must not render identically.
    """
    head = (f"tokens_in provenance: inspected={report['inspected']} "
            + " ".join(f"{s}={report[s]}" for s in TOKENS_IN_STATUSES)
            + ("" if report.get("applied") else "  (dry run -- nothing written)"))
    lines = [head]
    for arm, counts in sorted(report.get("by_arm", {}).items()):
        lines.append(f"  {arm:32s} "
                     + " ".join(f"{s}={counts[s]}" for s in TOKENS_IN_STATUSES))
    if report["inspected"] == 0:
        lines.append("  NO ROWS INSPECTED -- not a clean corpus, no corpus at all")
    return "\n".join(lines)


def recovered_tokens_in(usage_path):
    """run_id -> the ledger's re-parsed `tokens_in`, for rows it could recover.

    The other half of `tokens_in_status`. `stamp_provenance` deliberately does
    NOT write the recovered value back onto the results row (AC#2: labelling,
    never recomputation), so a reader that wants an input number for a
    "recovered_in_ledger" row joins it from here by run_id. This function is
    where that join lives, so no reader restates the ledger's shape or its rule.

    Only `retrofit_status == "measured"` is offered. "unfixable_floor_only" is
    the ledger saying it could not recover the value either -- its `tokens_in` is
    a floor, not a measurement, and returning it would hand a reader a number
    that looks recovered and is not. Same literal, same reason, as the docstring
    on `tokens_in_status`.
    """
    return {u["run_id"]: u["tokens_in"]
            for u in _read_jsonl(usage_path)
            if u.get("kind", "worker") == "worker"
            and u.get("retrofit_status") == "measured"
            and isinstance(u.get("tokens_in"), (int, float))}


def _read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


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


def resolve_repo_root(repo_root=None, environ=None, cwd=None):
    """Where the CLI looks for a tree, and what it will say it looked at.

    Returns (root, source). `source` exists so the failure below can name the
    thing that produced a wrong answer: "no corpus at <path>" is unactionable
    when the reader cannot tell whether the path came from a flag they typed, an
    exported variable they forgot, or the directory they happen to be standing
    in. Precedence is explicit-beats-ambient: flag, then $MODEL_GAUNTLET_ROOT,
    then the working directory.
    """
    if repo_root:
        return repo_root, "--repo-root"
    environ = os.environ if environ is None else environ
    from_env = environ.get("MODEL_GAUNTLET_ROOT")
    if from_env:
        return from_env, "$MODEL_GAUNTLET_ROOT"
    return (os.getcwd() if cwd is None else cwd), "the working directory"


def main():
    ap = argparse.ArgumentParser(description="model-gauntlet usage ledger")
    ap.add_argument("action", choices=["retrofit", "stamp-provenance"])
    # The root is a caller input, never derived from this file's location -- see
    # paths_for_repo. Running from a repo checkout needs no flag at all, which is
    # what keeps `python3 runner/usage_ledger.py retrofit` working unchanged.
    ap.add_argument("--repo-root", default=None,
                    help="tree holding runner/results/ (default: "
                         "$MODEL_GAUNTLET_ROOT, else the working directory)")
    ap.add_argument("--results", default=None)
    ap.add_argument("--transcripts", default=None)
    ap.add_argument("--usage", default=None)
    # Dry by default. The backfill edits the corpus in place, and a command that
    # rewrites 268 rows because someone typed the wrong subcommand is not a
    # command anyone should have to be careful with.
    ap.add_argument("--apply", action="store_true",
                    help="write the labels; without it, report only")
    args = ap.parse_args()

    root, source = resolve_repo_root(args.repo_root)
    default = paths_for_repo(root)
    results = args.results or default.results
    transcripts = args.transcripts or default.transcripts
    usage = args.usage or default.usage

    # Fail loud, not empty. Both actions read results.jsonl, and an absent corpus
    # used to be impossible (the path was computed from this file's own location
    # and always pointed at this repo). Now the root is an input, so it can be
    # wrong -- and when it is wrong the honest
    # answer is a named failure -- "retrofit wrote 0 rows" over a tree that was
    # never there reads exactly like a corpus with nothing left to ledger.
    if not os.path.exists(results):
        raise SystemExit(
            f"usage_ledger: no results corpus at {results}\n"
            f"  repo root came from {source}: {root}\n"
            f"  pass --repo-root /path/to/model-gauntlet (or --results directly), "
            f"or run this from a repo checkout.")

    if args.action == "stamp-provenance":
        print(format_provenance_report(
            stamp_provenance(results, usage, apply=args.apply)))
        return

    summary = retrofit(results, transcripts, usage)
    print(f"wrote {summary['written']} row(s), "
          f"skipped {summary['skipped_existing']} already-ledgered -> {usage}")


if __name__ == "__main__":
    main()
