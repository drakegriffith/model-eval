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
  THE EMPTY CHART IS A PICTURE, NOT A BLANK. A zero-point chart still draws,
     and what it draws states the zero-rows reason -- the AC#8 half that
     surface.py alone cannot prove, because "renders as a stated fact" is a
     claim about the canvas.

Skips cleanly when matplotlib is absent: the split in surface.py's docstring is
that only the drawing needs the third-party dependency, and this file is the
drawing's suite. In this repo matplotlib lives only in .venv, so the system
interpreter skips these and .venv runs them.
"""
import json
import os
import sys

import pytest

matplotlib = pytest.importorskip("matplotlib")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
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
        by_config = {f"{p.model} @ {p.effort} [{p.x.provenance}]": p
                     for p in chart.points}
        labels = annotations(ax)
        assert set(labels) == set(by_config)
        for text, artist in labels.items():
            on_right = by_config[text].x.number > x_mid
            assert artist.get_ha() == ("right" if on_right else "left")
            assert (artist.xyann[0] < 0) == on_right


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
