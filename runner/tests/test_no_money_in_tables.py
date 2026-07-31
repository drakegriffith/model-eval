"""test_no_money_in_tables.py -- ticket 20: no money renders in tables.py.

Two halves:

  THE SCAN. The whole rendered report -- every table, every note -- carries no
     money marker: no "$", no money word. Proven against a mixed synthetic
     corpus AND an empty one (an empty report and a full one must both be
     clean), with a control arm proving the scanner actually reads the tables:
     a model name salted with "$3" trips it, and names carrying "percent" /
     "recent" do not (word-boundary, not substring).
  THE DROP SEMANTICS. Table 6's input column resolves each row through its
     `tokens_in_status`: "measured" reads the row, "recovered_in_ledger" joins
     usage.jsonl by run_id, anything else is dropped -- loudly, with a count.
     A partial cell says (n=k/n) and names its losses; a cell that lost every
     row says "unavailable" and why; a fully-resolved corpus prints no
     "rows dropped" line at all.
"""
import re
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import tables  # noqa: E402

# CHECKER'S OWN COPY of the money-marker set (harness rule: checker != worker,
# and a gate must not read its rule from a worker-writable file). Typed by
# hand, deliberately duplicating _MONEY_SYMBOLS/_MONEY_WORDS/_MONEY_WORD_RE in
# product/gauntlet_playground/surface.py and .../intake.py. DO NOT import the
# set from either module and DO NOT DRY the copies into a shared helper -- the
# duplication IS the control. If the marker set legitimately changes, change
# every copy by hand.
_MONEY_SYMBOLS = ("$",)
_MONEY_WORD_RE = re.compile(
    r"\b(usd|dollar|dollars|cent|cents)\b", re.IGNORECASE)


def money_markers(text):
    """Every money marker in `text`: symbols by substring, words by boundary."""
    hits = [s for s in _MONEY_SYMBOLS if s in text]
    hits += [m.group(0) for m in _MONEY_WORD_RE.finditer(text)]
    return hits


def rrow(model, run_id, tokens_in_status=None, tokens_in=None, effort="low",
         task="t1-py-a", passed=True, tokens_out=1000, loc_changed=5):
    """One results row with everything build_report's six tables touch.

    table5 indexes r["task"] and reads loc_changed; table6 routes on
    tokens_in_status; the dispositions header wants exit_reason.
    """
    row = {"model": model, "effort": effort, "task": task, "pass": passed,
           "run_id": run_id, "tokens_out": tokens_out, "exit_reason": "ok",
           "loc_changed": loc_changed, "wall_s": 10.0}
    if tokens_in_status is not None:
        row["tokens_in_status"] = tokens_in_status
    if tokens_in is not None:
        row["tokens_in"] = tokens_in
    return row


def mixed_corpus():
    """One model per tokens_in disposition, three rows each (so table 5 has
    repeated cells and table 6 exercises every branch of the input column)."""
    rows = []
    ledger = {}
    for i in range(3):
        rows.append(rrow("m-measured", f"meas-{i}",
                         tokens_in_status="measured", tokens_in=40_000 + i))
        r = rrow("m-recovered", f"rec-{i}",
                 tokens_in_status="recovered_in_ledger", tokens_in=7)
        rows.append(r)
        ledger[f"rec-{i}"] = 50_000 + i
        rows.append(rrow("m-quarantined", f"quar-{i}",
                         tokens_in_status="quarantined"))
    return rows, ledger


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #
def test_tables_module_has_no_price_attrs():
    assert not hasattr(tables, "PRICES")
    assert not hasattr(tables, "dollars")


def test_full_report_carries_no_money_marker():
    rows, ledger = mixed_corpus()
    report = tables.build_report(rows, [], ledger)
    assert money_markers(report) == []


def test_empty_corpus_report_carries_no_money_marker():
    report = tables.build_report([], [], {})
    assert money_markers(report) == []


def test_scan_control_arm_salted_model_name_trips():
    # The negative results above are only evidence if the scanner reads what
    # the tables render: salt a model name with "$3" and the scan must fire.
    rows = [rrow("claude-$3-salt", f"s-{i}", tokens_in_status="measured",
                 tokens_in=1000) for i in range(3)]
    report = tables.build_report(rows, [], {})
    assert money_markers(report) != []


def test_scan_control_arm_word_boundary_survives_honest_words():
    # "percent" and "recent" contain "cent"; the word-boundary match must not
    # count them as money, or every honest sentence trips the gate.
    rows = [rrow("percent-recent-salt", f"w-{i}", tokens_in_status="measured",
                 tokens_in=1000) for i in range(3)]
    report = tables.build_report(rows, [], {})
    assert "percent-recent-salt" in report
    assert money_markers(report) == []


# --------------------------------------------------------------------------- #
# Table 6 drop semantics
# --------------------------------------------------------------------------- #
def test_measured_rows_resolve_on_the_row():
    rows = [rrow("m", f"r-{i}", tokens_in_status="measured", tokens_in=40_000)
            for i in range(3)]
    out = tables.table6_decision_matrix(rows, {}, {})
    assert "40,000" in out
    assert "rows dropped from `input tokens/task`" not in out


def test_recovered_rows_join_the_ledger_not_the_row():
    # The row's own tokens_in is the wrong pre-fix number (7); the truth lives
    # in the ledger under run_id. The cell must show the ledger's mean.
    rows = [rrow("m", f"r-{i}", tokens_in_status="recovered_in_ledger",
                 tokens_in=7) for i in range(3)]
    ledger = {f"r-{i}": 50_000 for i in range(3)}
    out = tables.table6_decision_matrix(rows, {}, ledger)
    assert "50,000" in out
    assert "rows dropped from `input tokens/task`" not in out


def test_partial_cell_shows_count_and_names_its_losses():
    rows = [
        rrow("m", "r-0", tokens_in_status="measured", tokens_in=1000),
        rrow("m", "r-1", tokens_in_status="measured", tokens_in=3000),
        rrow("m", "r-2", tokens_in_status="quarantined"),
    ]
    out = tables.table6_decision_matrix(rows, {}, {})
    assert "2,000 (n=2/3)" in out
    assert "rows dropped from `input tokens/task`" in out
    assert ("`m`: resolved over 2 of 3 rows; 1 dropped for want of a true "
            "`tokens_in`") in out


def test_all_dropped_cell_says_unavailable_and_why():
    rows = [rrow("m", f"r-{i}", tokens_in_status="quarantined")
            for i in range(3)]
    out = tables.table6_decision_matrix(rows, {}, {})
    assert "unavailable" in out
    assert ("`m`: 0 of 3 rows in the winning cell have a true `tokens_in` "
            "(all quarantined pre-fix)") in out


def test_recoverable_row_absent_from_ledger_is_dropped_not_zero():
    # Fail closed: a missing join is not a zero. Status says recoverable, but
    # the ledger has no entry for this run_id -- the row drops with a count.
    rows = [
        rrow("m", "r-0", tokens_in_status="measured", tokens_in=1000),
        rrow("m", "r-1", tokens_in_status="measured", tokens_in=3000),
        rrow("m", "r-2", tokens_in_status="recovered_in_ledger", tokens_in=7),
    ]
    out = tables.table6_decision_matrix(rows, {}, {})
    assert "2,000 (n=2/3)" in out
    assert ("`m`: resolved over 2 of 3 rows; 1 dropped for want of a true "
            "`tokens_in`") in out
