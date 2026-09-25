"""Daily and monthly totals. Returns plain data so the CLI, bot and API can all reuse it."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Kind, Transaction
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
