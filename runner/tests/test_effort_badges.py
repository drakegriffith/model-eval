"""test_effort_badges.py -- the effort-dial badges, proven where they matter
(ticket 45).

The badges answer one question per (model x effort) entry: what does the effort
dial actually DO here? Ticket 45 exists because three different answers were
being collapsed into one word. "The knob does nothing", "the knob runs the wrong
way" and "the knob does something we cannot yet credit" are three different
pieces of advice, and a reader who cannot tell them apart acts on the wrong one.

Every acceptance criterion that a green suite would not establish on its own has
a named test below:

  AC#1 -- WITHOUT COLOUR. The badges are told apart by glyph and label here, and
     by matplotlib MARKER SHAPE in test_render.py. Nothing in either path reads
     a colour, so a greyscale print and a colour screen carry the same three
     answers. The crop half is test_render.py's, geometrically.
  AC#2 -- THE MAP IS TOTAL, WITH NO FALLBACK BUCKET. The verdict set is read out
     of core's own source by `ast` rather than restated from memory, checked
     against an independent literal here, and every member is asserted to map to
     exactly one badge. An unmapped verdict raises.
  AC#3 -- THE THRESHOLDS LIVE IN CORE, AND ARE READ AT RUNTIME. Two directions,
     because either alone is satisfiable by a lie: (a) no product file restates a
     threshold, scanned two ways with the scanner itself proven to catch a
     planted restatement; (b) monkeypatching a core constant CHANGES the badge a
     chart renders, which authoring-time copies could not do.
  AC#4/#5 -- THE BOUNDARY. No product file reaches ladder_from_results, half B of
     the import gate passes over a counted subject set, and the known-broken
     portability entry is still named.
  AC#6 -- EXACT MODEL IDS. `claude-haiku-4-5` and `claude-haiku-4-5-20251001` are
     two entries with two badges. The failure mode guarded is prefix/family
     grouping, which would silently merge a dead dial with a backwards one.
  AC#7 -- A MEASURED DEAD IS NOT AN UNRESOLVED ONE. Each badge states its n, its
     rungs and the corpus it was decided over.

No test in this file draws. Everything runs without matplotlib, which is the
split surface.py's docstring is about.
"""
import ast
import glob
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
import effort_verdict  # noqa: E402
import import_gate  # noqa: E402
from gauntlet_playground import surface  # noqa: E402

EFFORT_VERDICT_PATH = os.path.join(RUNNER_DIR, "effort_verdict.py")


# --------------------------------------------------------------------------- #
# THE VERDICT SET -- COPY 2 OF 2
# --------------------------------------------------------------------------- #
# DO NOT DRY THIS against surface.VERDICT_BADGES' keys. This tuple is the
# checker's independent copy of what core can emit (harness #5: a gate must not
# read its decision rule from the file under inspection). If the only copy lived
# in the product, a worker who deleted a badge could also delete the verdict from
# the expectation and stay green. The third reading -- `core_verdicts_by_ast`
# below -- is taken from core's source, so the three must agree: product map,
# checker literal, and core itself.
CORE_VERDICTS = ("AMBIGUOUS", "BACKWARDS", "INSUFFICIENT", "NO-OP", "REAL",
                 "UNREPLICATED")

# The names whose ONE home must be runner/effort_verdict.py (AC#3, AC#8).
THRESHOLD_NAMES = ("REAL_SPREAD", "NOOP_SPREAD", "NOISE_MARGIN",
                   "MIN_N_FOR_VERDICT", "BACKWARDS_END_RATIO", "TIER_ORDER")
NUMERIC_THRESHOLDS = ("REAL_SPREAD", "NOOP_SPREAD", "NOISE_MARGIN",
                      "MIN_N_FOR_VERDICT", "BACKWARDS_END_RATIO")

# How core spells them in its source. Asserted to be core's real spelling by
# `test_core_spells_its_thresholds_the_way_the_text_scan_looks_for` before the
# scan is trusted -- a text scan for strings that appear nowhere would pass over
# nothing and read exactly like a scan that found nothing.
CORE_SPELLINGS = ("1.50", "1.20", "2.0", "0.95")


# --------------------------------------------------------------------------- #
# Fixture ladders
# --------------------------------------------------------------------------- #

def crow(model, effort="low", passed=True, tokens_out=1000, wall_s=10.0,
         task="t1-py-a", exit_reason="ok"):
    """One sealed-corpus row -- same shape as test_result_surface.crow."""
    return {"task": task, "model": model, "effort": effort, "pass": passed,
            "tokens_out": tokens_out, "wall_s": wall_s,
            "exit_reason": exit_reason}


def ladder(model, by_effort):
    """Corpus rows for one model's whole ladder: {effort: [tokens_out, ...]}."""
    return [crow(model, effort=effort, tokens_out=tok)
            for effort, samples in by_effort.items() for tok in samples]


# One ladder per verdict core can emit. The token counts are chosen against the
# imported constants and not against remembered numbers:
#   REAL          spread over REAL_SPREAD, monotone, between-rung variation well
#                 over NOISE_MARGIN x the within-rung noise
#   NO-OP         flat, so spread sits under NOOP_SPREAD
#   BACKWARDS     spread past NOOP_SPREAD but the top rung under
#                 BACKWARDS_END_RATIO of the bottom
#   AMBIGUOUS     the same middling spread, running forwards instead
#   UNREPLICATED  a rung with fewer than MIN_N_FOR_VERDICT runs
#   INSUFFICIENT  one rung, so there is no ladder to read
LADDERS = {
    "REAL": {"low": [1000, 1010], "high": [2000, 2020]},
    "NO-OP": {"low": [1000, 1000], "high": [1000, 1000]},
    "BACKWARDS": {"low": [980, 1020], "high": [690, 710]},
    "AMBIGUOUS": {"low": [1000, 1000], "high": [1300, 1300]},
    "UNREPLICATED": {"low": [1000], "high": [2000]},
    "INSUFFICIENT": {"low": [1000, 1000]},
}


def one_model_chart(verdict, model="m-1"):
    return surface.build_chart(ladder(model, LADDERS[verdict]))


def every_verdict_corpus():
    """One model per verdict, all six on one tab."""
    rows = []
    for verdict, rungs in LADDERS.items():
        rows += ladder(f"m-{verdict.lower()}", rungs)
    return rows


# --------------------------------------------------------------------------- #
# AC#2. The badge map is total over what core can emit, with no fallback bucket
# --------------------------------------------------------------------------- #

def core_verdicts_by_ast():
    """Every string `classify()` can put in its "verdict" key, read out of core's
    source rather than restated.

    Source-read rather than imported-and-called: calling classify over a grid of
    inputs finds the verdicts that grid happens to reach, and a verdict added to
    core with no matching input in the grid would be invisible -- which is the
    silent-default failure this whole file exists to make impossible.
    """
    with open(EFFORT_VERDICT_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=EFFORT_VERDICT_PATH)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "classify")
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id == "verdict"
                        and isinstance(node.value, ast.Constant)):
                    found.add(node.value.value)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "verdict"
                        and isinstance(value, ast.Constant)):
                    found.add(value.value)
    return found


class TestTheBadgeMapIsTotal:

    def test_core_source_and_the_checker_literal_agree_on_the_verdict_set(self):
        """Three readings, and they must agree. If core grows a seventh verdict,
        this fails before any badge test does, and the failure names the verdict
        rather than showing up as a rendered blank."""
        assert core_verdicts_by_ast() == set(CORE_VERDICTS)
        assert len(CORE_VERDICTS) == 6

    def test_every_core_verdict_maps_to_exactly_one_badge(self):
        assert set(surface.VERDICT_BADGES) == set(CORE_VERDICTS)
        badged = [surface.badge_for(v) for v in CORE_VERDICTS]
        assert len({b.key for b in badged}) == len(CORE_VERDICTS), (
            "two verdicts share a badge -- the three states ticket 45 is about "
            "were collapsed back into one bucket")

    def test_the_three_named_states_are_the_three_named_verdicts(self):
        """The ticket's own words, pinned to the map: NO-OP is dead, BACKWARDS is
        backwards, AMBIGUOUS is the one we cannot yet credit."""
        assert surface.badge_for("NO-OP").key == surface.BADGE_DEAD
        assert surface.badge_for("BACKWARDS").key == surface.BADGE_BACKWARDS
        assert surface.badge_for("AMBIGUOUS").key == surface.BADGE_WEAK_BUT_REAL

    def test_badges_are_told_apart_without_colour(self):
        """AC#1's half that lives in the data module: every badge carries its own
        glyph and its own label, so a greyscale print still separates them. No
        badge field is a colour."""
        badges = surface.BADGES
        assert len(badges) == 6
        assert len({b.key for b in badges}) == 6
        assert len({b.glyph for b in badges}) == 6
        assert len({b.label for b in badges}) == 6
        assert not any("color" in f or "colour" in f for f in surface.Badge._fields)

    def test_an_unmapped_verdict_raises_rather_than_bucketing(self):
        """No default. A verdict nobody mapped is a KeyError naming it, not a
        badge borrowed from a neighbour -- advice nobody decided to give."""
        with pytest.raises(KeyError, match="SIDEWAYS"):
            surface.badge_for("SIDEWAYS")
        with pytest.raises(KeyError):
            surface.badge_for(None)

    def test_the_rule_text_map_covers_the_same_verdicts(self):
        """`badge_rule` is the second verdict-keyed map in the module; if the two
        drift, one verdict renders with the wrong threshold in its sentence."""
        for verdict in CORE_VERDICTS:
            assert surface.badge_rule(verdict)
        with pytest.raises(KeyError):
            surface.badge_rule("SIDEWAYS")

    def test_every_verdict_is_reachable_and_badged_end_to_end(self):
        """Totality asserted over subjects that actually exist, and the subjects
        are COUNTED: a loop over an empty ladder set passes byte-identically."""
        chart = surface.build_chart(every_verdict_corpus())
        seen = {}
        for verdict in CORE_VERDICTS:
            reading = chart.badges[f"m-{verdict.lower()}"]
            assert reading.verdict == verdict, (
                f"{verdict} ladder classified as {reading.verdict}")
            seen[verdict] = reading.badge.key
        assert len(seen) == 6
        assert len(set(seen.values())) == 6

    def test_every_plotted_point_carries_its_model_s_badge(self):
        """The badge on the dot and the badge in the block are one decision, not
        two that agree today."""
        chart = surface.build_chart(every_verdict_corpus())
        assert chart.points
        for p in chart.points:
            assert p.badge == chart.badges[p.model].badge.key


# --------------------------------------------------------------------------- #
# AC#3a + AC#8. No threshold literal is restated under product/
# --------------------------------------------------------------------------- #

def product_py_files():
    return sorted(glob.glob(os.path.join(PRODUCT_DIR, "**", "*.py"),
                            recursive=True))


def repo_py_files():
    paths = []
    for root in (RUNNER_DIR, PRODUCT_DIR):
        paths += [p for p in glob.glob(os.path.join(root, "**", "*.py"),
                                       recursive=True)
                  if os.sep + "__pycache__" + os.sep not in p]
    return sorted(paths)


def threshold_restatements(src, path="<synthetic>"):
    """Numeric constants equal to a core threshold WHERE THEY WOULD DECIDE
    SOMETHING: the value of an assignment to a name, or an operand of a compare.

    Scoped that way on purpose, and the scoping is tuned against a real case
    rather than guessed. render.py draws the frontier ring with
    `linewidths=1.2`, which is the same number as NOOP_SPREAD and is not a
    threshold -- it is a line width. A scan that cannot tell the two apart has
    two options, banning the number 1.2 from the product or being switched off,
    and both are worse than naming the shapes a restatement actually takes. A
    threshold that decides anything is either bound to a name or compared
    against; a styling keyword is neither.

    `test_the_scanner_catches_a_planted_restatement` runs this over both shapes
    and over the exempt one, so the exemption is a recorded decision instead of
    a gap nobody noticed.
    """
    values = {name: getattr(effort_verdict, name) for name in NUMERIC_THRESHOLDS}
    found = []

    def check(node, where):
        if not isinstance(node, ast.Constant):
            return
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return
        for name, value in sorted(values.items()):
            if node.value == value:
                found.append(f"{path}:{node.lineno}: {where} restates "
                             f"{name} ({value!r})")

    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    check(node.value, f"assignment to {target.id}")
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                check(node.value, f"assignment to {node.target.id}")
        elif isinstance(node, ast.Compare):
            for operand in [node.left] + list(node.comparators):
                check(operand, "comparison")
    return found


def module_level_assignments(path, name):
    """Line numbers where `name` is assigned at any level in `path`."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    lines = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            lines.append(node.lineno)
    return lines


class TestNoThresholdLiteralUnderProduct:

    def test_the_scanner_catches_a_planted_restatement(self):
        """The scan proven on subjects it MUST flag before it is trusted on
        subjects it finds clean. A clean run over product/ and a scan that
        matches nothing print the same thing."""
        assert threshold_restatements("NOOP = 1.2\n")
        assert threshold_restatements("if spread < 0.95:\n    pass\n")
        assert threshold_restatements("if n < 2:\n    pass\n")
        # The documented exemption, asserted rather than assumed.
        assert not threshold_restatements("ax.scatter(x, linewidths=1.2)\n")

    def test_no_product_file_restates_a_threshold_value(self):
        scanned = 0
        violations = []
        for path in product_py_files():
            scanned += 1
            with open(path, encoding="utf-8") as f:
                violations += threshold_restatements(
                    f.read(), os.path.relpath(path, REPO_ROOT))
        assert scanned >= 1, "the scan found no product files to inspect"
        assert not violations, violations

    def test_core_spells_its_thresholds_the_way_the_text_scan_looks_for(self):
        """The text scan's subjects are real: each spelling it hunts for is a
        spelling core actually uses."""
        with open(EFFORT_VERDICT_PATH, encoding="utf-8") as f:
            src = f.read()
        for spelling in CORE_SPELLINGS:
            assert spelling in src, spelling

    def test_no_product_source_carries_core_s_spelling_of_a_threshold(self):
        scanned = 0
        for path in product_py_files():
            scanned += 1
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for spelling in CORE_SPELLINGS:
                assert spelling not in src, (
                    f"{os.path.relpath(path, REPO_ROOT)} carries the literal "
                    f"{spelling} -- thresholds are imported from "
                    f"runner/effort_verdict.py, never restated")
        assert scanned >= 1

    def test_each_threshold_has_exactly_one_home_and_it_is_core(self):
        """AC#8, extended past runner/ so a restatement under product/ IS caught.
        test_effort_verdict_backwards.py holds the same property for
        BACKWARDS_END_RATIO over runner/** alone; a product copy would have sat
        outside its scan."""
        for name in THRESHOLD_NAMES:
            homes = []
            for path in repo_py_files():
                with open(path, encoding="utf-8") as f:
                    if name not in f.read():
                        continue
                if module_level_assignments(path, name):
                    homes.append(os.path.relpath(path, REPO_ROOT))
            assert homes == ["runner/effort_verdict.py"], (name, homes)

    def test_the_product_reaches_the_thresholds_by_module_attribute(self):
        """The positive half: the surface does not merely avoid the numbers, it
        names the constants. `import effort_verdict` plus attribute access, not
        `from effort_verdict import REAL_SPREAD` -- a from-import binds the value
        once and the runtime-read proof below would stop holding."""
        path = os.path.join(PRODUCT_DIR, "gauntlet_playground", "surface.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "effort_verdict", (
                    "surface.py binds a core constant at import time")
        reached = {node.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name)
                   and node.value.id == "effort_verdict"}
        assert "classify" in reached
        assert "TIER_ORDER" in reached
        assert reached & set(NUMERIC_THRESHOLDS), sorted(reached)


# --------------------------------------------------------------------------- #
# AC#3b. The product reads core AT RUNTIME, not at authoring time
# --------------------------------------------------------------------------- #

class TestTheThresholdIsReadAtRuntime:

    def test_raising_the_noop_floor_turns_a_weak_dial_dead(self, monkeypatch):
        """The behavioural half of AC#3. A copy of the number taken when this
        code was written could not do this: the same corpus renders a different
        badge because core changed its mind, which is only possible if the
        product asks core every time."""
        rows = ladder("m-1", LADDERS["AMBIGUOUS"])
        before = surface.build_chart(rows).badges["m-1"]
        assert before.badge.key == surface.BADGE_WEAK_BUT_REAL

        monkeypatch.setattr(effort_verdict, "NOOP_SPREAD", 1.4)
        after = surface.build_chart(rows)
        assert after.badges["m-1"].badge.key == surface.BADGE_DEAD
        assert "DEAD" in surface.format_chart(after)
        assert after.badges["m-1"].badge.glyph in surface.format_chart(after)

    def test_lowering_the_real_bar_credits_the_same_dial(self, monkeypatch):
        """The other direction, so the first is not a one-sided artefact."""
        rows = ladder("m-1", LADDERS["AMBIGUOUS"])
        monkeypatch.setattr(effort_verdict, "REAL_SPREAD", 1.25)
        chart = surface.build_chart(rows)
        assert chart.badges["m-1"].badge.key == surface.BADGE_REAL
        assert chart.badges["m-1"].badge.credited is True

    def test_the_rule_sentence_quotes_the_live_constant(self, monkeypatch):
        """The number a reader sees in the badge's reason is the number core is
        deciding with right now, not a copy that reads plausibly."""
        assert f"{effort_verdict.NOOP_SPREAD:.2f}" in surface.badge_rule("NO-OP")
        monkeypatch.setattr(effort_verdict, "NOOP_SPREAD", 1.42)
        assert "1.42" in surface.badge_rule("NO-OP")

    def test_the_rung_order_is_core_s_and_reversing_it_is_visible(self, monkeypatch):
        """TIER_ORDER is load-bearing: `end_ratio` is the top rung over the
        bottom, so a ladder read in the wrong order reports the reverse
        direction. Reversing core's order flips a backwards dial forwards."""
        rows = ladder("m-1", LADDERS["BACKWARDS"])
        assert surface.build_chart(rows).badges["m-1"].badge.key == \
            surface.BADGE_BACKWARDS
        monkeypatch.setattr(effort_verdict, "TIER_ORDER",
                            list(reversed(effort_verdict.TIER_ORDER)))
        flipped = surface.build_chart(rows).badges["m-1"]
        assert flipped.badge.key != surface.BADGE_BACKWARDS
        assert [e for e, _ in flipped.rungs] == ["high", "low"]


# --------------------------------------------------------------------------- #
# AC#6. Two model ids, two entries, two badges
# --------------------------------------------------------------------------- #

class TestExactModelIdsAreNotGrouped:

    def _chart(self):
        rows = (ladder("claude-haiku-4-5", LADDERS["NO-OP"])
                + ladder("claude-haiku-4-5-20251001", LADDERS["BACKWARDS"]))
        return surface.build_chart(rows)

    def test_two_ids_are_two_badged_entries(self):
        """The failure mode guarded is prefix/family grouping: `claude-haiku-4-5`
        is a prefix of `claude-haiku-4-5-20251001`, so any grouping by
        startswith, split, or family name merges a dead dial into a backwards one
        and reports one answer where the corpus holds two."""
        chart = self._chart()
        assert set(chart.badges) == {"claude-haiku-4-5",
                                     "claude-haiku-4-5-20251001"}
        assert chart.badges["claude-haiku-4-5"].badge.key == surface.BADGE_DEAD
        assert (chart.badges["claude-haiku-4-5-20251001"].badge.key
                == surface.BADGE_BACKWARDS)

    def test_both_ids_reach_the_text_surface_with_their_own_badge(self):
        chart = self._chart()
        text = surface.format_chart(chart)
        assert "claude-haiku-4-5-20251001" in text
        dead = surface.badge_for("NO-OP")
        backwards = surface.badge_for("BACKWARDS")
        assert dead.label in text and backwards.label in text
        assert dead.glyph in text and backwards.glyph in text
        assert {p.model for p in chart.points} == set(chart.badges)

    def test_the_two_ids_keep_separate_dots(self):
        chart = self._chart()
        by_model = {}
        for p in chart.points:
            by_model.setdefault(p.model, set()).add(p.badge)
        assert by_model == {"claude-haiku-4-5": {surface.BADGE_DEAD},
                            "claude-haiku-4-5-20251001":
                                {surface.BADGE_BACKWARDS}}


# --------------------------------------------------------------------------- #
# AC#7. A measured dead is tellable from an unresolved one
# --------------------------------------------------------------------------- #

class TestEachBadgeStatesItsBasis:

    def test_the_badge_line_states_n_rungs_tier_and_corpus(self):
        chart = one_model_chart("NO-OP")
        line = surface.badge_line(chart.badges["m-1"])
        assert "m-1" in line
        assert "n=4" in line
        assert "low=n2" in line and "high=n2" in line
        assert chart.tier.key in line
        assert f"inspected={chart.corpus_inspected}" in line
        assert f"kept={chart.corpus_kept}" in line

    def test_a_measured_dead_reads_differently_from_an_unresolved_one(self):
        """The point of the whole ticket in one assertion: a dial measured flat
        at n=2 per rung and a dial nobody replicated must not print the same
        sentence. Both state their n, and the n is what separates them."""
        dead = surface.badge_line(one_model_chart("NO-OP").badges["m-1"])
        unresolved = surface.badge_line(
            one_model_chart("UNREPLICATED").badges["m-1"])
        assert dead != unresolved
        assert "DEAD" in dead and "DEAD" not in unresolved
        assert "UNREPLICATED" in unresolved
        assert "low=n2" in dead and "low=n1" in unresolved
        rule = surface.badge_rule("UNREPLICATED")
        assert str(effort_verdict.MIN_N_FOR_VERDICT) in rule
        assert any(rule in line for line in surface.badge_lines(
            one_model_chart("UNREPLICATED")))

    def test_an_insufficient_ladder_says_how_many_rungs_it_had(self):
        line = surface.badge_line(one_model_chart("INSUFFICIENT").badges["m-1"])
        assert "INSUFFICIENT" in line
        assert "n=2" in line
        assert "low=n2" in line and "high" not in line

    def test_an_effort_core_does_not_rank_is_counted_not_dropped(self):
        """A rung outside TIER_ORDER cannot enter the verdict, and a row that
        left the calculation without being counted is a row nobody knows is
        missing."""
        rows = (ladder("m-1", LADDERS["NO-OP"])
                + [crow("m-1", effort="turbo", tokens_out=9000)])
        reading = surface.build_chart(rows).badges["m-1"]
        assert reading.off_ladder == (("turbo", 1),)
        line = surface.badge_line(reading)
        assert "turbo=n1" in line
        assert "NOT-ON-LADDER" in line

    def test_zero_badged_models_is_a_sentence_not_a_silence(self):
        """Zero is a result requiring a decision, never a blank stretch of the
        block: an unpublished tier badges nothing, and says so."""
        chart = surface.build_chart([], tier_key="domain-2")
        assert chart.badges == {}
        block = "\n".join(surface.badge_lines(chart))
        assert "0 model(s) badged" in block
        assert "no published tasks" in block

    def test_the_legend_names_every_badge_so_a_crop_can_decode_a_glyph(self):
        chart = one_model_chart("NO-OP")
        legend = surface.badge_lines(chart)[0]
        for badge in surface.BADGES:
            assert badge.glyph in legend
            assert badge.label in legend
        assert "colour" in legend or "color" in legend

    def test_the_badge_block_rides_inside_the_honesty_lines(self):
        """AC#1's crop constraint at the data layer: render.py draws
        honesty_lines INSIDE the axes rectangle, so anything in that tuple
        survives a crop. The geometry itself is test_render.py's."""
        chart = one_model_chart("BACKWARDS")
        lines = surface.honesty_lines(chart)
        for line in surface.badge_lines(chart):
            assert line in lines

    def test_no_badge_text_carries_a_money_word(self):
        """Every badge string goes through the printed sentence's own price gate.
        The badges talk about spend, which is one careless word away from a price
        the product is barred from printing (spec §5)."""
        chart = surface.build_chart(every_verdict_corpus())
        texts = [b.glyph + " " + b.label + " " + b.meaning
                 for b in surface.BADGES]
        texts += [surface.badge_rule(v) for v in CORE_VERDICTS]
        texts += list(surface.badge_lines(chart))
        assert len(texts) >= 6 + 6 + 1
        for text in texts:
            assert surface._refuse_money(text) == text


# --------------------------------------------------------------------------- #
# AC#4 + AC#5. The boundary the badges must not cross
# --------------------------------------------------------------------------- #

class TestTheBoundaryHolds:

    def test_no_product_file_imports_ladder_from_results(self):
        """The ladder reader is non-core and manipulates sys.path; the badges get
        their classification from effort_verdict, which is core."""
        scanned = 0
        for path in product_py_files():
            scanned += 1
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            assert "ladder_from_results" not in import_gate.top_level_imports(tree), path
        assert scanned >= 1

    def test_half_b_passes_over_a_counted_subject_set(self):
        direction = import_gate.check_product_depends_on_core_only()
        assert direction["status"] == import_gate.PASS
        assert len(direction["subjects"]) >= 1
        assert not direction["violations"]

    def test_the_surface_reaches_effort_verdict_by_bare_name(self):
        """Importing through `runner.` would reach the gate as the name `runner`
        and pass unnoticed -- test_product_boundary.py's standing argument,
        restated for the import this ticket adds."""
        path = os.path.join(PRODUCT_DIR, "gauntlet_playground", "surface.py")
        with open(path, encoding="utf-8") as f:
            imports = import_gate.top_level_imports(ast.parse(f.read()))
        assert "effort_verdict" in imports
        assert "runner" not in imports

    def test_the_known_broken_portability_entry_is_untouched(self):
        """AC#5. A green badge run must not read as clearing the F3 site."""
        assert any(site.startswith("runner/ladder_from_results.py:40-41")
                   for site in import_gate.KNOWN_BROKEN_PORTABILITY)
