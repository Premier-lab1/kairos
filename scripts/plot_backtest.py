"""Charts for backtests/2026-10-19.md.

Every number below is copied from that report (section names in the comments). Nothing is
simulated: if the report changes, change the constants and rerun.

    pip install matplotlib
    python scripts/plot_backtest.py            # writes backtests/charts/*.png
    python scripts/plot_backtest.py --out DIR

Style: dark background, one accent colour, green for wins, red for rugs, no chart junk.
"""

import argparse
import math
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPORT = "backtests/2026-10-19.md"

# ───────────── numbers from the report ─────────────

# "Data" table: copy of the production database, pg_dump 18.10.2026 ~19:09 UTC, ~3.3 h of collection.
TOKENS_TOTAL = 5705
TOKENS_WITH_MENTIONS = 778
TOKENS_WITH_MENTIONS_AND_TRADES = 41
TOKENS_FINAL_24H = 0
SHILLERS = 101
MENTIONS = 3203
TRADES = 101_265

# "Labels v1 and v2" table (report headings are in Russian) (whole copy). Order of labels inside each group matters for stacking.
# group: {label: count}
V2_LABELS = {
    "creation, 1 h": {"success": 0, "rug": 14, "neutral": 0, "no_data": 5, "pending": 24},
    "creation, 24 h": {"success": 0, "rug": 4, "neutral": 0, "no_data": 2, "pending": 37},
    "creation, 7 d": {"success": 0, "rug": 3, "neutral": 0, "no_data": 2, "pending": 38},
    "first mention, 1 h": {"success": 0, "rug": 12, "neutral": 3, "no_data": 1, "pending": 25},
    "first mention, 24 h": {"success": 0, "rug": 0, "neutral": 0, "no_data": 0, "pending": 41},
    "first mention, 7 d": {"success": 0, "rug": 0, "neutral": 0, "no_data": 0, "pending": 41},
}

# "Why so little data", item 1: daily budgets (api_calls on the server), times are UTC, 18.10.2026.
COLLECTION_START = 15 + 48 / 60            # collection started 15:48
HELIUS_EXHAUSTED = 15 + 58 / 60            # the whole 20 000-credit Helius budget went between 15:50 and 15:58
FRONTRUN_EXHAUSTED = 17 + 7 / 60           # the 3 000-credit Frontrun budget was gone by 17:07
SNAPSHOT_TIME = 20 + 32 / 60               # state of the queue at 20:32
QUEUE_HELIUS = 2661                        # jobs waiting until midnight UTC
QUEUE_FRONTRUN = 412
HELIUS_BUDGET = 20_000
FRONTRUN_BUDGET = 3_000

# Item 2: delay between tweet_time and first_seen_at for live search mentions (frontrun_search).
# The report gives "about 2.5 days" / "about 6 days" and "18 minutes" / "2 minutes": values are approximate.
DELAY_ALL = {"n": 278, "median_min": 2.5 * 24 * 60, "tail_min": 6 * 24 * 60, "tail": "p90"}
DELAY_FRESH = {"n": 81, "median_min": 18, "tail_min": 2, "tail": "p10"}

# "Run", 1-hour mechanics check (not the main horizon): test part of the walk-forward split.
TEST_N = 8
TEST_SUCCESS = 0
TEST_RUG = 6
REPORTED_INTERVALS = {"success": (0.0, 0.32), "rug": (0.41, 0.93)}  # 95% Wilson, as printed in the report

# ───────────── style ─────────────

BG = "#0b0b0b"
FG = "#e8e8e8"
MUTED = "#8c8c8c"
GRID = "#262626"
ACCENT = "#FF4F17"
GREEN = "#3fb07a"
RED = "#d9534f"
GREY_NEUTRAL = "#9a9a9a"
GREY_NODATA = "#5c5c5c"
GREY_PENDING = "#2c2c2c"

W, H, DPI = 10.0, 5.625, 160  # 1600 x 900 px

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": FG, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": GRID, "font.family": "monospace",
    "font.monospace": ["DejaVu Sans Mono", "Consolas", "Menlo", "monospace"],
    "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "axes.spines.bottom": False,
    "xtick.major.size": 0, "ytick.major.size": 0,
})

SURFACE_GAP = 1.6  # px-ish gap between stacked segments, drawn as a background-coloured edge


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denom = 1 + z * z / n
    return max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom)


def new_figure(title: str, subtitle: str):
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    fig.text(0.055, 0.935, title, fontsize=15, fontweight="bold", color=FG, ha="left", va="center")
    fig.text(0.055, 0.885, subtitle, fontsize=9.5, color=MUTED, ha="left", va="center")
    return fig


def footer(fig, text: str = "") -> None:
    note = f"Source: {REPORT}" + (f"  |  {text}" if text else "")
    fig.text(0.055, 0.035, note, fontsize=7.5, color=MUTED, ha="left", va="center")
    fig.text(0.945, 0.035, "Kairos  |  not financial advice", fontsize=7.5, color=ACCENT, ha="right", va="center")


def save(fig, out: pathlib.Path, name: str) -> None:
    path = out / name
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    print("wrote", path)


# ───────────── chart 1: data funnel ─────────────

def chart_funnel(out: pathlib.Path) -> None:
    fig = new_figure("Why the backtest cannot judge anything yet",
                     "Tokens in the database copy, narrowed down to those that can be scored against a final outcome")
    ax = fig.add_axes([0.28, 0.14, 0.62, 0.66])
    steps = [
        ("tokens seen", TOKENS_TOTAL),
        ("with a shiller mention", TOKENS_WITH_MENTIONS),
        ("mention + trades loaded", TOKENS_WITH_MENTIONS_AND_TRADES),
        ("final 24 h outcome", TOKENS_FINAL_24H),
    ]
    ys = list(range(len(steps)))[::-1]
    for y, (label, value) in zip(ys, steps):
        ax.barh(y, value, height=0.5, color=ACCENT if value else GRID, zorder=3)
        ax.text(-TOKENS_TOTAL * 0.015, y, label, ha="right", va="center", color=FG)
        ax.text(value + TOKENS_TOTAL * 0.012, y, f"{value:,}", ha="left", va="center",
                color=ACCENT if value else FG, fontweight="bold")
    ax.set_xlim(0, TOKENS_TOTAL * 1.12)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.set_ylim(-0.6, len(steps) - 0.4)
    ax.text(0, -0.58, f"{SHILLERS} shillers, {MENTIONS:,} CA mentions, {TRADES:,} trades. Collection ran about 3.3 hours.",
            color=MUTED, fontsize=8.5, ha="left", va="top")
    footer(fig, "a 24 h outcome needs a full day after the first shill")
    save(fig, out, "01-data-funnel.png")


# ───────────── chart 2: label distribution ─────────────

def chart_labels(out: pathlib.Path) -> None:
    fig = new_figure("Almost every outcome is still pending",
                     "Token outcomes by reference point and horizon, rule set v2 (whole database copy)")
    ax = fig.add_axes([0.20, 0.17, 0.72, 0.62])
    colors = {"success": GREEN, "rug": RED, "neutral": GREY_NEUTRAL, "no_data": GREY_NODATA, "pending": GREY_PENDING}
    groups = list(V2_LABELS)
    ys = list(range(len(groups)))[::-1]
    for y, group in zip(ys, groups):
        left = 0
        total = sum(V2_LABELS[group].values())
        for label, color in colors.items():
            n = V2_LABELS[group][label]
            if not n:
                continue
            ax.barh(y, n, left=left, height=0.56, color=color, edgecolor=BG, linewidth=SURFACE_GAP, zorder=3)
            if label in ("rug", "neutral") or n >= 12:
                ax.text(left + n / 2, y, str(n), ha="center", va="center", fontsize=8.5,
                        color=BG if label == "neutral" else FG, fontweight="bold")
            left += n
        ax.text(total + 0.8, y, f"{total}", ha="left", va="center", color=MUTED, fontsize=8.5)
        ax.text(-1.0, y, group, ha="right", va="center", color=FG)
    ax.set_xlim(0, 50)
    ax.set_ylim(-0.6, len(groups) - 0.4)
    ax.set_yticks([])
    ax.set_xticks([])
    # legend as plain text swatches, one row
    x = 0.20
    for label, color in colors.items():
        fig.patches.append(plt.Rectangle((x, 0.105), 0.012, 0.022, transform=fig.transFigure, color=color))
        fig.text(x + 0.018, 0.116, label.replace("_", " "), fontsize=8.5, color=MUTED, va="center")
        x += 0.12
    footer(fig, "no `success` under v2")
    save(fig, out, "02-outcome-labels.png")


# ───────────── chart 3: budget timeline ─────────────

def chart_budgets(out: pathlib.Path) -> None:
    fig = new_figure("The daily API budgets ran out within hours",
                     "Server collection on 18.10.2026 (UTC). After a budget is spent, jobs wait until midnight")
    ax = fig.add_axes([0.20, 0.20, 0.72, 0.56])
    rows = [
        ("Helius", f"{HELIUS_BUDGET:,} credits/day", HELIUS_EXHAUSTED, QUEUE_HELIUS),
        ("Frontrun", f"{FRONTRUN_BUDGET:,} credits/day", FRONTRUN_EXHAUSTED, QUEUE_FRONTRUN),
    ]
    ys = [1, 0]
    for y, (name, budget, exhausted, queued) in zip(ys, rows):
        ax.barh(y, exhausted - COLLECTION_START, left=COLLECTION_START, height=0.34, color=ACCENT, zorder=3,
                edgecolor=BG, linewidth=SURFACE_GAP)
        ax.barh(y, SNAPSHOT_TIME - exhausted, left=exhausted, height=0.34, color=GREY_PENDING, zorder=3,
                edgecolor=GRID, linewidth=0.8, hatch="////")
        ax.text(COLLECTION_START - 0.04, y + 0.06, name, ha="right", va="center", color=FG, fontweight="bold")
        ax.text(COLLECTION_START - 0.04, y - 0.12, budget, ha="right", va="center", color=MUTED, fontsize=8)
        label = "budget gone by 17:07" if name == "Frontrun" else "20 000 credits gone in 8 min (15:50-15:58)"
        ax.text(exhausted + 0.06, y + 0.30, label, ha="left", va="center", color=ACCENT, fontsize=8.5)
        ax.text(SNAPSHOT_TIME + 0.03, y, f"{queued:,} jobs\nwaiting", ha="left", va="center", color=FG, fontsize=8.5)
    ax.set_xlim(COLLECTION_START - 0.02, SNAPSHOT_TIME + 0.55)
    ax.set_ylim(-0.6, 1.6)
    ticks = [16, 17, 18, 19, 20]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t}:00" for t in ticks])
    ax.set_yticks([])
    ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.text(COLLECTION_START, -0.52, "collection starts 15:48", color=MUTED, fontsize=8, ha="left", va="center")
    ax.text(SNAPSHOT_TIME, -0.52, "queue read at 20:32", color=MUTED, fontsize=8, ha="right", va="center")
    footer(fig, "fix after this run: BUDGET_PACING spreads each budget over the day")
    save(fig, out, "03-budget-timeline.png")


# ───────────── chart 4: collection delay ─────────────

def chart_delay(out: pathlib.Path) -> None:
    fig = new_figure("Mentions reach the database long after the tweet",
                     "Delay between the tweet and Kairos first storing it, live search mentions, log scale")
    ax = fig.add_axes([0.20, 0.20, 0.72, 0.54])
    rows = [(f"all live-search\nmentions (n={DELAY_ALL['n']})", DELAY_ALL, 1),
            (f"mentions of fresh\ntokens (n={DELAY_FRESH['n']})", DELAY_FRESH, 0)]
    for label, d, y in rows:
        lo, hi = sorted((d["median_min"], d["tail_min"]))
        ax.plot([lo, hi], [y, y], color=MUTED, linewidth=1.2, zorder=2)
        ax.scatter([d["median_min"]], [y], s=90, color=ACCENT, zorder=4, edgecolor=BG, linewidth=1.6)
        ax.scatter([d["tail_min"]], [y], s=46, color=BG, zorder=4, edgecolor=MUTED, linewidth=1.4)
        ax.text(0.8, y, label, ha="right", va="center", color=FG)
        med = "about 2.5 days" if d is DELAY_ALL else "18 minutes"
        tail = "p90 about 6 days" if d is DELAY_ALL else "p10 2 minutes"
        ax.text(d["median_min"], y + 0.2, f"median {med}", ha="center", va="bottom", color=ACCENT, fontsize=8.5)
        ax.text(d["tail_min"], y - 0.2, tail, ha="center", va="top", color=MUTED, fontsize=8.5)
    ax.set_xscale("log")
    ax.set_xlim(1, 20_000)
    ax.set_ylim(-0.7, 1.7)
    ticks = [1, 10, 60, 1440, 10080]
    ax.set_xticks(ticks)
    ax.set_xticklabels(["1 min", "10 min", "1 h", "1 day", "7 days"])
    ax.minorticks_off()
    ax.set_yticks([])
    ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
    ax.tick_params(axis="x", length=0, pad=6)
    fig.text(0.20, 0.125, "The long tail is mostly old tweets about tokens first seen at migration.\n"
             "The backtest reads tweet time, so it is optimistic compared with live scoring.",
             fontsize=8, color=MUTED, ha="left", va="center", linespacing=1.5)
    footer(fig, "orange: median, hollow: tail (approximate in the report)")
    save(fig, out, "04-collection-delay.png")


# ───────────── chart 5: 1 h mechanics check ─────────────

def chart_intervals(out: pathlib.Path) -> None:
    fig = new_figure("Eight test tokens say almost nothing",
                     "1-hour mechanics check (not the main horizon): rates in the test part with 95% Wilson intervals")
    ax = fig.add_axes([0.20, 0.20, 0.72, 0.54])
    rows = [("success", TEST_SUCCESS, GREEN, 1), ("rug", TEST_RUG, RED, 0)]
    for name, k, color, y in rows:
        lo, hi = wilson(k, TEST_N)
        rep_lo, rep_hi = REPORTED_INTERVALS[name]
        # the recomputed interval must match what the report prints (rounded to whole percent)
        assert abs(lo - rep_lo) < 0.01 and abs(hi - rep_hi) < 0.01, (name, lo, hi, rep_lo, rep_hi)
        ax.plot([lo * 100, hi * 100], [y, y], color=color, linewidth=3, solid_capstyle="round", zorder=3)
        ax.scatter([k / TEST_N * 100], [y], s=110, color=color, zorder=4, edgecolor=BG, linewidth=1.8)
        ax.text(-3, y, f"{name}: {k} of {TEST_N}", ha="right", va="center", color=FG)
        ax.text(hi * 100 + 2, y, f"{lo * 100:.0f}-{hi * 100:.0f}%", ha="left", va="center", color=MUTED, fontsize=9)
    ax.set_xlim(-2, 100)
    ax.set_ylim(-0.7, 1.7)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_yticks([])
    ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
    ax.tick_params(axis="x", length=0, pad=6)
    fig.text(0.20, 0.125, "15 final outcomes: 7 used as warm-up history, 8 as test.\n"
             "No signal reached the 40/50/60/70 thresholds, so no precision can be computed.",
             fontsize=8, color=MUTED, ha="left", va="center", linespacing=1.5)
    footer(fig, "intervals recomputed from counts, match the report")
    save(fig, out, "05-one-hour-check.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent.parent / "backtests" / "charts"))
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for chart in (chart_funnel, chart_labels, chart_budgets, chart_delay, chart_intervals):
        chart(out)


if __name__ == "__main__":
    main()
