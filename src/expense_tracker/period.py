"""Budget periods: a calendar month by default, or e.g. the 25th to the 24th of the next month.

Set TRACKER_PERIOD_START_DAY=25 when money comes in on the 25th, so "this month" means
25 Sep to 24 Oct instead of 1 to 30 Sep.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Period:
    start: date  # first day, inclusive
    end: date  # first day of the next period, exclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def is_calendar_month(self) -> bool:
        return self.start.day == 1

    def days_left(self, today: date) -> int:
        """Days left including today."""
        return (self.end - today).days

    def days_passed(self, today: date) -> int:
        """Days that have started so far (for averages)."""
        if today < self.start:
            return 0
        if today >= self.end:
            return self.days
        return (today - self.start).days + 1

    def __contains__(self, day: date) -> bool:
        return self.start <= day < self.end


def _clamp(start_day: int) -> int:
    # Day 29-31 doesn't exist in every month, so keep it at most 28.
    return min(max(start_day, 1), 28)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def period_starting_in(year: int, month: int, start_day: int = 1) -> Period:
    """The period that starts in the given month."""
    day = _clamp(start_day)
    ny, nm = _next_month(year, month)
    return Period(date(year, month, day), date(ny, nm, day))


def period_for(today: date, start_day: int = 1) -> Period:
    """The period that contains `today`."""
    day = _clamp(start_day)
    if today.day >= day:
        return period_starting_in(today.year, today.month, day)
    py, pm = _prev_month(today.year, today.month)
    return period_starting_in(py, pm, day)
