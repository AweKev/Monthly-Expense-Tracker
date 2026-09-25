"""Daily and monthly totals. Returns plain data so the CLI, bot and API can all reuse it."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .categorize import TRANSFER_ORANG
from .models import Kind, Transaction, TxnType
from .period import Period, period_starting_in

ADMIN_FEE = "Biaya Admin"


@dataclass
class DaySummary:
    day: date
    spent: int
    income: int
    by_category: list[tuple[str, int]]
    transactions: list[Transaction]
    budget: int = 0

    @property
    def budget_left(self) -> int | None:
        return self.budget - self.spent if self.budget else None


@dataclass
class MonthSummary:
    year: int
    month: int
    spent: int
    income: int
    internal: int
    by_category: list[tuple[str, int]]
    per_day: dict[date, int]
    top_counterparties: list[tuple[str, int, int]]  # name, total, count
    days_counted: int
    transaction_count: int
    excluded: list[tuple[str, int]] = field(default_factory=list)  # internal categories, shown but not counted
    period: Period | None = None

    @property
    def daily_average(self) -> int:
        return self.spent // self.days_counted if self.days_counted else 0


def _in_range(session: Session, start: datetime, end: datetime) -> list[Transaction]:
    query = (
        select(Transaction)
        .where(Transaction.occurred_at >= start, Transaction.occurred_at < end)
        .order_by(Transaction.occurred_at)
    )
    return list(session.scalars(query))


def spending_of(t: Transaction) -> int:
    """How much of a transaction counts as spending. Same rule everywhere (summary, budget, bot)."""
    if t.kind == Kind.EXPENSE:
        return t.total_out
    if t.kind == Kind.INTERNAL and t.direction == "out":
        return t.fee  # admin fee on an internal transfer is still real spending
    return 0


def _sorted_totals(totals: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def day_summary(session: Session, day: date, budget: int = 0) -> DaySummary:
    start = datetime.combine(day, datetime.min.time())
    txns = _in_range(session, start, start + timedelta(days=1))
    by_cat: dict[str, int] = defaultdict(int)
    spent = income = 0
    for t in txns:
        if t.kind == Kind.EXPENSE:
            spent += t.total_out
            by_cat[t.category] += t.total_out
        elif t.kind == Kind.INCOME:
            income += t.amount
        elif t.fee:  # admin fee on an internal transfer/top-up is still real spending
            spent += t.fee
            by_cat[ADMIN_FEE] += t.fee
    return DaySummary(day, spent, income, _sorted_totals(by_cat), txns, budget)


def month_summary(session: Session, year: int, month: int, today: date | None = None,
                  start_day: int = 1) -> MonthSummary:
    """Totals for the period that starts in this month (a calendar month when start_day is 1)."""
    return period_summary(session, period_starting_in(year, month, start_day), today)


def period_summary(session: Session, period: Period, today: date | None = None) -> MonthSummary:
    today = today or date.today()
    start = datetime.combine(period.start, datetime.min.time())
    end = datetime.combine(period.end, datetime.min.time())
    days_in_month = period.days
    txns = _in_range(session, start, end)

    by_cat: dict[str, int] = defaultdict(int)
    excluded: dict[str, int] = defaultdict(int)
    per_day: dict[date, int] = {start.date() + timedelta(days=i): 0 for i in range(days_in_month)}
    by_party: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    spent = income = internal = 0

    for t in txns:
        if t.kind == Kind.EXPENSE:
            spent += t.total_out
            by_cat[t.category] += t.total_out
            per_day[t.occurred_at.date()] += t.total_out
            name = t.counterparty or "(tanpa nama)"
            by_party[name][0] += t.total_out
            by_party[name][1] += 1
        elif t.kind == Kind.INCOME:
            income += t.amount
        else:
            internal += t.amount
            excluded[t.category] += t.amount
            if t.fee:  # admin fee on an internal transfer/top-up is still real spending
                spent += t.fee
                by_cat[ADMIN_FEE] += t.fee
                per_day[t.occurred_at.date()] += t.fee

    # Average over days that have passed, not the whole period, so mid-period numbers make sense.
    days_counted = period.days_passed(today)

    top = sorted(((n, v[0], v[1]) for n, v in by_party.items()), key=lambda x: x[1], reverse=True)[:5]
    return MonthSummary(
        period.start.year, period.start.month, spent, income, internal, _sorted_totals(by_cat), per_day, top,
        days_counted, len(txns), _sorted_totals(excluded), period,
    )


def rupiah(value: int) -> str:
    return "Rp" + f"{value:,}".replace(",", ".")


# ------------------------------------------------------------------ any date range, with names per category

@dataclass
class Item:
    name: str
    total: int
    count: int


@dataclass
class CategoryBlock:
    category: str
    total: int
    items: list[Item]  # who the money went to, biggest first
    transaction_ids: list[int]


@dataclass
class RangeSummary:
    start: date  # inclusive
    end: date  # exclusive
    spent: int
    income: int
    categories: list[CategoryBlock]
    per_day: dict[date, int]
    top_merchants: list[Item]  # stores only: no top-ups, transfers to people or internal moves
    transaction_count: int
    days_passed: int

    @property
    def daily_average(self) -> int:
        return self.spent // self.days_passed if self.days_passed else 0


def range_summary(session: Session, start: date, end: date, today: date | None = None) -> RangeSummary:
    """Spending between start (inclusive) and end (exclusive), grouped by category with the names inside."""
    today = today or date.today()
    txns = _in_range(session, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()))
    per_day = {start + timedelta(days=i): 0 for i in range((end - start).days)}
    blocks: dict[str, dict] = {}
    merchants: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    spent = income = count = 0

    for t in txns:
        if t.kind == Kind.INCOME:
            income += t.amount
            count += 1
            continue
        value = spending_of(t)
        if not value:
            continue
        count += 1
        category = t.category if t.kind == Kind.EXPENSE else ADMIN_FEE
        name = t.counterparty or "(tanpa nama)"
        block = blocks.setdefault(category, {"total": 0, "items": defaultdict(lambda: [0, 0]), "ids": []})
        block["total"] += value
        block["items"][name][0] += value
        block["items"][name][1] += 1
        block["ids"].append(t.id)
        spent += value
        per_day[t.occurred_at.date()] = per_day.get(t.occurred_at.date(), 0) + value
        if t.kind == Kind.EXPENSE and t.txn_type != TxnType.TOPUP and t.category != TRANSFER_ORANG:
            merchants[name][0] += value
            merchants[name][1] += 1

    def items(d: dict) -> list[Item]:
        return sorted((Item(n, v[0], v[1]) for n, v in d.items()), key=lambda i: i.total, reverse=True)

    categories = sorted(
        (CategoryBlock(cat, b["total"], items(b["items"]), b["ids"]) for cat, b in blocks.items()),
        key=lambda b: b.total, reverse=True,
    )
    if today < start:
        days_passed = 0
    elif today >= end:
        days_passed = (end - start).days
    else:
        days_passed = (today - start).days + 1
    return RangeSummary(start, end, spent, income, categories, per_day, items(merchants)[:5], count, days_passed)


def rupiah_short(value: int) -> str:
    """Rp62,4rb / Rp1,2jt: for buttons and name lines where space is tight."""
    if value < 1_000:
        return f"Rp{value}"
    if value < 999_950:
        text = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"Rp{text.replace('.', ',')}rb"
    text = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
    return f"Rp{text.replace('.', ',')}jt"
