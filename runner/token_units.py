#!/usr/bin/env python3
"""token_units.py -- is a Claude token the same unit as a Codex token?
(Ticket 20 items 1 and 2. Zero new model runs; every input is already on disk.)

THE SUSPICION, as ticket 01 6 and ticket 08 recorded it: Claude Code re-sends
the whole conversation every turn and counts each re-send at full weight, while
the codex binary emits one `turn.completed` per session "with cached input
already folded in", counted once. If that were true the two families' totals
would be different quantities wearing one column name -- the same defect ticket
17 ruled fatal for `turns` -- and the frontier could not share an X-axis.

THE MEASUREMENT SAYS OTHERWISE. Both CLIs report the SAME quantity:

    session total = SUM over model requests of (full context sent at that request)

Codex's `total_token_usage` is a running sum of its own `last_token_usage`
records, verified on every gauntlet rollout on disk with zero mismatches. Its
single `turn.completed` carries that cumulative sum, not one session context.
Ticket 08's "counted once" reading was inferred from "only one turn.completed
event per run", which is true and does not imply it. See the ticket for the
ruling that follows.

WHAT THIS MODULE PRODUCES

Three candidate figures per run, so the ruling rests on numbers rather than on
either family's docs:

  session_total       -- as above; what the CLIs report and what an API bills,
                         since every request pays for its whole input context.
  peak_context_final  -- the last request's context. Like-for-like with what
                         codex's turn.completed was ASSUMED to report. This is
                         a context-window occupancy figure, not a cost.
  cache_weighted      -- cache reads counted at CACHE_READ_WEIGHT instead of
                         full weight. The weight is a STATED PARAMETER, not a
                         measurement -- see the constant.

and one independent check (item 2): re-derive the total from the raw per-request
record and compare it to the CLI's own summary event, which is what the ledger
stores. Divergences are reported, never resolved by picking a side.

WHERE THE PER-REQUEST RECORD LIVES. Not in runner/results/transcripts/ -- those
hold each CLI's SUMMARY event, which is the thing being checked and so cannot
check itself. The per-request record is each CLI's own session log:

  claude/kimi  ~/.claude/projects/<slugified scratch dir>/<session_id>.jsonl
  codex        ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl  (matched on cwd)

Those live outside the repo and are pruned on their own schedule, so
`snapshot` writes the derived per-request series into
runner/results/context_series.jsonl and every later command reads that. The
ruling stays reproducible after the home-directory logs age out.

    python3 runner/token_units.py snapshot   # derive from the CLI session logs
    python3 runner/token_units.py check      # item 2, the independent check
    python3 runner/token_units.py report     # item 1, the per-family figures

Stdlib only.
"""
import argparse
import collections
import glob
import json
import os

# Membership in B''s core, declared on disk for runner/import_gate.py (read via
# ast, never imported). Deleting this line fails the gate rather than quietly
# shrinking the core.
CORE_MODULE = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")
USAGE_PATH = os.path.join(RUNNER_DIR, "results", "usage.jsonl")
TRANSCRIPTS_DIR = os.path.join(RUNNER_DIR, "results", "transcripts")
SERIES_PATH = os.path.join(RUNNER_DIR, "results", "context_series.jsonl")

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")

# Cache reads bill at roughly a tenth of fresh input on both providers' published
# list prices. This is a CHOSEN WEIGHT used to answer "what would this cost if it
# were metered", not something measured from these runs -- ticket 20's standing
# rule means any figure derived with it must carry the weight next to it, which
# figures() does.
CACHE_READ_WEIGHT = 0.1

Req = collections.namedtuple("Req", "fresh cache_creation cache_read out")


# --------------------------------------------------------------------------- #
# The three candidate figures
# --------------------------------------------------------------------------- #
def figures(series, cache_read_weight=CACHE_READ_WEIGHT):
    """Reduce a per-request series to the candidate units for the X-axis."""
    ctx = [r.fresh + r.cache_creation + r.cache_read for r in series]
    return {
        "requests": len(series),
        "session_total": sum(ctx),
        "peak_context_final": ctx[-1] if ctx else 0,
        "peak_context_max": max(ctx) if ctx else 0,
        "cache_weighted": sum(r.fresh + r.cache_creation
                              + cache_read_weight * r.cache_read for r in series),
        "cache_read_weight": cache_read_weight,
        "tokens_out": sum(r.out for r in series),
        "cache_read_total": sum(r.cache_read for r in series),
    }


# --------------------------------------------------------------------------- #
# Per-request parsers -- one per CLI session-log format
# --------------------------------------------------------------------------- #
def claude_series(session_path):
    """Per-API-response usage from a Claude Code session log.

    Deduplicated on message id: one API response can be written out as several
    assistant lines when its content is split into blocks, and counting lines
    instead of responses multiplies the total.
    """
    if not os.path.exists(session_path):
        return None
    seen, out = set(), []
    with open(session_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            mid = msg.get("id")
            if mid is not None and mid in seen:
                continue
            seen.add(mid)
            out.append(Req(fresh=int(u.get("input_tokens", 0) or 0),
                           cache_creation=int(u.get("cache_creation_input_tokens", 0) or 0),
                           cache_read=int(u.get("cache_read_input_tokens", 0) or 0),
                           out=int(u.get("output_tokens", 0) or 0)))
    return out or None


def _codex_token_events(rollout_path):
    """(last_token_usage, total_token_usage) per event, in file order."""
    evs = []
    with open(rollout_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload") or {}
            if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            if info.get("total_token_usage") and info.get("last_token_usage"):
                evs.append((info["last_token_usage"], info["total_token_usage"]))
    return evs


def codex_series(rollout_path):
    """Per-model-request usage from a codex rollout.

    codex reports a request's context as `input_tokens` with `cached_input_tokens`
    already inside it, so fresh = input - cached. It reports no cache-creation
    figure, which is why cache_creation is 0 for this family rather than guessed.
    A token_count event that does not advance the running total is a re-emission,
    not a new request, and is skipped.
    """
    if not os.path.exists(rollout_path):
        return None
    out, prev_total = [], None
    for last, total in _codex_token_events(rollout_path):
        if prev_total is not None and total == prev_total:
            continue
        prev_total = total
        cached = int(last.get("cached_input_tokens", 0) or 0)
        out.append(Req(fresh=int(last.get("input_tokens", 0) or 0) - cached,
                       cache_creation=0, cache_read=cached,
                       out=int(last.get("output_tokens", 0) or 0)))
    return out or None


def codex_reported_total(rollout_path):
    """The final running total codex itself reports -- the figure its single
    turn.completed carries. Compared against codex_series to establish that the
    total is cumulative, not a one-off session context."""
    evs = _codex_token_events(rollout_path)
    return evs[-1][1]["input_tokens"] if evs else None


# --------------------------------------------------------------------------- #
# Item 2 -- the independent check
# --------------------------------------------------------------------------- #
def crosscheck(run_id, ledger_tokens_in, ledger_tokens_out, series, alternates=None):
    """Compare the ledger (built from the CLI's SUMMARY event) against a total
    re-derived from the CLI's per-request session log.

    Both figures survive into the result. A divergence is a discrepancy to
    display, not a tie to break: the module has no basis for preferring one
    self-report over the other, and inventing one would be exactly the
    fabricated-certainty failure ticket 20 exists to prevent.

    `alternates` are the other sessions found in the same scratch directory.
    They are consulted ONLY after the pre-committed session choice has already
    disagreed, and only to separate two facts that would otherwise be reported
    as one: a run whose number no record supports, versus a run corroborated
    exactly by a sibling session whose mapping back to this run is ambiguous.
    Requiring both token counts to reproduce exactly is what keeps the second
    case from laundering the first.
    """
    if not series:
        return {"run_id": run_id, "agrees": None, "status": "no_independent_record",
                "ledger_tokens_in": ledger_tokens_in,
                "ledger_tokens_out": ledger_tokens_out,
                "rederived_tokens_in": None, "rederived_tokens_out": None,
                "delta_in": None, "delta_out": None}

    def totals(s):
        f = figures(s)
        return f["session_total"], f["tokens_out"]

    ti, to = totals(series)
    if ti == ledger_tokens_in and to == ledger_tokens_out:
        status, agrees = "agree", True
    else:
        status, agrees = "DISCREPANCY", False
        for alt in alternates or []:
            a_ti, a_to = totals(alt)
            if a_ti == ledger_tokens_in and a_to == ledger_tokens_out:
                ti, to = a_ti, a_to
                status, agrees = "agree_after_attribution", True
                break

    return {"run_id": run_id, "agrees": agrees, "status": status,
            "ledger_tokens_in": ledger_tokens_in, "ledger_tokens_out": ledger_tokens_out,
            "rederived_tokens_in": ti, "rederived_tokens_out": to,
            "delta_in": ti - ledger_tokens_in, "delta_out": to - ledger_tokens_out}


# --------------------------------------------------------------------------- #
# Locating each CLI's session log for a given run
# --------------------------------------------------------------------------- #
def _claude_session_index():
    """run_id -> that run's scratch-dir session directory."""
    out = {}
    if not os.path.isdir(CLAUDE_PROJECTS):
        return out
    marker = "model-gauntlet--scratch-"
    for d in os.listdir(CLAUDE_PROJECTS):
        if marker in d:
            out[d.split(marker, 1)[1]] = os.path.join(CLAUDE_PROJECTS, d)
    return out


def _codex_rollout_index():
    """run_id -> [rollout paths], oldest first.

    A scratch dir can hold more than one codex session (the judge runs in the
    same directory afterwards), so the worker run is the EARLIEST rollout. That
    rule is fixed in advance and never consults the ledger value it is about to
    be checked against -- selecting the rollout that happens to match would make
    the independent check circular.
    """
    out = {}
    if not os.path.isdir(CODEX_SESSIONS):
        return out
    for path in glob.glob(os.path.join(CODEX_SESSIONS, "**", "*.jsonl"), recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                head = json.loads(f.readline())
        except (json.JSONDecodeError, OSError):
            continue
        if head.get("type") != "session_meta":
            continue
        payload = head.get("payload") or {}
        cwd = payload.get("cwd", "")
        if "model-gauntlet" not in cwd:
            continue
        out.setdefault(os.path.basename(cwd), []).append(
            (payload.get("timestamp", ""), path))
    return {k: [p for _, p in sorted(v)] for k, v in out.items()}


def _claude_session_id(run_id):
    """The session id the summary transcript names, so the right log is read
    when a scratch dir holds several."""
    path = os.path.join(TRANSCRIPTS_DIR, run_id + ".txt")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.loads(f.read()).get("session_id")
    except (json.JSONDecodeError, OSError):
        return None


def series_for_run(run_id, family, claude_idx=None, codex_idx=None):
    """(series, source_path, alternates) from the CLI's own session log.

    `alternates` are the other sessions recorded against the same scratch
    directory. claude/kimi have none: the summary transcript names the exact
    session id, so there is nothing to disambiguate.
    """
    if family in ("claude", "kimi"):
        idx = claude_idx if claude_idx is not None else _claude_session_index()
        sid = _claude_session_id(run_id)
        d = idx.get(run_id)
        if not (sid and d):
            return None, None, []
        path = os.path.join(d, sid + ".jsonl")
        return claude_series(path), path, []
    idx = codex_idx if codex_idx is not None else _codex_rollout_index()
    found = [(p, codex_series(p)) for p in idx.get(run_id, [])]
    found = [(p, s) for p, s in found if s]
    if not found:
        return None, None, []
    path, series = found[0]
    return series, path, [s for _, s in found[1:]]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def read_usage(usage_path=USAGE_PATH):
    rows = []
    if not os.path.exists(usage_path):
        return rows
    with open(usage_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def worker_rows(usage_path=USAGE_PATH):
    """Rows written before ticket 20 carry no `kind` and are worker rows."""
    return [r for r in read_usage(usage_path) if r.get("kind", "worker") == "worker"]


def snapshot(usage_path=USAGE_PATH, series_path=SERIES_PATH):
    """Derive the per-request series for every `measured` row and persist it."""
    claude_idx, codex_idx = _claude_session_index(), _codex_rollout_index()
    written = collections.Counter()
    with open(series_path, "w", encoding="utf-8") as out:
        for row in worker_rows(usage_path):
            if row.get("retrofit_status") != "measured":
                written["skipped_not_measured"] += 1
                continue
            series, src, alts = series_for_run(row["run_id"], row["family"],
                                               claude_idx, codex_idx)
            if not series:
                written["no_session_log"] += 1
                continue
            out.write(json.dumps({
                "run_id": row["run_id"], "family": row["family"],
                "model_id": row.get("model_id"),
                "source": os.path.basename(src or ""),
                "requests": [list(r) for r in series],
                "alternates": [[list(r) for r in a] for a in alts],
            }) + "\n")
            written["derived"] += 1
    return dict(written)


def load_snapshot(series_path=SERIES_PATH):
    out = {}
    if not os.path.exists(series_path):
        return out
    with open(series_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec["requests"] = [Req(*r) for r in rec["requests"]]
            rec["alternates"] = [[Req(*r) for r in a]
                                 for a in rec.get("alternates", [])]
            out[rec["run_id"]] = rec
    return out


def _fmt(n):
    return f"{n:,.0f}"


def cmd_check(usage_path=USAGE_PATH, series_path=SERIES_PATH):
    snap = load_snapshot(series_path)
    rows = [r for r in worker_rows(usage_path) if r.get("retrofit_status") == "measured"]
    results = [crosscheck(r["run_id"], r["tokens_in"], r["tokens_out"],
                          (snap.get(r["run_id"]) or {}).get("requests"),
                          (snap.get(r["run_id"]) or {}).get("alternates"))
               for r in rows]
    by = collections.Counter(c["status"] for c in results)

    print("ITEM 2 -- independent check: the ledger is built from each CLI's summary")
    print("event; this re-derives the same total from that CLI's per-request session")
    print("log and compares. Neither is preferred when they disagree.\n")
    print(f"  measured rows                    {len(rows)}")
    print(f"  agree exactly                    {by['agree']}")
    print(f"  agree, session attribution vague {by['agree_after_attribution']}")
    print(f"  DISCREPANCY                      {by['DISCREPANCY']}")
    print(f"  no independent record            {by['no_independent_record']}")
    for c in results:
        if c["status"] == "agree_after_attribution":
            print(f"    ~ {c['run_id']}: reproduced exactly by a sibling session in a "
                  f"reused scratch dir, not by the earliest one")
        elif c["status"] == "DISCREPANCY":
            print(f"    ! {c['run_id']}: summary={_fmt(c['ledger_tokens_in'])} "
                  f"session-log={_fmt(c['rederived_tokens_in'])} "
                  f"delta={c['delta_in']:+,} -- NO session log reproduces the ledger")
        elif c["status"] == "no_independent_record":
            print(f"    ? {c['run_id']}: unchecked (no session log on disk)")

    # The same comparison, read the other way round, is item 1's load-bearing
    # fact: for every family the CLI's own session figure equals the SUM of its
    # per-request contexts. For codex that means its single turn.completed
    # carries a cumulative sum, not one session context counted once.
    fam_of = {r["run_id"]: r["family"] for r in rows}
    per = collections.defaultdict(lambda: [0, 0])
    for c in results:
        slot = per[fam_of[c["run_id"]]]
        slot[1] += 1
        if c["agrees"]:
            slot[0] += 1
    print("\n  CLI-reported session figure == sum of that CLI's per-request contexts:")
    for fam, (ok, n) in sorted(per.items()):
        print(f"    {fam:<8} {ok}/{n}")
    return by["DISCREPANCY"]


def cmd_report(usage_path=USAGE_PATH, series_path=SERIES_PATH):
    snap = load_snapshot(series_path)
    rows = worker_rows(usage_path)
    measured = [r for r in rows if r.get("retrofit_status") == "measured"]
    excluded = collections.Counter(r["retrofit_status"] for r in rows
                                   if r.get("retrofit_status") != "measured")

    per = collections.defaultdict(list)
    for r in measured:
        rec = snap.get(r["run_id"])
        if rec:
            per[(r["family"], r.get("model_id"))].append(figures(rec["requests"]))

    print("ITEM 1 -- candidate units for the frontier's X-axis. Measured rows only;")
    print(f"excluded and disclosed: {dict(excluded)} (no transcript to re-derive from).")
    print(f"cache-weighted uses a STATED weight of {CACHE_READ_WEIGHT} on cache reads, "
          "not a measured rate.\n")
    hdr = (f"{'model_id':<28}{'fam':<8}{'n':>4}{'reqs':>7}"
           f"{'session-total':>15}{'peak-ctx':>11}{'cache-wtd':>12}{'tot/peak':>10}")
    print(hdr)
    print("-" * len(hdr))
    for (fam, mid), fs in sorted(per.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        n = len(fs)
        tot = sum(f["session_total"] for f in fs) / n
        peak = sum(f["peak_context_final"] for f in fs) / n
        cw = sum(f["cache_weighted"] for f in fs) / n
        rq = sum(f["requests"] for f in fs) / n
        print(f"{str(mid):<28}{fam:<8}{n:>4}{rq:>7.1f}{_fmt(tot):>15}"
              f"{_fmt(peak):>11}{_fmt(cw):>12}{tot / peak:>9.1f}x")

    print("\nper family, and the share of the axis that is cache-read "
          "(a reader's fresh-input rate does NOT apply to it):")
    for fam in ("codex", "claude", "kimi"):
        fs = [f for (fm, _), lst in per.items() if fm == fam for f in lst]
        if not fs:
            continue
        tot = sum(f["session_total"] for f in fs)
        cr = sum(f["cache_read_total"] for f in fs)
        cw = sum(f["cache_weighted"] for f in fs)
        print(f"  {fam:<8} n={len(fs):<4} cache-read {cr / tot * 100:>5.1f}% of total; "
              f"a flat rate x session-total overstates a metered bill by "
              f"{tot / cw:>4.2f}x")


def main():
    ap = argparse.ArgumentParser(description="token comparability across CLI families")
    ap.add_argument("command", choices=["snapshot", "check", "report"])
    ap.add_argument("--usage", default=USAGE_PATH)
    ap.add_argument("--series", default=SERIES_PATH)
    args = ap.parse_args()

    if args.command == "snapshot":
        print(snapshot(args.usage, args.series))
    elif args.command == "check":
        cmd_check(args.usage, args.series)
    else:
        cmd_report(args.usage, args.series)


if __name__ == "__main__":
    main()
