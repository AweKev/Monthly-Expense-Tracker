from datetime import date, datetime

from expense_tracker.bot import actions, texts
from expense_tracker.budget import budget_status
from expense_tracker.period import period_for, period_starting_in
from expense_tracker.summary import period_summary

from .test_budget_bot import add, engine, settings  # noqa: F401 (engine is a fixture)


def test_calendar_month_is_the_default():
    p = period_for(date(2026, 9, 25))
    assert (p.start, p.end, p.days) == (date(2026, 9, 1), date(2026, 10, 1), 30)
    assert p.is_calendar_month


def test_period_from_the_25th():
    p = period_for(date(2026, 9, 25), 25)
    assert (p.start, p.end) == (date(2026, 9, 25), date(2026, 10, 25))
    assert p.days_left(date(2026, 9, 25)) == 30
    assert p.days_passed(date(2026, 9, 25)) == 1
    # Before the 25th we are still in the period that started last month.
    assert period_for(date(2026, 10, 24), 25).start == date(2026, 9, 25)
    assert period_for(date(2026, 10, 25), 25).start == date(2026, 10, 25)
    # Across the year boundary.
    assert period_for(date(2027, 1, 10), 25) == period_starting_in(2026, 12, 25)
    # Days that don't exist in every month are clamped to 28.
    assert period_for(date(2026, 2, 28), 31).start == date(2026, 2, 28)


def test_budget_ignores_spending_before_the_period(engine):
    s = settings(monthly_budget=1_500_000, period_start_day=25)
    with session_scope_(engine) as db:
        add(db, datetime(2026, 9, 10, 12), 900_000)   # previous period, not counted
        add(db, datetime(2026, 9, 25, 9), 40_000)     # today, first day of the period
        st = budget_status(db, s, date(2026, 9, 25))
    assert st.days_left == 30
    assert st.allowance == 1_500_000 // 30 == 50_000
    assert st.month_spent == 40_000
    assert st.left_today == 10_000


def test_period_report(engine):
    s = settings(monthly_budget=1_500_000, period_start_day=25)
    with session_scope_(engine) as db:
        add(db, datetime(2026, 9, 10, 12), 900_000)
        add(db, datetime(2026, 9, 26, 9), 40_000)
        m = period_summary(db, period_for(date(2026, 9, 27), 25), date(2026, 9, 27))
        text = actions.month_report(db, s, date(2026, 9, 27)).text
        budget_text = actions.budget_report(db, s, date(2026, 9, 27)).text
    assert m.spent == 40_000 and m.days_counted == 3 and len(m.per_day) == 30
    assert "25 Sep s.d. 24 Okt 2026" in text
    assert "Periode ini" in budget_text
    assert texts.period_label(period_for(date(2026, 9, 5))) == "September 2026"
    assert texts.period_label(period_for(date(2027, 1, 5), 25)) == "25 Des 2026 s.d. 24 Jan 2027"


def session_scope_(engine):
    from expense_tracker.db import session_scope
    return session_scope(engine)
