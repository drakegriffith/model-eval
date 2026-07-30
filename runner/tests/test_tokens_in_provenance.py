"""test_tokens_in_provenance.py — ticket 31. A row must say which parser
produced its `tokens_in`, and a reader must be able to tell recovered from lost.

WHAT WAS OPEN. Every one of the 268 corpus rows was recorded before `f11be7e`
(2026-07-27T15:38:36Z), which fixed a claude/kimi parse branch that summed only
`usage.input_tokens` and dropped the cache fields — undercounting real input by
30x–400x. `results.jsonl` carries those numbers with nothing on the row saying
so, so a consumer averaging `tokens_in` across models cannot tell a real total
from a 4,000x undercount.

WHAT THE 2026-07-30 AUDIT CHANGED. The ticket assumed the number was lost. For
56 of the 120 buggy-branch rows it is not: `retrofit()` re-parses the retained
transcript and `usage.jsonl` already holds the corrected value. Re-parsing all
56 independently reproduced the ledger exactly, 56/56. So there are THREE states,
not two, and collapsing the middle one is the mistake this file guards:

    measured             the number on the row is the true cache-inclusive total
    recovered_in_ledger  the row's number is wrong; usage.jsonl has the truth
    quarantined          nobody has the truth — fable's 64 transcript-less rows

WHAT IS ASSERTED HERE. The rule is stated once, in `usage_ledger`, and both the
write path and the report read that one copy. The load-bearing test is
`test_an_unheard_of_family_is_quarantined_by_default`: safety comes from
`family in UNAFFECTED_PARSE_BRANCHES` being a short allowlist of branches
*proven* unaffected, so anything else under the old parser is quarantined
because it was not proven clean — never because someone remembered to enlist it.
"""
import json
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import usage_ledger  # noqa: E402

# Reused rather than copied: the live-run assertion below needs run.py's real
# execute_run against a real grader, which is exactly the harness ticket 34
# built. A second copy would drift from it.
from test_pass_completeness_gate import execute, repo  # noqa: E402,F401

PRE_FIX_TS = "2026-07-26T09:00:00Z"
POST_FIX_TS = "2026-07-28T09:00:00Z"

V_OLD = usage_ledger.PARSER_VERSION_PRE_CACHE_FIX
V_NOW = usage_ledger.USAGE_PARSER_VERSION


# --------------------------------------------------------------------------- #
# The rule. Negative control first: a status function that returned
# "quarantined" unconditionally would satisfy every gate assertion below it.
# --------------------------------------------------------------------------- #
def test_a_row_from_the_current_parser_is_measured():
    assert usage_ledger.tokens_in_status("claude", V_NOW) == "measured"


def test_the_codex_branch_was_never_buggy_and_is_not_quarantined():
    """codex's `turn.completed` already folds cached tokens into input_tokens,
    so the old parser was correct there. 148 of the 268 rows are this case and
    quarantining them would throw away good data."""
    assert usage_ledger.tokens_in_status("codex", V_OLD) == "measured"


def test_a_pre_fix_claude_row_with_no_transcript_is_quarantined():
    """fable's 64 rows: recorded under the old parser, no transcript retained,
    so the true total is not recoverable from anything on disk."""
    assert usage_ledger.tokens_in_status("claude", V_OLD, None) == "quarantined"
    assert usage_ledger.tokens_in_status(
        "claude", V_OLD, "unfixable_floor_only") == "quarantined"


def test_a_pre_fix_row_recovered_in_the_ledger_is_neither_measured_nor_lost():
    """The state the ticket did not have. Saying "measured" would tell a reader
    to trust `results.jsonl`'s number, which is the 4,000x undercount; saying
    "quarantined" would discard 56 rows whose true value is sitting in
    usage.jsonl. It has to be its own answer."""
    assert usage_ledger.tokens_in_status(
        "claude", V_OLD, "measured") == "recovered_in_ledger"
    assert usage_ledger.tokens_in_status(
        "kimi", V_OLD, "measured") == "recovered_in_ledger"


def test_an_unheard_of_family_is_quarantined_by_default():
    """The by-default half. `gemini` appears in no branch, table or registry
    entry in this repo. It is quarantined under the old parser because it was
    never proven unaffected. Invert the rule to a denylist of known-buggy
    families and this goes red while everything else stays green — which is the
    point: the denylist is indistinguishable from the correct rule until the
    next family is added, and then it silently trusts a number nobody checked.
    """
    assert usage_ledger.tokens_in_status("gemini", V_OLD, None) == "quarantined"


# --------------------------------------------------------------------------- #
# The write path (AC#1). Provenance is recorded by the code that produced the
# number, so no reader ever has to infer it from `ts`.
# --------------------------------------------------------------------------- #
def test_a_live_run_records_the_parser_that_produced_its_tokens(repo, monkeypatch):
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert row["usage_parser_version"] == V_NOW
    assert row["tokens_in_status"] == "measured"


def test_the_live_row_derives_both_fields_rather_than_asserting_them(repo, monkeypatch):
    """Both fields must come from the shared rule, not from literals at the
    write site. A row that hardcodes `"measured"` would be asserting its own
    correctness, which is precisely what the 268 pre-f11be7e rows could not do
    and why this ticket exists.

    The lever is the rule function itself, not the version constant: pinning the
    constant moves the threshold and the stamp together, so it cannot tell a
    derived field from a hardcoded one. Swapping the rule can.
    """
    monkeypatch.setattr(usage_ledger, "USAGE_PARSER_VERSION", 99)
    monkeypatch.setattr(usage_ledger, "tokens_in_status",
                        lambda *a, **kw: "consulted_the_shared_rule")

    row = execute(repo, monkeypatch, solve=True, rc=0)

    assert row["usage_parser_version"] == 99, "the version is a literal at the write site"
    assert row["tokens_in_status"] == "consulted_the_shared_rule", \
        "run.py is deciding the status itself instead of asking the one rule"


# --------------------------------------------------------------------------- #
# The backfill (AC#2). Labelling, never recomputation.
# --------------------------------------------------------------------------- #
def corpus(tmp_path, rows, usage_rows=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = tmp_path / "results.jsonl"
    r.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    u = tmp_path / "usage.jsonl"
    u.write_text("".join(json.dumps(x) + "\n" for x in usage_rows), encoding="utf-8")
    return str(r), str(u)


PRE_FIX_ROWS = [
    {"run_id": "fable--r1", "ts": PRE_FIX_TS, "model": "fable",
     "tokens_in": 19378, "tokens_out": 4000},
    {"run_id": "haiku--r1", "ts": PRE_FIX_TS, "model": "claude-haiku-4-5",
     "tokens_in": 85, "tokens_out": 2128},
    {"run_id": "sol--r1", "ts": PRE_FIX_TS, "model": "sol",
     "tokens_in": 153874, "tokens_out": 9000},
]
LEDGER_ROWS = [
    {"run_id": "haiku--r1", "kind": "worker", "family": "claude",
     "tokens_in": 376248, "retrofit_status": "measured"},
    {"run_id": "fable--r1", "kind": "worker", "family": "claude",
     "tokens_in": 19378, "retrofit_status": "unfixable_floor_only"},
]


def test_the_backfill_adds_fields_and_rewrites_no_existing_value(tmp_path):
    """AC#2. The undercounted numbers stay exactly as recorded: they are wrong,
    and a "corrected" estimate written over them would launder a guess into the
    corpus. The recovered value lives in usage.jsonl and is joined, not copied.
    """
    rp, up = corpus(tmp_path, PRE_FIX_ROWS, LEDGER_ROWS)
    before = [json.loads(l) for l in open(rp) if l.strip()]

    usage_ledger.stamp_provenance(rp, up, apply=True)

    after = [json.loads(l) for l in open(rp) if l.strip()]
    assert len(after) == len(before)
    for old, new in zip(before, after):
        assert {k: new[k] for k in old} == old, "the backfill rewrote a recorded value"
        assert set(new) - set(old) == {"usage_parser_version", "tokens_in_status"}


def test_the_backfill_labels_each_row_against_the_verified_fix_instant(tmp_path):
    """Stamped once from `ts` against f11be7e's timestamp, then recorded. A row
    written after the fix is v2 even though it sits in the same file."""
    rows = PRE_FIX_ROWS + [{"run_id": "later--r1", "ts": POST_FIX_TS,
                            "model": "claude-haiku-4-5",
                            "tokens_in": 300000, "tokens_out": 2000}]
    rp, up = corpus(tmp_path, rows, LEDGER_ROWS)

    usage_ledger.stamp_provenance(rp, up, apply=True)

    got = {r["run_id"]: (r["usage_parser_version"], r["tokens_in_status"])
           for r in (json.loads(l) for l in open(rp) if l.strip())}
    assert got["fable--r1"] == (V_OLD, "quarantined")
    assert got["haiku--r1"] == (V_OLD, "recovered_in_ledger")
    assert got["sol--r1"] == (V_OLD, "measured")
    assert got["later--r1"] == (V_NOW, "measured")


def test_the_backfill_writes_nothing_unless_asked(tmp_path):
    rp, up = corpus(tmp_path, PRE_FIX_ROWS, LEDGER_ROWS)
    before = open(rp).read()

    report = usage_ledger.stamp_provenance(rp, up, apply=False)

    assert open(rp).read() == before
    assert report["quarantined"] == 1, "the dry run reported nothing to do"


# --------------------------------------------------------------------------- #
# Counting the subjects out loud (AC#4, AC#5'). Silence is not evidence.
# --------------------------------------------------------------------------- #
def test_the_report_counts_every_disposition_including_the_empty_ones(tmp_path):
    rp, up = corpus(tmp_path, PRE_FIX_ROWS, LEDGER_ROWS)

    report = usage_ledger.stamp_provenance(rp, up, apply=False)

    assert report["inspected"] == 3
    assert report["measured"] == 1
    assert report["recovered_in_ledger"] == 1
    assert report["quarantined"] == 1
    text = usage_ledger.format_provenance_report(report)
    for field in ("inspected", "measured", "recovered_in_ledger", "quarantined"):
        assert field in text


def test_a_clean_corpus_is_distinguishable_from_one_that_quarantined_everything(tmp_path):
    """The harness rule this ticket keeps tripping over: a gate that passed over
    zero subjects must not print the same thing as one that passed over all of
    them. Both runs below have work to report; only the counts differ, and the
    counts have to be in the output.
    """
    clean_rows = [dict(r, ts=POST_FIX_TS) for r in PRE_FIX_ROWS]
    rp_a, up_a = corpus(tmp_path / "a", clean_rows, LEDGER_ROWS)
    rp_b, up_b = corpus(tmp_path / "b", PRE_FIX_ROWS, [])

    clean = usage_ledger.format_provenance_report(
        usage_ledger.stamp_provenance(rp_a, up_a, apply=False))
    dirty = usage_ledger.format_provenance_report(
        usage_ledger.stamp_provenance(rp_b, up_b, apply=False))

    assert clean != dirty
    assert "quarantined=0" in clean
    assert "quarantined=2" in dirty, "two claude-family rows, neither recoverable"
