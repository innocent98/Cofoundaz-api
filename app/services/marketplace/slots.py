"""The slot generator (design D-MP-L6): weekly rules + date exceptions + minimum notice + buffer,
all in the expert's own timezone, producing UTC instants.

Pure maths with no database access, so the daylight-saving and exception cases are testable with
worked examples. Rules and exceptions are wall-clock times in `tz`. Each window is converted to UTC
on its own date, so a window that spans a clock change is its real length (a 00:00-06:00 window on
the spring-forward night is five hours, not six) and slots are stepped in real time. A wall-clock
time that does not exist (the spring-forward gap) resolves with `fold=0` to the same instant as the
time an hour later; one that happens twice (the fall-back hour) takes the first occurrence. A
window whose converted end is not after its start produces no slots."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_STEP_MIN = 30


@dataclass(frozen=True)
class RuleIn:
    weekday: int  # 0 = Monday
    start: time
    end: time
    slot_min: int = DEFAULT_STEP_MIN


@dataclass(frozen=True)
class ExceptionIn:
    on_date: date
    kind: str  # "blocked" | "extra"
    start: time | None = None
    end: time | None = None


@dataclass(frozen=True)
class Slot:
    starts_at: datetime  # UTC
    ends_at: datetime  # UTC


def _utc(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz).astimezone(UTC)


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return _utc(day, time.min, tz), _utc(day + timedelta(days=1), time.min, tz)


def _windows(
    day: date, tz: ZoneInfo, rules: Sequence[RuleIn], extras: Sequence[ExceptionIn]
) -> list[tuple[datetime, datetime, int]]:
    out: list[tuple[datetime, datetime, int]] = []
    for r in rules:
        if r.weekday == day.weekday():
            out.append((_utc(day, r.start, tz), _utc(day, r.end, tz), r.slot_min))
    step = min((r.slot_min for r in rules), default=DEFAULT_STEP_MIN)
    for e in extras:
        if e.kind == "extra" and e.on_date == day and e.start and e.end:
            out.append((_utc(day, e.start, tz), _utc(day, e.end, tz), step))
    return [w for w in out if w[1] > w[0]]


def _blocks(
    day: date, tz: ZoneInfo, exceptions: Sequence[ExceptionIn]
) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for e in exceptions:
        if e.kind != "blocked" or e.on_date != day:
            continue
        if e.start is None or e.end is None:
            out.append(_day_bounds(day, tz))
        else:
            out.append((_utc(day, e.start, tz), _utc(day, e.end, tz)))
    return out


def generate_slots(
    *,
    tz: ZoneInfo,
    rules: Sequence[RuleIn],
    exceptions: Sequence[ExceptionIn],
    duration_min: int,
    from_date: date,
    to_date: date,
    now: datetime,
    min_notice_h: int = 24,
    buffer_min: int = 0,
    busy: Sequence[tuple[datetime, datetime]] = (),
) -> list[Slot]:
    """Every bookable slot for `from_date`..`to_date` inclusive (dates in `tz`), earliest first.

    `busy` is a list of UTC intervals already taken (bookings, from slice C); each is widened by
    `buffer_min` on both sides before slots are tested against it."""
    length = timedelta(minutes=duration_min)
    earliest = now.astimezone(UTC) + timedelta(hours=min_notice_h)
    pad = timedelta(minutes=buffer_min)
    taken = [(b0 - pad, b1 + pad) for b0, b1 in busy]
    found: dict[datetime, Slot] = {}
    day = from_date
    while day <= to_date:
        blocked = _blocks(day, tz, exceptions) + taken
        for w_start, w_end, step in _windows(day, tz, rules, exceptions):
            cursor = w_start
            while cursor + length <= w_end:
                end = cursor + length
                free = all(not (cursor < b1 and end > b0) for b0, b1 in blocked)
                if free and cursor >= earliest:
                    found[cursor] = Slot(cursor, end)
                cursor += timedelta(minutes=step)
        day += timedelta(days=1)
    return [found[k] for k in sorted(found)]
