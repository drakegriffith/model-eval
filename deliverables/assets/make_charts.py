"""Generate the three deliverable charts from results.jsonl (single source of truth)."""
import json, math, statistics as st, collections, os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "..", "runner", "results", "results.jsonl")

FABLE = "#4A7DBF"   # validated categorical slot 1
SOL = "#E8853D"     # validated categorical slot 2 (contrast WARN -> direct labels everywhere)
INK = "#2b2b28"
MUTED = "#8a8a84"
SURFACE = "#fcfcfb"

rows = [json.loads(l) for l in open(RESULTS)]
s1 = [r for r in rows if r["sweep"] == "sweep1"]

cells = collections.defaultdict(list)
for r in s1:
    cells[(r["model"], r["effort"])].append(r)

ORDER = [("fable", "medium"), ("fable", "high"),
         ("sol", "low"), ("sol", "medium"), ("sol", "high")]
LABELS = ["Fable\nmedium", "Fable\nhigh", "Sol\nlow", "Sol\nmedium", "Sol\nhigh"]
COLORS = [FABLE, FABLE, SOL, SOL, SOL]


def wilson(x, n, z=1.959964):
    p = x / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=9)
    ax.set_facecolor(SURFACE)


def base(title, sub):
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    fig.suptitle(title, x=0.06, ha="left", fontsize=13, fontweight="bold", color=INK)
    ax.set_title(sub, loc="left", fontsize=9, color=MUTED, pad=10)
    style(ax)
    return fig, ax


def legend(ax, loc="upper left"):
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=FABLE, label="Claude Fable 5"),
                       Patch(color=SOL, label="GPT-5.6 Sol")],
              frameon=False, fontsize=9, loc=loc, labelcolor=INK)


# 1. Pass-rate ceiling with Wilson CIs -------------------------------------
fig, ax = base("Every configuration passed every task",
               "Pass rate with 95% Wilson confidence interval - n=24 runs per configuration, sweep 1 (bare)")
for i, key in enumerate(ORDER):
    v = cells[key]
    x, n = sum(r["pass"] for r in v), len(v)
    lo, hi = wilson(x, n)
    ax.plot([i, i], [lo * 100, hi * 100], color=COLORS[i], lw=2, solid_capstyle="round")
    ax.plot(i, x / n * 100, "o", ms=9, color=COLORS[i])
    ax.annotate(f"{x}/{n}", (i, x / n * 100), xytext=(0, 8),
                textcoords="offset points", ha="center", fontsize=9, color=INK)
ax.set_xticks(range(5), LABELS)
ax.set_ylim(60, 104)
ax.set_ylabel("pass rate (%)", fontsize=9, color=INK)
ax.axhline(100, color=MUTED, lw=0.5, ls=":")
legend(ax, loc="lower left")
fig.text(0.06, -0.03, "The interval, not the point, is the honest claim: 24/24 is consistent with a true rate as low as 86%.",
         fontsize=8, color=MUTED)
fig.savefig(os.path.join(HERE, "1-pass-rate-ceiling.png"), bbox_inches="tight")

# 2. Effort ladder vs output tokens ----------------------------------------
fig, ax = base("More reasoning effort bought nothing but tokens",
               "Median output tokens per run by effort setting - every bar is a 100% pass rate")
med_out = [st.median([r["tokens_out"] for r in cells[k]]) for k in ORDER]
bars = ax.bar(range(5), med_out, width=0.55, color=COLORS)
for i, v in enumerate(med_out):
    ax.annotate(f"{v:,.0f}", (i, v), xytext=(0, 4), textcoords="offset points",
                ha="center", fontsize=9, color=INK)
    ax.annotate("100% pass", (i, 60), ha="center", fontsize=7.5, color="white")
ax.set_xticks(range(5), LABELS)
ax.set_ylabel("median output tokens", fontsize=9, color=INK)
legend(ax)
fig.savefig(os.path.join(HERE, "2-effort-buys-nothing.png"), bbox_inches="tight")

# 3. Input-token economics ---------------------------------------------------
fig, ax = base("The bill looks very different per vendor",
               "Median input tokens per run (as reported by each vendor's CLI) - accounting methods differ; see note")
med_in = [st.median([r["tokens_in"] for r in cells[k]]) for k in ORDER]
ax.bar(range(5), med_in, width=0.55, color=COLORS)
for i, v in enumerate(med_in):
    ax.annotate(f"{v/1000:,.0f}k", (i, v), xytext=(0, 4), textcoords="offset points",
                ha="center", fontsize=9, color=INK)
ax.set_xticks(range(5), LABELS)
ax.set_ylabel("median input tokens", fontsize=9, color=INK)
legend(ax)
fig.text(0.06, -0.03, "Caution: the two CLIs count input tokens differently (context resends / cache reads). Within-model comparison is safe; cross-model is indicative only.",
         fontsize=8, color=MUTED)
fig.savefig(os.path.join(HERE, "3-input-token-economics.png"), bbox_inches="tight")

# aggregates for FINDINGS ----------------------------------------------------
print(f"{'config':16} {'pass':>6} {'out':>6} {'in':>8} {'wall':>6} {'turns':>5}")
for k in ORDER:
    v = cells[k]
    print(f"{k[0]+'/'+k[1]:16} {sum(r['pass'] for r in v):>3}/{len(v):<3}"
          f"{st.median([r['tokens_out'] for r in v]):>6.0f}"
          f"{st.median([r['tokens_in'] for r in v]):>9.0f}"
          f"{st.median([r['wall_s'] for r in v]):>7.1f}"
          f"{st.median([r['turns'] for r in v]):>5.0f}")
lo, hi = wilson(24, 24)
print(f"wilson 24/24: ({lo:.3f}, {hi:.3f})")
print("charts written:", [f for f in os.listdir(HERE) if f.endswith(".png")])
