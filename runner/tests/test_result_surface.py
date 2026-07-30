"""test_result_surface.py -- the chart's data half, proven where it matters
(ticket 44, slice S4).

Four of ticket 44's acceptance criteria are the kind a green suite does not
establish on its own, so each has named tests here:

  AC#2 -- LABELS ARE DERIVED, NOT DECLARED. The X axis changes provenance per
     visitor by design, so the label is read off the values plotted. The tests
     put live and corpus numbers on the same axis and watch the label name both,
     then take the live numbers away and watch it stop.
  AC#4 -- FLATNESS IS NOT MISSING DATA. `classify` distinguishes zero rows from
     zero spread, FLAT sits in HAS_DATA_CONDITIONS, and the executable claim
     raises when the membership is broken. Every zero-row reason is a distinct
     sentence, because the fixes are distinct.
  AC#5 -- THE PRINTED SENTENCE. Latency and token size come off the report the
     executor measured, quality is stated as a non-result twice over, and no
     sentence carries a price -- with the word-boundary matching proven in both
     directions: "5 cents" is refused, "40 percent" and "a recent run" are not.
  AC#6 ON THE REAL PATH -- test_provenance.py proves the guard fires on a
     constructed pair; `refreshed_value`'s docstring promises this file performs
     the pooling on the values that function actually selects between, so the
     blend 14a forbids is seen refused where it would be made.

No test here draws. Everything below runs without matplotlib installed, which
is the point of surface.py's split: the honesty argument is testable in the
module with no third-party dependency.
"""
import os
import sys
import types

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
import provenance  # noqa: E402
from gauntlet_playground import executor  # noqa: E402
from gauntlet_playground import surface  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture rows and reports
# --------------------------------------------------------------------------- #

def crow(model, effort="low", passed=True, tokens_out=1000, wall_s=10.0,
         task="t1-py-a", exit_reason="ok"):
    """One sealed-corpus row, shaped as the summaries read it: `pass` for
    stats.is_pass, `tokens_out` for stats.summary_tokens, `exit_reason` for the
    clean-exit gate. "ok" is corpus_gates.SUMMARIZABLE_EXIT_REASON."""
    return {"task": task, "model": model, "effort": effort, "pass": passed,
            "tokens_out": tokens_out, "wall_s": wall_s,
            "exit_reason": exit_reason}


def ran(task_id="t1-py-a", wall_s=5.0, tokens_out=500, model_id="kimi-k3"):
    return executor.TaskOutcome(
        task_id=task_id, model_id=model_id, status="ran", usd=0.01,
        reason="completed", run_id="testrun-1",
        usage_detail={"tokens_out": tokens_out}, ledgered=True, wall_s=wall_s)


def refused(task_id="t1-py-a", model_id="kimi-k3"):
    """A refusal carries no measurement: wall_s None, usage_detail None --
    executor.TaskOutcome's docstring refuses to let None mean zero."""
    return executor.TaskOutcome(
        task_id=task_id, model_id=model_id, status="refused", usd=0.0,
        reason="cap", run_id=None, usage_detail=None, ledgered=False,
        wall_s=None)


def report(outcomes, model_id="kimi-k3", effort="low"):
    return executor.RunReport(
        model_id=model_id, family="kimi", cap_usd=1.0, spent_usd=0.02,
        outcomes=tuple(outcomes), ledger_rows_written=len(outcomes),
        effort=effort)


# --------------------------------------------------------------------------- #
# AC#6 on the real path: the pooling refreshed_value refuses to do
# --------------------------------------------------------------------------- #

class TestNeverBlendOnTheRealPath:

    def _series(self):
        live = [provenance.Value(400.0, provenance.LIVE,
                                 surface.TOKENS_OUT.key, "your run"),
                provenance.Value(600.0, provenance.LIVE,
                                 surface.TOKENS_OUT.key, "your run")]
        corpus = [provenance.Value(1000.0, provenance.CORPUS,
                                   surface.TOKENS_OUT.key, "sealed corpus")]
        return live, corpus

    def test_pooling_the_selected_series_is_refused(self):
        """The natural wrong move on this exact data -- three live attempts,
        one corpus run, pool them for a tighter mean -- raises, on the very
        values refreshed_value selects between."""
        live, corpus = self._series()
        value, n = surface.refreshed_value(live, corpus)
        assert value.provenance == provenance.LIVE
        with pytest.raises(provenance.ProvenanceBlend):
            provenance.mean(live + corpus)

    def test_live_refreshes_rather_than_adds(self):
        live, corpus = self._series()
        value, n = surface.refreshed_value(live, corpus)
        assert value.number == 500.0     # mean of live alone, corpus untouched
        assert n == 2                    # the count is the live count

    def test_corpus_serves_when_the_visitor_did_not_run(self):
        _, corpus = self._series()
        value, n = surface.refreshed_value([], corpus)
        assert value.provenance == provenance.CORPUS
        assert (value.number, n) == (1000.0, 1)

    def test_no_numbers_is_none_and_zero_not_a_mean_over_nothing(self):
        assert surface.refreshed_value([], []) == (None, 0)


# --------------------------------------------------------------------------- #
# AC#4: three conditions, and flatness has data in it
# --------------------------------------------------------------------------- #

def _val(n):
    return provenance.Value(n, provenance.CORPUS, "quality_pass_rate", "corpus")


class TestConditions:

    def test_zero_rows_and_zero_spread_are_different_facts(self):
        assert surface.classify([]) == surface.NO_ROWS
        assert surface.classify([_val(1.0), _val(1.0)]) == surface.FLAT
        assert surface.classify([_val(1.0), _val(0.5)]) == surface.MEASURED

    def test_flat_sits_with_measured(self):
        assert surface.FLAT in surface.HAS_DATA_CONDITIONS
        assert surface.FLAT not in surface.NO_DATA_CONDITIONS
        surface.assert_flatness_is_not_missing_data()

    def test_removing_flat_from_has_data_raises(self, monkeypatch):
        monkeypatch.setattr(surface, "HAS_DATA_CONDITIONS", (surface.MEASURED,))
        with pytest.raises(AssertionError, match="left HAS_DATA_CONDITIONS"):
            surface.assert_flatness_is_not_missing_data()

    def test_adding_flat_to_no_data_raises(self, monkeypatch):
        monkeypatch.setattr(surface, "NO_DATA_CONDITIONS",
                            (surface.NO_ROWS, surface.FLAT))
        with pytest.raises(AssertionError, match="NO_DATA_CONDITIONS"):
            surface.assert_flatness_is_not_missing_data()

    def test_a_condition_in_both_sets_raises(self, monkeypatch):
        monkeypatch.setattr(surface, "HAS_DATA_CONDITIONS",
                            (surface.MEASURED, surface.FLAT, surface.NO_ROWS))
        with pytest.raises(AssertionError, match="both sets"):
            surface.assert_flatness_is_not_missing_data()


class TestFlatnessNote:

    def test_flat_by_design_and_flat_cites_the_ruling(self):
        note = surface.flatness_note(surface.QUALITY, surface.FLAT, 6)
        assert "FLAT BY DESIGN" in note
        assert "saturated" in note

    def test_flat_by_design_but_measured_is_news_not_a_stale_label(self):
        note = surface.flatness_note(surface.QUALITY, surface.MEASURED, 6)
        assert "NO LONGER FLAT" in note
        assert "de-saturated" in note

    def test_undeclared_flatness_is_still_a_measurement(self):
        note = surface.flatness_note(surface.TOKENS_OUT, surface.FLAT, 4)
        assert "measured and equal" in note

    def test_zero_rows_makes_no_spread_claim(self):
        note = surface.flatness_note(surface.QUALITY, surface.NO_ROWS, 0)
        assert "no measurement was made" in note


# --------------------------------------------------------------------------- #
# The corpus partition counts what it drops
# --------------------------------------------------------------------------- #

class TestPartitionByTier:

    def test_untiered_tasks_are_counted_never_defaulted(self):
        rows = [crow("kimi-k3"), crow("kimi-k3", task="t9-mystery"),
                crow("kimi-k3", task="t9-mystery")]
        by_tier, untiered = surface.partition_by_tier(rows)
        assert len(by_tier["domain-1"]) == 1
        assert untiered == {"t9-mystery": 2}

    def test_every_corpus_task_has_a_declared_tier(self):
        """TASK_TIERS is written out, not defaulted -- so the declaration must
        cover the task tree this repo actually has. A task added to tasks/
        without a tier here lands on no tab, and the chart would report it as
        untiered forever."""
        tasks_dir = os.path.join(REPO_ROOT, "tasks")
        on_disk = {d for d in os.listdir(tasks_dir)
                   if os.path.isdir(os.path.join(tasks_dir, d))}
        declared = set(surface.TASK_TIERS)
        assert declared <= on_disk, (
            f"TASK_TIERS declares tasks that do not exist: "
            f"{sorted(declared - on_disk)}")
        assert on_disk <= declared, (
            f"tasks on disk missing a tier declaration (they will render as "
            f"untiered on every tab): {sorted(on_disk - declared)}")


# --------------------------------------------------------------------------- #
# Dominance and the frontier
# --------------------------------------------------------------------------- #

def _point(model, x, y, effort="low",
           x_axis=surface.TOKENS_OUT, y_axis=surface.QUALITY):
    return surface.Point(
        model, effort,
        provenance.Value(x, provenance.CORPUS, x_axis.key, "corpus"),
        provenance.Value(y, provenance.CORPUS, y_axis.key, "corpus"),
        1, 1)


class TestPareto:

    def test_lower_tokens_higher_quality_dominates(self):
        a, b = _point("a", 500, 1.0), _point("b", 900, 0.5)
        assert surface.dominates(a, b, surface.TOKENS_OUT, surface.QUALITY)
        assert not surface.dominates(b, a, surface.TOKENS_OUT, surface.QUALITY)

    def test_equal_points_dominate_neither_way(self):
        a, b = _point("a", 500, 1.0), _point("b", 500, 1.0)
        assert not surface.dominates(a, b, surface.TOKENS_OUT, surface.QUALITY)
        assert not surface.dominates(b, a, surface.TOKENS_OUT, surface.QUALITY)

    def test_a_tradeoff_leaves_both_on_the_frontier(self):
        pts = [_point("cheap", 500, 0.5), _point("good", 900, 1.0)]
        front = surface.pareto_front(pts, surface.TOKENS_OUT, surface.QUALITY)
        assert front == {("cheap", "low"), ("good", "low")}

    def test_points_from_a_different_chart_are_refused(self):
        """Two charts' points would order a token count against a latency
        quite happily; the axis check is what makes that a traceback."""
        a = _point("a", 500, 1.0)
        b = _point("b", 3.0, 1.0, x_axis=surface.LATENCY)
        with pytest.raises(ValueError, match="measure different things"):
            surface.dominates(a, b, surface.TOKENS_OUT, surface.QUALITY)


# --------------------------------------------------------------------------- #
# build_chart: corpus-only, refreshed, and the zero-row reasons
# --------------------------------------------------------------------------- #

def _corpus_two_configs():
    return [crow("kimi-k3", tokens_out=500), crow("kimi-k3", tokens_out=700),
            crow("sol-1", tokens_out=900), crow("sol-1", tokens_out=1100)]


class TestBuildChart:

    def test_corpus_only_chart(self):
        chart = surface.build_chart(_corpus_two_configs())
        assert {p.config for p in chart.points} == {("kimi-k3", "low"),
                                                    ("sol-1", "low")}
        by_model = {p.model: p for p in chart.points}
        assert by_model["kimi-k3"].x.number == 600.0
        assert by_model["kimi-k3"].x.provenance == provenance.CORPUS
        assert by_model["kimi-k3"].y.number == 1.0
        assert chart.x_provenances == (provenance.CORPUS,)
        assert (chart.corpus_inspected, chart.corpus_kept) == (4, 4)
        assert chart.zero_rows_reason is None

    def test_saturated_corpus_reads_flat_and_frontier_is_a_cost_ordering(self):
        chart = surface.build_chart(_corpus_two_configs())
        assert chart.y_condition == surface.FLAT
        assert chart.frontier == {("kimi-k3", "low")}
        note = surface.frontier_note(chart)
        assert "cost ordering" in note and "not a winner" in note

    def test_a_failing_row_desaturates_and_the_note_changes(self):
        rows = _corpus_two_configs() + [crow("sol-1", passed=False,
                                             tokens_out=1000)]
        chart = surface.build_chart(rows)
        assert chart.y_condition == surface.MEASURED
        assert "beats them on both" in surface.frontier_note(chart)
        assert "NO LONGER FLAT" in surface.flatness_note(
            chart.y_axis, chart.y_condition, sum(p.n_y for p in chart.points))

    def test_live_report_refreshes_the_x_axis_for_its_config_only(self):
        live = report([ran(wall_s=2.0, tokens_out=200),
                       ran(wall_s=4.0, tokens_out=400)])
        chart = surface.build_chart(_corpus_two_configs(),
                                    live_reports=(live,))
        by_model = {p.model: p for p in chart.points}
        kimi, sol = by_model["kimi-k3"], by_model["sol-1"]
        assert kimi.x.provenance == provenance.LIVE
        assert (kimi.x.number, kimi.n_x) == (300.0, 2)   # live mean, live n
        assert sol.x.provenance == provenance.CORPUS      # untouched
        assert chart.x_provenances == (provenance.LIVE, provenance.CORPUS)
        assert chart.y_provenances == (provenance.CORPUS,)  # quality never live
        assert chart.live_configs == (("kimi-k3", "low"),)

    def test_live_point_lands_on_the_reports_effort_rung(self):
        """RunReport.effort is why the field exists: a kimi run at high effort
        refreshes the ("kimi-k3", "high") rung, never the low one. The corpus
        must measure the high rung too -- quality has no live source, so a rung
        the corpus never graded is half a point and plots nothing (that case
        has its own test below)."""
        rows = _corpus_two_configs() + [crow("kimi-k3", effort="high",
                                             tokens_out=800)]
        live = report([ran(tokens_out=200)], effort="high")
        chart = surface.build_chart(rows, live_reports=(live,))
        by_config = {p.config: p for p in chart.points}
        assert by_config[("kimi-k3", "high")].x.provenance == provenance.LIVE
        assert by_config[("kimi-k3", "high")].x.number == 200.0
        assert by_config[("kimi-k3", "low")].x.provenance == provenance.CORPUS
        assert chart.live_configs == (("kimi-k3", "high"),)

    def test_a_report_without_an_effort_is_refused_not_guessed(self):
        bare = types.SimpleNamespace(model_id="kimi-k3",
                                     outcomes=(ran(),))
        with pytest.raises(ValueError, match="carries no effort"):
            surface.build_chart(_corpus_two_configs(), live_reports=(bare,))

    def test_a_refusal_contributes_no_live_number(self):
        live = report([refused()])
        chart = surface.build_chart(_corpus_two_configs(),
                                    live_reports=(live,))
        assert chart.live_configs == ()
        assert chart.x_provenances == (provenance.CORPUS,)

    def test_a_live_config_with_no_corpus_quality_plots_no_point(self):
        """The executor does not grade. A model the corpus never measured has
        an X number and no Y number, and half a point is not a point."""
        live = report([ran(model_id="new-model")], model_id="new-model")
        chart = surface.build_chart(_corpus_two_configs(),
                                    live_reports=(live,))
        assert ("new-model", "low") not in {p.config for p in chart.points}
        assert ("new-model", "low") in chart.live_configs  # counted, not hidden

    def test_latency_axis_reads_wall_s_from_both_sources(self):
        live = report([ran(wall_s=2.0), ran(wall_s=4.0)])
        chart = surface.build_chart(_corpus_two_configs(),
                                    x_axis_key=surface.LATENCY.key,
                                    live_reports=(live,))
        by_model = {p.model: p for p in chart.points}
        assert by_model["kimi-k3"].x.number == 3.0        # live mean
        assert by_model["sol-1"].x.number == 10.0         # crow's wall_s

    def test_untiered_rows_are_reported_on_the_chart(self):
        rows = _corpus_two_configs() + [crow("kimi-k3", task="t9-mystery")]
        chart = surface.build_chart(rows)
        assert chart.untiered == {"t9-mystery": 1}
        assert "t9-mystery=1 row(s)" in surface.format_chart(chart)


class TestZeroRowReasons:
    """Four distinct reasons, four distinct fixes -- "no data" tells a reader
    which none, so each sentence is pinned and pairwise different."""

    def test_unpublished_tier(self):
        chart = surface.build_chart(_corpus_two_configs(),
                                    tier_key="domain-2")
        assert "no published tasks" in chart.zero_rows_reason
        assert "unauthored tier" in chart.zero_rows_reason

    def test_everything_deselected(self):
        chart = surface.build_chart(_corpus_two_configs(), models=())
        assert "no models selected" in chart.zero_rows_reason

    def test_selection_matches_nothing(self):
        chart = surface.build_chart(_corpus_two_configs(),
                                    models=("nobody-v9",))
        assert "none of the selected models" in chart.zero_rows_reason

    def test_no_row_survived_the_clean_exit_gate(self):
        rows = [crow("kimi-k3", exit_reason="hard_kill"),
                crow("sol-1", exit_reason="hard_kill")]
        chart = surface.build_chart(rows)
        assert "clean-exit gate" in chart.zero_rows_reason
        assert (chart.corpus_inspected, chart.corpus_kept) == (2, 0)
        assert chart.corpus_excluded == {"hard_kill": 2}

    def test_the_four_reasons_are_pairwise_distinct(self):
        reasons = [
            surface.build_chart(_corpus_two_configs(),
                                tier_key="domain-2").zero_rows_reason,
            surface.build_chart(_corpus_two_configs(),
                                models=()).zero_rows_reason,
            surface.build_chart(_corpus_two_configs(),
                                models=("nobody-v9",)).zero_rows_reason,
            surface.build_chart(
                [crow("kimi-k3", exit_reason="hard_kill")]).zero_rows_reason,
        ]
        assert len(set(reasons)) == 4

    def test_zero_rows_still_classifies_and_notes_honestly(self):
        chart = surface.build_chart([], tier_key="domain-2")
        assert chart.x_condition == surface.NO_ROWS
        assert chart.frontier == frozenset()
        assert "not computed" in surface.frontier_note(chart)
        assert surface.format_row_count(chart) == chart.zero_rows_reason


# --------------------------------------------------------------------------- #
# AC#2: labels derived from the data
# --------------------------------------------------------------------------- #

class TestAxisLabels:

    def test_axes_and_labels_count_the_same_because_built_together(self):
        chart = surface.build_chart(_corpus_two_configs())
        assert len(surface.chart_axes(chart)) == len(surface.axis_labels(chart))

    def test_corpus_only_visitor_gets_a_corpus_only_label(self):
        """The corpus label text is "sealed corpus, not your run" -- it names
        the live provenance in order to deny it, so `"your run" not in label`
        refuses the honest label. Corpus-only is: the tail IS the corpus label,
        alone, with no " + " joining a second provenance."""
        chart = surface.build_chart(_corpus_two_configs())
        x_label, y_label = surface.axis_labels(chart)
        corpus_only = provenance.label(provenance.CORPUS)
        assert x_label.split(" -- ", 1)[1] == corpus_only
        assert y_label.split(" -- ", 1)[1] == corpus_only

    def test_a_visitor_who_ran_sees_both_provenances_named(self):
        live = report([ran(tokens_out=200)])
        chart = surface.build_chart(_corpus_two_configs(),
                                    live_reports=(live,))
        x_label, _ = surface.axis_labels(chart)
        assert "your run" in x_label and "sealed corpus" in x_label
        assert " + " in x_label

    def test_an_axis_with_nothing_on_it_says_so(self):
        assert "0 rows, no source" in surface.axis_label(surface.TOKENS_OUT, ())


class TestHonestyLines:

    def test_the_five_claims_in_drawing_order(self):
        chart = surface.build_chart(_corpus_two_configs())
        lines = surface.honesty_lines(chart)
        assert len(lines) == 5
        assert lines[0].startswith("X | ") and lines[1].startswith("Y | ")
        assert "FLAT BY DESIGN" in lines[2]
        assert "cost ordering" in lines[3]
        assert "inspected=4 kept=4" in lines[4]

    def test_format_chart_shows_all_four_tabs_and_no_ranking(self):
        text = surface.format_chart(surface.build_chart(_corpus_two_configs()))
        for tier in surface.TIERS:
            assert tier.key in text
        assert text.count("(empty)") == 3          # the three unauthored tiers
        assert "No score, no rank" in text


# --------------------------------------------------------------------------- #
# AC#5: the printed sentence
# --------------------------------------------------------------------------- #

class TestPrintedSentence:

    def test_figures_are_the_reports_not_recomputed(self):
        rep = report([ran(task_id="t1-py-a", wall_s=2.0, tokens_out=100),
                      ran(task_id="t1-py-b", wall_s=4.0, tokens_out=300)])
        text = surface.printed_sentence(rep, _corpus_two_configs())
        assert "finished in 3.0s" in text            # mean of the two wall_s
        assert "wrote 200 output tokens" in text     # mean of the two counts
        assert "t1-py-a, t1-py-b" in text
        assert "effort=low" in text

    def test_quality_is_a_non_result_twice_over(self):
        rep = report([ran()])
        text = surface.printed_sentence(rep, _corpus_two_configs())
        assert "Quality is a non-result here, twice over" in text
        assert "all 4 of 4 sealed-corpus runs" in text      # the catalog claim
        assert "any gap at all" in text                     # the power claim
        assert "not a verdict" in text

    def test_at_24_attempts_the_power_claim_carries_a_number(self):
        rep = report([ran() for _ in range(24)])
        text = surface.printed_sentence(rep, _corpus_two_configs())
        assert "37 points" in text                   # stats.min_detectable_effect(24)
        assert "percent" not in text                 # "points", by prior regression

    def test_an_empty_corpus_is_said_not_papered_over(self):
        text = surface.printed_sentence(report([ran()]), corpus_rows=())
        assert "no rows on this tier" in text

    def test_a_run_where_nothing_ran_gets_a_sentence_not_a_traceback(self):
        rep = report([refused(), refused(task_id="t1-py-b")])
        text = surface.printed_sentence(rep, _corpus_two_configs())
        assert text.startswith("0 of 2 task(s) ran")
        assert "refused" in text
        assert "no quality result either" in text

    def test_the_refusals_own_effort_gap_is_defaulted_visibly(self):
        bare = types.SimpleNamespace(model_id="kimi-k3",
                                     outcomes=(refused(),))
        text = surface.printed_sentence(bare, ())
        assert text.startswith("0 of 1 task(s) ran")   # no live point, no raise


class TestMoneyGate:
    """Spec §5 checked on the finished string. The word-boundary fix is proven
    in the direction that costs a working sentence: an honest sentence with
    "percent" or "recent" in it must come back, and a price must not."""

    @pytest.mark.parametrize("text", [
        "the run cost $0.02 in total",
        "the run cost 2 cents",
        "one cent per task",
        "about 5 dollars all in",
        "a Dollar here, a dollar there",
        "billed in USD as usual",
    ])
    def test_a_price_is_refused(self, text):
        with pytest.raises(ValueError, match="no slice may print a price"):
            surface._refuse_money(text)

    @pytest.mark.parametrize("text", [
        "a recent run improved by 40 percent",
        "the 90th percentile latency",
        "recently re-run, 40 points better",
        "a decent, incentive-free measurement",
    ])
    def test_an_honest_sentence_is_returned_unchanged(self, text):
        assert surface._refuse_money(text) == text

    def test_the_refusal_names_every_marker_it_found(self):
        with pytest.raises(ValueError, match=r"'\$', 'cents'"):
            surface._refuse_money("$3, or 300 cents")

    def test_the_real_sentence_passes_the_gate_it_is_wrapped_in(self):
        """The report in hand carries spent_usd=0.02 one attribute access away;
        the sentence built from it must not mention it."""
        text = surface.printed_sentence(report([ran()]), _corpus_two_configs())
        assert surface._refuse_money(text) == text
