"""test_tables.py -- issue #31: tables 2, 3, 4 and 6 dropped sub-minimum or
unresolvable cells silently. #21's split (PR #33) fixed table5's silent
`continue`; this file pins the same pattern for the four tables issue #31
named, each with its own drop shape:

  table2_efficiency_frontier   `token_rows()` drops a cell's truncated rows
  table3_harness_delta         from the mean it computes; the count came
  table4_hybrid_vs_solo        back from `token_rows` and was thrown away.

  table6_decision_matrix       the "best config" selection scores a 0-scored
                                candidate `rate = 0` via `if n else 0`, which
                                is numerically identical to a REAL 0%-pass
                                cell, so an untested config could win the
                                tiebreak against a real, if-poor, measured one
                                (its `toks` falls back to `or 0`, reading as
                                free rather than absent). This is the harness's
                                "silence is not evidence" failure shape.

Every fixture below forces the real gate (`corpus_gates.summarizable`, via
`run_status.partition_for_rate` feeding `cap_exhausted`/`timeout` rows into a
cell) rather than monkeypatching the predicate -- a fake gate proves nothing
about the real one.
"""
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import tables  # noqa: E402


def row(rep, exit_reason, pass_=False, tok=100, model="glm-4.7-local",
        effort="high", harness=False, task="t1-py-a", driver=None):
    """One results.jsonl row, shaped like the fixtures in
    test_estimand_readers.py -- the same corpus these tables actually read."""
    r = {
        "run_id": f"{model}--{effort}--{'harness' if harness else 'bare'}--"
                  f"{task}--r{rep}--{exit_reason}",
        "sweep": "s", "model": model, "model_id": model, "effort": effort,
        "task": task, "rep": rep, "harness": harness,
        "pass": pass_, "exit_reason": exit_reason,
        "tokens_in": 100, "tokens_out": tok, "wall_s": 1.0,
        "loc_changed": 5, "turns": 1, "tokens_in_status": "measured",
        "invocation_mode": "multi_turn",
    }
    if driver is not None:
        r["driver"] = driver
    return r


# --------------------------------------------------------------------------- #
# (a) table2_efficiency_frontier: dropped token-axis rows must be disclosed
# --------------------------------------------------------------------------- #
def test_table2_discloses_rows_it_drops_from_the_token_axis():
    """A cell of 3 rows -- 2 clean, 1 cap_exhausted -- keeps all 3 in the pass
    denominator (cap_exhausted is SCORED, amendment A1) but must drop the
    cap_exhausted row from the token mean (its tokens_out measures where the
    broker's K-request cap cut it off, not what the tier spent). Before this
    fix, `token_rows()`'s dropped count was computed and discarded; the table
    said nothing about the 1-of-3 exclusion."""
    rows = [
        row(0, "ok", pass_=True, tok=100),
        row(1, "ok", pass_=True, tok=200),
        row(2, "cap_exhausted", pass_=False, tok=999999),
    ]

    out = tables.table2_efficiency_frontier(rows)

    assert "1 of 3 row(s) excluded from the token axis" in out, (
        f"table2 does not name its dropped token-axis row:\n{out}")
    assert "cap_exhausted=1" in out, f"the drop's cause is not named:\n{out}"
    # And the mean itself must actually have excluded the capped row's
    # runaway tokens_out -- the disclosure and the number must agree.
    assert "999999" not in out.split("\n> ")[0], (
        f"the dropped row's tokens_out leaked into the rendered mean:\n{out}")


def test_table2_names_no_drop_when_the_cell_is_clean():
    out = tables.table2_efficiency_frontier(
        [row(0, "ok", pass_=True), row(1, "ok", pass_=True)])

    assert "excluded from the token axis" not in out, (
        f"a clean cell must not print a drop footnote:\n{out}")


# --------------------------------------------------------------------------- #
# (b) table3_harness_delta / table4_hybrid_vs_solo: same shape
# --------------------------------------------------------------------------- #
def test_table3_discloses_rows_it_drops_from_the_token_axis():
    """The `bare` cell has 1 clean + 1 cap_exhausted row; `harness` is clean.
    Both cells keep 2 rows in the pass denominator; `bare`'s token mean must
    drop the capped row's runaway tokens_out and say so."""
    rows = [
        row(0, "ok", pass_=True, tok=100, harness=False),
        row(1, "cap_exhausted", pass_=False, tok=999999, harness=False),
        row(2, "ok", pass_=True, tok=150, harness=True),
        row(3, "ok", pass_=False, tok=175, harness=True),
    ]

    out = tables.table3_harness_delta(rows)

    assert "glm-4.7-local/bare" in out and "1 of 2 row(s) excluded from the token axis" in out, (
        f"table3 does not name its dropped bare-cell row:\n{out}")
    assert "cap_exhausted=1" in out, f"the drop's cause is not named:\n{out}"
    assert "999999" not in out.split("\n> ")[0], (
        f"the dropped row's tokens_out leaked into the rendered mean:\n{out}")


def test_table4_discloses_rows_it_drops_from_the_token_axis():
    """T3 task, one model, 2 clean rows + 1 cap_exhausted. cap_exhausted is
    SCORED for the pass axis but still excluded from the token mean, which is
    the case table4 threw away before this fix."""
    rows = [
        row(0, "ok", pass_=True, tok=100, task="t3-hybrid-a"),
        row(1, "ok", pass_=True, tok=120, task="t3-hybrid-a"),
        row(2, "cap_exhausted", pass_=False, tok=999999, task="t3-hybrid-a"),
    ]

    out = tables.table4_hybrid_vs_solo(rows)

    assert "1 of 3 row(s) excluded from the token axis" in out, (
        f"table4 does not name its dropped token-axis row:\n{out}")
    assert "cap_exhausted=1" in out, f"the drop's cause is not named:\n{out}"
    assert "999999" not in out.split("\n> ")[0], (
        f"the dropped row's tokens_out leaked into the rendered mean:\n{out}")


# --------------------------------------------------------------------------- #
# (c) table6_decision_matrix: a 0-scored cell must never render as 0%
# --------------------------------------------------------------------------- #
def test_table6_never_lets_an_unmeasured_config_outrank_a_real_one():
    """model has two candidate configs:
      (effort=high, bare): 2 rows, both `timeout` -> 0 scored rows (n=0)
      (effort=low,  bare): 2 rows, both `ok` but pass=False -> a REAL 0%
                           cell, n=2, passes=0, with real tokens_out spent.

    `rate = passes / n if n else 0` scores BOTH candidates `rate == 0`, so the
    old tiebreak (`-toks`, with an empty cell's `toks` falling back to `or 0`,
    i.e. reading as free) picked the UNTESTED config over the real one,
    rendering the whole model as 'no measured runs' / 'no basis to advise'
    even though a real 0%-pass measurement existed. The real, if-poor,
    measurement must win the selection, and the table must print its actual
    0% -- distinct from 'no basis to advise', which is reserved for a model
    with truly no measured config at all."""
    rows = [
        row(0, "timeout", pass_=False, tok=1, effort="high", harness=False),
        row(1, "timeout", pass_=False, tok=1, effort="high", harness=False),
        row(2, "ok", pass_=False, tok=500, effort="low", harness=False),
        row(3, "ok", pass_=False, tok=520, effort="low", harness=False),
    ]

    out = tables.table6_decision_matrix(rows, {})

    assert "no measured runs" not in out, (
        f"a real 0%-pass config exists; the model must not render as "
        f"unmeasured:\n{out}")
    assert "no basis to advise" not in out, f"same, on the advice column:\n{out}"
    assert "0%" in out, f"the real, measured 0%-pass config must render as 0%:\n{out}"
    assert "low/bare" in out, (
        f"the real measured config (effort=low) must be the one selected, "
        f"not the untested effort=high cell:\n{out}")


def test_table6_still_says_no_basis_when_truly_nothing_was_measured():
    """Positive control on the OTHER side: a model whose every config is
    unmeasured (all timeouts) must still say so -- this fix must not turn
    every empty cell into a fabricated 0%."""
    rows = [
        row(0, "timeout", pass_=False, tok=1, effort="high", harness=False),
        row(1, "timeout", pass_=False, tok=1, effort="high", harness=False),
    ]

    out = tables.table6_decision_matrix(rows, {})

    assert "no measured runs" in out, f"a truly unmeasured model must say so:\n{out}"
    assert "no basis to advise" in out, f"and its advice column must say so:\n{out}"
    assert "0%" not in out, f"an empty cell must never render as 0%:\n{out}"


# --------------------------------------------------------------------------- #
# issue #41 (1): table5 crashes on a cell whose scored rows are all
# cap_exhausted -- min()/max() over an empty spend
# --------------------------------------------------------------------------- #
def test_table5_survives_a_cell_whose_scored_rows_are_all_capped():
    """2 scored rows (cap_exhausted -- SCORED under amendment A1, both clear
    the >=2 minimum just above) but BOTH excluded from the token axis (a
    capped run's tokens_out measures where the broker's K-request cap cut it
    off, not what the tier spent). `spend` is empty, so `min(locs)` on
    master raises ValueError and aborts the whole report. This cell must
    render as a named drop instead of a crash."""
    rows = [
        row(0, "cap_exhausted", pass_=False, tok=1),
        row(1, "cap_exhausted", pass_=False, tok=1),
    ]

    out = tables.table5_variance(rows)  # must not raise

    assert out.startswith("_(no data)_"), (
        f"a cell with no token-axis evidence cannot render a min/med/max row:\n{out}")
    assert "excluded from the token axis" in out, (
        f"the all-capped cell is not named:\n{out}")
    assert "cap_exhausted=2" in out, f"the drop's cause is not named:\n{out}"

