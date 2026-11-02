from datetime import date

import pytest

from app.db.models.enums import Recurrence
from app.services.legal.recurrence import MONTHS, next_due_on


def test_none_has_no_next_occurrence():
    assert next_due_on(date(2026, 1, 31), Recurrence.none, 31) is None


def test_the_step_sizes():
    assert MONTHS == {
        Recurrence.monthly: 1,
        Recurrence.quarterly: 3,
        Recurrence.annually: 12,
    }
    assert Recurrence.none not in MONTHS


@pytest.mark.parametrize(
    "start,recurrence,anchor,expected",
    [
        # MONTHLY from the 31st. February clamps to its last day, and the series then returns to
        # the 31st in March. Computing from the PREVIOUS occurrence instead of the anchor would
        # give 28 Mar and walk the series backwards forever.
        (date(2026, 1, 31), Recurrence.monthly, 31, date(2026, 2, 28)),
        (date(2026, 2, 28), Recurrence.monthly, 31, date(2026, 3, 31)),
        (date(2026, 3, 31), Recurrence.monthly, 31, date(2026, 4, 30)),
        (date(2026, 4, 30), Recurrence.monthly, 31, date(2026, 5, 31)),
        # Same, in a leap year: February clamps to the 29th.
        (date(2028, 1, 31), Recurrence.monthly, 31, date(2028, 2, 29)),
        # QUARTERLY from 30 November.
        (date(2026, 11, 30), Recurrence.quarterly, 30, date(2027, 2, 28)),
        (date(2027, 2, 28), Recurrence.quarterly, 30, date(2027, 5, 30)),
        (date(2027, 5, 30), Recurrence.quarterly, 30, date(2027, 8, 30)),
        # ANNUALLY from 29 February. It clamps to the 28th for three years, then returns to the
        # 29th in 2032 — the next leap year. The anchor is never lost, which is the whole point.
        (date(2028, 2, 29), Recurrence.annually, 29, date(2029, 2, 28)),
        (date(2029, 2, 28), Recurrence.annually, 29, date(2030, 2, 28)),
        (date(2030, 2, 28), Recurrence.annually, 29, date(2031, 2, 28)),
        (date(2031, 2, 28), Recurrence.annually, 29, date(2032, 2, 29)),
        # An ordinary mid-month date is unremarkable.
        (date(2026, 7, 15), Recurrence.monthly, 15, date(2026, 8, 15)),
        (date(2026, 12, 15), Recurrence.monthly, 15, date(2027, 1, 15)),
        (date(2026, 7, 15), Recurrence.quarterly, 15, date(2026, 10, 15)),
        (date(2026, 7, 15), Recurrence.annually, 15, date(2027, 7, 15)),
    ],
)
def test_next_due_on_anchors_and_clamps(start, recurrence, anchor, expected):
    assert next_due_on(start, recurrence, anchor) == expected


def test_a_monthly_series_from_the_31st_never_drifts():
    """The regression this whole design exists for.

    Twelve hops from 31 January: every month that has a 31st must land on it. Computing from the
    previous occurrence would settle on the 28th after February and stay there.
    """
    anchor = 31
    d = date(2026, 1, 31)
    landed = []
    for _ in range(12):
        d = next_due_on(d, Recurrence.monthly, anchor)
        landed.append(d)

    assert [x.month for x in landed] == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1]
    # Every 31-day month is on the 31st; the shorter ones clamp and recover.
    assert [x.day for x in landed] == [28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31, 31]
