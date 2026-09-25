"""Message texts for the Telegram bot (HTML parse mode). Pure functions, easy to test."""

from datetime import date, timedelta
from html import escape as _escape


def escape(value: str) -> str:
    return _escape(value, quote=False)

from ..budget import BudgetStatus
from ..models import Counterparty, Kind, PartyType, Transaction, TxnType
from ..summary import DaySummary, MonthSummary, rupiah

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


HELP = (
    "<b>Perintah</b>\n"
    "/hariini - pengeluaran hari ini\n"
    "/bulanini - ringkasan bulan ini\n"
    "/budget - sisa budget dan jatah besok\n"
    "/sync - cek email sekarang\n\n"
    "Setiap transaksi baru dikirim otomatis. Tombol di bawahnya buat ganti kategori "
    "atau bilang itu teman / toko."
)
