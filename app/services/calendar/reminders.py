"""Reminder rules (PRD 19.4) and the scan that finds reminders now due.

Rule kinds: milestone (roadmap milestones and calendar milestone events), compliance (calendar
`deadline` events; compliance filings join once Legal ships) and meeting. A `task` event has no
rule. Offsets are minutes before the start; an all-day item is anchored at 09:00 in its zone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models.calendar import CalendarAttendee, CalendarEvent, CalendarReminderRule
from app.db.models.enums import CalendarEventType, MembershipRole, MembershipStatus, RoadmapStatus
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup
from app.schemas.calendar import DEFAULT_TZ
from app.services.calendar.errors import _validation

KINDS = ("milestone", "compliance", "meeting")
DEFAULTS: dict[str, list[int]] = {
    "milestone": [7 * 1440, 1440],
    "compliance": [14 * 1440, 7 * 1440, 1440],
    "meeting": [15],
}
ALL_DAY_ANCHOR = time(9, 0)
CATCH_UP = timedelta(hours=12)  # never announce a reminder that is more than this late
SCAN_AHEAD_DAYS = 15
EVENT_KIND = {
    CalendarEventType.meeting: "meeting",
    CalendarEventType.milestone: "milestone",
    CalendarEventType.deadline: "compliance",
}


def check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise _validation("kind", "Reminder kind must be milestone, compliance or meeting.")


def rules_for(db: Session, startup_id: uuid.UUID) -> dict[str, list[int]]:
    """Stored offsets per kind, with the PRD defaults where the workspace never edited one."""
    out = {k: list(v) for k, v in DEFAULTS.items()}
    for r in db.query(CalendarReminderRule).filter_by(startup_id=startup_id):
        if r.kind in out:
            out[r.kind] = [int(x) for x in r.offsets_minutes]
    return out


def set_rule(db: Session, *, startup_id: uuid.UUID, kind: str, offsets: list[int]) -> None:
    check_kind(kind)
    row = db.query(CalendarReminderRule).filter_by(startup_id=startup_id, kind=kind).one_or_none()
    if row is None:
        db.add(CalendarReminderRule(startup_id=startup_id, kind=kind, offsets_minutes=offsets))
    else:
        row.offsets_minutes = offsets
    db.flush()


def reset_rule(db: Session, *, startup_id: uuid.UUID, kind: str) -> None:
    check_kind(kind)
    db.query(CalendarReminderRule).filter_by(startup_id=startup_id, kind=kind).delete()
    db.flush()


def serialize_rules(db: Session, startup_id: uuid.UUID) -> list[dict]:
    stored = {r.kind for r in db.query(CalendarReminderRule).filter_by(startup_id=startup_id)}
    rules = rules_for(db, startup_id)
    return [
        {
            "kind": k,
            "offsets_minutes": rules[k],
            "is_default": k not in stored,
            "default_offsets_minutes": DEFAULTS[k],
        }
        for k in KINDS
    ]


def human_offset(minutes: int) -> str:
    if minutes % 1440 == 0:
        n = minutes // 1440
        return "1 day" if n == 1 else f"{n} days"
    if minutes % 60 == 0:
        n = minutes // 60
        return "1 hour" if n == 1 else f"{n} hours"
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


@dataclass(frozen=True)
class ReminderDue:
    startup_id: uuid.UUID
    scope: str
    kind: str
    source: str  # "event" | "roadmap_milestone"
    source_id: uuid.UUID
    title: str
    offset_minutes: int
    on: date
    user_ids: list[uuid.UUID]


def _anchor(on: date, start: time | None, tz: str) -> datetime:
    return datetime.combine(on, start or ALL_DAY_ANCHOR, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _due(anchor: datetime, offsets: list[int], now: datetime) -> list[int]:
    return [
        o
        for o in offsets
        if now < anchor
        and anchor - timedelta(minutes=o) <= now
        and now - (anchor - timedelta(minutes=o)) <= CATCH_UP
    ]


def _members(db: Session, startup_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == startup_id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_([MembershipRole.founder, MembershipRole.team_member]),
        )
        .all()
    )
    return [r[0] for r in rows]


def scan(db: Session, now: datetime) -> list[ReminderDue]:
    today = now.astimezone(ZoneInfo(DEFAULT_TZ)).date()
    window_lo, window_hi = today - timedelta(days=1), today + timedelta(days=SCAN_AHEAD_DAYS)
    out: list[ReminderDue] = []
    rules_cache: dict[uuid.UUID, dict[str, list[int]]] = {}
    members_cache: dict[uuid.UUID, list[uuid.UUID]] = {}

    def rules(sid: uuid.UUID) -> dict[str, list[int]]:
        if sid not in rules_cache:
            rules_cache[sid] = rules_for(db, sid)
        return rules_cache[sid]

    def members(sid: uuid.UUID) -> list[uuid.UUID]:
        if sid not in members_cache:
            members_cache[sid] = _members(db, sid)
        return members_cache[sid]

    events = (
        db.query(CalendarEvent)
        .join(Startup, Startup.id == CalendarEvent.startup_id)
        .filter(
            Startup.deleted_at.is_(None),
            CalendarEvent.event_date >= window_lo,
            CalendarEvent.event_date <= window_hi,
            CalendarEvent.type.in_(list(EVENT_KIND)),
        )
        .all()
    )
    for ev in events:
        kind = EVENT_KIND[ev.type]
        anchor = _anchor(ev.event_date, None if ev.all_day else ev.start_time, ev.timezone)
        offsets = _due(anchor, rules(ev.startup_id)[kind], now)
        if not offsets:
            continue
        if kind == "meeting" or ev.visibility == "private":
            # A private item's title goes only to its creator and invitees, never the team.
            who = {
                a.user_id
                for a in db.query(CalendarAttendee).filter_by(event_id=ev.id)
                if a.user_id is not None
            }
            if ev.created_by:
                who.add(ev.created_by)
            recipients = sorted(who, key=str)
        else:
            recipients = members(ev.startup_id)
        for o in offsets:
            out.append(
                ReminderDue(
                    ev.startup_id,
                    f"ev-{ev.id}:{o}",
                    kind,
                    "event",
                    ev.id,
                    ev.title,
                    o,
                    ev.event_date,
                    recipients,
                )
            )

    ms_rows = (
        db.query(RoadmapMilestone, Roadmap.startup_id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .join(Startup, Startup.id == Roadmap.startup_id)
        .filter(
            Startup.deleted_at.is_(None),
            RoadmapMilestone.status != RoadmapStatus.done,
            RoadmapMilestone.due_on >= window_lo,
            RoadmapMilestone.due_on <= window_hi,
        )
        .all()
    )
    for m, sid in ms_rows:
        anchor = _anchor(m.due_on, None, DEFAULT_TZ)
        for o in _due(anchor, rules(sid)["milestone"], now):
            out.append(
                ReminderDue(
                    sid,
                    f"ms-{m.id}:{o}",
                    "milestone",
                    "roadmap_milestone",
                    m.id,
                    m.title,
                    o,
                    m.due_on,
                    members(sid),
                )
            )
    return out
