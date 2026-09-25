"""Bot logic without Telegram: decides what to say and what buttons to show.

Everything returns a Reply (text + button rows of (label, callback_data), optionally a chart image),
so it can be unit-tested and the Telegram layer (app.py) only has to send it.

Callback data (max 64 bytes). <r> is a report view: d (today), w (last 7 days), p (budget period).
A trailing :<r> on transaction actions means "came from that report", so a Kembali button can go back.
  v:<r>             show report view            g:<r>:<crc>     transactions in one category
  f:<r>             send the chart image
  c:<txn>[:<r>]     show category buttons       s:<txn>:<i>[:<r>]  pick category i (then ask "always?")
  a:<txn>[:<r>]     "always" for this name/QR   k:<txn>[:<r>]   "just this once"
  q:<txn>:<i>       new store: category i, remembered right away
  p:<txn>:t|m       it's a teman / toko         x:<txn>[:<r>]   back to the alert
"""

import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import categorize as cat
from .. import parties, pipeline
from ..budget import BudgetStatus, budget_status
from ..config import Settings
from ..models import CategoryRule, Counterparty, Kind, PartyType, SyncState, Transaction, TxnType
from ..period import period_for
from ..summary import RangeSummary, range_summary, rupiah_short
from . import texts

CATEGORIES = [cat.MAKAN, cat.BELANJA, cat.ONLINE, cat.TRANSPORT, cat.TAGIHAN, cat.PULSA, cat.HIBURAN,
              cat.KESEHATAN, cat.PENDIDIKAN, cat.TRANSFER_ORANG, cat.TUNAI, cat.LAINNYA]
VIEWS = ("d", "w", "p")
ASK_TYPES = (TxnType.QRIS, TxnType.PURCHASE, TxnType.OTHER)

Buttons = list[list[tuple[str, str]]]


@dataclass
class Reply:
    text: str
    buttons: Buttons = field(default_factory=list)
    photo: bytes | None = None  # when set, app.py sends a new photo message with `text` as caption


# ------------------------------------------------------------------ helpers

def _party(session: Session, t: Transaction) -> Counterparty | None:
    if not t.counterparty_key:
        return None
    return session.scalars(select(Counterparty).where(Counterparty.key == t.counterparty_key)).first()


def _status(session: Session, settings: Settings, t: Transaction | None, today: date) -> BudgetStatus:
    # Alerts for an older transaction show that day's budget, not today's.
    return budget_status(session, settings, t.occurred_at.date() if t else today)


def needs_category(t: Transaction) -> bool:
    """A store we don't know yet: ask right away instead of silently filing it under Lainnya."""
    return (t.kind == Kind.EXPENSE and t.category == cat.LAINNYA and t.category_source == "default"
            and t.txn_type in ASK_TYPES)


def _category_grid(prefix: str, t: Transaction, suffix: str = "") -> Buttons:
    rows: Buttons = []
    for i in range(0, len(CATEGORIES), 2):
        rows.append([(texts.short_category(c) if prefix == "q" else c, f"{prefix}:{t.id}:{j}{suffix}")
                     for j, c in enumerate(CATEGORIES[i:i + 2], start=i)])
    return rows


def alert_buttons(t: Transaction, party: Counterparty | None) -> Buttons:
    if t.kind != Kind.EXPENSE:
        return []
    if needs_category(t):
        return _category_grid("q", t)
    rows: Buttons = [[("Ganti kategori", f"c:{t.id}")]]
    if party is not None and party.party_source == "guess":
        rows.append([("Ini teman", f"p:{t.id}:t"), ("Ini toko", f"p:{t.id}:m")])
    return rows


def alert_reply(session: Session, settings: Settings, t: Transaction, note: str = "",
                buttons: bool = True, back: str | None = None) -> Reply:
    party = _party(session, t)
    status = _status(session, settings, t, t.occurred_at.date())
    if buttons and not note and needs_category(t):
        note = "<b>Toko baru. Ini kategori apa?</b> (diingat untuk seterusnya)"
    rows = alert_buttons(t, party) if buttons else []
    if back:
        rows = rows + [[("Kembali ke laporan", f"v:{back}")]]
    return Reply(texts.alert(t, status, party, note), rows)


def remember_category(session: Session, settings: Settings, t: Transaction, category: str) -> int:
    """Use `category` for this transaction and every past/future one with the same QR (or name).

    Picking "Transfer ke Orang" marks the counterparty as a person instead.
    Returns how many transactions were updated.
    """
    t.category, t.category_source = category, "user"
    party = _party(session, t)
    if party is not None:
        if category == cat.TRANSFER_ORANG:
            parties.tag(party, PartyType.PERSON, category="")
        elif party.party_type == PartyType.PERSON:
            parties.tag(party, category=category)  # a friend who, say, sells food: keep them a person
        else:
            parties.tag(party, PartyType.MERCHANT, category=category)
        session.flush()
        return pipeline.reclassify_party(session, settings, party.key)
    if t.counterparty:
        session.add(CategoryRule(pattern=t.counterparty, category=category, priority=200))
        session.flush()
        return pipeline.reparse(session, settings, only_unparsed=False).parsed
    return 1


# ------------------------------------------------------------------ new transactions

def pending_alerts(session: Session, settings: Settings, now: datetime) -> list[tuple[int, Reply]]:
    """Transactions not alerted yet. Old ones (first backfill) and internal transfers are marked silently."""
    cutoff = now - timedelta(hours=settings.alert_max_age_hours)
    pending = session.scalars(
        select(Transaction).where(Transaction.notified_at.is_(None)).order_by(Transaction.occurred_at)
    ).all()
    out = []
    for t in pending:
        silent = t.occurred_at < cutoff or (t.kind == Kind.INTERNAL and t.txn_type != TxnType.TOPUP)
        if silent:
            t.notified_at = now
        else:
            out.append((t.id, alert_reply(session, settings, t)))
    return out


def mark_notified(session: Session, txn_id: int, now: datetime) -> None:
    t = session.get(Transaction, txn_id)
    if t is not None:
        t.notified_at = now


def budget_warnings(session: Session, settings: Settings, today: date) -> list[Reply]:
    """At most one 80% heads-up and one 100% warning per day."""
    status = budget_status(session, settings, today)
    if not status.enabled or status.spent_today == 0:
        return []
    replies = []
    for level, threshold in ((80, 0.8), (100, 1.0)):
        key = f"budget_warned_{level}:{today.isoformat()}"
        over = status.spent_today > status.allowance if level == 100 else status.used_ratio >= threshold
        if over and session.get(SyncState, key) is None:
            session.add(SyncState(key=key, value="1"))
            replies.append(Reply(texts.overspend(status, level)))
    return replies[-1:]  # if both fire at once, only send the stronger one


# ------------------------------------------------------------------ report views

def view_range(settings: Settings, view: str, today: date) -> tuple[date, date]:
    if view == "d":
        return today, today + timedelta(days=1)
    if view == "w":
        return today - timedelta(days=6), today + timedelta(days=1)
    period = period_for(today, settings.period_start_day)
    return period.start, period.end


def _crc(category: str) -> str:
    return f"{zlib.crc32(category.encode()):08x}"


def _view_buttons(summary: RangeSummary, view: str) -> Buttons:
    tabs = [((f"• {name}" if key == view else name), f"v:{key}") for key, name in texts.RANGE_NAMES.items()]
    rows: Buttons = [tabs]
    blocks = summary.categories[:8]
    for i in range(0, len(blocks), 2):
        rows.append([(f"{texts.short_category(b.category)} {rupiah_short(b.total)}", f"g:{view}:{_crc(b.category)}")
                     for b in blocks[i:i + 2]])
    rows.append([("Grafik", f"f:{view}")])
    return rows


def report(session: Session, settings: Settings, view: str, today: date, title: str | None = None) -> Reply:
    start, end = view_range(settings, view, today)
    summary = range_summary(session, start, end, today)
    status = budget_status(session, settings, today)
    return Reply(texts.card(summary, status, view, title), _view_buttons(summary, view))


def category_view(session: Session, settings: Settings, view: str, crc: str, today: date) -> Reply:
    start, end = view_range(settings, view, today)
    summary = range_summary(session, start, end, today)
    block = next((b for b in summary.categories if _crc(b.category) == crc), None)
    if block is None:
        return report(session, settings, view, today)
    txns = sorted((session.get(Transaction, i) for i in block.transaction_ids),
                  key=lambda t: t.occurred_at, reverse=True)
    rows: Buttons = []
    for t in txns[:8]:
        if t.kind != Kind.EXPENSE:
            continue
        name = t.counterparty or "(tanpa nama)"
        rows.append([(f"Ubah: {name[:24]} {rupiah_short(t.total_out)}", f"c:{t.id}:{view}")])
    rows.append([("Kembali", f"v:{view}")])
    range_text = texts._date_range(start, end)
    return Reply(texts.category_detail(block, txns, view, range_text), rows)


def chart(session: Session, settings: Settings, view: str, today: date) -> Reply:
    from ..charts import render_report  # matplotlib is heavy; only load it when a chart is asked for

    chart_view = "w" if view == "d" else view  # one day is too short for a chart
    start, end = view_range(settings, chart_view, today)
    summary = range_summary(session, start, end, today)
    status = budget_status(session, settings, today)
    title = ("7 hari terakhir" if chart_view == "w" else "Periode") + f" · {texts._date_range(start, end)}"
    caption = f"<b>{title}</b>\nKeluar {texts.rupiah(summary.spent)}"
    if status.mode == "monthly" and chart_view == "p":
        caption += f" dari {texts.rupiah(status.month_budget)}"
    return Reply(caption, photo=render_report(summary, status, title, today))


# Old names, still used by commands and the daily summary job.
def today_report(session: Session, settings: Settings, today: date, title: str = "Hari ini") -> Reply:
    return report(session, settings, "d", today, title)


def week_report(session: Session, settings: Settings, today: date) -> Reply:
    return report(session, settings, "w", today)


def month_report(session: Session, settings: Settings, today: date) -> Reply:
    return report(session, settings, "p", today)


def budget_report(session: Session, settings: Settings, today: date) -> Reply:
    return Reply(texts.budget_report(budget_status(session, settings, today)))


def chart_report(session: Session, settings: Settings, today: date) -> Reply:
    return chart(session, settings, "p", today)


# ------------------------------------------------------------------ buttons

def _split_back(parts: list[str], required: int) -> tuple[list[str], str | None]:
    """Strip an optional trailing view code (the report the user came from)."""
    if len(parts) > required and parts[-1] in VIEWS:
        return parts[:-1], parts[-1]
    return parts, None


def handle_callback(session: Session, settings: Settings, data: str, today: date | None = None) -> Reply | None:
    today = today or date.today()
    parts = data.split(":")
    action = parts[0]

    if action == "v" and len(parts) == 2 and parts[1] in VIEWS:
        return report(session, settings, parts[1], today)
    if action == "g" and len(parts) == 3 and parts[1] in VIEWS:
        return category_view(session, settings, parts[1], parts[2], today)
    if action == "f" and len(parts) == 2 and parts[1] in VIEWS:
        return chart(session, settings, parts[1], today)

    required = {"c": 2, "s": 3, "a": 2, "k": 2, "q": 3, "p": 3, "x": 2}.get(action)
    if required is None:
        return None
    parts, back = _split_back(parts, required)
    try:
        t = session.get(Transaction, int(parts[1]))
    except (IndexError, ValueError):
        return None
    if t is None:
        return Reply("Transaksi ini sudah tidak ada.")
    name = t.counterparty or "ini"
    suffix = f":{back}" if back else ""

    if action == "c":
        rows = _category_grid("s", t, suffix)
        rows.append([("Batal", f"x:{t.id}{suffix}")])
        return Reply(texts.alert(t, _status(session, settings, t, t.occurred_at.date()), None,
                                 "\n<b>Pilih kategori:</b>"), rows)

    if action == "s":
        category = CATEGORIES[int(parts[2])]
        t.category, t.category_source = category, "user"
        can_remember = bool(t.counterparty_key or t.counterparty)
        if not can_remember:
            return alert_reply(session, settings, t, f"<i>Kategori diganti ke {category}.</i>", back=back)
        return Reply(texts.alert(t, _status(session, settings, t, t.occurred_at.date()), None,
                                 f"\nSelalu pakai <b>{category}</b> untuk {texts.escape(name)}?"),
                     [[("Ya, selalu", f"a:{t.id}{suffix}"), ("Sekali ini aja", f"k:{t.id}{suffix}")]])

    if action == "a":
        n = remember_category(session, settings, t, t.category)
        return alert_reply(session, settings, t, f"<i>Oke, {texts.escape(name)} selalu {t.category} "
                                                 f"({n} transaksi diperbarui).</i>", buttons=False, back=back)

    if action == "q":
        category = CATEGORIES[int(parts[2])]
        n = remember_category(session, settings, t, category)
        session.refresh(t)
        label = "teman, masuk Transfer ke Orang" if category == cat.TRANSFER_ORANG else category
        more = f" ({n} transaksi diperbarui)" if n > 1 else ""
        return alert_reply(session, settings, t, f"<i>Oke, {texts.escape(name)} = {label}. "
                                                 f"Diingat untuk seterusnya{more}.</i>", buttons=False, back=back)

    if action == "k":
        return alert_reply(session, settings, t, f"<i>Kategori diganti ke {t.category}.</i>", buttons=False,
                           back=back)

    if action == "p":
        party = _party(session, t)
        if party is None:
            return alert_reply(session, settings, t, back=back)
        is_person = parts[2] == "t"
        parties.tag(party, PartyType.PERSON if is_person else PartyType.MERCHANT)
        session.flush()
        pipeline.reclassify_party(session, settings, party.key)
        session.refresh(t)
        label = "teman, masuk Transfer ke Orang" if is_person else "toko"
        return alert_reply(session, settings, t, f"<i>Oke, {texts.escape(name)} = {label}.</i>", back=back)

    if action == "x":
        if back:
            return report(session, settings, back, today)
        return alert_reply(session, settings, t)

    return None
