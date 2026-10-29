"""One merged, range-bounded list for Month / Week / Agenda (PRD 19.1 `GET /calendar/feed`).

Layers: milestone (roadmap), compliance (empty until Legal ships), meeting, team (events of any
other type created here), marketing (scheduled content), external (the viewer's own Google
events, full detail) and busy (teammates' Google events, TITLE-LESS: stripped here, server side).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models.calendar import CalendarConnection, CalendarExternalEvent
from app.db.models.enums import CalendarEventType, ContentStatus, MembershipStatus
from app.db.models.marketing import ContentCalendarEntry
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.schemas.calendar import DEFAULT_TZ
from app.services.calendar import service as calendar_svc
from app.services.calendar.errors import _validation
from app.services.calendar.milestones import calendar_status

LAYERS = ("milestone", "compliance", "meeting", "team", "marketing", "external", "busy")
MAX_RANGE_DAYS = 366


def parse_layers(raw: str | None) -> set[str]:
    if not raw:
        return set(LAYERS)
    wanted = {p.strip() for p in raw.split(",") if p.strip()}
    unknown = wanted - set(LAYERS)
    if unknown:
        raise _validation("layers", f"Unknown layer: {sorted(unknown)[0]}")
    return wanted


def _hhmm(t: time | None) -> str | None:
    return None if t is None else t.isoformat(timespec="minutes")


def _item(**kw: Any) -> dict[str, Any]:
    base = {
        "end_date": None,
        "start_time": None,
        "end_time": None,
        "all_day": True,
        "timezone": DEFAULT_TZ,
        "status": None,
        "ref": None,
        "private": False,
    }
    base.update(kw)
    return base


def _events(db: Session, startup_id: uuid.UUID, uid: uuid.UUID, d0: date, d1: date) -> list[dict]:
    rows = [
        r
        for r in calendar_svc.list_events(db, startup_id=startup_id, date_to=d1, viewer_id=uid)
        if (r.end_date or r.event_date) >= d0
    ]
    out = []
    for r in rows:
        is_meeting = r.type == CalendarEventType.meeting
        out.append(
            _item(
                id=str(r.id),
                layer="meeting" if is_meeting else "team",
                kind=r.type.value,
                title=r.title,
                date=r.event_date.isoformat(),
                end_date=r.end_date.isoformat() if r.end_date else None,
                start_time=_hhmm(r.start_time),
                end_time=_hhmm(r.end_time),
                all_day=r.all_day,
                timezone=r.timezone,
                ref={"type": "event", "id": str(r.id)},
                private=r.visibility == "private",
            )
        )
    return out


def _milestones(db: Session, startup_id: uuid.UUID, d0: date, d1: date, today: date) -> list[dict]:
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(
            Roadmap.startup_id == startup_id,
            RoadmapMilestone.due_on >= d0,
            RoadmapMilestone.due_on <= d1,
        )
        .all()
    )
    return [
        _item(
            id=f"milestone-{m.id}",
            layer="milestone",
            kind="milestone",
            title=m.title,
            date=m.due_on.isoformat(),
            status=calendar_status(m.status, m.due_on, today),
            ref={"type": "milestone", "id": str(m.id)},
        )
        for m in rows
        if m.due_on is not None
    ]


def _marketing(db: Session, startup_id: uuid.UUID, d0: date, d1: date) -> list[dict]:
    zone = ZoneInfo(DEFAULT_TZ)
    lo = datetime.combine(d0, time.min, tzinfo=zone).astimezone(UTC)
    hi = datetime.combine(d1 + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    rows = (
        db.query(ContentCalendarEntry)
        .filter(
            ContentCalendarEntry.startup_id == startup_id,
            ContentCalendarEntry.scheduled_at.isnot(None),
            ContentCalendarEntry.scheduled_at >= lo,
            ContentCalendarEntry.scheduled_at < hi,
            ContentCalendarEntry.status != ContentStatus.draft,
        )
        .all()
    )
    out = []
    for r in rows:
        if r.scheduled_at is None:
            continue
        local = r.scheduled_at.astimezone(zone)
        out.append(
            _item(
                id=f"marketing-{r.id}",
                layer="marketing",
                kind="marketing_post",
                title=r.title,
                date=local.date().isoformat(),
                start_time=_hhmm(local.time()),
                all_day=False,
                status=r.status.value,
                ref={"type": "marketing_post", "id": str(r.id)},
            )
        )
    return out


def _external(db: Session, startup_id: uuid.UUID, uid: uuid.UUID, d0: date, d1: date) -> list[dict]:
    zone = ZoneInfo(DEFAULT_TZ)
    lo = datetime.combine(d0 - timedelta(days=1), time.min, tzinfo=UTC)
    hi = datetime.combine(d1 + timedelta(days=2), time.min, tzinfo=UTC)
    rows = (
        db.query(CalendarExternalEvent, CalendarConnection.user_id)
        .join(CalendarConnection, CalendarConnection.id == CalendarExternalEvent.connection_id)
        .join(
            Membership,
            (Membership.user_id == CalendarConnection.user_id)
            & (Membership.startup_id == CalendarConnection.startup_id),
        )
        .filter(
            Membership.status == MembershipStatus.active,
            CalendarConnection.startup_id == startup_id,
            CalendarConnection.status == "active",
            CalendarExternalEvent.starts_at < hi,
            CalendarExternalEvent.ends_at >= lo,
        )
        .all()
    )
    out = []
    for ev, owner in rows:
        mine = owner == uid
        if ev.all_day:
            start_d, end_d = ev.starts_at.date(), (ev.ends_at - timedelta(days=1)).date()
            st: time | None = None
            et: time | None = None
        else:
            s_l, e_l = ev.starts_at.astimezone(zone), ev.ends_at.astimezone(zone)
            start_d, end_d, st, et = s_l.date(), e_l.date(), s_l.time(), e_l.time()
        if end_d < d0 or start_d > d1:
            continue
        out.append(
            _item(
                id=f"ext-{ev.id}",
                layer="external" if mine else "busy",
                kind="external",
                # Teammates get NO title, ever: it is removed here, not hidden by the client.
                title=ev.title if mine else "Busy",
                date=start_d.isoformat(),
                end_date=end_d.isoformat() if end_d != start_d else None,
                start_time=_hhmm(st),
                end_time=_hhmm(et),
                all_day=ev.all_day,
                ref={"type": "external", "id": str(ev.id)} if mine else None,
                private=True,
            )
        )
    return out


def build_feed(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    date_from: date,
    date_to: date,
    layers: set[str],
    today: date | None = None,
) -> list[dict[str, Any]]:
    if date_to < date_from:
        raise _validation("date_to", "date_to may not be earlier than date_from")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise _validation("date_to", f"Ask for at most {MAX_RANGE_DAYS} days at a time.")
    today = today or datetime.now(UTC).date()
    items: list[dict[str, Any]] = []
    if layers & {"meeting", "team"}:
        items += [
            i for i in _events(db, startup_id, user_id, date_from, date_to) if i["layer"] in layers
        ]
    if "milestone" in layers:
        items += _milestones(db, startup_id, date_from, date_to, today)
    if "marketing" in layers:
        items += _marketing(db, startup_id, date_from, date_to)
    if layers & {"external", "busy"}:
        items += [
            i
            for i in _external(db, startup_id, user_id, date_from, date_to)
            if i["layer"] in layers
        ]
    # "compliance" is accepted and returns nothing until the Legal module owns that data.
    items.sort(key=lambda i: (i["date"], i["start_time"] or "", i["title"]))
    return items


def count_in_range(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(LAYERS, 0)
    for i in items:
        counts[i["layer"]] += 1
    return counts
