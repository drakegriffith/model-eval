"""test_invocation_mode.py — ticket 32. A row must say HOW its model was
invoked (one shot vs an agentic multi-turn session), because the two are
different measurements and nothing on the row said which one it was.

WHAT WAS OPEN. Ticket 13's table declares the contract — codex CLIs run
single_shot, claude/kimi CLIs run multi_turn — but that fact lived in a ticket,
not in the corpus. `turns` cannot stand in for it: it is structurally 1 on all
148 codex rows regardless of what the model did, and barred from citation.
A reader pooling tokens_out across families is pooling two instruments and has
no field telling it so.

WHAT IS ASSERTED HERE. The rule is stated once, in `usage_ledger`, keyed on
family — the same place `build_cli_cmd` (run.py:447) dispatches the actual
invocation. Fail-closed: an unheard-of family is "unknown", written explicitly
on the row, never guessed from `turns` (AC#4). The write path derives through
the shared rule (never a literal at the write site), the backfill labels the
268 archived rows without rewriting any value, and table 2 — the cross-family
tokens table that would have caught t13 — renders the field and warns when it
is mixing modes in one table.
"""
import json
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import corpus_gates  # noqa: E402
import tables  # noqa: E402
import usage_ledger  # noqa: E402

# Reused rather than copied: the live-run assertions need run.py's real
# execute_run against a real grader, which is exactly the harness ticket 34
# built. A second copy would drift from it.
from test_pass_completeness_gate import execute, repo  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# The rule. Keyed on family because the invocation IS a family property:
# build_cli_cmd dispatches on family, so mode follows the same key.
# --------------------------------------------------------------------------- #
def test_claude_and_kimi_families_are_multi_turn():
    assert usage_ledger.invocation_mode("claude") == "multi_turn"
    assert usage_ledger.invocation_mode("kimi") == "multi_turn"


def test_the_local_family_is_multi_turn():
    """`local` rides the same `claude -p` agentic session as claude/kimi, only
    pointed at LM Studio (run.py's local branch), so its mode is that binary's
    mode. Undeclared, every GLM row landed labelled "unknown" — a real property
    of the invocation reported as unknowable."""
    assert usage_ledger.invocation_mode("local") == "multi_turn"


def test_the_codex_family_is_single_shot():
    assert usage_ledger.invocation_mode("codex") == "single_shot"


def test_an_unheard_of_family_is_unknown_not_guessed():
    """The by-default half (AC#4). `gemini` appears in no branch, table or
    registry entry in this repo. Its mode is "unknown" because nobody declared
    it — never inferred from turn counts, which are an artifact of the parse
    branch, not the invocation."""
    assert usage_ledger.invocation_mode("gemini") == "unknown"


# --------------------------------------------------------------------------- #
# The write path (AC#1). Recorded by the code that ran the CLI, so no reader
# ever reconstructs it from family tables in a ticket.
# --------------------------------------------------------------------------- #
def test_a_live_run_records_its_invocation_mode(repo, monkeypatch):
    """The fixture model is claude-haiku-4-5 — claude family, multi_turn."""
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert row["invocation_mode"] == "multi_turn"


def test_the_live_row_derives_the_mode_rather_than_asserting_it(repo, monkeypatch):
    """The field must come from the shared rule, not a literal at the write
    site. Same lever as ticket 31's provenance test: swap the rule function and
    the row must follow it."""
    monkeypatch.setattr(usage_ledger, "invocation_mode",
                        lambda *a, **kw: "consulted_the_shared_rule")
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert row["invocation_mode"] == "consulted_the_shared_rule", \
        "run.py is deciding the mode itself instead of asking the one rule"


def test_a_mock_run_carries_the_field_as_none(repo, monkeypatch):
    """No model was invoked, so the question is vacuous — same shape as
    `sealed`. The KEY is present (an absent key means unstamped, which is a
    different fact); the value is None."""
    monkeypatch.setenv("GAUNTLET_MOCK", "1")
    row = execute(repo, monkeypatch, solve=True, rc=0)
    assert "invocation_mode" in row
    assert row["invocation_mode"] is None


# --------------------------------------------------------------------------- #
# The backfill (AC#2/#4). Labelling, never recomputation.
# --------------------------------------------------------------------------- #
def corpus(tmp_path, rows):
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = tmp_path / "results.jsonl"
    r.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    return str(r)


ARCHIVE_ROWS = [
    {"run_id": "sol--r1", "model": "sol", "tokens_in": 153874, "turns": 1},
    {"run_id": "haiku--r1", "model": "claude-haiku-4-5", "tokens_in": 85,
     "turns": 12},
    {"run_id": "mystery--r1", "model": "totally-unregistered-model",
     "tokens_in": 7, "turns": 3},
]


def test_the_backfill_adds_only_the_field_and_rewrites_no_value(tmp_path):
    rp = corpus(tmp_path, ARCHIVE_ROWS)
    before = [json.loads(l) for l in open(rp) if l.strip()]

    usage_ledger.stamp_invocation_mode(rp, apply=True)

    after = [json.loads(l) for l in open(rp) if l.strip()]
    assert len(after) == len(before)
    for old, new in zip(before, after):
        assert {k: new[k] for k in old} == old, "the backfill rewrote a recorded value"
        assert set(new) - set(old) == {"invocation_mode"}


def test_the_backfill_labels_by_family_and_writes_unknown_explicitly(tmp_path):
    """AC#4: "unknown" lands ON the row. An unstamped row and a row nobody
    could classify must not be the same bytes."""
    rp = corpus(tmp_path, ARCHIVE_ROWS)

    usage_ledger.stamp_invocation_mode(rp, apply=True)

    got = {r["run_id"]: r["invocation_mode"]
           for r in (json.loads(l) for l in open(rp) if l.strip())}
    assert got["sol--r1"] == "single_shot"
    assert got["haiku--r1"] == "multi_turn"
    assert got["mystery--r1"] == "unknown"


def test_the_backfill_respects_a_row_that_already_carries_the_field(tmp_path):
    """A mock row wrote invocation_mode=None at run time; the backfill must
    not "fix" it to a family guess. Present-with-None means inapplicable and
    stays exactly as recorded."""
    rows = ARCHIVE_ROWS + [{"run_id": "mock--r1", "model": "claude-haiku-4-5",
                            "invocation_mode": None}]
    rp = corpus(tmp_path, rows)

    report = usage_ledger.stamp_invocation_mode(rp, apply=True)

    got = {r["run_id"]: r for r in (json.loads(l) for l in open(rp) if l.strip())}
    assert got["mock--r1"]["invocation_mode"] is None
    assert report["inapplicable"] == 1


def test_the_backfill_writes_nothing_unless_asked(tmp_path):
    rp = corpus(tmp_path, ARCHIVE_ROWS)
    before = open(rp).read()

    report = usage_ledger.stamp_invocation_mode(rp, apply=False)

    assert open(rp).read() == before
    assert report["unknown"] == 1, "the dry run reported nothing to do"


# --------------------------------------------------------------------------- #
# Counting the subjects out loud (AC#6). Silence is not evidence.
# --------------------------------------------------------------------------- #
def test_the_report_counts_every_mode_including_the_empty_ones(tmp_path):
    rp = corpus(tmp_path, ARCHIVE_ROWS)

    report = usage_ledger.stamp_invocation_mode(rp, apply=False)

    assert report["inspected"] == 3
    assert report["single_shot"] == 1
    assert report["multi_turn"] == 1
    assert report["unknown"] == 1
    assert report["inapplicable"] == 0
    text = usage_ledger.format_invocation_mode_report(report)
    for field in ("inspected", "single_shot", "multi_turn", "unknown",
                  "inapplicable"):
        assert field in text


def test_a_fully_classified_corpus_prints_differently_from_an_unclassifiable_one(tmp_path):
    """A corpus with zero unknowns and a corpus that is ALL unknowns must not
    render identically — the counts have to be in the output."""
    rp_a = corpus(tmp_path / "a", ARCHIVE_ROWS[:2])
    rp_b = corpus(tmp_path / "b", [ARCHIVE_ROWS[2]])

    clean = usage_ledger.format_invocation_mode_report(
        usage_ledger.stamp_invocation_mode(rp_a, apply=False))
    dark = usage_ledger.format_invocation_mode_report(
        usage_ledger.stamp_invocation_mode(rp_b, apply=False))

    assert clean != dark
    assert "unknown=0" in clean
    assert "unknown=1" in dark


# --------------------------------------------------------------------------- #
# The consumer (AC#5). The reader that would have caught t13: a cross-family
# token table must say which instrument produced each cell, and must warn when
# one table mixes the two.
# --------------------------------------------------------------------------- #
def test_the_reader_disposition_has_three_answers():
    assert corpus_gates.invocation_mode_of({}) == "unstamped"
    assert corpus_gates.invocation_mode_of({"invocation_mode": None}) == "inapplicable"
    assert corpus_gates.invocation_mode_of(
        {"invocation_mode": "single_shot"}) == "single_shot"


# `exit_reason` is present because these rows must reach table2's DENOMINATOR to
# render a mode column at all (issue #12 d): a row carrying no exit_reason has no
# estimand disposition, and an undispositioned row is excluded rather than assumed
# clean -- the same fail-closed rule corpus_gates already applies to an unstamped
# `tokens_in`. Deliberately NOT `invocation_mode`: these tests are about the mode
# column, and the row stays unstamped on that axis so the "unstamped" case below
# still has a subject.
T2_ROW = {"effort": "high", "pass": True, "tokens_out": 1000, "exit_reason": "ok"}


def test_table2_mode_column_follows_the_field_never_turns():
    """A sol row with turns=13 still renders single_shot: the column reads the
    recorded field, and `turns` — structurally 1 on codex rows and an artifact
    of the parse branch — is never consulted."""
    rows = [dict(T2_ROW, model="sol", turns=13, invocation_mode="single_shot")]
    out = tables.table2_efficiency_frontier(rows)
    assert "single_shot" in out
    assert "multi_turn" not in out


def test_table2_warns_when_it_mixes_invocation_modes():
    """The would-have-caught-t13 assertion: two families, two instruments, one
    tokens table — the mixing must be said out loud. Publishability of the
    mixed table is tickets 03/20's ruling, not this one's; this only makes the
    mixing visible."""
    rows = [dict(T2_ROW, model="sol", invocation_mode="single_shot"),
            dict(T2_ROW, model="fable", invocation_mode="multi_turn")]
    out = tables.table2_efficiency_frontier(rows)
    assert "single_shot" in out and "multi_turn" in out
    assert "mix" in out.lower(), "no warning that the table pools two instruments"


def test_table2_stays_quiet_when_every_row_shares_one_mode():
    rows = [dict(T2_ROW, model="sol", invocation_mode="single_shot"),
            dict(T2_ROW, model="luna", invocation_mode="single_shot")]
    out = tables.table2_efficiency_frontier(rows)
    assert "mix" not in out.lower()


def test_table2_renders_an_unstamped_row_as_unstamped():
    """A row that never went through the backfill is a row nobody
    dispositioned — fail closed, never guess a mode for it."""
    rows = [dict(T2_ROW, model="sol")]
    out = tables.table2_efficiency_frontier(rows)
    assert "unstamped" in out
