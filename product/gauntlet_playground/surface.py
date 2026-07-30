"""surface.py -- the screen a visitor gets back after a run (ticket 44, slice S4).

WHAT THIS MODULE IS. The data half of the result surface: it turns a run report
from executor.py plus the sealed corpus into a `Chart` -- axes, labelled points,
a non-dominated set, and the counts a reader needs to know what they are looking
at. render.py draws it. The split is where the third-party dependency starts:
everything here is stdlib plus the core, so the whole honesty argument below is
testable without matplotlib installed, and only the drawing needs it.

THE TWO PROVENANCES, AND WHERE THEY MEET.
14b's settled hybrid: the live run REFRESHES latency and output size; quality is
served from the sealed corpus. So the X axis carries live numbers for the
configs the visitor actually ran and corpus numbers for the rest of the grid --
one axis, two provenances, side by side and never summed. The Y axis is corpus
only. This is what makes the never-blend guard load-bearing rather than
decorative: the obvious wrong move on this exact data is "the visitor ran three
attempts, the corpus has six, pool them for a better estimate", and
`refreshed_value` is where that move would be made. It is refused by
provenance.mean rather than by this docstring.

WHY THE AXIS LABEL IS DERIVED AND NOT DECLARED. AC#2 counts axes against labels.
A static label per axis would pass that count while saying the wrong thing the
moment an axis's data changed provenance -- which the X axis does, per visitor,
by design. So `axis_label` reads the provenances off the values actually plotted.
An axis with no live points says corpus; an axis with both says both.

FLATNESS IS A CONDITION WITH DATA IN IT.
The public catalog is saturated by ruling (spec §2(a)): every model passes every
public task, so the quality axis is flat. Flat and empty are different facts that
render identically if you let them, so the conditions here are three and not two
-- MEASURED, FLAT, NO_ROWS -- and FLAT lives in HAS_DATA_CONDITIONS next to
MEASURED, not next to NO_ROWS. `assert_flatness_is_not_missing_data()` states
that as an executable claim rather than as a comment, and render.py calls it
before it draws.

`Axis.flat_by_design` carries the reason saturation makes the quality axis flat,
and it is checked against the data rather than printed over it: an axis declared
flat by design that comes back MEASURED means a de-saturated tier has arrived,
which is news, and `flatness_note` says so instead of repeating a stale claim.

WHAT THIS SURFACE DOES NOT COMPUTE. Pass is `stats.is_pass`, output tokens are
`stats.summary_tokens` (output-only since ticket 31 quarantined `tokens_in`), the
clean-exit filter is `corpus_gates.summarizable_rows`, and the detectable-effect
floor is `stats.min_detectable_effect`. The product does not get a second
counting rule -- ticket 44's AC on figures, and ticket 40 AC#8 before it. The two
live axes are `wall_s` and `tokens_out` precisely because ticket 31 left both
untouched; there is no third live axis to offer.
"""
from typing import NamedTuple

import corpus_gates
import provenance
import stats

# --------------------------------------------------------------------------- #
# Axes
# --------------------------------------------------------------------------- #


class Axis(NamedTuple):
    """One measured quantity the chart can plot.

    `better` is "lower" or "higher" and is what the Pareto test reads; writing it
    on the axis rather than at the comparison keeps the direction next to the
    unit, where a reader adding an axis will see it.

    `default_provenance` is where the number comes from when the visitor's own
    run does not cover a config. It is not a claim about every value on the axis
    -- `axis_label` reads that off the data -- it is the fallback source.

    `flat_by_design` is None for an axis nobody has ruled about, and the reason
    text for one where flatness is a decision that was taken. See `flatness_note`
    for why it is compared against the data instead of printed unconditionally.
    """
    key: str
    title: str
    unit: str
    better: str
    default_provenance: str
    flat_by_design: str = None


TOKENS_OUT = Axis(
    key="tokens_out",
    title="output tokens written",
    unit="tokens",
    better="lower",
    default_provenance=provenance.CORPUS,
)

LATENCY = Axis(
    key="latency_s",
    title="wall-clock time to finish",
    unit="seconds",
    better="lower",
    default_provenance=provenance.CORPUS,
)

QUALITY = Axis(
    key="quality_pass_rate",
    title="pass rate",
    unit="0-1",
    better="higher",
    default_provenance=provenance.CORPUS,
    flat_by_design=(
        "every public task is saturated at 1.000 by ruling (playground spec "
        "§2(a)): the public catalog is the already-spent subset of the corpus, "
        "so no model can separate from another on it. Measured and equal -- "
        "not missing, not unbuilt"),
)

# The X axis is the visitor's choice; the Y axis is not, because quality is the
# only axis the corpus serves and the only one the live run cannot produce (the
# product's executor runs tasks, it does not grade them -- grading is a separate
# ticket, and pretending otherwise here would put an ungraded number on a quality
# axis).
X_AXES = {a.key: a for a in (TOKENS_OUT, LATENCY)}
Y_AXIS = QUALITY

# --------------------------------------------------------------------------- #
# Difficulty tiers
# --------------------------------------------------------------------------- #


class Tier(NamedTuple):
    """One difficulty tab. `published` is False for a tier that exists in the
    taxonomy and has no tasks yet, which is a different fact from a tier whose
    tasks all got filtered out, and both are different from a tier that was never
    declared."""
    key: str
    title: str
    published: bool
    note: str


# Ticket 19's adopted taxonomy -- training density x whether the model's prior
# helps or hurts. All four domains are tabs from the start, including the three
# with nothing in them, because a tab bar showing one tab reads as "this is the
# whole taxonomy" and a tab bar showing four reads as "three of these are not
# authored yet", which is the true statement. Selecting an unpublished tier is
# how the zero-row path gets reached in ordinary use rather than only in a test.
TIERS = (
    Tier("domain-1", "Application / business logic", True,
         "ledgers, config, money, CRUD, API glue. Every task in the sealed "
         "corpus is here, and every one of them is saturated"),
    Tier("domain-2", "Adversarial convention / legacy interop", False,
         "the codebase's documented rules contradict the idiomatic prior. "
         "Authored under ticket 24, none published"),
    Tier("domain-3", "Numerical / scientific", False,
         "tolerance, stability, unit discipline. Gated behind domain 2's "
         "result; not authored"),
    Tier("domain-4", "Concurrency / systems", False,
         "races, ordering, cleanup under failure. Needs a deterministic "
         "interleaving harness that does not exist; not authored"),
)
TIERS_BY_KEY = {t.key: t for t in TIERS}
DEFAULT_TIER = TIERS[0].key

# Which tier each corpus task belongs to, written out rather than defaulted.
# A task missing from here is not silently domain-1: `partition_by_tier` counts
# it as untiered and the chart reports the count, because a task quietly dropped
# from every tab is rows nobody knows are missing.
TASK_TIERS = {
    "t1-py-a": "domain-1", "t1-py-b": "domain-1",
    "t1-ts-a": "domain-1", "t1-ts-b": "domain-1",
    "t2-py-a": "domain-1", "t2-py-b": "domain-1",
    "t2-ts-a": "domain-1", "t2-ts-b": "domain-1",
    "t3-a": "domain-1",
    "t4-py-a": "domain-1", "t4-py-b": "domain-1", "t4-ts-a": "domain-1",
    "t5-py-a": "domain-1", "t5-py-b": "domain-1", "t5-ts-a": "domain-1",
}

# --------------------------------------------------------------------------- #
# Axis conditions. THREE, and the third one is the point.
# --------------------------------------------------------------------------- #
MEASURED = "measured"
FLAT = "flat"
NO_ROWS = "no-rows"

# The membership that AC#4 is about. FLAT sits with MEASURED because a flat axis
# HAS rows -- somebody measured, and the answers agreed. Moving it into
# NO_DATA_CONDITIONS is the defect this split exists to make impossible to write
# by accident, and `assert_flatness_is_not_missing_data` is the executable form.
HAS_DATA_CONDITIONS = (MEASURED, FLAT)
NO_DATA_CONDITIONS = (NO_ROWS,)
CONDITIONS = HAS_DATA_CONDITIONS + NO_DATA_CONDITIONS


def assert_flatness_is_not_missing_data():
    """Raise unless flatness is classified as a condition that has data.

    Called at import time here and again by render.py before it draws, rather
    than only asserted in a test: the test proves the classification is right in
    the tree it ran against, and this proves it in the process that is about to
    put a picture in front of somebody.
    """
    if FLAT not in HAS_DATA_CONDITIONS:
        raise AssertionError(
            "FLAT has left HAS_DATA_CONDITIONS -- a flat axis has rows in it, "
            "and classifying it as absent data renders a measured result as an "
            "unbuilt feature (ticket 44 AC#4)")
    if FLAT in NO_DATA_CONDITIONS:
        raise AssertionError(
            "FLAT has been added to NO_DATA_CONDITIONS -- the quality axis is "
            "flat because every public task is saturated (spec §2(a)), which is "
            "a measurement, not a missing-data condition")
    overlap = set(HAS_DATA_CONDITIONS) & set(NO_DATA_CONDITIONS)
    if overlap:
        raise AssertionError(
            f"conditions {sorted(overlap)} are in both sets -- a condition that "
            f"is both present and absent renders as whichever branch is checked "
            f"first")


assert_flatness_is_not_missing_data()


def classify(values):
    """MEASURED, FLAT or NO_ROWS for one axis's values.

    Zero rows is decided first and named, never folded into "no spread". A mean
    over nothing and a mean over six identical numbers are different facts.
    """
    values = list(values)
    if not values:
        return NO_ROWS
    return FLAT if len({v.number for v in values}) == 1 else MEASURED


def flatness_note(axis, condition, n_rows):
    """The sentence the picture carries about this axis's spread.

    Four cases, and the reason all four exist is that three of them read alike
    if you write only one:

      - declared flat by design AND flat: say so, and cite why (AC#3).
      - declared flat by design and NOT flat: a de-saturated tier has arrived.
        Repeating the by-design claim here would be a stale label over live data,
        so it says the opposite, out loud.
      - not declared, and flat: still a measurement. Reported as one.
      - no rows: this axis has nothing on it, and that is `format_row_count`'s
        sentence, not a flatness claim. Flatness is undefined over zero rows.
    """
    if condition == NO_ROWS:
        return (f"{axis.title}: 0 rows -- no measurement was made, so this axis "
                f"makes no claim about spread")
    if axis.flat_by_design:
        if condition == FLAT:
            return (f"{axis.title}: FLAT BY DESIGN over {n_rows} corpus runs -- "
                    f"{axis.flat_by_design}")
        return (f"{axis.title}: NO LONGER FLAT over {n_rows} corpus runs -- this "
                f"axis was flat by design because every public task was "
                f"saturated; a de-saturated tier is now present and the "
                f"by-design claim no longer holds")
    if condition == FLAT:
        return (f"{axis.title}: identical across every config over {n_rows} "
                f"runs -- measured and equal")
    return f"{axis.title}: varies across configs over {n_rows} runs"


# --------------------------------------------------------------------------- #
# Reading the corpus
# --------------------------------------------------------------------------- #


def partition_by_tier(rows):
    """Split corpus rows by declared tier, and count what has no tier.

    Returns `(by_tier, untiered)`. `untiered` maps task id to a row count and is
    returned always, on the same argument corpus_gates.partition returns its
    exclusion tally: a caller that filters without receiving the tally cannot
    report what the filter cost.
    """
    by_tier, untiered = {}, {}
    for r in rows:
        task = r.get("task")
        tier = TASK_TIERS.get(task)
        if tier is None:
            untiered[task] = untiered.get(task, 0) + 1
            continue
        by_tier.setdefault(tier, []).append(r)
    return by_tier, untiered


def _corpus_reader(axis):
    """How a corpus row yields this axis's number -- the instrument's own
    functions where the instrument has one, so the surface is reading the same
    formula and not a second copy of it."""
    if axis is QUALITY:
        return lambda r: 1.0 if stats.is_pass(r) else 0.0
    if axis is TOKENS_OUT:
        return stats.summary_tokens
    if axis is LATENCY:
        return lambda r: float(r.get("wall_s") or 0.0)
    raise ValueError(f"no corpus reader for axis {axis.key!r}")


def _live_reader(axis):
    """How one TaskOutcome yields this axis's number.

    `wall_s` is the executor's monotonic measurement and `tokens_out` comes out
    of `usage_detail`, which usage_ledger.parse_usage_detailed produced. Neither
    is recomputed here. An outcome that did not run has neither and is filtered
    by `_ran` before this is called, rather than contributing a zero.
    """
    if axis is LATENCY:
        return lambda o: float(o.wall_s)
    if axis is TOKENS_OUT:
        return lambda o: float(o.usage_detail["tokens_out"])
    raise ValueError(
        f"no live reader for axis {axis.key!r} -- the product's executor runs "
        f"tasks and does not grade them, so quality has no live source")


def _ran(outcome):
    """True for an outcome that produced a measurement.

    Both conditions, not just `status`: a "ran" row whose `wall_s` is None would
    plot at the origin as the fastest result on the chart, which is what
    executor.TaskOutcome's docstring refuses to let None mean.
    """
    return (outcome.status == "ran" and outcome.wall_s is not None
            and outcome.usage_detail is not None)


def refreshed_value(live_values, corpus_values):
    """One point estimate for one config: the visitor's own numbers if they have
    any, the corpus's otherwise. Returns `(Value, n)`, or `(None, 0)`.

    THE MISTAKE THIS FUNCTION EXISTS TO NOT MAKE. The visitor ran three
    attempts; the corpus has six for the same config; pooling them gives nine and
    a tighter interval. It is the natural thing to reach for and it is the blend
    14a's ruling forbids -- the nine-run mean describes no population, since the
    corpus ran on our hardware against our task tree and the live three ran on
    theirs. `provenance.mean(live + corpus)` raises ProvenanceBlend rather than
    returning that number, and runner/tests/test_result_surface.py performs
    exactly this pooling against the values this function selects between, so the
    guard is known to fire on the real path and not only on a constructed pair.

    The live run REFRESHES rather than adds. That is 14b's settled hybrid, and
    the reason it is a replacement is that only one of the two describes the
    machine the visitor is about to make a decision on.
    """
    if live_values:
        return provenance.mean(live_values), len(live_values)
    if corpus_values:
        return provenance.mean(corpus_values), len(corpus_values)
    return None, 0


# --------------------------------------------------------------------------- #
# Points and the non-dominated set
# --------------------------------------------------------------------------- #


class Point(NamedTuple):
    """One (model x effort) dot. Effort is a field, never marginalized into the
    model: 14a's ruling is that every effort tier of a selected model renders as
    its own dot, so a model with four tiers is four rows here."""
    model: str
    effort: str
    x: provenance.Value
    y: provenance.Value
    n_x: int
    n_y: int

    @property
    def config(self):
        return (self.model, self.effort)


def _better(a, b, direction):
    return a < b if direction == "lower" else a > b


def dominates(a, b, x_axis, y_axis):
    """True if `a` is at least as good as `b` on both axes and strictly better on
    one. Comparison only -- two numbers are ordered, never combined -- so this
    path does not go through COMBINING_OPS and must not: a Pareto test that
    scalarized its inputs would be the composite score ticket 03 forbids.

    The axis check is not defensive clutter. Points arriving from two different
    charts would compare a token count against a latency quite happily and
    produce a frontier, and nothing downstream would look wrong.
    """
    for p in (a, b):
        if p.x.axis != x_axis.key or p.y.axis != y_axis.key:
            raise ValueError(
                f"point {p.config} carries axes ({p.x.axis}, {p.y.axis}) but "
                f"this chart is ({x_axis.key}, {y_axis.key}) -- refusing to "
                f"order values that measure different things")
    x_ge = not _better(b.x.number, a.x.number, x_axis.better)
    y_ge = not _better(b.y.number, a.y.number, y_axis.better)
    strict = (_better(a.x.number, b.x.number, x_axis.better)
              or _better(a.y.number, b.y.number, y_axis.better))
    return x_ge and y_ge and strict


def pareto_front(points, x_axis, y_axis):
    """The non-dominated configs, as a frozenset of `(model, effort)`.

    O(n^2) on purpose: the grid is a few dozen dots and the readable form of the
    definition is worth more here than the sort-and-sweep.
    """
    points = list(points)
    return frozenset(
        p.config for p in points
        if not any(dominates(q, p, x_axis, y_axis) for q in points if q is not p))


def frontier_note(chart):
    """What the non-dominated set means on THIS chart, which depends on whether
    the quality axis had any spread.

    Said out loud because a one-element frontier over a flat quality axis looks
    exactly like a winner, and ticket 03 forbids naming one. It is not a winner:
    with every config tied on quality, "non-dominated" degenerates to "cheapest",
    and the chart is reporting a cost ordering with the quality question still
    open.
    """
    n = len(chart.frontier)
    if chart.y_condition == NO_ROWS or chart.x_condition == NO_ROWS:
        return ("non-dominated set: not computed -- an axis has 0 rows, and a "
                "frontier over one measured axis is a sort, not a frontier")
    if chart.y_condition == FLAT:
        return (f"non-dominated set: {n} config(s). Every config ties on "
                f"{chart.y_axis.title}, so this reduces to the lowest "
                f"{chart.x_axis.title} -- a cost ordering, NOT a quality "
                f"verdict and not a winner")
    return (f"non-dominated set: {n} config(s) -- no other tested config beats "
            f"them on both {chart.x_axis.title} and {chart.y_axis.title} at once")


# --------------------------------------------------------------------------- #
# The chart
# --------------------------------------------------------------------------- #


class Chart(NamedTuple):
    """Everything render.py needs, and everything a reader needs counted.

    `zero_rows_reason` is None or a sentence. It is a field rather than something
    the renderer infers from an empty `points` tuple, because "why" is the half
    that makes 0 rows a result instead of a blank.
    """
    tier: Tier
    x_axis: Axis
    y_axis: Axis
    points: tuple
    frontier: frozenset
    x_condition: str
    y_condition: str
    x_provenances: tuple
    y_provenances: tuple
    corpus_inspected: int
    corpus_kept: int
    corpus_excluded: dict
    untiered: dict
    live_configs: tuple
    zero_rows_reason: str


def build_chart(corpus_rows, tier_key=DEFAULT_TIER, x_axis_key=TOKENS_OUT.key,
                models=None, live_reports=()):
    """Assemble one tab of the surface.

    `live_reports` is a sequence of executor.RunReport. Each contributes the
    visitor's own numbers for one (model, effort), which REFRESH the corpus's
    numbers for that config on the X axis and never pool with them. Quality stays
    corpus-sourced for every config, because the executor does not grade.

    `models` is the multi-select. None means every model the tier has; an empty
    sequence means the visitor deselected everything, which is a 0-row selection
    with a different reason from an unpublished tier, and both are reported as
    themselves.
    """
    assert_flatness_is_not_missing_data()
    tier = TIERS_BY_KEY[tier_key]
    x_axis = X_AXES[x_axis_key]
    y_axis = Y_AXIS

    inspected = len(corpus_rows)
    kept, excluded = corpus_gates.summarizable_rows(corpus_rows)
    by_tier, untiered = partition_by_tier(kept)
    tier_rows = by_tier.get(tier_key, [])

    live_by_config = {}
    for report in live_reports:
        for outcome in report.outcomes:
            if not _ran(outcome):
                continue
            key = (report.model_id, _effort_of(report, outcome))
            live_by_config.setdefault(key, []).append(outcome)

    available = sorted({r["model"] for r in tier_rows} | {c[0] for c in live_by_config})
    selected = available if models is None else [m for m in available if m in set(models)]

    corpus_by_config = {}
    for r in tier_rows:
        corpus_by_config.setdefault((r["model"], r["effort"]), []).append(r)

    configs = sorted({c for c in corpus_by_config if c[0] in selected}
                     | {c for c in live_by_config if c[0] in selected})

    x_read_corpus, y_read_corpus = _corpus_reader(x_axis), _corpus_reader(y_axis)
    x_read_live = _live_reader(x_axis)

    points = []
    for model, effort in configs:
        c_rows = corpus_by_config.get((model, effort), [])
        l_outs = live_by_config.get((model, effort), [])
        corpus_source = f"sealed corpus, {len(c_rows)} run(s)"
        live_source = f"your run, {len(l_outs)} attempt(s)"

        x_live = [provenance.Value(x_read_live(o), provenance.LIVE, x_axis.key,
                                   live_source) for o in l_outs]
        x_corpus = [provenance.Value(x_read_corpus(r), provenance.CORPUS,
                                     x_axis.key, corpus_source) for r in c_rows]
        x_value, n_x = refreshed_value(x_live, x_corpus)

        # Quality has no live series at all -- not an empty one. Passing [] as
        # the live argument would read as "the visitor ran and we ignored it";
        # the executor never produced a quality number to ignore.
        y_corpus = [provenance.Value(y_read_corpus(r), provenance.CORPUS,
                                     y_axis.key, corpus_source) for r in c_rows]
        y_value, n_y = refreshed_value([], y_corpus)

        if x_value is None or y_value is None:
            continue
        points.append(Point(model, effort, x_value, y_value, n_x, n_y))

    points = tuple(points)
    x_condition = classify([p.x for p in points])
    y_condition = classify([p.y for p in points])
    frontier = (pareto_front(points, x_axis, y_axis)
                if points else frozenset())

    return Chart(
        tier=tier,
        x_axis=x_axis,
        y_axis=y_axis,
        points=points,
        frontier=frontier,
        x_condition=x_condition,
        y_condition=y_condition,
        x_provenances=_provenances([p.x for p in points]),
        y_provenances=_provenances([p.y for p in points]),
        corpus_inspected=inspected,
        corpus_kept=len(kept),
        corpus_excluded=excluded,
        untiered=untiered,
        live_configs=tuple(sorted(live_by_config)),
        zero_rows_reason=_zero_rows_reason(tier, tier_rows, models, selected,
                                           points),
    )


def _effort_of(report, outcome):
    """The effort a live outcome was produced at.

    Read off the report rather than assumed, and defaulted only when the report
    predates the field -- an unknown effort silently becoming "medium" would put
    a dot on the medium rung that was never run there, and effort rungs are the
    one thing 14a's ruling refuses to see averaged.
    """
    effort = getattr(report, "effort", None)
    if effort:
        return effort
    raise ValueError(
        f"run report for {report.model_id} carries no effort -- a live point "
        f"needs the rung it was measured at, and guessing one puts a dot on a "
        f"tier nobody ran")


def _provenances(values):
    """The provenances actually present, in declaration order so the label text
    is stable between renders of the same data."""
    present = {v.provenance for v in values}
    return tuple(p for p in provenance.PROVENANCES if p in present)


def _zero_rows_reason(tier, tier_rows, models, selected, points):
    """Why this chart has no points -- None when it has some.

    The reasons are distinct because the fixes are: an unpublished tier is
    ticket 24's work, an empty multi-select is one click, and a tier with tasks
    whose rows all failed the clean-exit gate is a corpus problem. "No data"
    covers all three and tells a reader which none.
    """
    if points:
        return None
    if not tier.published:
        return (f"0 rows: the {tier.title} tier has no published tasks. "
                f"{tier.note}. This is an unauthored tier, not a failed query")
    if models is not None and not list(models):
        return ("0 rows: no models selected. Every model was deselected, so "
                "there is nothing to plot -- the corpus is intact")
    if not selected:
        return (f"0 rows: none of the selected models appear in the "
                f"{tier.title} tier")
    if not tier_rows:
        return (f"0 rows: the {tier.title} tier has published tasks but no run "
                f"survived the clean-exit gate, so every row describes a "
                f"truncated session")
    return (f"0 rows: the selection over the {tier.title} tier returned no "
            f"config with values on both axes")


# --------------------------------------------------------------------------- #
# Text the picture and the terminal both use
# --------------------------------------------------------------------------- #


def chart_axes(chart):
    """The axes this chart draws. One place, so AC#2's count has one answer."""
    return (chart.x_axis, chart.y_axis)


def axis_label(axis, provenances):
    """The label drawn beside `axis`, naming every provenance on it.

    Derived from the values plotted, never declared: the X axis carries live
    numbers for the configs the visitor ran and corpus numbers for the rest, so
    a fixed string would be wrong for most visitors and right for none of them
    twice.
    """
    if not provenances:
        return (f"{axis.title} ({axis.unit}) -- 0 rows, no source")
    return (f"{axis.title} ({axis.unit}) -- "
            + " + ".join(provenance.label(p) for p in provenances))


def axis_labels(chart):
    """One label per axis, in the same order as `chart_axes`. AC#2 counts these
    two tuples against each other; they are built here from the same chart so
    the count is a fact about the render, not about two lists kept in step."""
    return (axis_label(chart.x_axis, chart.x_provenances),
            axis_label(chart.y_axis, chart.y_provenances))


def format_row_count(chart):
    """The row tally, with 0 rendered as its own sentence.

    Follows corpus_gates.format_exclusions: a corpus with nothing excluded and a
    corpus never inspected must not print identically, and neither must a chart
    over zero rows and a chart over a full one.
    """
    if chart.zero_rows_reason:
        return chart.zero_rows_reason
    n_rows = sum(p.n_x + p.n_y for p in chart.points)
    return (f"{len(chart.points)} config(s) over {n_rows} measurement(s); "
            f"corpus inspected={chart.corpus_inspected} "
            f"kept={chart.corpus_kept}")


def honesty_lines(chart):
    """The claims that have to survive a crop, in the order they are drawn.

    Every one of these is a fact a reader needs to not be misled by the picture
    alone, which is why render.py puts them INSIDE the plotting rectangle rather
    than in a title or a caption. The list is built here, in the module with no
    third-party dependency, so a test can check the sentences without drawing.
    """
    x_label, y_label = axis_labels(chart)
    lines = [f"X | {x_label}", f"Y | {y_label}"]
    n_y = sum(p.n_y for p in chart.points)
    lines.append(flatness_note(chart.y_axis, chart.y_condition, n_y))
    lines.append(frontier_note(chart))
    lines.append(format_row_count(chart))
    return tuple(lines)


def format_chart(chart):
    """The whole tab as text. Not a fallback for the picture -- it is what a
    terminal run prints, and it carries the same sentences the picture does so
    the two cannot drift into disagreeing."""
    lines = [f"tier: {chart.tier.title} [{chart.tier.key}]"
             f"{'' if chart.tier.published else '  (no published tasks)'}",
             "tabs: " + " | ".join(
                 f"{'*' if t.key == chart.tier.key else ' '}{t.key}"
                 f"{'' if t.published else ' (empty)'}" for t in TIERS)]
    lines += list(honesty_lines(chart))
    if chart.untiered:
        lines.append("untiered tasks NOT on any tab: " + ", ".join(
            f"{k}={v} row(s)" for k, v in sorted(chart.untiered.items())))
    for p in sorted(chart.points, key=lambda p: (p.model, p.effort)):
        mark = "*" if p.config in chart.frontier else " "
        lines.append(
            f" {mark} {p.model:26s} effort={p.effort:7s} "
            f"{chart.x_axis.key}={p.x.number:10.2f} [{p.x.provenance}, n={p.n_x}] "
            f"{chart.y_axis.key}={p.y.number:.3f} [{p.y.provenance}, n={p.n_y}]")
    lines.append("  * = non-dominated. No score, no rank, no ordering below the "
                 "frontier")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The printed sentence (14b "Item 2 -- RULING", ticket 44 AC#5)
# --------------------------------------------------------------------------- #

# Ticket 08 records no verified prices and tables.py:29-30 are self-labelled
# placeholders, so spec §5 gates every slice from printing one. This is checked
# against the finished string rather than trusted to the format calls above it:
# the executor knows each outcome's cost and the report carries `spent_usd`, so
# the number is one attribute access away from anyone editing this function.
_MONEY_MARKERS = ("$", "usd", "USD", "dollar", "cent")


def printed_sentence(report, corpus_rows=(), tier_key=DEFAULT_TIER):
    """Item 2's sentence, with the visitor's own figures substituted.

    Four things it must contain, from the ruling: the latency, the output-token
    size, an explicit statement that quality is a non-result, and no price. The
    first two come from the run report -- `wall_s` measured by the executor
    around the invocation, `tokens_out` off `usage_detail`, which came from
    usage_ledger's parser. Neither is recomputed here.

    The quality non-result is TWO claims, because they fail differently. The
    corpus says every public task is saturated, so the axis cannot discriminate
    no matter how long the visitor runs. The visitor's own attempt count says
    what size of gap they could have seen if one existed. Printing only the
    second reads as "run more attempts and you will find out", which is false on
    a saturated catalog.

    A run where nothing ran gets a sentence saying so. It does not get a mean
    over zero attempts -- provenance.mean refuses that, and this returns the
    refusal as a sentence rather than propagating it as a traceback, because the
    visitor whose cap refused every task still needs a screen back.
    """
    ran = [o for o in report.outcomes if _ran(o)]
    n_total = len(report.outcomes)
    effort = getattr(report, "effort", None) or "unspecified"

    if not ran:
        statuses = ", ".join(sorted({o.status for o in report.outcomes})) or "none"
        text = (f"0 of {n_total} task(s) ran on your key for "
                f"{report.model_id} (outcome: {statuses}), so there is no "
                f"latency and no output-token size to report -- and no quality "
                f"result either: the public catalog is saturated, so a run that "
                f"had completed would still not have produced a quality "
                f"comparison.")
        return _refuse_money(text)

    tasks = ", ".join(sorted({o.task_id for o in ran}))
    latency = provenance.mean([
        provenance.Value(float(o.wall_s), provenance.LIVE, LATENCY.key,
                         "your run") for o in ran])
    tokens = provenance.mean([
        provenance.Value(float(o.usage_detail["tokens_out"]), provenance.LIVE,
                         TOKENS_OUT.key, "your run") for o in ran])

    corpus_n, corpus_passes = _corpus_quality_counts(corpus_rows, tier_key)
    mde = stats.min_detectable_effect(n=len(ran))
    mde_txt = (f"{round(mde * 100)} points" if mde is not None
               else "any gap at all -- your attempt count cannot resolve one")

    saturation = (
        f"all {corpus_passes} of {corpus_n} sealed-corpus runs on this tier "
        f"passed" if corpus_n else
        "the sealed corpus has no rows on this tier")

    text = (
        f"On {len(ran)} of {n_total} task(s) ({tasks}) at effort={effort}, on "
        f"your key: {report.model_id} finished in {latency.number:.1f}s and "
        f"wrote {tokens.number:,.0f} output tokens. "
        f"Quality is a non-result here, twice over: {saturation}, so the public "
        f"catalog is saturated and its quality axis is flat by design; and at "
        f"{len(ran)} attempt(s) you could not have seen a quality gap smaller "
        f"than {mde_txt} even on a catalog that discriminated. "
        f"This is a speed-and-size measurement, not a verdict on which model is "
        f"better.")
    return _refuse_money(text)


def _corpus_quality_counts(corpus_rows, tier_key):
    """(rows, passes) on this tier, after the instrument's clean-exit gate."""
    kept, _ = corpus_gates.summarizable_rows(list(corpus_rows))
    by_tier, _ = partition_by_tier(kept)
    rows = by_tier.get(tier_key, [])
    return len(rows), sum(1 for r in rows if stats.is_pass(r))


def _refuse_money(text):
    """Refuse to return a sentence carrying a price.

    Structural rather than remembered: spec §5 gates the whole product from
    printing a dollar figure while ticket 08 has no verified prices, and the
    cost of every outcome is sitting on the report this function was handed.
    The check is on the finished string, which is the only place a price added
    by any future edit has to pass through.
    """
    for marker in _MONEY_MARKERS:
        if marker in text:
            raise ValueError(
                f"printed sentence contains {marker!r} -- no slice may print a "
                f"price (playground spec §5; ticket 08 records no verified "
                f"prices and tables.py:29-30 are placeholders). Sentence was: "
                f"{text!r}")
    return text
