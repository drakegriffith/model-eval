"""test_provenance.py -- the never-blend rule, proven where it lives (ticket 44).

14a's never-blend ruling says live and corpus numbers may share a chart and may
never be summed, weighted or normalised together. Ticket 44 AC#6 asks for that
to be STRUCTURAL rather than remembered: "a value carries its provenance, and the
combining path refuses mixed provenance instead of trusting the caller not to
try."

Three claims are load-bearing, and each is the kind that reads as true whether or
not anyone checked it:

  1. THE GUARD FIRES. A never-fired guard is not known to work. Every combining
     operation the ruling names -- sum, mean, weighted, normalise -- is called
     with a mixed pair and each one is watched to raise. Counted, so that an
     operation added later without a guard shows up as a count mismatch rather
     than as a test that quietly covers one fewer case.
  2. THE GUARD IS NOT THE ONLY PATH. A refusal proves nothing if the same
     arithmetic is reachable by another route, so the module is scanned for
     combining entry points and every one of them is required to be guarded.
     Otherwise "the combining path refuses" describes one function and the
     surface blends through the next one.
  3. THE GUARD IS IN THE CORE, not in the surface. Same argument the spend cap
     was built on (ticket 40 AC#4): a rule enforced in the renderer holds for
     exactly one caller, and this surface already has two -- the chart and the
     printed sentence. Asserted from an interpreter where product/ is not on the
     import path at all.

Legitimate same-provenance combining must still work; a guard that refuses
everything is not a guard, it is an outage. That direction is asserted too.
"""
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
import provenance  # noqa: E402

LIVE_A = provenance.Value(20.1, provenance.LIVE, "latency_s", "your run")
LIVE_B = provenance.Value(85.5, provenance.LIVE, "latency_s", "your run")
CORPUS_A = provenance.Value(1.0, provenance.CORPUS, "quality_pass_rate",
                            "sealed corpus, 268 runs")


# --------------------------------------------------------------------------- #
# AC#6 -- blending is structurally refused, and the refusal is SEEN.
# --------------------------------------------------------------------------- #

def test_every_combining_operation_refuses_a_mixed_pair():
    """The negative control AC#6 asks for, run over every operation the ruling
    names rather than over one representative of them."""
    mixed = [LIVE_A, CORPUS_A]
    fired = []
    for name, op in sorted(provenance.COMBINING_OPS.items()):
        with pytest.raises(provenance.ProvenanceBlend) as exc:
            op(mixed)
        assert "live" in str(exc.value) and "corpus" in str(exc.value), (
            f"{name} refused without naming both provenances, so the caller "
            f"cannot tell what it tried to blend")
        fired.append(name)

    assert sorted(fired) == ["mean", "normalise", "sum", "weighted"], (
        "the operations 14a's ruling names are sum, weighted and normalise, plus "
        "the mean the printed sentence needs -- this list is the ruling's own "
        "vocabulary and a change to it is a change to the ruling")
    assert len(fired) == 4


def test_the_refusal_names_the_axes_it_was_asked_to_combine():
    """A refusal a developer cannot act on is a crash with better manners."""
    with pytest.raises(provenance.ProvenanceBlend) as exc:
        provenance.mean([LIVE_A, CORPUS_A])
    msg = str(exc.value)
    assert "latency_s" in msg and "quality_pass_rate" in msg


def test_no_combining_entry_point_is_left_unguarded():
    """Claim 2. The module is scanned for every public function that takes a
    sequence of Values, and each one must be registered in COMBINING_OPS. A
    combining function reachable from outside but absent from the registry is a
    blend path the negative control above never visits."""
    registered = set(provenance.COMBINING_OPS.values())
    unguarded = []
    for name in dir(provenance):
        if name.startswith("_"):
            continue
        obj = getattr(provenance, name)
        if not callable(obj) or not getattr(obj, "_combines_values", False):
            continue
        if obj not in registered:
            unguarded.append(name)
    assert unguarded == [], (
        f"combining functions not registered in COMBINING_OPS, so AC#6's "
        f"negative control never exercises them: {unguarded}")


def test_a_mixed_series_is_refused_even_when_the_live_value_is_zero():
    """Zero is the value a blend guard is most likely to wave through, because
    summing it changes nothing arithmetically. It is still a blend."""
    zero_live = provenance.Value(0.0, provenance.LIVE, "tokens_out", "your run")
    with pytest.raises(provenance.ProvenanceBlend):
        provenance.sum_([zero_live, CORPUS_A])


def test_a_single_value_series_is_not_a_blend():
    """One value has one provenance. Refusing here would be a guard that fired
    on its own shadow."""
    assert provenance.mean([LIVE_A]).number == pytest.approx(20.1)
    assert provenance.mean([LIVE_A]).provenance == provenance.LIVE


def test_an_empty_series_is_refused_rather_than_returning_zero():
    """Zero rows is a result requiring a decision, never a silent default: a
    mean over nothing that returns 0.0 renders as a measurement."""
    with pytest.raises(ValueError, match="no values"):
        provenance.mean([])


# --------------------------------------------------------------------------- #
# The other direction -- same-provenance combining still works.
# --------------------------------------------------------------------------- #

def test_same_provenance_values_combine_and_keep_their_provenance():
    """A guard that refuses everything is an outage, not a control. The result
    carries the shared provenance forward so a combined value is still labelled
    downstream."""
    out = provenance.mean([LIVE_A, LIVE_B])
    assert out.number == pytest.approx((20.1 + 85.5) / 2)
    assert out.provenance == provenance.LIVE
    assert out.axis == "latency_s"
    assert provenance.sum_([LIVE_A, LIVE_B]).number == pytest.approx(105.6)


def test_combining_values_from_different_axes_is_refused_too():
    """Same provenance is necessary, not sufficient: a mean of a latency and a
    token count is a category error the chart could still draw."""
    tokens = provenance.Value(3553.0, provenance.LIVE, "tokens_out", "your run")
    with pytest.raises(provenance.ProvenanceBlend, match="axis"):
        provenance.mean([LIVE_A, tokens])


def test_normalise_over_one_provenance_returns_a_ratio_per_value():
    vals = provenance.normalise([LIVE_A, LIVE_B])
    assert [v.provenance for v in vals] == [provenance.LIVE, provenance.LIVE]
    assert vals[1].number == pytest.approx(1.0)
    assert vals[0].number == pytest.approx(20.1 / 85.5)


# --------------------------------------------------------------------------- #
# Provenance labels -- AC#2's per-axis label comes from the value, not the chart.
# --------------------------------------------------------------------------- #

def test_every_declared_provenance_has_a_label_and_they_are_distinct():
    """AC#2 counts axes against labels. That count is only meaningful if every
    provenance a value can carry has a label to contribute."""
    labels = {p: provenance.label(p) for p in provenance.PROVENANCES}
    assert len(labels) == len(provenance.PROVENANCES) == 2
    assert len(set(labels.values())) == 2, "two provenances sharing one label"
    for p, text in labels.items():
        assert text.strip(), f"{p} has an empty label"


def test_the_labels_say_which_run_the_number_came_from():
    """A label reading 'source A' / 'source B' would pass a count check and tell
    a screenshot reader nothing."""
    assert "your run" in provenance.label(provenance.LIVE).lower()
    assert "corpus" in provenance.label(provenance.CORPUS).lower()


def test_an_undeclared_provenance_is_refused_at_construction():
    """A typo'd provenance string must not become a third silent bucket that
    blends with nothing and is therefore never refused."""
    with pytest.raises(ValueError, match="provenance"):
        provenance.Value(1.0, "live-ish", "latency_s", "your run")


def test_label_refuses_an_undeclared_provenance():
    with pytest.raises(ValueError, match="provenance"):
        provenance.label("live-ish")


# --------------------------------------------------------------------------- #
# Claim 3 -- the guard is in the core, not in the surface.
# --------------------------------------------------------------------------- #

def test_provenance_is_a_declared_core_module():
    assert provenance.CORE_MODULE is True
    src = open(os.path.join(RUNNER_DIR, "provenance.py"), encoding="utf-8").read()
    assert "CORE_MODULE = True" in src


def test_the_blend_guard_fires_in_an_interpreter_that_cannot_import_the_product():
    """The strong form of claim 3. If product/ is not on sys.path and the refusal
    still happens, the rule is not being enforced by the surface."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "assert not any(%r in p for p in sys.path), 'product dir on the path'\n"
        "import provenance\n"
        "live = provenance.Value(1.0, provenance.LIVE, 'latency_s', 'your run')\n"
        "corp = provenance.Value(1.0, provenance.CORPUS, 'quality_pass_rate', 'corpus')\n"
        "try:\n"
        "    provenance.sum_([live, corp])\n"
        "except provenance.ProvenanceBlend as e:\n"
        "    print('REFUSED', e)\n"
        "else:\n"
        "    raise SystemExit('blend was allowed')\n"
    ) % (RUNNER_DIR, PRODUCT_DIR)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, cwd=REPO_ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("REFUSED"), out.stdout


def test_provenance_imports_the_stdlib_only():
    """Half A of the import gate covers this too; asserted here as well because
    this module is new and the gate's literal is a separate edit that a reviewer
    could forget."""
    import ast
    tree = ast.parse(open(os.path.join(RUNNER_DIR, "provenance.py"),
                          encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), (
        f"non-stdlib imports in a core module: "
        f"{sorted(imported - set(sys.stdlib_module_names))}")
