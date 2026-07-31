"""test_render.py -- the chart's drawing half, proven on the canvas
(ticket 44, slice S4).

test_result_surface.py proves every sentence and every number without
matplotlib installed; this file proves the two things only a render can show:

  THE PICTURE CARRIES THE CLAIMS. AC#7 is about a screenshot cropped to the
     chart alone, so the honesty block is asserted GEOMETRICALLY against the
     rendered output: after the Agg renderer draws, the block's window extent
     lies inside the axes rectangle. A block outside that rectangle -- a title,
     a caption, a figure-level text -- passes a "the text exists somewhere"
     check and is the first thing a crop loses.
  THE EFFORT BADGE IS A SHAPE (ticket 45 AC#1). Colour on a dot already means
     provenance and dies in a greyscale print, so each badge gets its own marker
     SHAPE, its label is spelled out beside the dot, and the legend that decodes
     the shapes rides inside the crop with the rest of the honesty block. The
     shapes are asserted as marker geometry, counted per point -- never as a
     colour, and never as "a square appeared somewhere".
  THE EMPTY CHART IS A PICTURE, NOT A BLANK. A zero-point chart still draws,
     and what it draws states the zero-rows reason -- the AC#8 half that
     surface.py alone cannot prove, because "renders as a stated fact" is a
     claim about the canvas.

Skips cleanly when matplotlib is absent: the split in surface.py's docstring is
that only the drawing needs the third-party dependency, and this file is the
drawing's suite. In this repo matplotlib lives only in .venv, so the system
interpreter skips these and .venv runs them.
"""
import collections
import json
import os
import sys

import pytest

matplotlib = pytest.importorskip("matplotlib")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.markers import MarkerStyle  # noqa: E402

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
import stats  # noqa: E402
from gauntlet_playground import render  # noqa: E402
from gauntlet_playground import surface  # noqa: E402


def crow(model, effort="low", passed=True, tokens_out=1000, wall_s=10.0,
         task="t1-py-a", exit_reason="ok"):
    """One sealed-corpus row -- same shape as test_result_surface.crow."""
    return {"task": task, "model": model, "effort": effort, "pass": passed,
            "tokens_out": tokens_out, "wall_s": wall_s,
            "exit_reason": exit_reason}


def spread_chart():
    """Two configs far apart on X, so one dot sits in each half of the span."""
    return surface.build_chart([crow("m-cheap", tokens_out=100),
                                crow("m-costly", tokens_out=1000)])


def badged_chart():
    """Four models, one per badge state the corpus can reach in two rungs:
    dead, backwards, weak-but-real and real. The shapes on this canvas are
    therefore four different shapes, not one repeated."""
    rows = []
    for model, rungs in (
            ("m-dead", {"low": [1000, 1000], "high": [1000, 1000]}),
            ("m-backwards", {"low": [980, 1020], "high": [690, 710]}),
            ("m-weak", {"low": [1000, 1000], "high": [1300, 1300]}),
            ("m-real", {"low": [1000, 1010], "high": [2000, 2020]})):
        for effort, samples in rungs.items():
            rows += [crow(model, effort=effort, tokens_out=t) for t in samples]
    return surface.build_chart(rows)


def label_of(point):
    """The annotation render.draw writes for one point -- restated here rather
    than imported, so the checker is not reading its expectation out of the
    file under inspection."""
    badge = surface.BADGES_BY_KEY[point.badge]
    return (f"{point.model} @ {point.effort} [{point.x.provenance}] "
            f"{badge.glyph} {badge.label}")


def rendered(fig):
    """Attach the Agg canvas, draw, and hand back its renderer -- window
    extents are undefined until a real backend has laid the figure out."""
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    return canvas.get_renderer()


def honesty_text(ax, chart):
    """The one text artist carrying the wrapped honesty block. Found by exact
    text so a second artist with a similar string is a failure, not a match."""
    expected = render._wrap(surface.honesty_lines(chart))
    matches = [t for t in ax.texts if t.get_text() == expected]
    assert len(matches) == 1, (
        f"expected exactly one honesty block, found {len(matches)}")
    return matches[0]


def annotations(ax):
    """The per-dot labels: every text artist naming a (model @ effort)."""
    return {t.get_text(): t for t in ax.texts if " @ " in t.get_text()}


class TestTheFigureBuilds:

    def test_draw_returns_one_axes_carrying_both_derived_labels(self):
        """The figure's labels are surface.axis_labels' strings, not copies:
        AC#2's count of axes against labels holds on the canvas because the
        canvas reads the same tuple the count is defined over."""
        chart = spread_chart()
        fig = render.draw(chart)
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        x_label, y_label = surface.axis_labels(chart)
        assert ax.get_xlabel() == x_label
        assert ax.get_ylabel() == y_label

    def test_every_point_gets_a_dot_and_a_label(self):
        chart = spread_chart()
        ax = render.draw(chart).axes[0]
        labels = annotations(ax)
        assert len(labels) == len(chart.points) == 2

    def test_save_writes_a_nonempty_png_and_returns_the_path(self, tmp_path):
        """Presence asserted positively: the file exists and has bytes in it,
        not merely that savefig raised nothing."""
        out = str(tmp_path / "chart.png")
        assert render.save(spread_chart(), out) == out
        assert os.path.getsize(out) > 0


class TestLabelsHangOffTheFrame:

    def test_right_half_labels_hang_left(self):
        """A dot in the right half of the span hangs its label leftward
        (ha=right, negative offset) so the text stays inside the frame; a dot
        in the left half hangs right. Asserted per dot against the dot's own X,
        not by counting alignments."""
        chart = spread_chart()
        ax = render.draw(chart).axes[0]
        xs = [p.x.number for p in chart.points]
        x_mid = (min(xs) + max(xs)) / 2
        by_config = {label_of(p): p for p in chart.points}
        labels = annotations(ax)
        assert set(labels) == set(by_config)
        for text, artist in labels.items():
            on_right = by_config[text].x.number > x_mid
            assert artist.get_ha() == ("right" if on_right else "left")
            assert (artist.xyann[0] < 0) == on_right


class TestTheBadgeIsAShapeNotAColour:
    """Ticket 45 AC#1 on the canvas.

    A badge told apart by hue is a badge that vanishes into a greyscale print
    and collides with the provenance colour that is already on the dot. So the
    assertions below read PATHS -- the actual marker geometry matplotlib drew --
    and text, never a colour.
    """

    def _marker_vertices(self, name):
        style = MarkerStyle(name)
        return style.get_path().transformed(style.get_transform()).vertices

    def _dot_collections(self, ax):
        """The scattered dots, not the frontier rings. The rings are drawn with
        `facecolors="none"`, which is what tells the two apart."""
        return [c for c in ax.collections if len(c.get_facecolors())]

    def test_the_marker_map_is_total_over_the_badges(self):
        """Every badge surface can hand out has a shape here, and no badge is
        missing -- a badge with no marker is a KeyError at draw time, which is
        the right failure but a worse place to find out."""
        assert set(render._BADGE_MARKER) == {b.key for b in surface.BADGES}
        assert len(render._BADGE_MARKER) == 6

    def test_the_six_shapes_are_six_different_shapes(self):
        """Asserted on the geometry, not on the marker letters: two distinct
        letters that render the same outline would pass a string comparison and
        fail the reader."""
        drawn = [tuple(map(tuple, self._marker_vertices(m)))
                 for m in render._BADGE_MARKER.values()]
        assert len(set(drawn)) == 6

    def test_each_dot_is_drawn_with_its_own_badge_s_shape(self):
        """Counted per point rather than "at least one square appeared": four
        models with four verdicts must put four different outlines on the
        canvas, in the right numbers."""
        chart = badged_chart()
        assert len({p.badge for p in chart.points}) == 4
        ax = render.draw(chart).axes[0]

        by_shape = {tuple(map(tuple, self._marker_vertices(name))): name
                    for name in set(render._BADGE_MARKER.values())}
        drawn = collections.Counter()
        for coll in self._dot_collections(ax):
            key = tuple(map(tuple, coll.get_paths()[0].vertices))
            assert key in by_shape, "a dot was drawn with an unknown marker"
            drawn[by_shape[key]] += 1
        expected = collections.Counter(
            render._BADGE_MARKER[p.badge] for p in chart.points)
        assert drawn == expected
        assert len(drawn) == 4

    def test_every_annotation_spells_out_the_badge_beside_the_dot(self):
        """The shape needs the legend to decode; the label does not. Both are on
        the canvas, so a crop that keeps a dot keeps an answer."""
        chart = badged_chart()
        ax = render.draw(chart).axes[0]
        labels = annotations(ax)
        assert set(labels) == {label_of(p) for p in chart.points}
        for p in chart.points:
            badge = surface.BADGES_BY_KEY[p.badge]
            assert badge.label in label_of(p)
            assert badge.glyph in label_of(p)

    def test_an_unbadged_dot_raises_rather_than_getting_a_default_shape(self):
        """The _PROVENANCE_COLOR rule, restated for shapes: a point carrying a
        badge nobody declared is a traceback, not a dot drawn in whatever the
        default marker is and read as classified."""
        chart = badged_chart()
        broken = chart._replace(
            points=(chart.points[0]._replace(badge="sideways"),))
        with pytest.raises(KeyError):
            render.draw(broken)


class TestTheHonestyBlockSurvivesACrop:

    def test_block_is_drawn_in_axes_coordinates(self):
        chart = spread_chart()
        ax = render.draw(chart).axes[0]
        assert honesty_text(ax, chart).get_transform() == ax.transAxes

    def test_block_renders_inside_the_axes_rectangle(self):
        """AC#7 against the rendered output: after the Agg backend lays the
        figure out, the block's window extent sits inside the axes frame, so a
        crop to the chart alone keeps every line -- both axis provenances (the
        X | / Y | lines) and the by-design flatness ride inside the crop."""
        chart = spread_chart()
        fig = render.draw(chart)
        ax = fig.axes[0]
        renderer = rendered(fig)
        block = honesty_text(ax, chart).get_window_extent(renderer)
        frame = ax.get_window_extent(renderer)
        assert block.x0 >= frame.x0 and block.x1 <= frame.x1
        assert block.y0 >= frame.y0 and block.y1 <= frame.y1

    def test_block_carries_provenance_and_by_design_flatness(self):
        """The two facts AC#11 says the crop must carry, read off the artist
        that the geometry test above proved lives inside the crop."""
        chart = spread_chart()
        ax = render.draw(chart).axes[0]
        text = honesty_text(ax, chart).get_text()
        assert "X |" in text and "Y |" in text
        assert "sealed corpus" in text
        assert "FLAT BY DESIGN" in text
        assert "saturated" in text

    def test_the_badge_block_rides_inside_the_crop_too(self):
        """Ticket 45 AC#1's crop half, on the chart where the block is tallest:
        four badged models, each stating its own n and corpus. The legend is
        what makes a cropped greyscale screenshot's marker shapes decodable, so
        it has to be inside the rectangle for the same reason the axis
        provenances are."""
        chart = badged_chart()
        fig = render.draw(chart)
        ax = fig.axes[0]
        renderer = rendered(fig)
        artist = honesty_text(ax, chart)
        block = artist.get_window_extent(renderer)
        frame = ax.get_window_extent(renderer)
        assert block.x0 >= frame.x0 and block.x1 <= frame.x1
        assert block.y0 >= frame.y0 and block.y1 <= frame.y1

        text = artist.get_text()
        assert "effort-dial badges" in text
        assert len(chart.badges) == 4
        for model, reading in chart.badges.items():
            assert model in text
            assert reading.badge.glyph in text
            assert reading.badge.label in text
        assert f"inspected={chart.corpus_inspected}" in text

    def test_the_block_still_fits_the_frame_over_the_whole_sealed_corpus(self):
        """The chart with the most models on it, which is the one that broke.

        Ticket 45's block grows by one basis line per badged model. Every
        synthetic chart in this file badges four models or fewer and cleared the
        frame with room to spare; the sealed corpus badges eight, and the first
        version of the block ran 100px past the bottom of the axes -- the crop
        rule failing silently, in the only chart anybody would actually publish.
        So the geometry is asserted against the real corpus and not only against
        fixtures, and the model count is asserted too, since a corpus that
        stopped loading would pass this by rendering nothing.
        """
        path = os.path.join(REPO_ROOT, "runner", "results", "results.jsonl")
        chart = surface.build_chart(stats.load_jsonl(path))
        assert len(chart.badges) >= 8
        assert len({r.badge.key for r in chart.badges.values()}) >= 3

        fig = render.draw(chart)
        ax = fig.axes[0]
        renderer = rendered(fig)
        block = honesty_text(ax, chart).get_window_extent(renderer)
        frame = ax.get_window_extent(renderer)
        assert block.x0 >= frame.x0 and block.x1 <= frame.x1
        assert block.y0 >= frame.y0 and block.y1 <= frame.y1


class TestTheEmptyChartStillDraws:

    def test_zero_points_renders_the_reason_not_a_blank(self):
        """An unpublished tier draws: no dots, but the honesty block is on the
        canvas and its row-count line is the tier's zero-rows sentence -- a
        chart over zero rows is not byte-identical to a chart over a corpus."""
        chart = surface.build_chart([], tier_key="domain-2")
        assert not chart.points
        fig = render.draw(chart)
        ax = fig.axes[0]
        rendered(fig)
        text = honesty_text(ax, chart).get_text()
        assert "0 rows" in text
        assert "no published tasks" in text
        assert not annotations(ax)

    def test_zero_point_chart_saves(self, tmp_path):
        out = str(tmp_path / "empty.png")
        render.save(surface.build_chart([], tier_key="domain-2"), out)
        assert os.path.getsize(out) > 0


class TestTheConsoleScript:

    def test_manifest_names_render_main(self):
        """Same pin test_product_boundary.py holds for the other two scripts:
        rename render.main and the installed script breaks with no test
        failing, unless this line fails first."""
        with open(os.path.join(PRODUCT_DIR, "pyproject.toml"),
                  encoding="utf-8") as f:
            manifest = f.read()
        assert ('gauntlet-playground-chart = '
                '"gauntlet_playground.render:main"') in manifest
        assert callable(render.main)

    def test_main_builds_from_a_results_file_and_saves(self, tmp_path, capsys):
        results = tmp_path / "results.jsonl"
        results.write_text("\n".join(json.dumps(r) for r in (
            crow("m-cheap", tokens_out=100),
            crow("m-costly", tokens_out=1000))) + "\n")
        out = tmp_path / "chart.png"
        assert render.main(["--results", str(results),
                            "--out", str(out)]) == 0
        assert os.path.getsize(out) > 0
        printed = capsys.readouterr().out
        assert f"saved: {out}" in printed
        assert "FLAT BY DESIGN" in printed
