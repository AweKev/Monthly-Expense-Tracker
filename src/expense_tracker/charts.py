"""Chart image for the bot (/grafik): spending per category and per day, as a PNG sized for a phone.

Design notes: one series per panel, so one color (no legend needed); the chart title names it.
Horizontal bars for categories (long names, easy to compare), vertical bars for days with a dashed
reference line for the average daily budget. Values are printed as text next to the bars.
"""

import io
from datetime import date

import matplotlib

matplotlib.use("Agg")  # no screen needed
import matplotlib.pyplot as plt  # noqa: E402

from .budget import BudgetStatus  # noqa: E402
from .summary import RangeSummary, rupiah, rupiah_short  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e8e7e3"
SERIES = "#2a78d6"  # categorical slot 1
FUTURE = "#e8e7e3"


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=10, length=0)


def render_report(summary: RangeSummary, status: BudgetStatus, title: str, today: date) -> bytes:
    categories = summary.categories[:8]
    fig = plt.figure(figsize=(7.2, 9.0), dpi=150, facecolor=SURFACE)
    grid = fig.add_gridspec(3, 1, height_ratios=[0.55, max(1.2, 0.32 * len(categories) + 0.4), 1.5],
                            hspace=0.42, left=0.30, right=0.93, top=0.97, bottom=0.07)

    # Header: the number is the headline.
    head = fig.add_subplot(grid[0])
    head.axis("off")
    head.text(-0.38, 1.0, title, transform=head.transAxes, fontsize=12, color=INK_2, va="top")
    head.text(-0.38, 0.62, rupiah(summary.spent), transform=head.transAxes, fontsize=26,
              color=INK, va="top", fontweight="bold")
    sub = f"{summary.transaction_count} transaksi · rata-rata {rupiah_short(summary.daily_average)}/hari"
    if status.mode == "monthly" and status.month_budget and summary.start == (status.period.start if status.period else None):
        pct = summary.spent / status.month_budget
        sub = f"dari budget {rupiah(status.month_budget)} ({pct:.0%}) · " + sub
    head.text(-0.38, 0.05, sub, transform=head.transAxes, fontsize=10, color=INK_2, va="top")

    # Per category: horizontal bars, biggest on top.
    ax = fig.add_subplot(grid[1])
    _style(ax)
    ax.set_title("Per kategori", loc="left", fontsize=11, color=INK, x=-0.38, pad=8)
    if categories:
        names = [b.category for b in categories][::-1]
        values = [b.total for b in categories][::-1]
        bars = ax.barh(names, values, color=SERIES, height=0.62)
        peak = max(values)
        for bar, value in zip(bars, values):
            ax.text(bar.get_width() + peak * 0.02, bar.get_y() + bar.get_height() / 2, rupiah_short(value),
                    va="center", fontsize=9.5, color=INK)
        ax.set_xlim(0, peak * 1.25)
    else:
        ax.text(0.5, 0.5, "Belum ada pengeluaran", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)

    # Per day: vertical bars; days still to come are shown as empty slots.
    ax2 = fig.add_subplot(grid[2])
    _style(ax2)
    ax2.set_title("Per hari", loc="left", fontsize=11, color=INK, x=-0.38, pad=8)
    days = list(summary.per_day.keys())
    values = [summary.per_day[d] for d in days]
    colors = [SERIES if d <= today else FUTURE for d in days]
    ax2.bar(range(len(days)), values, color=colors, width=0.8)
    ax2.grid(axis="y", color=GRID, linewidth=0.8)
    ax2.set_axisbelow(True)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: rupiah_short(int(v)) if v else "0"))
    step = 1 if len(days) <= 10 else 5
    ax2.set_xticks(range(0, len(days), step))
    ax2.set_xticklabels([f"{d.day}/{d.month}" for d in days[::step]])
    if status.mode == "monthly" and status.period and status.period.days:
        target = status.month_budget / status.period.days
        ax2.axhline(target, color=INK_2, linestyle=(0, (4, 3)), linewidth=1.2)
        ax2.text(len(days) - 0.5, target, f" rata-rata jatah {rupiah_short(int(target))}", color=INK_2,
                 fontsize=9, va="bottom", ha="right")
        top = max(max(values, default=0), target) * 1.2
        ax2.set_ylim(0, top or 1)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buffer.getvalue()
