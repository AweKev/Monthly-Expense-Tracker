"""Bot logic without Telegram: decides what to say and what buttons to show.

Everything returns a Reply (text + button rows of (label, callback_data)), so it can be
unit-tested and the Telegram layer (app.py) only has to send it.
Callback data (max 64 bytes):
  c:<txn>        show category buttons        s:<txn>:<i>  pick category i
  a:<txn>        "always" for this name/QR    k:<txn>      "just this once"
  p:<txn>:t|m    it's a teman / toko          x:<txn>      back to the alert
"""

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
from ..summary import day_summary, period_summary
from . import texts

CATEGORIES = [cat.MAKAN, cat.BELANJA, cat.ONLINE, cat.TRANSPORT, cat.TAGIHAN, cat.PULSA, cat.HIBURAN,
              cat.KESEHATAN, cat.PENDIDIKAN, cat.TRANSFER_ORANG, cat.TUNAI, cat.LAINNYA]

Buttons = list[list[tuple[str, str]]]


@dataclass
class Reply:
    text: str
    buttons: Buttons = field(default_factory=list)


# ------------------------------------------------------------------ helpers

def _party(session: Session, t: Transaction) -> Counterparty | None:
    if not t.counterparty_key:
        return None
    return session.scalars(select(Counterparty).where(Counterparty.key == t.counterparty_key)).first()


def _status(session: Session, settings: Settings, t: Transaction | None, today: date) -> BudgetStatus:
    # Alerts for an older transaction show that day's budget, not today's.
    return budget_status(session, settings, t.occurred_at.date() if t else today)


def alert_buttons(t: Transaction, party: Counterparty | None) -> Buttons:
    if t.kind != Kind.EXPENSE:
        return []
    rows: Buttons = [[("Ganti kategori", f"c:{t.id}")]]
    if party is not None and party.party_source == "guess":
        rows.append([("Ini teman", f"p:{t.id}:t"), ("Ini toko", f"p:{t.id}:m")])
    return rows


def alert_reply(session: Session, settings: Settings, t: Transaction, note: str = "",
                buttons: bool = True) -> Reply:
    party = _party(session, t)
    status = _status(session, settings, t, t.occurred_at.date())
    return Reply(texts.alert(t, status, party, note), alert_buttons(t, party) if buttons else [])


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


# ------------------------------------------------------------------ reports

def today_report(session: Session, settings: Settings, today: date, title: str = "Hari ini") -> Reply:
    status = budget_status(session, settings, today)
    return Reply(texts.day_report(day_summary(session, today), status, title))


def month_report(session: Session, settings: Settings, today: date) -> Reply:
    status = budget_status(session, settings, today)
    period = period_for(today, settings.period_start_day)
    return Reply(texts.month_report(period_summary(session, period, today), status))


def budget_report(session: Session, settings: Settings, today: date) -> Reply:
    return Reply(texts.budget_report(budget_status(session, settings, today)))


# ------------------------------------------------------------------ buttons

def handle_callback(session: Session, settings: Settings, data: str) -> Reply | None:
    parts = data.split(":")
    action = parts[0]
    try:
        t = session.get(Transaction, int(parts[1]))
    except (IndexError, ValueError):
        return None
    if t is None:
        return Reply("Transaksi ini sudah tidak ada.")
    name = t.counterparty or "ini"

    if action == "c":
        rows: Buttons = []
        for i in range(0, len(CATEGORIES), 2):
            rows.append([(c, f"s:{t.id}:{j}") for j, c in enumerate(CATEGORIES[i:i + 2], start=i)])
        rows.append([("Batal", f"x:{t.id}")])
        return Reply(texts.alert(t, _status(session, settings, t, t.occurred_at.date()), None,
                                 "\n<b>Pilih kategori:</b>"), rows)

    if action == "s":
        category = CATEGORIES[int(parts[2])]
        t.category, t.category_source = category, "user"
        can_remember = bool(t.counterparty_key or t.counterparty)
        if not can_remember:
            return alert_reply(session, settings, t, f"<i>Kategori diganti ke {category}.</i>")
        return Reply(texts.alert(t, _status(session, settings, t, t.occurred_at.date()), None,
                                 f"\nSelalu pakai <b>{category}</b> untuk {texts.escape(name)}?"),
                     [[("Ya, selalu", f"a:{t.id}"), ("Sekali ini aja", f"k:{t.id}")]])

    if action == "a":
        party = _party(session, t)
        if party is not None:
            parties.tag(party, category=t.category)
            session.flush()
            n = pipeline.reclassify_party(session, settings, party.key)
        else:
            session.add(CategoryRule(pattern=t.counterparty, category=t.category, priority=200))
            session.flush()
            n = pipeline.reparse(session, settings, only_unparsed=False).parsed
        return alert_reply(session, settings, t, f"<i>Oke, {texts.escape(name)} selalu {t.category} "
                                                 f"({n} transaksi diperbarui).</i>", buttons=False)

    if action == "k":
        return alert_reply(session, settings, t, f"<i>Kategori diganti ke {t.category}.</i>", buttons=False)

    if action == "p":
        party = _party(session, t)
        if party is None:
            return alert_reply(session, settings, t)
        is_person = parts[2] == "t"
        parties.tag(party, PartyType.PERSON if is_person else PartyType.MERCHANT)
        session.flush()
        pipeline.reclassify_party(session, settings, party.key)
        session.refresh(t)
        label = "teman, masuk Transfer ke Orang" if is_person else "toko"
        return alert_reply(session, settings, t, f"<i>Oke, {texts.escape(name)} = {label}.</i>")

    if action == "x":
        return alert_reply(session, settings, t)

    return None
