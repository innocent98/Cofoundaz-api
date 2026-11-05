"""Worked examples for the slot generator (design D-MP-L6): timezones, daylight saving, exceptions,
notice and buffer. Pure maths, no database. `now` is always passed in so nothing depends on the
clock."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.marketplace.slots import ExceptionIn, RuleIn, generate_slots

LAGOS = ZoneInfo("Africa/Lagos")  # UTC+1, no DST
NY = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")  # UTC+5:30
AKL = ZoneInfo("Pacific/Auckland")
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def starts(slots):
    return [s.starts_at for s in slots]


def gen(tz, rules, day, to=None, dur=60, exceptions=(), **kw):
    kw.setdefault("now", LONG_AGO)
    kw.setdefault("min_notice_h", 0)
    return generate_slots(
        tz=tz,
        rules=rules,
        exceptions=exceptions,
        duration_min=dur,
        from_date=day,
        to_date=to or day,
        **kw,
    )


def test_a_plain_morning_in_lagos_is_one_hour_ahead_of_utc():
    # Monday 2026-11-09, 09:00-12:00 Lagos (UTC+1) = 08:00-11:00Z
    slots = gen(LAGOS, [RuleIn(0, time(9), time(12), 60)], date(2026, 11, 9))
    assert starts(slots) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 9), utc(2026, 11, 9, 10)]
    assert slots[0].ends_at == utc(2026, 11, 9, 9)


def test_slot_start_step_and_session_length_are_independent():
    # 09:00-11:00, a start every 30 min, 60-minute sessions: 9:00, 9:30, 10:00 (10:30 would overrun)
    slots = gen(LAGOS, [RuleIn(0, time(9), time(11), 30)], date(2026, 11, 9))
    assert starts(slots) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 30), utc(2026, 11, 9, 9)]


def test_a_window_shorter_than_the_session_gives_nothing():
    assert gen(LAGOS, [RuleIn(0, time(9), time(9, 45), 15)], date(2026, 11, 9)) == []


def test_the_rule_only_applies_on_its_weekday():
    rules = [RuleIn(0, time(9), time(10), 60)]  # Monday only
    assert gen(LAGOS, rules, date(2026, 11, 10)) == []  # Tuesday
    assert len(gen(LAGOS, rules, date(2026, 11, 9))) == 1


def test_us_spring_forward_the_window_loses_an_hour():
    # Sunday 2026-03-08 in New York: 02:00 EST jumps to 03:00 EDT. 00:00-06:00 local is five real
    # hours (05:00Z to 10:00Z), so five one-hour slots, not six.
    slots = gen(NY, [RuleIn(6, time(0), time(6), 60)], date(2026, 3, 8))
    assert starts(slots) == [utc(2026, 3, 8, h) for h in (5, 6, 7, 8, 9)]
    assert slots[-1].ends_at == utc(2026, 3, 8, 10)


def test_us_fall_back_the_window_gains_an_hour():
    # Sunday 2026-11-01: 02:00 EDT falls back to 01:00 EST. 00:00-06:00 local is seven real hours
    # (04:00Z to 11:00Z): seven one-hour slots.
    slots = gen(NY, [RuleIn(6, time(0), time(6), 60)], date(2026, 11, 1))
    assert starts(slots) == [utc(2026, 11, 1, h) for h in range(4, 11)]


def test_the_same_nine_oclock_moves_in_utc_across_the_clock_change():
    sat = gen(NY, [RuleIn(5, time(9), time(10), 60)], date(2026, 3, 7))  # Saturday, EST (UTC-5)
    sun = gen(NY, [RuleIn(6, time(9), time(10), 60)], date(2026, 3, 8))  # Sunday, EDT (UTC-4)
    assert starts(sat) == [utc(2026, 3, 7, 14)]
    assert starts(sun) == [utc(2026, 3, 8, 13)]


def test_a_window_that_starts_inside_the_spring_forward_gap_resolves_to_a_real_instant():
    # 02:30 does not exist on 2026-03-08 in New York. It resolves (fold=0) to 07:30Z, the same
    # instant as 03:30 EDT, so 02:30-04:30 local is a real one hour: exactly one 60-minute slot.
    slots = gen(NY, [RuleIn(6, time(2, 30), time(4, 30), 60)], date(2026, 3, 8))
    assert starts(slots) == [utc(2026, 3, 8, 7, 30)]


def test_half_hour_offsets_are_handled():
    # Monday 09:00 in India (UTC+5:30) = 03:30Z
    slots = gen(IST, [RuleIn(0, time(9), time(10), 60)], date(2026, 11, 9))
    assert starts(slots) == [utc(2026, 11, 9, 3, 30)]


def test_local_monday_can_start_on_utc_sunday():
    # Auckland in June is UTC+12: Monday 2026-06-08 09:00 local = Sunday 2026-06-07 21:00Z. The
    # range is in the expert's own dates, so this is a Monday slot even though its UTC date is Sunday.
    slots = gen(AKL, [RuleIn(0, time(9), time(10), 60)], date(2026, 6, 8))
    assert starts(slots) == [utc(2026, 6, 7, 21)]
    assert slots[0].starts_at.astimezone(AKL).date() == date(2026, 6, 8)
    assert gen(AKL, [RuleIn(0, time(9), time(10), 60)], date(2026, 6, 7)) == []


def test_a_blocked_day_removes_every_slot_that_day_only():
    rules = [RuleIn(0, time(9), time(11), 60)]
    block = [ExceptionIn(date(2026, 11, 9), "blocked")]
    assert gen(LAGOS, rules, date(2026, 11, 9), exceptions=block) == []
    nxt = gen(LAGOS, rules, date(2026, 11, 9), to=date(2026, 11, 16), exceptions=block)
    assert starts(nxt) == [utc(2026, 11, 16, 8), utc(2026, 11, 16, 9)]


def test_a_partial_block_removes_only_the_overlap():
    rules = [RuleIn(0, time(9), time(13), 60)]
    block = [ExceptionIn(date(2026, 11, 9), "blocked", time(10), time(12))]
    slots = gen(LAGOS, rules, date(2026, 11, 9), exceptions=block)
    # 9-10 and 12-13 local survive; 10-11 and 11-12 are inside the block
    assert starts(slots) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 11)]


def test_slots_touching_a_block_edge_are_kept_but_overlapping_ones_are_not():
    rules = [RuleIn(0, time(9), time(12), 30)]
    block = [ExceptionIn(date(2026, 11, 9), "blocked", time(10, 30), time(11))]
    slots = gen(LAGOS, rules, date(2026, 11, 9), dur=60, exceptions=block)
    # Local 9:00 and 9:30 end at or before the block starts (10:30) and are kept; 10:00 and 10:30
    # overlap it and go; 11:00 starts exactly when the block ends and is kept.
    assert starts(slots) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 30), utc(2026, 11, 9, 10)]


def test_extra_hours_open_a_day_with_no_weekly_rule():
    extra = [ExceptionIn(date(2026, 11, 10), "extra", time(14), time(16))]  # a Tuesday
    slots = gen(LAGOS, [RuleIn(0, time(9), time(10), 60)], date(2026, 11, 10), exceptions=extra)
    assert starts(slots) == [utc(2026, 11, 10, 13), utc(2026, 11, 10, 14)]


def test_extra_hours_alone_use_the_default_half_hour_step():
    extra = [ExceptionIn(date(2026, 11, 10), "extra", time(14), time(15))]
    slots = gen(LAGOS, [], date(2026, 11, 10), dur=30, exceptions=extra)
    assert starts(slots) == [utc(2026, 11, 10, 13), utc(2026, 11, 10, 13, 30)]


def test_overlapping_windows_never_produce_duplicate_slots():
    rules = [RuleIn(0, time(9), time(11), 60), RuleIn(0, time(10), time(12), 60)]
    slots = gen(LAGOS, rules, date(2026, 11, 9))
    assert starts(slots) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 9), utc(2026, 11, 9, 10)]


def test_minimum_notice_hides_slots_that_start_too_soon():
    rules = [RuleIn(0, time(9), time(12), 60)]
    now = utc(2026, 11, 8, 9)  # Sunday 10:00 Lagos
    # 24 h of notice: nothing before Monday 10:00 Lagos (09:00Z). The 08:00Z slot is dropped; a slot
    # exactly at the notice boundary (09:00Z) is allowed.
    slots = gen(LAGOS, rules, date(2026, 11, 9), now=now, min_notice_h=24)
    assert starts(slots) == [utc(2026, 11, 9, 9), utc(2026, 11, 9, 10)]


def test_busy_time_and_buffer_remove_neighbouring_slots():
    rules = [RuleIn(0, time(9), time(14), 60)]  # 08:00Z to 13:00Z
    busy = [(utc(2026, 11, 9, 10), utc(2026, 11, 9, 11))]
    plain = gen(LAGOS, rules, date(2026, 11, 9), busy=busy)
    assert utc(2026, 11, 9, 10) not in starts(plain)
    assert utc(2026, 11, 9, 9) in starts(plain) and utc(2026, 11, 9, 11) in starts(plain)
    buffered = gen(LAGOS, rules, date(2026, 11, 9), busy=busy, buffer_min=30)
    # 30 minutes either side of 10:00-11:00 makes 09:30-11:30 unavailable: 09:00 (ends 10:00) and 11:00 go
    assert starts(buffered) == [utc(2026, 11, 9, 8), utc(2026, 11, 9, 12)]


def test_a_range_is_inclusive_and_sorted_earliest_first():
    rules = [RuleIn(0, time(9), time(10), 60), RuleIn(2, time(9), time(10), 60)]  # Mon, Wed
    slots = gen(LAGOS, rules, date(2026, 11, 9), to=date(2026, 11, 18))  # Mon 9th to Wed 18th
    assert starts(slots) == [
        utc(2026, 11, 9, 8),
        utc(2026, 11, 11, 8),
        utc(2026, 11, 16, 8),
        utc(2026, 11, 18, 8),
    ]
    assert starts(slots) == sorted(starts(slots))


def test_slots_are_never_in_the_past():
    rules = [RuleIn(0, time(9), time(12), 60)]
    now = utc(2026, 11, 9, 9, 30)
    slots = gen(LAGOS, rules, date(2026, 11, 9), now=now)
    assert starts(slots) == [utc(2026, 11, 9, 10)]


def test_duration_and_end_times_agree():
    for s in gen(LAGOS, [RuleIn(0, time(9), time(12), 30)], date(2026, 11, 9), dur=90):
        assert s.ends_at - s.starts_at == timedelta(minutes=90)
