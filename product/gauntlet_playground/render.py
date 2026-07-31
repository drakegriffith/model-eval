"""render.py -- the drawing half of the result surface (ticket 44, slice S4).

surface.py builds the Chart; this module puts it on a canvas. The split is
where the third-party dependency starts: every sentence and every number below
was decided in surface.py with stdlib plus the core, so nothing here computes
-- it places. A defect in this file can put a claim in the wrong spot; it
cannot change what the claim says, and the tests that prove the claims run
without matplotlib installed.

WHY matplotlib.figure AND NOT pyplot. pyplot selects a backend at import time
and keeps a process-global figure registry, which makes "import the module" a
side effect and a headless environment a special case. Building a Figure
directly does neither: no backend is chosen until savefig, and the figure
handed back is owned by the caller, not by a global.

THE HONESTY BLOCK GOES INSIDE THE AXES RECTANGLE. surface.honesty_lines is the
list of claims a reader needs to not be misled by the picture alone -- the
provenance of each axis, the flatness of the quality axis, what the frontier
does and does not mean, and the row count. A title or caption is outside the
rectangle and is the first thing a crop or a screenshot loses; the dots are
the last. So the block is drawn in axes coordinates, inside the frame, and
whatever survives a crop of this picture survives with its caveats attached.

THE GATE IS CHECKED IN THE PROCESS THAT DRAWS. `draw` calls
surface.assert_flatness_is_not_missing_data() before it touches the canvas.
surface.py already ran it at import time and the tests prove it in the tree
they ran against; this call proves it in the process that is about to put a
picture in front of somebody, which is the process the claim is about.
"""
import textwrap

from matplotlib.figure import Figure

import provenance

from . import surface

# One fixed colour per provenance, keyed by the core's declared names so an
# undeclared provenance is a KeyError here rather than a default-coloured dot
# pretending it was classified.
_PROVENANCE_COLOR = {
    provenance.LIVE: "#c44e00",
    provenance.CORPUS: "#1f5fa8",
}

_HONESTY_WRAP_COLS = 96


def _wrap(lines):
    """Wrap each claim to the block's width, indenting continuations so the
    eye can count the claims -- five sentences must read as five lines'
    starts, not as a paragraph."""
    return "\n".join(
        textwrap.fill(line, _HONESTY_WRAP_COLS, subsequent_indent="      ")
        for line in lines)


def draw(chart):
    """One Chart onto one new Figure, returned to the caller unsaved.

    Every dot is a (model x effort) config from chart.points, coloured by the
    X value's provenance and ringed when the config is in the non-dominated
    set. A chart with zero points still draws: the empty rectangle carries the
    honesty block, whose last line is the zero-rows reason, so "nothing to
    plot" renders as a stated fact and not as a blank picture.
    """
    surface.assert_flatness_is_not_missing_data()

    fig = Figure(figsize=(9.6, 6.4))
    ax = fig.add_subplot()

    x_label, y_label = surface.axis_labels(chart)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(
        f"{chart.tier.title} [{chart.tier.key}]"
        f"{'' if chart.tier.published else '  (no published tasks)'}",
        loc="left")

    for p in chart.points:
        color = _PROVENANCE_COLOR[p.x.provenance]
        ax.scatter(p.x.number, p.y.number, s=42, color=color, zorder=3)
        if p.config in chart.frontier:
            ax.scatter(p.x.number, p.y.number, s=160, facecolors="none",
                       edgecolors="#333333", linewidths=1.2, zorder=2)
        ax.annotate(f"{p.model} @ {p.effort} [{p.x.provenance}]",
                    (p.x.number, p.y.number), textcoords="offset points",
                    xytext=(7, -3), fontsize=8)

    # The quality axis is 0-1 by declaration. Fixed limits rather than
    # autoscale: a saturated tier's dots all sit at 1.0, and autoscale would
    # zoom to a sliver around them, rendering "measured and equal" as noise.
    ax.set_ylim(-0.04, 1.08)
    if not chart.points:
        ax.set_xlim(0, 1)

    ax.text(0.015, 0.03, _wrap(surface.honesty_lines(chart)),
            transform=ax.transAxes, ha="left", va="bottom",
            fontsize=7.5, family="monospace", zorder=4,
            bbox={"boxstyle": "round,pad=0.45", "facecolor": "white",
                  "alpha": 0.85, "edgecolor": "#999999"})

    return fig


def save(chart, path):
    """Draw `chart` and write it to `path`. Returns `path` so a caller can
    print where the picture went instead of asserting from silence that it
    went somewhere."""
    draw(chart).savefig(path, dpi=144, bbox_inches="tight")
    return path
