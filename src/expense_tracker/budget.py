"""Daily allowance from a monthly budget.

allowance today = (monthly budget - spent this period before today) / days left including today

The period is a calendar month, or starts on TRACKER_PERIOD_START_DAY (see period.py).

So overspending today shrinks tomorrow's allowance, and underspending grows it.
E-wallet top-ups don't count toward *today's* warning (you usually spend a top-up over several days),
but they do count in the month, so they get spread over the remaining days automatically.
With no monthly budget, a fixed TRACKER_DAILY_BUDGET is used instead.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Transaction, TxnType
from .period import Period, period_for
from .summary import spending_of


@dataclass
class BudgetStatus:
    day: date
    mode: str  # monthly | daily | off
    allowance: int  # what you can spend today
    spent_today: int  # counts toward today's allowance (top-ups excluded)
    topups_today: int  # spent today but only counted in the month
    month_budget: int
    month_spent: int  # everything this month, today and top-ups included
    days_left: int  # including today
    period: Period | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def left_today(self) -> int:
        return self.allowance - self.spent_today

    @property
    def used_ratio(self) -> float:
        return self.spent_today / self.allowance if self.allowance else (1.0 if self.spent_today else 0.0)

    @property
    def month_left(self) -> int:
        return self.month_budget - self.month_spent

    @property
    def tomorrow_allowance(self) -> int:
        """What tomorrow's allowance becomes if nothing else is spent today (monthly mode)."""
        if self.mode != "monthly":
            return self.allowance
        if self.days_left <= 1:
            return 0
        return max(0, self.month_left) // (self.days_left - 1)


def counts_for_today(t: Transaction) -> bool:
    return t.txn_type != TxnType.TOPUP


def budget_status(session: Session, settings: Settings, today: date) -> BudgetStatus:
    period = period_for(today, settings.period_start_day)
    month_start = datetime.combine(period.start, datetime.min.time())
    day_start = datetime.combine(today, datetime.min.time())
    days_left = period.days_left(today)

    txns = session.scalars(
        select(Transaction).where(Transaction.occurred_at >= month_start,
                                  Transaction.occurred_at < day_start + timedelta(days=1))
    ).all()
    before_today = spent_today = topups_today = 0
    for t in txns:
        value = spending_of(t)
        if t.occurred_at < day_start:
            before_today += value
        elif counts_for_today(t):
            spent_today += value
        else:
            topups_today += value
    month_spent = before_today + spent_today + topups_today

    if settings.monthly_budget > 0:
        allowance = max(0, settings.monthly_budget - before_today) // days_left
        mode = "monthly"
    elif settings.daily_budget > 0:
        allowance, mode = settings.daily_budget, "daily"
    else:
        allowance, mode = 0, "off"

    return BudgetStatus(today, mode, allowance, spent_today, topups_today,
                        settings.monthly_budget, month_spent, days_left, period)
