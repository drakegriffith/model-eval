#!/usr/bin/env python3
"""provenance.py -- live and corpus numbers may share a chart, never a sum
(ticket 44 AC#6, enforcing 14a's never-blend ruling).

WHAT THE RULING ACTUALLY SAYS. The playground shows two kinds of number side by
side: latencies and token counts measured during the visitor's own run, and
quality figures read off the sealed corpus. Both are real; they answer different
questions and were produced by different populations. Putting them on one chart
is the point of the surface. Adding them together -- a mean across both, a
weighted score, a normalised "overall" bar -- produces a figure that describes
no population at all and reads exactly like a measurement.

WHY THIS IS A MODULE AND NOT A CONVENTION. "Do not blend" enforced by memory
holds until the second caller. This surface already has two -- the Pareto chart
and the printed sentence -- and the third is whoever adds an "overall score"
because the chart looked bare. So a number here carries its provenance as data,
and the combining path refuses a mixed series instead of trusting its caller to
have read this docstring. Same argument the spend cap was built on (ticket 40
AC#4): a rule enforced in the renderer holds for exactly one renderer.

WHY IT IS IN THE CORE. product/ is where the chart and the sentence live, and
both import this. If the guard lived beside them, a second surface -- a notebook,
a future export, anything reaching for the same values -- would reach the
arithmetic without the refusal. runner/tests/test_provenance.py asserts the
strong form: it runs the blend in an interpreter with product/ nowhere on
sys.path and watches the refusal happen anyway.

THE REGISTRY, AND WHY EVERY OPERATION IS TAGGED. COMBINING_OPS is not a
convenience export. AC#6's negative control iterates it and requires each entry
to raise on a mixed pair, so an operation that exists but is not registered is a
blend path no test ever visits. `_combines_values = True` is the other half:
the test scans this module for public callables carrying that tag and fails on
any that COMBINING_OPS does not hold. Adding a fifth operation therefore costs
two lines -- the decorator and the registry entry -- and skipping either one is
a red test rather than a silent gap.

WHAT THIS MODULE DOES NOT DO. It does not decide which numbers belong on a
chart, and it does not make a corpus number comparable to a live one. It refuses
arithmetic across the boundary and labels which side of it a value came from.
Deciding that a quality figure is worth showing at all, flat and corpus-sourced,
is the render surface's judgment call -- this module only guarantees that the
figure cannot be quietly averaged into the latency next to it.
"""
import collections
import functools

CORE_MODULE = True

# The two populations. These strings are user-visible inside refusal messages,
# so they are the words a developer reading a traceback needs to see.
LIVE = "live"
CORPUS = "corpus"

# The closed set. A value may not carry a provenance outside it, checked at
# construction: a typo'd third bucket would blend with nothing, be refused by
# nothing, and render with no label -- three silent passes for one typo.
PROVENANCES = (LIVE, CORPUS)

# Per-provenance label text. AC#2 counts rendered axes against rendered labels,
# and that count only means something if the label says which run produced the
# number. "source A" / "source B" would satisfy a count check and tell a reader
# looking at a screenshot nothing.
_LABELS = {
    LIVE: "your run, measured just now",
    CORPUS: "sealed corpus, not your run",
}


class ProvenanceBlend(Exception):
    """Raised when a combining operation is handed values it must not combine.

    The message names both provenances AND both axes on purpose. A refusal a
    developer cannot act on is a crash with better manners: "cannot blend" sends
    them reading this module, "live latency_s + corpus quality_pass_rate" sends
    them to the call site that assembled the series.
    """


_ValueFields = collections.namedtuple("Value", "number provenance axis source")


class Value(_ValueFields):
    """A number that knows where it came from.

    number      the figure itself
    provenance  LIVE or CORPUS -- refused at construction if it is neither
    axis        what is being measured (latency_s, tokens_out, ...)
    source      human text for the label ("your run", "sealed corpus, 268 runs")

    Validation happens in __new__ rather than at a `make_value()` helper because
    a helper is one of two ways to build a value and the tuple constructor is the
    other. The refusal has to sit where the object comes into existence, or the
    undeclared provenance arrives by the route nobody guarded.

    (Spelled as a collections.namedtuple subclass rather than typing.NamedTuple
    because Python 3.14 forbids overriding __new__ in the latter -- same tuple,
    same fields, and the constructor stays checkable.)
    """

    __slots__ = ()

    def __new__(cls, number, provenance, axis, source):
        if provenance not in PROVENANCES:
            raise ValueError(
                f"undeclared provenance {provenance!r} -- a value must carry one "
                f"of {PROVENANCES}; an unlisted string is a third bucket that "
                f"blends with nothing and is therefore refused by nothing")
        return _ValueFields.__new__(cls, number, provenance, axis, source)


def label(provenance):
    """The text a chart puts next to an axis drawn from `provenance`."""
    if provenance not in PROVENANCES:
        raise ValueError(
            f"undeclared provenance {provenance!r} -- no label exists for it; "
            f"declared provenances are {PROVENANCES}")
    return _LABELS[provenance]


def _guard(values, op_name):
    """The single choke point. Every combining operation runs this first.

    Three refusals in a fixed order, because the first one that applies is the
    one worth reporting:

      1. An empty series. Zero rows is a result requiring a decision, never a
         silent default -- a mean over nothing that returns 0.0 renders as a
         measurement of zero.
      2. Mixed provenance. The ruling itself.
      3. Mixed axis. Same provenance is necessary and not sufficient: a mean of
         a latency and a token count is a category error the chart could still
         draw quite happily.
    """
    values = list(values)
    if not values:
        raise ValueError(
            f"{op_name}: no values -- refusing to combine an empty series rather "
            f"than returning a zero that renders as a measurement")

    provenances = [v.provenance for v in values]
    if len(set(provenances)) > 1:
        pairs = ", ".join(f"{v.provenance} ({v.axis})" for v in values)
        raise ProvenanceBlend(
            f"{op_name}: refusing to combine values of mixed provenance -- {pairs}. "
            f"Live and corpus numbers may share a chart and may never share an "
            f"arithmetic result (14a's never-blend ruling).")

    axes = [v.axis for v in values]
    if len(set(axes)) > 1:
        raise ProvenanceBlend(
            f"{op_name}: refusing to combine values on a different axis each -- "
            f"{', '.join(sorted(set(axes)))} (all {provenances[0]}). Same "
            f"provenance is necessary, not sufficient.")

    return values


def _combining(op_name):
    """Mark a function as a combining entry point and guard its input.

    The tag is what lets the test scan this module for arithmetic that escaped
    COMBINING_OPS. The guard is what makes registration meaningful.
    """
    def decorate(func):
        @functools.wraps(func)
        def wrapper(values, *args, **kwargs):
            return func(_guard(values, op_name), *args, **kwargs)
        wrapper._combines_values = True
        return wrapper
    return decorate


def _shared_source(values):
    """One source line for a combined value.

    Same source for every input is the common case and stays verbatim. A mix
    within one provenance (two live runs, say) is legitimate, so it is described
    rather than refused.
    """
    sources = {v.source for v in values}
    if len(sources) == 1:
        return sources.pop()
    return f"{len(values)} values: " + ", ".join(sorted(sources))


@_combining("sum")
def sum_(values):
    """Total. Named with the trailing underscore so it does not shadow the
    builtin inside this module; registered under the ruling's word, "sum"."""
    total = 0.0
    for v in values:
        total += v.number
    return Value(total, values[0].provenance, values[0].axis, _shared_source(values))


@_combining("mean")
def mean(values):
    """Average. The printed sentence needs it, so the ruling's list of guarded
    operations grows by one here rather than the sentence doing its own division
    on bare floats."""
    return Value(sum(v.number for v in values) / len(values),
                 values[0].provenance, values[0].axis, _shared_source(values))


@_combining("weighted")
def weighted(values, weights=None):
    """Weighted mean. `weights` defaults to equal weight so the operation is
    callable with a series alone -- which is what AC#6's negative control does,
    and what a caller reaching for a composite score would do."""
    if weights is None:
        weights = [1.0] * len(values)
    weights = list(weights)
    if len(weights) != len(values):
        raise ValueError(
            f"weighted: {len(weights)} weights for {len(values)} values")
    total_weight = sum(weights)
    if total_weight == 0:
        raise ValueError("weighted: weights sum to zero, no average is defined")
    number = sum(v.number * w for v, w in zip(values, weights)) / total_weight
    return Value(number, values[0].provenance, values[0].axis,
                 _shared_source(values))


@_combining("normalise")
def normalise(values):
    """Each value as a ratio of the largest in the series, provenance carried
    through so a normalised number is still labelled downstream.

    This is the operation most likely to be reached for innocently -- putting
    two axes on one 0..1 scale so they can share a bar chart -- which is exactly
    the blend the ruling forbids, hence the guard.
    """
    largest = max(abs(v.number) for v in values)
    if largest == 0:
        raise ValueError(
            "normalise: every value is zero -- there is no scale to normalise "
            "against, and returning zeros would render as a measured result")
    return [Value(v.number / largest, v.provenance, v.axis, v.source)
            for v in values]


# --------------------------------------------------------------------------- #
# THE REGISTRY. AC#6's negative control iterates this dict and requires every
# entry to refuse a mixed pair; the same test scans the module for `
# _combines_values` tags absent from it. Both directions, so neither a
# registered-but-unguarded operation nor a guarded-but-unregistered one survives.
# The keys are 14a's own vocabulary -- sum, weighted, normalise -- plus the mean
# the printed sentence needs. Changing this list is changing the ruling.
# --------------------------------------------------------------------------- #
COMBINING_OPS = {
    "sum": sum_,
    "mean": mean,
    "weighted": weighted,
    "normalise": normalise,
}
