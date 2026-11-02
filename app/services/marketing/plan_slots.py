"""When "Plan my first week" proposes each post: never in the past, never on a taken slot."""

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.marketing import ContentCalendarEntry

SLOT_HOUR_UTC = 9  # one post a day at 09:00 UTC (the workspace has no timezone setting yet)


def assign_slots(db: Session, startup_id: Any, entries: list[dict], now: datetime) -> list[dict]:
    """Adds an ISO `scheduled_at` to each entry, in day_offset order. The first slot is the next
    09:00 UTC after `now`; an entry never lands before its own day_offset, a slot is used once,
    and slots already holding a calendar entry are skipped."""
    first = datetime.combine(now.date(), time(SLOT_HOUR_UTC), tzinfo=UTC)
    if first <= now:
        first += timedelta(days=1)
    taken = {
        row[0]
        for row in db.query(ContentCalendarEntry.scheduled_at).filter(
            ContentCalendarEntry.startup_id == startup_id,
            ContentCalendarEntry.scheduled_at >= first,
        )
    }
    out: list[dict] = []
    cursor = first
    for e in sorted(entries, key=lambda x: _offset(x)):
        slot = max(cursor, first + timedelta(days=_offset(e)))
        while slot in taken:
            slot += timedelta(days=1)
        taken.add(slot)
        cursor = slot + timedelta(days=1)
        out.append({**e, "scheduled_at": slot.isoformat()})
    return out


def _offset(e: dict) -> int:
    v = e.get("day_offset")
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6 else 0
