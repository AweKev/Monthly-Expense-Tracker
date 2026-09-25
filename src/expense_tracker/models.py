"""Database tables.

Times are stored as naive datetimes in local time (Asia/Jakarta by default),
since every transaction here happens in one timezone.
Amounts are whole Rupiah (int).
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TxnType(StrEnum):
    QRIS = "qris"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    TOPUP = "topup"
    BILL = "bill"  # PLN, pulsa, paket data, BPJS, etc.
    ATM_WITHDRAWAL = "atm_withdrawal"
    PURCHASE = "purchase"  # debit card / online payment not covered above
    OTHER = "other"


class Direction(StrEnum):
    OUT = "out"
    IN = "in"


class Kind(StrEnum):
    """How a transaction counts in the totals."""

    EXPENSE = "expense"
    INCOME = "income"
    INTERNAL = "internal"  # own accounts / own e-wallet top-ups: not spending


class PartyType(StrEnum):
    PERSON = "person"  # friend/family: payments go to "Transfer ke Orang" (and later the utang flow)
    MERCHANT = "merchant"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    SKIPPED = "skipped"  # not a transaction email (promo, OTP, newsletter)
    FAILED = "failed"


class RawEmail(Base):
    """Every bank email we fetched, kept so it can be re-parsed when the parser improves."""

    __tablename__ = "raw_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32))  # gmail | eml
    received_at: Mapped[datetime] = mapped_column(DateTime)
    sender: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    parse_status: Mapped[str] = mapped_column(String(16), default=ParseStatus.PENDING)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped["Transaction | None"] = relationship(back_populates="raw_email")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("raw_email_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_email_id: Mapped[int] = mapped_column(ForeignKey("raw_emails.id"))
    bank: Mapped[str] = mapped_column(String(32), default="mandiri")

    txn_type: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16), index=True)

    amount: Mapped[int] = mapped_column(Integer)
    fee: Mapped[int] = mapped_column(Integer, default=0)

    counterparty: Mapped[str] = mapped_column(String(255), default="")
    counterparty_account: Mapped[str] = mapped_column(String(64), default="")
    reference: Mapped[str] = mapped_column(String(128), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Stable identity of the other side: "pan:<Merchant PAN>", "acct:<account>" or "name:<NAME>".
    counterparty_key: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    counterparty_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # raw Merchant PAN
    acquirer: Mapped[str | None] = mapped_column(String(64), nullable=True)

    category: Mapped[str] = mapped_column(String(64), default="Lainnya")
    category_source: Mapped[str] = mapped_column(String(16), default="default")  # rule | user | default
    note: Mapped[str] = mapped_column(Text, default="")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Telegram alert sent/skipped

    raw_email: Mapped[RawEmail] = relationship(back_populates="transaction")

    @property
    def total_out(self) -> int:
        return self.amount + self.fee


class Counterparty(Base):
    """Someone you pay or get paid by, recognized by a stable key (QR Merchant PAN, account, or name).

    party_type starts as a guess and becomes "user" once you tag it (CLI now, Telegram bot in phase 2).
    """

    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    party_type: Mapped[str] = mapped_column(String(16), default=PartyType.MERCHANT)
    party_source: Mapped[str] = mapped_column(String(16), default="guess")  # guess | user
    guess_reason: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)  # your fixed category for them
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CategoryRule(Base):
    """User-defined rules (phase 2 bot corrections land here). Seed rules live in code."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(255))  # case-insensitive substring of counterparty
    category: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=100)  # higher wins
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SyncState(Base):
    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
