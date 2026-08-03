#!/usr/bin/env python3
"""Emit four diverging per-task cost-delta charts as inline SVG markup strings.

These are the four cells of "which model is cheaper", each rendered as its own
figure for the Actual Intelligence Labs site. They are NOT the site's built-in
{"chart": ...} block: chalkBarChart (build/build-pages.mjs) scales by max(value)
with an 8px bar floor and cannot draw a negative bar, and every value here is a
signed per-task delta.

House style is copied from the lollipop chart in
content/research/claude-vs-gpt-154-run-experiment.json: viewBox '0 0 760 H',
Claude/Fable #2D68FF, GPT/Sol #f4f4f3, gridlines rgba(244,244,243,.12), axis
rgba(244,244,243,.28), tick labels rgba(244,244,243,.48) at 10.5px, axis title
9.5px letter-spacing .5 rgba(244,244,243,.42), legend swatches across the top.

All four charts share one x domain (+/-130%) on purpose. The whole point of the
figure set is that three framings of the same question land near zero and the
fourth does not, which only reads if the bars are drawn to the same ruler.

Numbers trace to deliverables/STATS-CURRENT-2026-08-03.md:
  cell 1 -> section 5   (all tiers pooled)
  cell 2 -> section 6b  (effort medium, both models)
  cell 3 -> section 6b  (effort high, both models)
  cell 4 -> section 6a  (each model at its winning effort)
Percent deltas are round(fable_median / sol_median - 1) on the median output
tokens columns of those tables; verify_against_stats() re-derives them.

Usage:
    python3 make_delta_charts.py            # writes delta-charts.json next to this file
    python3 make_delta_charts.py --check    # re-derive percentages from the stats tables
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

FABLE = "#2D68FF"   # Claude
SOL = "#f4f4f3"     # GPT
GRID = "rgba(244,244,243,.12)"
AXIS = "rgba(244,244,243,.28)"
TICK = "rgba(244,244,243,.48)"
TITLE = "rgba(244,244,243,.42)"
LEGEND = "rgba(244,244,243,.72)"
NEUTRAL = "rgba(244,244,243,.40)"

# geometry
W = 760
X0, X1 = 90, 700        # plot band
ZERO = 395              # x of the zero line, and the horizontal centre of the figure
HALF = 305              # px from zero to either edge
DOMAIN = 130            # percent at the plot edge; shared by all four charts
SCALE = HALF / DOMAIN
Y0 = 48                 # first row top
ROW_H = 26
BAR_H = 14

TASKS_9 = ["t1-py-a", "t1-py-b", "t1-ts-a", "t1-ts-b",
           "t2-py-a", "t2-py-b", "t2-ts-a", "t2-ts-b", "t3-a"]
TASKS_8 = TASKS_9[:-1]

# (key, caption, tasks, deltas, note)
CELLS = [
    (
        "pooled",
        "1. All tiers pooled: no detectable difference",
        TASKS_9,
        [14, -8, -14, 0, 35, 10, -3, 16, -18],
        "Every passing run of each model, across whatever effort tiers each was "
        "run at. Fable spends more on 4 of 9 tasks. Exact sign-flip permutation "
        "test on per-task log differences, p = 0.68. Stats appendix section 5.",
    ),
    (
        "medium",
        "2. Matched effort medium: still no detectable difference",
        TASKS_8,
        [12, -11, -15, 2, 70, -7, 1, 29],
        "Both models at the effort label medium, bare runs only, so nothing "
        "varies but the model. Fable spends more on 5 of 8 tasks, p = 0.43. "
        "Stats appendix section 6b.",
    ),
    (
        "high",
        "3. Matched effort high: the sign flips",
        TASKS_8,
        [-11, -22, -40, 10, 9, -5, -14, -13],
        "The same contrast one rung up. Fable now spends less on 6 of 8 tasks, "
        "the opposite direction from the medium chart above, p = 0.086. Stats "
        "appendix section 6b.",
    ),
    (
        "winning",
        "4. Each model at its own winning effort: a real gap",
        TASKS_9,
        [55, 63, 24, 106, 115, 48, 127, 89, -15],
        "Fable at medium against Sol at low, the two cells the pass-rate test "
        "uses. Fable spends more on 8 of 9 tasks, p = 0.0078. Read the cell "
        "names first: these are different tiers. Stats appendix section 6a.",
    ),
]

ARIA = {
    "pooled": "Diverging bar chart of the percent difference in median output "
              "tokens per task, Claude Fable 5 minus GPT-5.6 Sol, with all "
              "effort tiers pooled. Nine tasks. Fable spends more on four of "
              "them, from 10 to 35 percent, and less on five, from 3 to 18 "
              "percent. The bars point both ways and none of them is large. "
              "Two-sided p equals 0.68.",
    "medium": "Diverging bar chart of the percent difference in median output "
              "tokens per task, Fable minus Sol, with both models held at "
              "effort medium. Eight tasks. Fable spends more on five, "
              "including one 70 percent outlier, and less on three by 7 to 15 "
              "percent. Two-sided p equals 0.43.",
    "high":   "Diverging bar chart of the percent difference in median output "
              "tokens per task, Fable minus Sol, with both models held at "
              "effort high. Eight tasks. The direction reverses: Fable spends "
              "less on six of eight, by up to 40 percent, and more on only "
              "two. Two-sided p equals 0.086.",
    "winning": "Diverging bar chart of the percent difference in median output "
               "tokens per task, Fable at medium minus Sol at low, each model "
               "at its own winning effort. Nine tasks. Fable spends more on "
               "eight of them, by 24 to 127 percent, and less on only one, by "
               "15 percent. The bars are far longer than in the other three "
               "charts. Two-sided p equals 0.0078.",
}

# Median output tokens straight out of STATS-CURRENT-2026-08-03.md, used only by
# --check to re-derive the percentages above. Kept beside the deltas rather than
# generating from it, so a typo in either copy shows up as a mismatch.
STATS_MEDIANS = {
    "pooled": [(1120, 984), (1074, 1164), (994, 1162), (1719, 1719), (2420, 1790),
               (2862, 2600), (3210, 3293), (2679, 2318), (2461, 3013)],
    "medium": [(1105, 984), (1039, 1164), (987, 1162), (1546, 1517), (2420, 1427),
               (2326, 2498), (3379, 3344), (2679, 2069)],
    "high": [(1136, 1277), (1104, 1408), (1002, 1678), (1990, 1812), (2185, 1997),
             (2862, 2999), (3589, 4152), (2385, 2755)],
    "winning": [(1105, 715), (1039, 638), (987, 793), (1546, 752), (2420, 1128),
                (2326, 1572), (3379, 1490), (2679, 1414), (2088, 2466)],
}


def verify_against_stats():
    """Recompute every delta from the published medians. Returns list of errors."""
    errs = []
    for key, _, tasks, deltas, _ in CELLS:
        meds = STATS_MEDIANS[key]
        if len(meds) != len(deltas) or len(meds) != len(tasks):
            errs.append(f"{key}: length mismatch")
            continue
        for task, (fab, sol), got in zip(tasks, meds, deltas):
            want = round((fab / sol - 1) * 100)
            if want != got:
                errs.append(f"{key}/{task}: stats give {want:+d}%, chart says {got:+d}%")
    return errs


def x_of(pct):
    return ZERO + pct * SCALE


def chart(tasks, deltas):
    n = len(tasks)
    bottom = Y0 + n * ROW_H
    h = bottom + 50
    p = []

    # legend, centred on the zero line
    p.append(
        f"<rect x='285' y='16' width='12' height='12' rx='1.5' fill='{FABLE}'/>"
        f"<text x='303' y='26' font-size='11' font-weight='500' fill='{LEGEND}'>Fable cheaper</text>"
        f"<rect x='421' y='16' width='12' height='12' rx='1.5' fill='{SOL}'/>"
        f"<text x='439' y='26' font-size='11' font-weight='500' fill='{LEGEND}'>Sol cheaper</text>"
    )

    # vertical gridlines
    grid = "".join(
        f"<line x1='{x_of(v):.1f}' y1='{Y0 - 4}' x2='{x_of(v):.1f}' y2='{bottom}'/>"
        for v in (-100, -50, 50, 100)
    )
    p.append(f"<g stroke='{GRID}' stroke-width='1'>{grid}</g>")

    # zero line and category baseline
    p.append(f"<line x1='{ZERO}' y1='{Y0 - 8}' x2='{ZERO}' y2='{bottom + 4}' "
             f"stroke='{AXIS}' stroke-width='1.2'/>")
    p.append(f"<line x1='{X0}' y1='{bottom}' x2='{X1}' y2='{bottom}' "
             f"stroke='{AXIS}' stroke-width='1'/>")

    # rows
    labels, bars, vals = [], [], []
    for i, (task, v) in enumerate(zip(tasks, deltas)):
        cy = Y0 + i * ROW_H + ROW_H / 2
        by = cy - BAR_H / 2
        labels.append(f"<text x='80' y='{cy + 4:.0f}'>{task}</text>")
        if v == 0:
            bars.append(f"<rect x='{ZERO - 1.5:.1f}' y='{by:.0f}' width='3' "
                        f"height='{BAR_H}' rx='1.5' fill='{NEUTRAL}'/>")
            vals.append(f"<text x='{ZERO + 8:.0f}' y='{cy + 4:.0f}' text-anchor='start' "
                        f"fill='{NEUTRAL}'>0%</text>")
            continue
        colour = SOL if v > 0 else FABLE   # positive = Fable spent more = Sol cheaper
        w = abs(v) * SCALE
        x = ZERO if v > 0 else ZERO - w
        bars.append(f"<rect x='{x:.1f}' y='{by:.0f}' width='{w:.1f}' height='{BAR_H}' "
                    f"rx='1.5' fill='{colour}'/>")
        if v > 0:
            vals.append(f"<text x='{ZERO + w + 6:.1f}' y='{cy + 4:.0f}' text-anchor='start' "
                        f"fill='{colour}'>+{v}%</text>")
        else:
            vals.append(f"<text x='{ZERO - w - 6:.1f}' y='{cy + 4:.0f}' text-anchor='end' "
                        f"fill='{colour}'>{v}%</text>")

    p.append(f"<g fill='{SOL}' font-size='11.5' font-weight='600' text-anchor='end'>"
             + "".join(labels) + "</g>")
    p.append("<g>" + "".join(bars) + "</g>")
    p.append("<g font-size='10' font-weight='700'>" + "".join(vals) + "</g>")

    # x ticks
    ticks = "".join(
        f"<text x='{x_of(v):.1f}' y='{bottom + 18}'>{lab}</text>"
        for v, lab in ((-100, "-100%"), (-50, "-50%"), (0, "0"), (50, "+50%"), (100, "+100%"))
    )
    p.append(f"<g fill='{TICK}' font-size='10.5' font-weight='500' text-anchor='middle'>"
             + ticks + "</g>")

    # axis title
    p.append(f"<text x='{ZERO}' y='{bottom + 38}' text-anchor='middle' font-size='9.5' "
             f"font-weight='600' letter-spacing='.5' fill='{TITLE}'>"
             f"FABLE MINUS SOL, MEDIAN OUTPUT TOKENS PER TASK (%)</text>")

    return f"<svg viewBox='0 0 {W} {h}' xmlns='http://www.w3.org/2000/svg'>" + "".join(p) + "</svg>"


def main():
    errs = verify_against_stats()
    for e in errs:
        print("MISMATCH:", e, file=sys.stderr)
    if errs:
        return 1
    print(f"checked {sum(len(c[3]) for c in CELLS)} deltas against the stats medians: all match")
    if "--check" in sys.argv:
        return 0

    blocks = [
        {"svg": {"caption": caption,
                 "aria": ARIA[key],
                 "markup": chart(tasks, deltas),
                 "note": note}}
        for key, caption, tasks, deltas, note in CELLS
    ]
    out = HERE / "delta-charts.json"
    out.write_text(json.dumps(blocks, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({len(blocks)} svg prose blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
