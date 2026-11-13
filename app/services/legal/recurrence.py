"""Next-occurrence date maths for recurring compliance filings (spec D16).

Pure: no Session, no models. The drift bug this exists to prevent only shows up over several
hops, so it is tested on its own in `tests/services/legal/test_recurrence.py`.
"""

from datetime import date

from dateutil.relativedelta import relativedelta

from app.db.models.enums import Recurrence

MONTHS: dict[Recurrence, int] = {
    Recurrence.monthly: 1,
    Recurrence.quarterly: 3,
    Recurrence.annually: 12,
}


def next_due_on(due_on: date, recurrence: Recurrence, anchor_day: int) -> date | None:
    """The next occurrence, anchored so the series cannot drift. None when not recurring.

    `day=anchor_day` sets the day-of-month and clamps to the month's last day, so a series
    anchored on the 31st reads 31 Jan -> 28 Feb -> **31 Mar**: February clamps, and March
    recovers.

    Computing from the previous occurrence instead would walk the series backwards and never
    recover — 31 Jan -> 28 Feb -> 28 Mar -> 28 Apr — which is why the anchor is stored on the row
    rather than inferred from whatever date the last occurrence happened to land on.

    The same holds over years: an annual series anchored on 29 February clamps to the 28th for
    three years and returns to the 29th in the next leap year.
    """
    months = MONTHS.get(recurrence)
    if months is None:
        return None
    # Annotated because relativedelta's __radd__ is untyped, so the sum is Any to mypy.
    nxt: date = due_on + relativedelta(months=+months, day=anchor_day)
    return nxt
