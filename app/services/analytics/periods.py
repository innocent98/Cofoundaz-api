"""Period model (D-ANA-L3): presets, custom ranges and the comparison period.

The workspace has no stored timezone yet (gap 72), so periods are UTC calendar days. All ranges are
inclusive `[start, end]` dates. A range is at most 24 months.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.services.analytics.errors import validation

PRESETS = ("7d", "30d", "90d", "12m", "this_month", "last_month", "custom")
COMPARES = ("none", "previous", "year")
MAX_DAYS = 24 * 31
MAX_BUCKETS = 12
MIN_DATE = date(2000, 1, 1)
MAX_AHEAD_DAYS = 366  # `to` may be at most a year ahead


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def iso(self) -> dict[str, str]:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


def today_utc() -> date:
    return datetime.now(UTC).date()


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _shift_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def resolve(
    preset: str = "30d",
    start: date | None = None,
    end: date | None = None,
    *,
    today: date | None = None,
) -> Period:
    today = today or today_utc()
    if preset not in PRESETS:
        raise validation("range", f"Pick one of: {', '.join(PRESETS)}.")
    if preset == "custom" or (start is not None and end is not None):
        if start is None or end is None:
            raise validation("from", "A custom range needs both from and to.")
        period = Period(start, end)
    elif preset == "7d":
        period = Period(today - timedelta(days=6), today)
    elif preset == "30d":
        period = Period(today - timedelta(days=29), today)
    elif preset == "90d":
        period = Period(today - timedelta(days=89), today)
    elif preset == "12m":
        period = Period(_shift_months(_month_start(today), -11), today)
    elif preset == "this_month":
        period = Period(_month_start(today), today)
    else:  # last_month
        first = _shift_months(_month_start(today), -1)
        period = Period(first, _month_start(today) - timedelta(days=1))
    if period.start > period.end:
        raise validation("from", "The start date must be on or before the end date.")
    if period.start < MIN_DATE:
        raise validation("from", "Pick a start date from 2000-01-01 onwards.")
    if period.end > today + timedelta(days=MAX_AHEAD_DAYS):
        raise validation("to", "Pick an end date within a year from today.")
    if period.days > MAX_DAYS:
        raise validation("to", "Pick a range of at most 24 months.")
    return period


def comparison(period: Period, mode: str) -> Period | None:
    if mode not in COMPARES:
        raise validation("compare", f"Pick one of: {', '.join(COMPARES)}.")
    if mode == "none":
        return None
    if mode == "previous":
        cmp = Period(period.start - timedelta(days=period.days), period.start - timedelta(days=1))
    else:
        try:
            cmp = Period(
                period.start.replace(year=period.start.year - 1),
                period.end.replace(year=period.end.year - 1),
            )
        except ValueError:  # 29 Feb
            cmp = Period(period.start - timedelta(days=365), period.end - timedelta(days=365))
    # A comparison that falls before the supported range has no data to compare: none, not an error.
    return cmp if cmp.start >= MIN_DATE else None


def buckets(period: Period) -> list[Period]:
    """At most 12 contiguous sub-periods covering `period`: days, weeks or calendar months."""
    if period.days <= MAX_BUCKETS:
        return [
            Period(d, d) for d in (period.start + timedelta(days=i) for i in range(period.days))
        ]
    if period.days <= MAX_BUCKETS * 7:
        out, cur = [], period.start
        while cur <= period.end:
            out.append(Period(cur, min(cur + timedelta(days=6), period.end)))
            cur += timedelta(days=7)
        return out
    # Calendar months, merged in steps of 2, 3... months so a long range still yields <= 12 buckets.
    months = (period.end.year - period.start.year) * 12 + period.end.month - period.start.month + 1
    step = -(-months // MAX_BUCKETS)
    out, cur = [], period.start
    while cur <= period.end:
        nxt = _shift_months(_month_start(cur), step)
        out.append(Period(cur, min(nxt - timedelta(days=1), period.end)))
        cur = nxt
    return out
