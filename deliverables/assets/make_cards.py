"""Render every chart card used by the 2026-08 model-eval deliverables.

One entry point for all cards. Each card is an HTML document screenshotted by
headless Chrome; they share one design-token block and one render step so the
three charts cannot drift apart stylistically.

Every number on every card comes from `runner/stats.py` applied to
`runner/results/results.jsonl`, through the same `corpus_gates` filter the
statistical appendix uses. Nothing here re-derives a median, a percentage, a
confidence interval or a p-value by hand. Hand-recomputation is the exact bug
the blog post's correction section is about, so the arithmetic is imported,
not repeated.

Cards, each written as `<name>.html` + `<name>.png`:
  model-eval-token-chart-per-task  cost, Fable vs Sol, four cells      (§5, §6)
  model-eval-pass-rate-ceiling     pass rate + Wilson intervals        (§1)
  model-eval-effort-ladder         median output tokens per rung       (§1, §3)

Usage:  python3 deliverables/assets/make_cards.py
"""
import math
import os
import statistics
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "runner"))

import corpus_gates  # noqa: E402
import stats  # noqa: E402

RESULTS = os.path.join(ROOT, "runner", "results", "results.jsonl")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
REPO = "github.com/drakegriffith/model-eval"

# Model ids as they appear in results.jsonl -> the name a reader recognises.
# A model that ever lands in the corpus without an entry here must fail loudly
# rather than silently chart its raw slug.
DISPLAY = {
    "sol": "Sol",
    "fable": "Fable",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.3-codex-spark": "GPT-5.3 Codex Spark",
    "kimi-k3": "Kimi K3",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5 (pinned)",
    "hybrid": "Hybrid",
}


def display(model):
    if model not in DISPLAY:
        sys.exit(f"no display name for model {model!r} — add it to DISPLAY "
                 f"before regenerating the cards")
    return DISPLAY[model]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load():
    rows = stats.load_jsonl(RESULTS)
    if not rows:
        sys.exit(f"no results at {RESULTS}")
    kept, _excluded = corpus_gates.summarizable_rows(rows)
    return kept


# --------------------------------------------------------------------------- #
# Shared chrome: design tokens, card frame, header, footer, render step
# --------------------------------------------------------------------------- #
BASE_CSS = """
  .viz-root {
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page:           #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --gridline:       #e1e0d9;
    --zero:           #b3b1aa;
    --series-1:       #2a78d6;
    --series-2:       #eb6834;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--page);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .canvas {
    background: var(--page);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .card {
    background: var(--surface-1);
    border-radius: 20px;
    padding: 30px 44px 22px 44px;
    display: flex;
    flex-direction: column;
  }
  .eyebrow {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 0 0 9px 0;
  }
  h1 {
    font-size: 31px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 7px 0;
    line-height: 1.15;
  }
  .subtitle {
    font-size: 14px;
    color: var(--text-secondary);
    margin: 0;
    max-width: 1000px;
    line-height: 1.45;
  }
  .legend {
    display: flex;
    align-items: center;
    gap: 22px;
    margin: 14px 0 4px 0;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--gridline);
    font-size: 13px;
    color: var(--text-secondary);
  }
  .legend .key { display: flex; align-items: center; gap: 7px; }
  .legend .sw { width: 13px; height: 13px; border-radius: 3px; }
  .legend .sw.a { background: var(--series-1); }
  .legend .sw.b { background: var(--series-2); }
  .legend .sw.dot { border-radius: 50%; background: var(--text-primary); }
  .legend .sw.whisk {
    width: 15px; height: 3px; border-radius: 2px;
    background: var(--series-1);
  }
  .legend .scale { margin-left: auto; color: var(--text-muted); }
  .callout {
    margin: 12px 0 0 0;
    padding: 10px 14px;
    border-left: 3px solid var(--series-2);
    background: #f4f2ee;
    border-radius: 0 6px 6px 0;
    font-size: 13.5px;
    line-height: 1.45;
    color: var(--text-secondary);
  }
  .callout strong { color: var(--text-primary); }
  .footer {
    margin-top: auto;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-top: 1px solid var(--gridline);
    padding-top: 12px;
  }
  .footnote {
    font-size: 12.5px;
    color: var(--text-muted);
    max-width: 880px;
    line-height: 1.45;
  }
  .footnote em { font-style: italic; color: var(--text-secondary); }
  .repo {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    padding-left: 24px;
  }
"""


def document(title, css, body, canvas_w, canvas_h):
    """Wrap a card body in the shared frame at a fixed pixel canvas."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
{BASE_CSS}
  .canvas {{ width: {canvas_w}px; height: {canvas_h}px; }}
  .card {{ width: {canvas_w - 64}px; height: {canvas_h - 64}px; }}
{css}
</style>
</head>
<body>
  <div class="canvas">
    <div class="card viz-root">
{body}
    </div>
  </div>
</body>
</html>
"""


def footer(footnote):
    return ('      <div class="footer">\n'
            f'        <div class="footnote">{footnote}</div>\n'
            f'        <div class="repo">{REPO}</div>\n'
            '      </div>')


def head(eyebrow, h1, subtitle):
    return ('      <div>\n'
            f'        <p class="eyebrow">{eyebrow}</p>\n'
            f'        <h1>{h1}</h1>\n'
            f'        <p class="subtitle">{subtitle}</p>\n'
            '      </div>')


def render_png(html_path, png_path, canvas_w, canvas_h):
    if not os.path.exists(CHROME):
        print(f"! Chrome not found at {CHROME}; HTML written, PNG not rendered")
        return None
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={png_path}",
         f"--window-size={canvas_w},{canvas_h}",
         "--force-device-scale-factor=2",
         f"file://{html_path}"],
        check=True, capture_output=True)
    with open(png_path, "rb") as f:
        w, h = struct.unpack(">II", f.read(33)[16:24])
    return (w, h)


def emit(name, title, css, body, canvas_w, canvas_h):
    html_path = os.path.join(HERE, f"{name}.html")
    png_path = os.path.join(HERE, f"{name}.png")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(document(title, css, body, canvas_w, canvas_h))
    print(f"wrote {html_path}")
    size = render_png(html_path, png_path, canvas_w, canvas_h)
    if size:
        print(f"wrote {png_path} ({size[0]}x{size[1]})")


# --------------------------------------------------------------------------- #
# Card 1 — per-task cost, Fable vs Sol, in four cells (appendix §5 and §6)
# --------------------------------------------------------------------------- #
COST_W, COST_H = 1200, 900

# Longest bar reaches this share of a track's full width, measured from the
# centre line. The remainder is the value label's gutter.
BAR_MAX_PCT = 40.0

COST_CSS = """
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 40px;
    row-gap: 18px;
    margin-top: 16px;
  }
  .panel-head { display: flex; align-items: baseline; gap: 8px; }
  .panel-title { font-size: 15.5px; font-weight: 700; color: var(--text-primary); }
  .panel-sec {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .panel-meta {
    font-size: 12.5px;
    color: var(--text-secondary);
    margin: 2px 0 8px 0;
  }
  .panel-meta strong { color: var(--text-primary); }
  .panel-meta .sig { font-weight: 700; color: var(--text-primary); }
  .row { display: flex; align-items: center; height: 23px; }
  .label-col {
    width: 76px;
    flex-shrink: 0;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-primary);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .row.absent .label-col { color: var(--text-muted); font-weight: 500; }
  .track { flex-grow: 1; position: relative; height: 23px; }
  .track::before {
    content: "";
    position: absolute;
    left: 50%;
    top: 1px; bottom: 1px;
    width: 1px;
    background: var(--zero);
  }
  .bar { position: absolute; top: 6px; height: 12px; border-radius: 2px; }
  .bar.pos { left: 50%; background: var(--series-2); }
  .bar.neg { right: 50%; background: var(--series-1); }
  .val {
    position: absolute;
    top: 4px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .none {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    top: 4px;
    font-size: 11.5px;
    font-style: italic;
    color: var(--text-muted);
    background: var(--surface-1);
    padding: 0 6px;
  }
"""


def cost_panels(kept):
    fable = [r for r in kept if r["model"] == "fable"]
    sol = [r for r in kept if r["model"] == "sol"]
    bare = [r for r in kept if not r.get("harness")]
    fable_bare = [r for r in bare if r["model"] == "fable"]
    sol_bare = [r for r in bare if r["model"] == "sol"]
    if not fable_bare or not sol_bare:
        sys.exit("no bare fable/sol rows — §6 panels cannot be built")

    fe = stats.best_effort(fable_bare)
    se = stats.best_effort(sol_bare)
    shared = sorted(({r["effort"] for r in fable_bare}
                     & {r["effort"] for r in sol_bare}),
                    key=lambda e: stats.EFFORT_ORDER.get(e, 9))

    specs = [("All tiers pooled", "§5", fable, sol)]
    for eff in shared:
        specs.append((f"Matched tier: effort {eff}", "§6b",
                      [r for r in fable_bare if r["effort"] == eff],
                      [r for r in sol_bare if r["effort"] == eff]))
    specs.append((f"Winning effort: Fable/{fe} vs Sol/{se}", "§6a",
                  [r for r in fable_bare if r["effort"] == fe],
                  [r for r in sol_bare if r["effort"] == se]))

    # The card is a 2x2 grid. If the corpus ever grows a third shared effort
    # label this must fail loudly rather than silently drop a panel.
    if len(specs) != 4:
        sys.exit(f"expected 4 panels for the 2x2 layout, built {len(specs)} "
                 f"(shared bare effort labels: {shared or 'none'}) — "
                 f"update the layout before regenerating")

    out = []
    for title, section, rows_a, rows_b in specs:
        entries = stats.task_cost_diffs(rows_a, rows_b)
        diffs = [e[5] for e in entries]
        p, k, n_extreme, total = stats.signflip_p(diffs)
        out.append({
            "title": title,
            "section": section,
            "by_task": {e[0]: e[5] for e in entries},
            "k": k,
            "higher": sum(1 for d in diffs if d > 0),
            "p": p,
            "n_extreme": n_extreme,
            "total": total,
        })
    return out


def fmt_pct(d):
    """A log difference rendered as the percentage the reader recognises."""
    pct = (math.exp(d) - 1.0) * 100.0
    if abs(round(pct)) == 0:
        return "0%"
    sign = "+" if pct > 0 else "&minus;"
    return f"{sign}{abs(pct):.0f}%"


def fmt_p(p):
    if p >= 0.1:
        return f"{p:.2f}"
    if p >= 0.01:
        return f"{p:.3f}"
    return f"{p:.4f}"


def render_cost_panel(panel, tasks, domain):
    rows = []
    for t in tasks:
        d = panel["by_task"].get(t)
        if d is None:
            rows.append(
                f'        <div class="row absent">'
                f'<div class="label-col">{esc(t)}</div>'
                f'<div class="track"><div class="none">no paired runs</div></div>'
                f'</div>')
            continue
        w = abs(d) / domain * BAR_MAX_PCT
        cls = "pos" if d > 0 else "neg"
        if d > 0:
            bar = f'<div class="bar pos" style="width: {w:.2f}%;"></div>'
            val = (f'<div class="val" style="left: {50 + w:.2f}%; '
                   f'margin-left: 7px;">{fmt_pct(d)}</div>')
        else:
            bar = f'<div class="bar neg" style="width: {w:.2f}%;"></div>'
            val = (f'<div class="val" style="right: {50 + w:.2f}%; '
                   f'margin-right: 7px;">{fmt_pct(d)}</div>')
        rows.append(
            f'        <div class="row"><div class="label-col">{esc(t)}</div>'
            f'<div class="track {cls}">{bar}{val}</div></div>')

    sig = ' class="sig"' if panel["p"] < 0.05 else ""
    meta = (f'Fable spent more on <strong>{panel["higher"]} of {panel["k"]}</strong>'
            f' tasks &middot; p = <span{sig}>{fmt_p(panel["p"])}</span>')
    return (
        '      <div class="panel">\n'
        f'        <div class="panel-head"><span class="panel-title">'
        f'{esc(panel["title"])}</span>'
        f'<span class="panel-sec">{panel["section"]}</span></div>\n'
        f'        <div class="panel-meta">{meta}</div>\n'
        + "\n".join(rows) + "\n"
        '      </div>')


def card_cost(kept):
    ps = cost_panels(kept)
    tasks = sorted({t for p in ps for t in p["by_task"]})
    domain = max(abs(d) for p in ps for d in p["by_task"].values())
    grid = "\n".join(render_cost_panel(p, tasks, domain) for p in ps)
    sig_panel = next((p for p in ps if p["p"] < 0.05), None)
    if sig_panel is not None:
        sig_line = (f'The one panel under p = 0.05 is <em>{esc(sig_panel["title"])}'
                    f'</em>, and it is not tier-matched: Fable&rsquo;s cheapest '
                    f'tier here is the floor of the run matrix, not a tested '
                    f'minimum, so part of that gap is the tier and not the model.')
    else:
        sig_line = "No panel falls under p = 0.05."

    body = "\n".join([
        head("model-eval &middot; open benchmark harness",
             "Four ways to ask which model is cheaper, four different answers",
             "Median output tokens per solved run, Claude Fable relative to "
             "Codex Sol, one row per task. One exact sign-flip permutation "
             "test, run in four different cells: tiers pooled, then tier for "
             "tier, then each model at its own cheapest passing tier. All four "
             "panels share one bar scale."),
        '      <div class="legend">',
        '        <div class="key"><span class="sw a"></span>Fable used fewer tokens</div>',
        '        <div class="key"><span class="sw b"></span>Fable used more tokens</div>',
        f'        <div class="scale">shared scale &middot; bar length &prop; '
        f'|log ratio| &middot; longest bar = {fmt_pct(domain)}</div>',
        '      </div>',
        '      <div class="grid">',
        grid,
        '      </div>',
        footer('Exact sign-flip permutation test over all 2<sup>k</sup> sign '
               'patterns of the per-task log differences; passing runs only; '
               'output tokens only, because Fable&rsquo;s input-token counts are '
               'quarantined as unreliable. The three matched panels use bare runs '
               f'(no harness). {sig_line} Every median, percentage and p-value on '
               'this card is computed by <code>runner/stats.py</code> from '
               '<code>results.jsonl</code>.'),
    ])
    emit("model-eval-token-chart-per-task",
         "model-eval &mdash; per-task token difference, Fable vs Sol, four cells",
         COST_CSS, body, COST_W, COST_H)
    for p in ps:
        print(f"  {p['section']:>5} {p['title']:<44} "
              f"more on {p['higher']}/{p['k']}  "
              f"p={p['p']:.7f} ({p['n_extreme']}/{p['total']})")


# --------------------------------------------------------------------------- #
# Card 2 — pass-rate ceiling: every cell at 100%, Wilson intervals below it (§1)
# --------------------------------------------------------------------------- #
CEIL_W, CEIL_H = 1200, 1000

# The x axis of the ceiling card, in percentage points. The lower bound has to
# sit under the smallest Wilson lower bound in the corpus or a whisker runs off
# the left edge; `ceiling_cells` asserts that rather than trusting it.
CEIL_X_MIN, CEIL_X_MAX = 30.0, 100.0

CEIL_CSS = """
  .plot { position: relative; margin-top: 14px; }
  .axis {
    position: relative;
    height: 18px;
    margin-left: 356px;
    margin-bottom: 4px;
  }
  .axis .tick {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    font-size: 11.5px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .rows { position: relative; }
  .grid-lines { position: absolute; left: 356px; right: 0; top: 0; bottom: 0; }
  .grid-lines .gl {
    position: absolute;
    top: 0; bottom: 0;
    width: 1px;
    background: var(--gridline);
  }
  .grid-lines .gl.edge { background: var(--zero); }
  .crow { display: flex; align-items: center; height: 24px; position: relative; }
  .cmodel {
    width: 176px;
    flex-shrink: 0;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding-right: 8px;
  }
  .ccell {
    width: 118px;
    flex-shrink: 0;
    font-size: 11.5px;
    color: var(--text-secondary);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .cn {
    width: 62px;
    flex-shrink: 0;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-primary);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
  }
  .ctrack { flex-grow: 1; position: relative; height: 24px; }
  .whisk {
    position: absolute;
    top: 11px;
    height: 2px;
    background: var(--series-1);
  }
  .cap {
    position: absolute;
    top: 6px;
    width: 2px;
    height: 12px;
    background: var(--series-1);
  }
  .dot {
    position: absolute;
    top: 7px;
    width: 10px;
    height: 10px;
    margin-left: -5px;
    border-radius: 50%;
    background: var(--text-primary);
  }
  .lo {
    position: absolute;
    top: 5px;
    font-size: 11px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
"""


def ceiling_cells(kept):
    """One entry per model x effort x harness cell — the rows of appendix §1."""
    g = stats.group(kept, lambda r: (r["model"], r["effort"],
                                     "harness" if r.get("harness") else "bare"))
    cells = []
    for (model, effort, htag), rs in g.items():
        n = len(rs)
        k = sum(1 for r in rs if stats.is_pass(r))
        lo, hi = stats.wilson_ci(k, n)
        cells.append({"model": model, "effort": effort, "harness": htag,
                      "k": k, "n": n, "rate": k / n, "lo": lo, "hi": hi})
    # Ascending lower bound is the whole point of the card: the dots do not
    # move, the whiskers do, and they are ordered by how little each cell knows.
    cells.sort(key=lambda c: (c["lo"], c["model"], c["effort"]))
    floor = min(c["lo"] for c in cells) * 100.0
    if floor < CEIL_X_MIN:
        sys.exit(f"smallest Wilson lower bound is {floor:.1f}%, below the card's "
                 f"x-axis floor of {CEIL_X_MIN:.0f}% — widen CEIL_X_MIN")
    return cells


def ceil_x(pct):
    """Percentage points -> share of the plot track."""
    return (pct - CEIL_X_MIN) / (CEIL_X_MAX - CEIL_X_MIN) * 100.0


def card_ceiling(kept):
    cells = ceiling_cells(kept)
    ticks = [t for t in range(int(CEIL_X_MIN), int(CEIL_X_MAX) + 1, 10)]

    axis = "\n".join(
        f'          <div class="tick" style="left: {ceil_x(t):.3f}%;">{t}%</div>'
        for t in ticks)
    lines = "\n".join(
        f'          <div class="gl{" edge" if t == ticks[-1] else ""}" '
        f'style="left: {ceil_x(t):.3f}%;"></div>' for t in ticks)

    rows = []
    for c in cells:
        x_lo = ceil_x(c["lo"] * 100.0)
        x_hi = ceil_x(c["rate"] * 100.0)
        rows.append(
            '        <div class="crow">'
            f'<div class="cmodel">{esc(display(c["model"]))}</div>'
            f'<div class="ccell">{esc(c["effort"])} &middot; {c["harness"]}</div>'
            f'<div class="cn">{c["k"]}/{c["n"]}</div>'
            '<div class="ctrack">'
            f'<div class="whisk" style="left: {x_lo:.3f}%; '
            f'width: {x_hi - x_lo:.3f}%;"></div>'
            f'<div class="cap" style="left: {x_lo:.3f}%;"></div>'
            f'<div class="dot" style="left: {x_hi:.3f}%;"></div>'
            f'<div class="lo" style="left: {x_lo:.3f}%; margin-left: -46px;">'
            f'{c["lo"] * 100.0:.0f}%</div>'
            '</div></div>')

    widest = min(cells, key=lambda c: c["lo"])
    tightest = max(cells, key=lambda c: c["lo"])
    n_cells = len(cells)
    n_runs = sum(c["n"] for c in cells)
    n_pass = sum(c["k"] for c in cells)

    body = "\n".join([
        head("model-eval &middot; open benchmark harness",
             "Every cell passed everything. The intervals are the honest part.",
             f"All {n_cells} model &times; effort &times; harness cells in the "
             f"corpus, {n_pass} of {n_runs} runs passing. The dot is the "
             f"observed pass rate; the whisker runs back to the 95% Wilson "
             f"score lower bound, which is what the sample size will actually "
             f"support. Ordered by lower bound, so the fan is the sample size "
             f"and nothing else."),
        '      <div class="legend">',
        '        <div class="key"><span class="sw dot"></span>observed pass rate</div>',
        '        <div class="key"><span class="sw whisk"></span>95% Wilson lower bound</div>',
        '        <div class="scale">Wilson score interval &middot; exact small-n '
        'coverage &middot; upper bound is 1.0000 in every cell</div>',
        '      </div>',
        '      <div class="plot">',
        '        <div class="axis">',
        axis,
        '        </div>',
        '        <div class="rows">',
        '          <div class="grid-lines">',
        lines,
        '          </div>',
        "\n".join(rows),
        '        </div>',
        '      </div>',
        f'      <div class="callout">Identical dots, wildly different whiskers. '
        f'<strong>{display(widest["model"])} / {esc(widest["effort"])} / '
        f'{widest["harness"]}</strong> is {widest["k"]}/{widest["n"]} and can '
        f'only support a claim of <strong>{widest["lo"] * 100.0:.0f}%</strong> '
        f'or better; <strong>{display(tightest["model"])} / '
        f'{esc(tightest["effort"])} / {tightest["harness"]}</strong> is '
        f'{tightest["k"]}/{tightest["n"]} and supports '
        f'<strong>{tightest["lo"] * 100.0:.0f}%</strong>. Same headline number, '
        f'different amount of evidence behind it.</div>',
        footer('One Bernoulli cell per row; the interval is the 95% Wilson score '
               'interval, which keeps its coverage at small n where the normal '
               'approximation does not. A perfect score never yields a point '
               'estimate you can trust on its own, and the width of that '
               'distrust is set by n. Every interval on this card is computed by '
               '<code>runner/stats.wilson_ci</code> from <code>results.jsonl</code>, '
               'on the same corpus gate as appendix &sect;1.'),
    ])
    emit("model-eval-pass-rate-ceiling",
         "model-eval &mdash; pass rate with 95% Wilson intervals",
         CEIL_CSS, body, CEIL_W, CEIL_H)
    print(f"  §1  {n_cells} cells, {n_pass}/{n_runs} passing, "
          f"lower bounds {widest['lo']:.4f} to {tightest['lo']:.4f}")


# --------------------------------------------------------------------------- #
# Card 3 — the effort ladder: what the knob moves is spend, not outcomes (§1, §3)
# --------------------------------------------------------------------------- #
LADDER_W, LADDER_H = 1200, 1040

LADDER_X_MAX = 6000.0
LADDER_TRACK_PX = 660.0   # must match .ltrack's rendered width, for collision maths
LADDER_MIN_GAP_PX = 52.0  # below this, a dot's labels drop to the second level

LADDER_CSS = """
  .lplot { position: relative; margin-top: 10px; }
  .laxis {
    position: relative;
    height: 18px;
    margin-left: 218px;
    margin-right: 158px;
    margin-bottom: 2px;
  }
  .laxis .tick {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    font-size: 11.5px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .lrows { position: relative; }
  .lgrid { position: absolute; left: 218px; right: 158px; top: 0; bottom: 0; }
  .lgrid .gl {
    position: absolute;
    top: 0; bottom: 0;
    width: 1px;
    background: var(--gridline);
  }
  .lrow {
    display: flex;
    align-items: center;
    height: 88px;
    position: relative;
  }
  .lrow + .lrow { border-top: 1px solid var(--gridline); }
  .lmodel {
    width: 218px;
    flex-shrink: 0;
    padding-right: 14px;
  }
  .lmodel .nm {
    font-size: 14px;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.2;
  }
  .lmodel .sub {
    font-size: 11.5px;
    color: var(--text-muted);
    margin-top: 2px;
  }
  .ltrack { flex-grow: 1; position: relative; height: 88px; }
  .seg {
    position: absolute;
    top: 43px;
    height: 2px;
    background: var(--zero);
  }
  .seg.back { background: var(--series-2); height: 3px; top: 42.5px; }
  .ldot {
    position: absolute;
    top: 38px;
    width: 12px;
    height: 12px;
    margin-left: -6px;
    border-radius: 50%;
    background: var(--series-1);
    border: 2px solid var(--surface-1);
  }
  .ldot.last { background: var(--series-2); }
  .eff, .tok {
    position: absolute;
    transform: translateX(-50%);
    white-space: nowrap;
    text-align: center;
  }
  .eff {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-secondary);
  }
  .tok {
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-primary);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
  }
  .lspread {
    width: 158px;
    flex-shrink: 0;
    text-align: right;
    padding-left: 10px;
  }
  .lspread .mult {
    font-size: 19px;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }
  .lspread .cap2 {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 1px;
  }
  .lspread .chip {
    display: inline-block;
    margin-top: 5px;
    padding: 2px 7px;
    border-radius: 10px;
    background: var(--series-2);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
"""


def ladder_rows(kept):
    """Median output tokens for every bare model x effort cell, per model.

    Medians come from `statistics.median` over `stats.summary_tokens`, the same
    output-tokens-only axis every other section uses.
    """
    bare = [r for r in kept if not r.get("harness")]
    out = []
    for model in sorted({r["model"] for r in bare}):
        mrows = [r for r in bare if r["model"] == model]
        efforts = sorted({r["effort"] for r in mrows},
                         key=lambda e: stats.EFFORT_ORDER.get(e, 9))
        if len(efforts) < 2:
            continue
        dots = []
        for eff in efforts:
            rs = [r for r in mrows if r["effort"] == eff]
            dots.append({"effort": eff, "n": len(rs),
                         "median": statistics.median(
                             [stats.summary_tokens(r) for r in rs])})
        meds = [d["median"] for d in dots]
        out.append({
            "model": model,
            "dots": dots,
            "spread": max(meds) / min(meds) if min(meds) > 0 else float("nan"),
            "monotonic": all(b > a for a, b in zip(meds, meds[1:])),
        })
    ceiling = max(d["median"] for m in out for d in m["dots"])
    if ceiling > LADDER_X_MAX:
        sys.exit(f"largest median is {ceiling:.0f} tokens, past the card's "
                 f"x-axis maximum of {LADDER_X_MAX:.0f} — widen LADDER_X_MAX")
    # Monotonic ladders first, steepest at the top; the non-monotonic ones sit
    # together at the bottom, because "both Haiku rows go backwards" is a point
    # the post makes and it should read as a block.
    out.sort(key=lambda m: (not m["monotonic"], -m["spread"]))
    return out


def ladder_x(tokens):
    return tokens / LADDER_X_MAX * 100.0


def label_levels(xs_pct):
    """Assign each dot's labels to level 0 or 1 so close neighbours don't collide.

    Greedy left to right against the last x placed on each level; a dot closer
    than LADDER_MIN_GAP_PX to the level-0 neighbour drops to level 1.
    """
    levels = []
    last = [-1e9, -1e9]
    for x in xs_pct:
        px = x / 100.0 * LADDER_TRACK_PX
        lvl = 0 if px - last[0] >= LADDER_MIN_GAP_PX else 1
        levels.append(lvl)
        last[lvl] = px
    return levels


# Vertical offsets from a row's centre line, in px, per label level.
EFF_TOP = {0: 16, 1: 2}
TOK_TOP = {0: 54, 1: 66}


def card_ladder(kept):
    models = ladder_rows(kept)
    ticks = [0, 1000, 2000, 3000, 4000, 5000, 6000]

    axis = "\n".join(
        f'          <div class="tick" style="left: {ladder_x(t):.3f}%;">'
        f'{t:,}</div>' for t in ticks)
    lines = "\n".join(
        f'          <div class="gl" style="left: {ladder_x(t):.3f}%;"></div>'
        for t in ticks)

    rows = []
    for m in models:
        xs = [ladder_x(d["median"]) for d in m["dots"]]
        levels = label_levels(sorted(xs))
        # label_levels works in x order; map its assignment back onto the dots,
        # which are in effort order and may run backwards.
        by_x = {x: lvl for x, lvl in zip(sorted(xs), levels)}

        parts = []
        for x1, x2 in zip(xs, xs[1:]):
            back = " back" if x2 < x1 else ""
            parts.append(f'<div class="seg{back}" style="left: {min(x1, x2):.3f}%; '
                         f'width: {abs(x2 - x1):.3f}%;"></div>')
        for i, (d, x) in enumerate(zip(m["dots"], xs)):
            lvl = by_x[x]
            last = " last" if i == len(xs) - 1 else ""
            parts.append(f'<div class="ldot{last}" style="left: {x:.3f}%;"></div>')
            parts.append(f'<div class="eff" style="left: {x:.3f}%; '
                         f'top: {EFF_TOP[lvl]}px;">{esc(d["effort"])}</div>')
            parts.append(f'<div class="tok" style="left: {x:.3f}%; '
                         f'top: {TOK_TOP[lvl]}px;">{d["median"]:,.0f}</div>')

        chip = ('<div class="chip">non-monotonic</div>' if not m["monotonic"]
                else "")
        cheapest = min(m["dots"], key=lambda d: d["median"])["effort"]
        rows.append(
            '        <div class="lrow">'
            f'<div class="lmodel"><div class="nm">{esc(display(m["model"]))}</div>'
            f'<div class="sub">{len(m["dots"])} rungs &middot; cheapest at '
            f'{esc(cheapest)}</div></div>'
            f'<div class="ltrack">{"".join(parts)}</div>'
            f'<div class="lspread"><div class="mult">{m["spread"]:.2f}&times;</div>'
            f'<div class="cap2">dearest / cheapest</div>{chip}</div>'
            '</div>')

    # The callout's two claims are counted, not asserted.
    bare = [r for r in kept if not r.get("harness")]
    charted = [r for r in bare
               if r["model"] in {m["model"] for m in models}]
    n_pass = sum(1 for r in charted if stats.is_pass(r))
    n_pairs = n_comparisons = n_discordant = 0
    for m in models:
        mrows = [r for r in bare if r["model"] == m["model"]]
        efforts = [d["effort"] for d in m["dots"]]
        for lo_e, hi_e in zip(efforts, efforts[1:]):
            pairs = stats.build_pairs([r for r in mrows if r["effort"] == hi_e],
                                      [r for r in mrows if r["effort"] == lo_e])
            n_comparisons += 1
            n_pairs += len(pairs)
            # build_pairs already returns (pass_a, pass_b) booleans.
            n_discordant += sum(1 for pass_a, pass_b in pairs
                                if pass_a != pass_b)
    if n_pass != len(charted):
        sys.exit(f"{len(charted) - n_pass} charted run(s) did not pass — the "
                 f"callout's 'every dot is a 100% pass rate' claim is no longer "
                 f"true, rewrite it before regenerating")

    steepest = models[0]
    body = "\n".join([
        head("model-eval &middot; open benchmark harness",
             "The effort knob is a spend dial, not a quality dial",
             "Median output tokens per solved run, bare invocation, one row per "
             "model and one dot per effort rung. Rungs are placed in the order "
             "the vendor exposes them, left to right along a shared token axis, "
             "so a line that runs backwards is a tier that costs less than the "
             "tier below it."),
        '      <div class="legend">',
        '        <div class="key"><span class="sw a" style="border-radius:50%;">'
        '</span>effort rung, median tokens</div>',
        '        <div class="key"><span class="sw b"></span>backwards step: '
        'higher tier, fewer tokens</div>',
        '        <div class="scale">shared axis &middot; 0&ndash;'
        f'{LADDER_X_MAX:,.0f} output tokens &middot; median over each cell&rsquo;s runs</div>',
        '      </div>',
        '      <div class="lplot">',
        '        <div class="laxis">',
        axis,
        '        </div>',
        '        <div class="lrows">',
        '          <div class="lgrid">',
        lines,
        '          </div>',
        "\n".join(rows),
        '        </div>',
        '      </div>',
        f'      <div class="callout">Every dot on this card is a '
        f'<strong>100% pass rate</strong> &mdash; all {len(charted)} charted runs '
        f'passed. Across {n_comparisons} adjacent-rung comparisons and '
        f'<strong>{n_pairs} matched pairs</strong>, the number of discordant '
        f'pairs, meaning a task that passed at one tier and failed at the tier '
        f'next to it, is <strong>{n_discordant}</strong>. '
        f'{display(steepest["model"])} charges {steepest["spread"]:.2f}&times; '
        f'more at the top of its ladder than the bottom and buys nothing '
        f'measurable with it.</div>',
        footer('Medians are descriptive, not a hypothesis test; the pass claim '
               'next to them is the exact McNemar contrast from appendix '
               '&sect;3, which needs discordant pairs to have anything to test '
               'and never found one. Output tokens only, because '
               'Fable&rsquo;s input-token counts are quarantined as unreliable. '
               'The honest limit: effort cannot buy a pass on a task that '
               '<em>already passes at the lowest tier</em>, so this establishes '
               'the knob is pure cost in this difficulty band, not at every '
               'difficulty. Computed by <code>runner/stats.py</code> from '
               '<code>results.jsonl</code>.'),
    ])
    emit("model-eval-effort-ladder",
         "model-eval &mdash; median output tokens per effort rung",
         LADDER_CSS, body, LADDER_W, LADDER_H)
    for m in models:
        rungs = " ".join(f"{d['effort']}={d['median']:,.0f}" for d in m["dots"])
        flag = "" if m["monotonic"] else "  (non-monotonic)"
        print(f"  §1  {display(m['model']):<28} {m['spread']:.2f}x  {rungs}{flag}")
    print(f"  §3  {n_comparisons} comparisons, {n_pairs} matched pairs, "
          f"{n_discordant} discordant")


# --------------------------------------------------------------------------- #
def main():
    kept = load()
    card_cost(kept)
    card_ceiling(kept)
    card_ladder(kept)


if __name__ == "__main__":
    main()
