"""Message texts for the Telegram bot (HTML parse mode). Pure functions, easy to test."""

from datetime import date, timedelta
from html import escape as _escape

from ..budget import BudgetStatus
from ..models import Counterparty, Kind, PartyType, Transaction, TxnType
from ..summary import DaySummary, MonthSummary, rupiah, rupiah_short

def escape(value: str) -> str:
    return _escape(value, quote=False)


HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]


def tanggal(day: date) -> str:
    return f"{HARI[day.weekday()]}, {day.day} {BULAN[day.month - 1][:3]}"


def period_label(period) -> str:
    """'September 2026' for a calendar month, '25 Sep s.d. 24 Okt 2026' otherwise."""
    if period is None or period.is_calendar_month:
        start = period.start if period else None
        return f"{BULAN[start.month - 1]} {start.year}" if start else ""
    last = period.end - timedelta(days=1)
    year = f" {period.start.year}" if period.start.year != last.year else ""
    return (f"{period.start.day} {BULAN[period.start.month - 1][:3]}{year} s.d. "
            f"{last.day} {BULAN[last.month - 1][:3]} {last.year}")


def budget_line(status: BudgetStatus) -> str:
    if not status.enabled:
        return ""
    if status.left_today >= 0:
        return f"Sisa hari ini: <b>{rupiah(status.left_today)}</b> dari {rupiah(status.allowance)}"
    return f"Lewat budget hari ini: <b>{rupiah(-status.left_today)}</b> (jatah {rupiah(status.allowance)})"


def alert(t: Transaction, status: BudgetStatus, party: Counterparty | None = None, note: str = "") -> str:
    name = escape(t.counterparty or "(tanpa nama)")
    when = t.occurred_at.strftime("%H:%M")
    lines = []
    if t.kind == Kind.INCOME:
        lines.append(f"<b>+{rupiah(t.amount)}</b> dari {name}")
        lines.append(f"Uang masuk · {when}")
    elif t.txn_type == TxnType.TOPUP:
        fee = f" + biaya {rupiah(t.fee)}" if t.fee else ""
        lines.append(f"<b>-{rupiah(t.amount)}</b>{fee} · Top-up {name}")
        lines.append(f"Dihitung ke bulan, bukan ke hari ini · {when}")
        if status.mode == "monthly":
            lines.append(f"Jatah besok jadi {rupiah(status.tomorrow_allowance)}")
    else:
        fee = f" + biaya {rupiah(t.fee)}" if t.fee else ""
        lines.append(f"<b>-{rupiah(t.amount)}</b>{fee} · {name}")
        lines.append(f"{escape(t.category)} · {when}")
        if status.enabled:
            lines.append(budget_line(status))
    if party is not None and party.party_source == "guess" and t.kind == Kind.EXPENSE:
        guess = "teman" if party.party_type == PartyType.PERSON else "toko"
        lines.append(f"<i>Tebakan: {guess} ({escape(party.guess_reason)})</i>")
    if note:
        lines.append(note)
    return "\n".join(lines)


def overspend(status: BudgetStatus, level: int) -> str:
    if level >= 100:
        text = (f"<b>Budget hari ini sudah lewat.</b>\nKeluar {rupiah(status.spent_today)} "
                f"dari jatah {rupiah(status.allowance)} (lewat {rupiah(-status.left_today)}).")
    else:
        text = (f"<b>Budget hari ini tinggal {rupiah(status.left_today)}.</b>\n"
                f"Sudah {rupiah(status.spent_today)} dari {rupiah(status.allowance)}.")
    if status.mode == "monthly":
        text += f"\nKalau berhenti di sini, jatah besok {rupiah(status.tomorrow_allowance)}."
    return text


def day_report(d: DaySummary, status: BudgetStatus, title: str = "Ringkasan") -> str:
    lines = [f"<b>{title} {tanggal(d.day)}</b>"]
    lines.append(f"Keluar: <b>{rupiah(d.spent)}</b>" + (f" · Masuk: {rupiah(d.income)}" if d.income else ""))
    if status.enabled:
        lines.append(budget_line(status))
        if status.topups_today:
            lines.append(f"Top-up {rupiah(status.topups_today)} dihitung ke bulan")
    if d.by_category:
        lines.append("")
        lines += [f"• {escape(cat)}: {rupiah(value)}" for cat, value in d.by_category]
    count = sum(1 for t in d.transactions if t.kind != Kind.INTERNAL)
    lines.append(f"\n{count} transaksi")
    if status.mode == "monthly" and status.days_left > 1:
        lines.append(f"Jatah besok: <b>{rupiah(status.tomorrow_allowance)}</b>")
    return "\n".join(lines)


def month_report(m: MonthSummary, status: BudgetStatus) -> str:
    title = period_label(m.period) if m.period else f"{BULAN[m.month - 1]} {m.year}"
    lines = [f"<b>{title}</b>"]
    lines.append(f"Keluar: <b>{rupiah(m.spent)}</b> · Masuk: {rupiah(m.income)}")
    if status.mode == "monthly":
        pct = m.spent / status.month_budget if status.month_budget else 0
        lines.append(f"Budget: {rupiah(status.month_budget)} ({pct:.0%} terpakai), sisa {rupiah(status.month_left)}")
    lines.append(f"Rata-rata per hari: {rupiah(m.daily_average)}")
    if m.by_category:
        lines.append("")
        lines += [f"• {escape(cat)}: {rupiah(value)}" for cat, value in m.by_category[:8]]
    if m.top_counterparties:
        lines.append("\n<b>Paling banyak</b>")
        lines += [f"• {escape(name[:30])}: {rupiah(value)} ({count}x)" for name, value, count in m.top_counterparties]
    return "\n".join(lines)


def budget_report(status: BudgetStatus) -> str:
    if not status.enabled:
        return ("Budget belum diatur. Isi TRACKER_MONTHLY_BUDGET di .env "
                "(contoh: 1500000), lalu restart bot.")
    lines = ["<b>Budget</b>", budget_line(status),
             f"Keluar hari ini: {rupiah(status.spent_today)}"]
    if status.topups_today:
        lines.append(f"Top-up hari ini: {rupiah(status.topups_today)} (dihitung ke bulan)")
    if status.mode == "monthly":
        lines += [
            f"{'Bulan' if status.period is None or status.period.is_calendar_month else 'Periode'} ini: "
            f"{rupiah(status.month_spent)} dari {rupiah(status.month_budget)}, "
            f"sisa {rupiah(status.month_left)}",
            f"Sisa {status.days_left} hari (termasuk hari ini)",
            f"Jatah besok kalau berhenti sekarang: {rupiah(status.tomorrow_allowance)}",
        ]
    return "\n".join(lines)


# Shown in Telegram's "/" menu. (command, short description)
COMMANDS = [
    ("hariini", "Pengeluaran hari ini"),
    ("minggu", "7 hari terakhir"),
    ("bulanini", "Ringkasan periode ini"),
    ("grafik", "Grafik periode ini"),
    ("budget", "Sisa budget dan jatah besok"),
    ("sync", "Cek email sekarang"),
    ("help", "Daftar perintah"),
]

HELP = (
    "<b>Perintah</b>\n"
    "/hariini - pengeluaran hari ini\n"
    "/minggu - 7 hari terakhir\n"
    "/bulanini - ringkasan periode ini\n"
    "/grafik - grafik per kategori dan per hari\n"
    "/budget - sisa budget dan jatah besok\n"
    "/sync - cek email sekarang\n\n"
    "Setiap transaksi baru dikirim otomatis. Toko baru langsung ditanya kategorinya. "
    "Di laporan, tap kategori buat lihat isinya dan betulkan yang salah."
)


# ------------------------------------------------------------------ cards (v2 reports)
# Numbers sit in a <pre> block so they line up in columns on a phone (~30 chars wide).

WIDTH = 30

SHORT_CATEGORY = {
    "Makan & Minum": "Makan", "Belanja Harian": "Belanja", "Belanja Online": "Online",
    "Transportasi": "Transport", "Tagihan & Utilitas": "Tagihan", "Pulsa & Internet": "Pulsa",
    "Hiburan & Langganan": "Hiburan", "Transfer ke Orang": "Ke teman", "Top-up E-wallet": "Top-up",
    "Kesehatan": "Kesehatan", "Pendidikan": "Pendidikan", "Biaya Admin": "Biaya admin",
}
RANGE_NAMES = {"d": "Hari ini", "w": "7 hari", "p": "Periode"}


def short_category(category: str) -> str:
    return SHORT_CATEGORY.get(category, category[:12])


def _row(label: str, value: str, width: int = WIDTH) -> str:
    room = max(1, width - len(value) - 1)
    return f"{label[:room]:<{room}} {value}"


def progress(ratio: float, width: int = 15) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled) + f" {ratio:.0%}"


def _date_range(start: date, end: date) -> str:
    """'25 Sep s.d. 24 Okt' (end exclusive)."""
    last = end - timedelta(days=1)
    if start == last:
        return f"{start.day} {BULAN[start.month - 1][:3]}"
    left = f"{start.day}" if start.month == last.month else f"{start.day} {BULAN[start.month - 1][:3]}"
    return f"{left} s.d. {last.day} {BULAN[last.month - 1][:3]}"


def _pre(lines: list[str]) -> str:
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def _category_block(summary, max_categories: int = 8, max_items: int = 3) -> str:
    if not summary.categories:
        return "Belum ada pengeluaran."
    lines: list[str] = []
    shown = summary.categories[:max_categories]
    for block in shown:
        lines.append(_row(block.category, rupiah(block.total)))
        many = len(block.items) > 1
        for item in block.items[:max_items]:
            extra = (f" {item.count}x" if item.count > 1 else "") + (f" {rupiah_short(item.total)}" if many else "")
            room = WIDTH - 2 - len(extra)
            name = item.name if len(item.name) <= room else item.name[: room - 1] + "…"
            lines.append(f"  {name}{extra}")
        if len(block.items) > max_items:
            lines.append(f"  +{len(block.items) - max_items} lainnya")
    rest = summary.categories[max_categories:]
    if rest:
        lines.append(_row(f"{len(rest)} kategori lain", rupiah(sum(b.total for b in rest))))
    return "<b>Per kategori</b>\n" + _pre(lines)


def card(summary, status: BudgetStatus, view: str, title: str | None = None) -> str:
    """One report card. view: d (today), w (last 7 days), p (budget period)."""
    parts: list[str] = []
    if view == "d":
        head = title or "Hari ini"
        parts.append(f"<b>{head} · {tanggal(summary.start)}</b>")
        lines = [_row("Keluar", rupiah(summary.spent))]
        if summary.income:
            lines.append(_row("Masuk", rupiah(summary.income)))
        if status.enabled:
            lines.append(_row("Jatah hari ini", rupiah(status.allowance)))
            lines.append(progress(status.used_ratio))
            if status.left_today >= 0:
                lines.append(_row("Sisa hari ini", rupiah(status.left_today)))
            else:
                lines.append(_row("Lewat budget", rupiah(-status.left_today)))
            if status.topups_today:
                lines.append(_row("Top-up (ke periode)", rupiah(status.topups_today)))
            if status.mode == "monthly" and status.days_left > 1:
                lines.append(_row("Jatah besok", rupiah(status.tomorrow_allowance)))
        parts.append(_pre(lines))
    elif view == "w":
        parts.append(f"<b>{title or '7 hari terakhir'} · {_date_range(summary.start, summary.end)}</b>")
        lines = [_row("Keluar", rupiah(summary.spent)), _row("Rata-rata/hari", rupiah(summary.daily_average))]
        if summary.income:
            lines.append(_row("Masuk", rupiah(summary.income)))
        lines.append("")
        peak = max(summary.per_day.values(), default=0) or 1
        for day, value in summary.per_day.items():
            bar = "█" * round(10 * value / peak)
            lines.append(f"{HARI[day.weekday()][:3]} {day.day:>2} {bar:<10} {rupiah_short(value) if value else '-'}")
        parts.append(_pre(lines))
    else:
        period = status.period
        label = period_label(period) if period else _date_range(summary.start, summary.end)
        parts.append(f"<b>{title or 'Periode'} · {label}</b>")
        lines = [_row("Keluar", rupiah(summary.spent))]
        if summary.income:
            lines.append(_row("Masuk", rupiah(summary.income)))
        if status.mode == "monthly":
            ratio = summary.spent / status.month_budget if status.month_budget else 0
            lines += [_row("Budget", rupiah(status.month_budget)), progress(ratio),
                      _row("Sisa", rupiah(status.month_left)),
                      _row("Sisa hari", str(status.days_left)),
                      _row("Jatah hari ini", rupiah(status.allowance))]
        lines.append(_row("Rata-rata/hari", rupiah(summary.daily_average)))
        parts.append(_pre(lines))
        if summary.top_merchants:
            top = [_row(i.name if len(i.name) <= 18 else i.name[:17] + "…",
                        f"{i.count}x {rupiah_short(i.total)}") for i in summary.top_merchants]
            parts.append("<b>Paling sering</b>\n" + _pre(top))
    parts.append(_category_block(summary))
    return "\n".join(parts)


def category_detail(block, transactions: list, view: str, range_text: str) -> str:
    lines = [f"<b>{escape(block.category)} · {RANGE_NAMES.get(view, '')} ({range_text})</b>",
             f"{rupiah(block.total)} · {len(transactions)} transaksi"]
    rows = []
    for t in transactions[:20]:
        amount = rupiah_short(t.total_out if t.kind == Kind.EXPENSE else t.fee)
        when = t.occurred_at.strftime("%d/%m")
        room = WIDTH - len(when) - len(amount) - 2
        name = t.counterparty or "(tanpa nama)"
        name = name if len(name) <= room else name[: room - 1] + "…"
        rows.append(f"{when} {name:<{room}} {amount}")
    if len(transactions) > 20:
        rows.append(f"+{len(transactions) - 20} transaksi lain")
    return "\n".join(lines) + "\n" + _pre(rows) + "\nTap transaksi di bawah untuk ganti kategorinya."
