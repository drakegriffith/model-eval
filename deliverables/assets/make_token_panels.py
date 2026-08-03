"""Render the per-task token chart as four small multiples (pooled + matched cells).

Every number on the card comes from `runner/stats.py` applied to
`runner/results/results.jsonl` — the same functions §5 and §6 of the appendix
call, on the same corpus gate. Nothing here re-derives a median, a percentage,
or a p-value by hand; hand-recomputation is the exact bug the blog post's
correction section is about, so the arithmetic is imported, not repeated.

Two outputs, both regenerated on every run:
  model-eval-token-chart-per-task.html  (the card)
  model-eval-token-chart-per-task.png   (headless-Chrome screenshot of it)

Usage:  python3 deliverables/assets/make_token_panels.py
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "runner"))

import corpus_gates  # noqa: E402
import stats  # noqa: E402

RESULTS = os.path.join(ROOT, "runner", "results", "results.jsonl")
HTML_OUT = os.path.join(HERE, "model-eval-token-chart-per-task.html")
PNG_OUT = os.path.join(HERE, "model-eval-token-chart-per-task.png")

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CANVAS_W, CANVAS_H = 1200, 900

# Longest bar reaches this share of a track's full width, measured from the
# centre line. The remainder is the value label's gutter.
BAR_MAX_PCT = 40.0


# --------------------------------------------------------------------------- #
# Data — every panel is one call into stats.py's own comparison functions
# --------------------------------------------------------------------------- #
def panels():
    rows = stats.load_jsonl(RESULTS)
    if not rows:
        sys.exit(f"no results at {RESULTS}")
    kept, _excluded = corpus_gates.summarizable_rows(rows)

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


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def fmt_pct(d):
    """A log difference rendered as the percentage the reader recognises."""
    pct = (math.exp(d) - 1.0) * 100.0
    if abs(round(pct)) == 0:
        return "0%"
    sign = "+" if pct > 0 else "−"
    return f"{sign}{abs(pct):.0f}%"


def fmt_p(p):
    if p >= 0.1:
        return f"{p:.2f}"
    if p >= 0.01:
        return f"{p:.3f}"
    return f"{p:.4f}"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_panel(panel, tasks, domain):
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


def build_html(ps):
    tasks = sorted({t for p in ps for t in p["by_task"]})
    domain = max(abs(d) for p in ps for d in p["by_task"].values())
    scale_note = fmt_pct(domain)
    grid = "\n".join(render_panel(p, tasks, domain) for p in ps)
    sig_panel = next((p for p in ps if p["p"] < 0.05), None)
    if sig_panel is not None:
        sig_line = (f'The one panel under p = 0.05 is <em>{esc(sig_panel["title"])}'
                    f'</em>, and it is not tier-matched: Fable&rsquo;s cheapest '
                    f'tier here is the floor of the run matrix, not a tested '
                    f'minimum, so part of that gap is the tier and not the model.')
    else:
        sig_line = "No panel falls under p = 0.05."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>model-eval &mdash; per-task token difference, Fable vs Sol, four cells</title>
<style>
  .viz-root {{
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
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    background: var(--page);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .canvas {{
    width: {CANVAS_W}px;
    height: {CANVAS_H}px;
    background: var(--page);
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .card {{
    width: 1136px;
    height: 836px;
    background: var(--surface-1);
    border-radius: 20px;
    padding: 30px 44px 22px 44px;
    display: flex;
    flex-direction: column;
  }}
  .eyebrow {{
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 0 0 9px 0;
  }}
  h1 {{
    font-size: 31px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 7px 0;
    line-height: 1.15;
  }}
  .subtitle {{
    font-size: 14px;
    color: var(--text-secondary);
    margin: 0;
    max-width: 1000px;
    line-height: 1.45;
  }}
  .legend {{
    display: flex;
    align-items: center;
    gap: 22px;
    margin: 14px 0 4px 0;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--gridline);
    font-size: 13px;
    color: var(--text-secondary);
  }}
  .legend .key {{ display: flex; align-items: center; gap: 7px; }}
  .legend .sw {{ width: 13px; height: 13px; border-radius: 3px; }}
  .legend .sw.a {{ background: var(--series-1); }}
  .legend .sw.b {{ background: var(--series-2); }}
  .legend .scale {{ margin-left: auto; color: var(--text-muted); }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 40px;
    row-gap: 18px;
    margin-top: 16px;
  }}
  .panel-head {{
    display: flex;
    align-items: baseline;
    gap: 8px;
  }}
  .panel-title {{
    font-size: 15.5px;
    font-weight: 700;
    color: var(--text-primary);
  }}
  .panel-sec {{
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .panel-meta {{
    font-size: 12.5px;
    color: var(--text-secondary);
    margin: 2px 0 8px 0;
  }}
  .panel-meta strong {{ color: var(--text-primary); }}
  .panel-meta .sig {{ font-weight: 700; color: var(--text-primary); }}
  .row {{
    display: flex;
    align-items: center;
    height: 23px;
  }}
  .label-col {{
    width: 76px;
    flex-shrink: 0;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-primary);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .row.absent .label-col {{ color: var(--text-muted); font-weight: 500; }}
  .track {{
    flex-grow: 1;
    position: relative;
    height: 23px;
  }}
  .track::before {{
    content: "";
    position: absolute;
    left: 50%;
    top: 1px; bottom: 1px;
    width: 1px;
    background: var(--zero);
  }}
  .bar {{
    position: absolute;
    top: 6px;
    height: 12px;
    border-radius: 2px;
  }}
  .bar.pos {{ left: 50%; background: var(--series-2); }}
  .bar.neg {{ right: 50%; background: var(--series-1); }}
  .val {{
    position: absolute;
    top: 4px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    white-space: nowrap;
  }}
  .none {{
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    top: 4px;
    font-size: 11.5px;
    font-style: italic;
    color: var(--text-muted);
    background: var(--surface-1);
    padding: 0 6px;
  }}
  .footer {{
    margin-top: auto;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-top: 1px solid var(--gridline);
    padding-top: 12px;
  }}
  .footnote {{
    font-size: 12.5px;
    color: var(--text-muted);
    max-width: 880px;
    line-height: 1.45;
  }}
  .footnote em {{ font-style: italic; color: var(--text-secondary); }}
  .repo {{
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    padding-left: 24px;
  }}
</style>
</head>
<body>
  <div class="canvas">
    <div class="card viz-root">
      <div>
        <p class="eyebrow">model-eval &middot; open benchmark harness</p>
        <h1>Four ways to ask which model is cheaper, four different answers</h1>
        <p class="subtitle">Median output tokens per solved run, Claude Fable relative to Codex Sol, one row per task. One exact sign-flip permutation test, run in four different cells: tiers pooled, then tier for tier, then each model at its own cheapest passing tier. All four panels share one bar scale.</p>
      </div>

      <div class="legend">
        <div class="key"><span class="sw a"></span>Fable used fewer tokens</div>
        <div class="key"><span class="sw b"></span>Fable used more tokens</div>
        <div class="scale">shared scale &middot; bar length &prop; |log ratio| &middot; longest bar = {scale_note}</div>
      </div>

      <div class="grid">
{grid}
      </div>

      <div class="footer">
        <div class="footnote">Exact sign-flip permutation test over all 2<sup>k</sup> sign patterns of the per-task log differences; passing runs only; output tokens only, because Fable&rsquo;s input-token counts are quarantined as unreliable. The three matched panels use bare runs (no harness). {sig_line} Every median, percentage and p-value on this card is computed by <code>runner/stats.py</code> from <code>results.jsonl</code>.</div>
        <div class="repo">github.com/drakegriffith/model-eval</div>
      </div>
    </div>
  </div>
</body>
</html>
"""


def render_png():
    if not os.path.exists(CHROME):
        print(f"! Chrome not found at {CHROME}; HTML written, PNG not rendered")
        return False
    cmd = [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
           f"--screenshot={PNG_OUT}",
           f"--window-size={CANVAS_W},{CANVAS_H}",
           "--force-device-scale-factor=2",
           f"file://{HTML_OUT}"]
    subprocess.run(cmd, check=True, capture_output=True)
    return True


def main():
    ps = panels()
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(build_html(ps))
    print(f"wrote {HTML_OUT}")
    for p in ps:
        print(f"  {p['section']:>5} {p['title']:<44} "
              f"more on {p['higher']}/{p['k']}  "
              f"p={p['p']:.7f} ({p['n_extreme']}/{p['total']})")
    if render_png():
        import struct
        d = open(PNG_OUT, "rb").read(33)
        w, h = struct.unpack(">II", d[16:24])
        print(f"wrote {PNG_OUT} ({w}x{h})")


if __name__ == "__main__":
    main()
