"""Fetch -> store raw email -> parse -> categorize -> save transaction."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import parties
from .categorize import classify, load_user_rules
from .config import Settings
from .models import ParseStatus, RawEmail, SyncState, Transaction
from .parsing import NotATransaction, ParseFailure, parse_message
from .sources.base import RawMessage


@dataclass
class SyncStats:
    fetched: int = 0
    new: int = 0
    parsed: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def merge(self, status: ParseStatus, note: str = "") -> None:
        if status == ParseStatus.PARSED:
            self.parsed += 1
        elif status == ParseStatus.SKIPPED:
            self.skipped += 1
        elif status == ParseStatus.FAILED:
            self.failed += 1
            self.failures.append(note)


def _to_raw_email(msg: RawMessage) -> RawEmail:
    return RawEmail(
        message_id=msg.message_id,
        source=msg.source,
        received_at=msg.received_at,
        sender=msg.sender[:255],
        subject=msg.subject[:500],
        body_html=msg.body_html,
        body_text=msg.body_text,
    )


def _to_message(raw: RawEmail) -> RawMessage:
    return RawMessage(
        message_id=raw.message_id,
        source=raw.source,
        received_at=raw.received_at,
        sender=raw.sender,
        subject=raw.subject,
        body_html=raw.body_html,
        body_text=raw.body_text,
    )


def process_raw(session: Session, raw: RawEmail, settings: Settings, user_rules=None) -> tuple[ParseStatus, str]:
    """Parse one stored email into a Transaction (creating or updating it). User-set categories are kept."""
    try:
        parsed = parse_message(_to_message(raw))
    except NotATransaction as exc:
        raw.parse_status, raw.parse_error = ParseStatus.SKIPPED, str(exc)
        if raw.transaction:
            session.delete(raw.transaction)
        return ParseStatus.SKIPPED, str(exc)
    except ParseFailure as exc:
        raw.parse_status, raw.parse_error = ParseStatus.FAILED, str(exc)
        return ParseStatus.FAILED, f"{raw.subject!r}: {exc}"

    party = parties.get_or_create(session, parsed)
    cls = classify(parsed, settings, user_rules, party)
    txn = raw.transaction or Transaction(raw_email=raw)
    keep_user_category = txn.category_source == "user"

    txn.txn_type = parsed.txn_type
    txn.direction = parsed.direction
    txn.kind = cls.kind
    txn.amount = parsed.amount
    txn.fee = parsed.fee
    txn.counterparty = parsed.counterparty
    txn.counterparty_account = parsed.counterparty_account
    txn.reference = parsed.reference
    txn.counterparty_key = party.key if party else None
    txn.counterparty_id = parsed.counterparty_id or None
    txn.acquirer = parsed.acquirer or None
    txn.occurred_at = parsed.occurred_at
    if not keep_user_category:
        txn.category, txn.category_source = cls.category, cls.category_source

    session.add(txn)
    raw.parse_status, raw.parse_error = ParseStatus.PARSED, None
    return ParseStatus.PARSED, ""


def sync(session: Session, source, settings: Settings, since: datetime | None = None) -> SyncStats:
    """Pull new emails from a source. With no `since`, continues from the last synced email (or everything)."""
    stats = SyncStats()
    state_key = f"last_received:{source.name}"
    if since is None:
        state = session.get(SyncState, state_key)
        since = datetime.fromisoformat(state.value) if state else None

    known = set(session.scalars(select(RawEmail.message_id)))
    user_rules = load_user_rules(session)
    latest = since

    fetch_kwargs = {"since": since}
    if source.name == "gmail":
        fetch_kwargs["skip_ids"] = known  # don't even download emails we already have

    for msg in source.fetch(**fetch_kwargs):
        stats.fetched += 1
        if msg.message_id in known:
            continue
        known.add(msg.message_id)
        raw = _to_raw_email(msg)
        session.add(raw)
        stats.new += 1
        status, note = process_raw(session, raw, settings, user_rules)
        stats.merge(status, note)
        if latest is None or msg.received_at > latest:
            latest = msg.received_at
        if stats.new % 50 == 0:
            # Save progress, so an error halfway (rate limit, no internet) doesn't throw away
            # everything downloaded so far. The next sync skips these emails by id.
            # SyncState is only written at the end, so no older email gets skipped.
            session.commit()

    if latest is not None:
        session.merge(SyncState(key=state_key, value=latest.isoformat()))
    return stats


def reparse(session: Session, settings: Settings, only_unparsed: bool = True) -> SyncStats:
    """Run the parser again over stored emails, e.g. after improving it. Keeps user-set categories."""
    stats = SyncStats()
    query = select(RawEmail)
    if only_unparsed:
        query = query.where(RawEmail.parse_status.in_([ParseStatus.FAILED, ParseStatus.PENDING]))
    user_rules = load_user_rules(session)
    for raw in session.scalars(query):
        stats.fetched += 1
        status, note = process_raw(session, raw, settings, user_rules)
        stats.merge(status, note)
    return stats


def reclassify_party(session: Session, settings: Settings, key: str) -> int:
    """Re-run parse + classify for every email involving one counterparty (after tagging it)."""
    raws = session.scalars(
        select(RawEmail).join(Transaction).where(Transaction.counterparty_key == key)
    ).all()
    user_rules = load_user_rules(session)
    for raw in raws:
        process_raw(session, raw, settings, user_rules)
    return len(raws)


def counts_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(select(RawEmail.parse_status, func.count()).group_by(RawEmail.parse_status))
    return {status: count for status, count in rows}
