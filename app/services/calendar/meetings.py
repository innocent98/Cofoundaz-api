"""Meetings (PRD 19.3): an event of type `meeting` + agenda + attendees + the post-meeting
summary. Founders and team members manage them; every other role sees only the meetings it is
invited to, read-only (D-CAL-6), and never the transcript."""

from __future__ import annotations

import html
import secrets
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.calendar import (
    CalendarAttendee,
    CalendarEvent,
    CalendarMeeting,
)
from app.db.models.enums import CalendarEventType, MembershipRole, MembershipStatus, RoadmapStatus
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.schemas.calendar import (
    ActionItemPatch,
    AttendeeIn,
    MeetingCreate,
    MeetingUpdate,
)
from app.services.calendar.errors import _validation
from app.services.roadmap.service import recompute_milestone_progress

JOB_INVITES = "calendar.invites"
JOB_SUMMARY = "ai.calendar.meeting_summary"
MANAGERS = frozenset({MembershipRole.founder, MembershipRole.team_member})
# Fields whose change must re-invite external attendees.
_RESEND_FIELDS = {
    "title",
    "event_date",
    "end_date",
    "start_time",
    "end_time",
    "timezone",
    "location",
    "video_url",
}


def can_manage(role: MembershipRole) -> bool:
    return role in MANAGERS


def _email(value: str) -> str:
    return value.strip().lower()


def _members_by_email(db: Session, startup_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = (
        db.query(User.email, User.id)
        .join(Membership, Membership.user_id == User.id)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )
    return {_email(str(e)): uid for e, uid in rows}


def _meeting_of(db: Session, event_id: uuid.UUID) -> CalendarMeeting:
    row = db.query(CalendarMeeting).filter_by(event_id=event_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def get_meeting_event(
    db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID, user: User, role: MembershipRole
) -> tuple[CalendarEvent, CalendarMeeting]:
    """Uniform 404 for a missing meeting, another workspace's, a non-meeting event, a private
    one the viewer cannot see, or (non-managers) one they are not invited to."""
    ev = db.query(CalendarEvent).filter_by(id=event_id, startup_id=startup_id).one_or_none()
    if ev is None or ev.type != CalendarEventType.meeting:
        raise NotFound()
    invited = _is_invited(db, ev.id, user)
    if can_manage(role):
        if ev.visibility == "private" and ev.created_by != user.id and not invited:
            raise NotFound()
    elif not invited:
        raise NotFound()
    return ev, _meeting_of(db, ev.id)


def _is_invited(db: Session, event_id: uuid.UUID, user: User) -> bool:
    return (
        db.query(CalendarAttendee.id)
        .filter(
            CalendarAttendee.event_id == event_id,
            or_(
                CalendarAttendee.user_id == user.id,
                CalendarAttendee.email == _email(str(user.email)),
            ),
        )
        .first()
        is not None
    )


def _set_attendees(
    db: Session, ev: CalendarEvent, items: list[AttendeeIn], *, reset_all: bool
) -> list[str]:
    """Make the attendee set equal `items`. Returns the emails removed after an invite went out
    (they get a cancellation). Existing rows keep their invite state unless `reset_all`."""
    by_email = _members_by_email(db, ev.startup_id)
    wanted: dict[str, AttendeeIn] = {}
    for a in items:
        wanted.setdefault(_email(str(a.email)), a)
    current = {a.email: a for a in db.query(CalendarAttendee).filter_by(event_id=ev.id)}
    removed_invited: list[str] = []
    for email, row in current.items():
        if email not in wanted:
            if row.invite_state == "sent" and row.user_id is None:
                removed_invited.append(email)
            db.delete(row)
        elif reset_all and row.user_id is None:
            row.invite_state = "pending"
    for email, a in wanted.items():
        uid = by_email.get(email)
        existing = current.get(email)
        if existing is None:
            db.add(
                CalendarAttendee(
                    event_id=ev.id,
                    email=email,
                    name=a.name,
                    user_id=uid,
                    # Internal members are told in-app, not by email.
                    invite_state="internal" if uid else "pending",
                )
            )
        else:
            existing.name = a.name or existing.name
    db.flush()
    return removed_invited


def create_meeting(
    db: Session, *, startup_id: uuid.UUID, user: User, data: MeetingCreate
) -> CalendarEvent:
    ev = CalendarEvent(
        startup_id=startup_id,
        created_by=user.id,
        type=CalendarEventType.meeting,
        title=data.title,
        event_date=data.event_date,
        end_date=data.end_date,
        start_time=data.start_time,
        end_time=data.end_time,
        all_day=False,
        timezone=data.timezone,
        location=data.location,
        video_url=data.video_url,
        description=data.description,
        visibility=data.visibility,
    )
    db.add(ev)
    db.flush()
    db.add(
        CalendarMeeting(
            event_id=ev.id, agenda=data.agenda, assistant_enabled=data.assistant_enabled
        )
    )
    _set_attendees(db, ev, data.attendees, reset_all=False)
    write_audit(
        db,
        "calendar.meeting.created",
        actor_user_id=user.id,
        startup_id=startup_id,
        entity_type="calendar_event",
        entity_id=ev.id,
    )
    _after_attendee_change(db, ev, user, removed=[])
    return ev


def update_meeting(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    role: MembershipRole,
    event_id: uuid.UUID,
    data: MeetingUpdate,
) -> CalendarEvent:
    ev, meeting = get_meeting_event(
        db, startup_id=startup_id, event_id=event_id, user=user, role=role
    )
    changes = data.model_dump(exclude_unset=True)
    attendees = changes.pop("attendees", None)
    agenda_set = "agenda" in changes
    agenda = changes.pop("agenda", None)
    assistant = changes.pop("assistant_enabled", None)
    merged = {
        **{k: getattr(ev, k) for k in ("event_date", "end_date", "start_time", "end_time")},
        **{
            k: v
            for k, v in changes.items()
            if k in ("event_date", "end_date", "start_time", "end_time")
        },
    }
    if (
        merged["end_time"] is not None
        and merged["start_time"] is not None
        and (
            (merged["end_date"] or merged["event_date"]) == merged["event_date"]
            and merged["end_time"] < merged["start_time"]
        )
    ):
        raise _validation("end_time", "end_time may not be earlier than start_time")
    if merged["end_date"] is not None and merged["end_date"] < merged["event_date"]:
        raise _validation("end_date", "end_date may not be earlier than event_date")
    resend = bool(_RESEND_FIELDS & changes.keys())
    for k, v in changes.items():
        setattr(ev, k, v)
    if agenda_set:
        meeting.agenda = agenda
    if assistant is not None:
        meeting.assistant_enabled = assistant
    removed: list[str] = []
    if attendees is not None:
        removed = _set_attendees(db, ev, [AttendeeIn(**a) for a in attendees], reset_all=resend)
    elif resend:
        db.query(CalendarAttendee).filter_by(event_id=ev.id, user_id=None).update(
            {"invite_state": "pending"}
        )
    db.flush()
    _after_attendee_change(db, ev, user, removed=removed)
    return ev


def _after_attendee_change(
    db: Session, ev: CalendarEvent, user: User, *, removed: list[str]
) -> None:
    pending = (
        db.query(CalendarAttendee.id).filter_by(event_id=ev.id, invite_state="pending").first()
    )
    if pending is not None or removed:
        job_dispatcher.enqueue(
            db,
            JOB_INVITES,
            {"event_id": str(ev.id), "cancel_emails": removed, "organizer_id": str(user.id)},
            ev.startup_id,
        )
    internal = [
        str(a.user_id)
        for a in db.query(CalendarAttendee).filter(
            CalendarAttendee.event_id == ev.id,
            CalendarAttendee.user_id.isnot(None),
            CalendarAttendee.user_id != user.id,
        )
    ]
    if internal:
        event_bus.publish(
            db,
            "calendar.meeting.invited",
            {
                "startup_id": str(ev.startup_id),
                "event_id": str(ev.id),
                "title": ev.title,
                "date": ev.event_date.isoformat(),
                "user_ids": internal,
                "actor_id": str(user.id),
            },
        )


def delete_meeting_snapshot(db: Session, ev: CalendarEvent, user: User) -> None:
    """Called BEFORE the event row is deleted: queue cancellations for already-invited externals
    with a self-contained snapshot (the row, and so the attendee list, will be gone)."""
    emails = [
        a.email
        for a in db.query(CalendarAttendee).filter_by(event_id=ev.id, invite_state="sent")
        if a.user_id is None
    ]
    if not emails:
        return
    job_dispatcher.enqueue(
        db,
        JOB_INVITES,
        {
            "cancel_snapshot": {
                "event_id": str(ev.id),
                "title": ev.title,
                "date": ev.event_date.isoformat(),
                "end_date": ev.end_date.isoformat() if ev.end_date else None,
                "start_time": ev.start_time.isoformat() if ev.start_time else None,
                "end_time": ev.end_time.isoformat() if ev.end_time else None,
                "timezone": ev.timezone,
                "location": ev.location,
                "emails": emails,
            },
            "organizer_id": str(user.id),
        },
        ev.startup_id,
    )


# ---------- reads -----------------------------------------------------------------------


def _attendees(db: Session, event_id: uuid.UUID) -> list[CalendarAttendee]:
    return (
        db.query(CalendarAttendee)
        .filter_by(event_id=event_id)
        .order_by(CalendarAttendee.created_at, CalendarAttendee.email)
        .all()
    )


def serialize_meeting(
    db: Session, ev: CalendarEvent, meeting: CalendarMeeting, *, manager: bool
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(ev.id),
        "title": ev.title,
        "event_date": ev.event_date.isoformat(),
        "end_date": ev.end_date.isoformat() if ev.end_date else None,
        "start_time": ev.start_time.isoformat(timespec="minutes") if ev.start_time else None,
        "end_time": ev.end_time.isoformat(timespec="minutes") if ev.end_time else None,
        "timezone": ev.timezone,
        "location": ev.location,
        "video_url": ev.video_url,
        "description": ev.description,
        "visibility": ev.visibility,
        "created_by": str(ev.created_by) if ev.created_by else None,
        "agenda": meeting.agenda,
        "assistant_enabled": meeting.assistant_enabled,
        "assistant_state": meeting.assistant_state,
        "assistant_error": meeting.assistant_error,
        "summary": meeting.summary,
        "decisions": meeting.decisions,
        "action_items": meeting.action_items,
        "has_transcript": bool(meeting.transcript),
        "summarised_at": meeting.summarised_at.isoformat() if meeting.summarised_at else None,
        "attendees": [
            {
                "email": a.email,
                "name": a.name,
                "user_id": str(a.user_id) if a.user_id else None,
                "internal": a.user_id is not None,
                "invite_state": a.invite_state,
            }
            for a in _attendees(db, ev.id)
        ],
        "can_manage": manager,
    }
    if manager:
        out["transcript"] = meeting.transcript
    return out


def list_meetings(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    role: MembershipRole,
    when: str = "all",
    today: date | None = None,
) -> list[tuple[CalendarEvent, CalendarMeeting]]:
    today = today or datetime.now(UTC).date()
    q = (
        db.query(CalendarEvent, CalendarMeeting)
        .join(CalendarMeeting, CalendarMeeting.event_id == CalendarEvent.id)
        .filter(
            CalendarEvent.startup_id == startup_id, CalendarEvent.type == CalendarEventType.meeting
        )
    )
    invited = db.query(CalendarAttendee.event_id).filter(
        or_(CalendarAttendee.user_id == user.id, CalendarAttendee.email == _email(str(user.email)))
    )
    if can_manage(role):
        q = q.filter(
            or_(
                CalendarEvent.visibility == "team",
                CalendarEvent.created_by == user.id,
                CalendarEvent.id.in_(invited),
            )
        )
    else:
        q = q.filter(CalendarEvent.id.in_(invited))
    if when == "upcoming":
        q = q.filter(CalendarEvent.event_date >= today).order_by(
            CalendarEvent.event_date.asc(), CalendarEvent.start_time.asc()
        )
    elif when == "past":
        q = q.filter(CalendarEvent.event_date < today).order_by(
            CalendarEvent.event_date.desc(), CalendarEvent.start_time.desc()
        )
    else:
        q = q.order_by(CalendarEvent.event_date.desc(), CalendarEvent.start_time.desc())
    return [(e, m) for e, m in q.limit(500).all()]


# ---------- AI assistant (D-CAL-1: paste notes or a transcript) ------------------------


def request_summary(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    role: MembershipRole,
    event_id: uuid.UUID,
    transcript: str,
) -> CalendarMeeting:
    ev, meeting = get_meeting_event(
        db, startup_id=startup_id, event_id=event_id, user=user, role=role
    )
    if not can_manage(role):
        raise NotFound()
    if not meeting.assistant_enabled:
        raise AppError(
            "ASSISTANT_DISABLED", "Turn on the meeting assistant for this meeting first.", 409
        )
    locked = db.query(CalendarMeeting).filter_by(id=meeting.id).with_for_update().one()
    if locked.assistant_state == "queued":
        raise AppError("SUMMARY_IN_PROGRESS", "A summary is already being written.", 409)
    locked.transcript = transcript.strip()
    locked.assistant_state = "queued"
    locked.assistant_error = None
    db.flush()
    job_dispatcher.enqueue(
        db, JOB_SUMMARY, {"event_id": str(ev.id), "startup_id": str(startup_id)}, startup_id
    )
    return locked


def new_item_id() -> str:
    return secrets.token_hex(4)


def patch_action_item(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    role: MembershipRole,
    event_id: uuid.UUID,
    item_id: str,
    data: ActionItemPatch,
) -> CalendarMeeting:
    _ev, meeting = get_meeting_event(
        db, startup_id=startup_id, event_id=event_id, user=user, role=role
    )
    if not can_manage(role):
        raise NotFound()
    changes = data.model_dump(exclude_unset=True)
    if changes.get("assignee_id") is not None:
        _require_member(db, startup_id, changes["assignee_id"])
    items = [dict(i) for i in meeting.action_items]
    target = next((i for i in items if i.get("id") == item_id), None)
    if target is None:
        raise NotFound()
    if target.get("task_id"):
        raise AppError("ITEM_ALREADY_ADDED", "That action item is already a task.", 409)
    if "title" in changes:
        target["title"] = changes["title"]
    if "assignee_id" in changes:
        target["assignee_id"] = str(changes["assignee_id"]) if changes["assignee_id"] else None
    meeting.action_items = items  # reassign: JSONB mutation is not tracked in place
    db.flush()
    return meeting


def _require_member(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> None:
    ok = (
        db.query(Membership.id)
        .filter(
            Membership.startup_id == startup_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    if ok is None:
        raise _validation("assignee_id", "That user is not an active member of this workspace.")


def add_to_tasks(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    role: MembershipRole,
    event_id: uuid.UUID,
    item_ids: list[str] | None,
    milestone_id: uuid.UUID | None,
) -> dict[str, Any]:
    ev, meeting = get_meeting_event(
        db, startup_id=startup_id, event_id=event_id, user=user, role=role
    )
    if not can_manage(role):
        raise NotFound()
    locked = db.query(CalendarMeeting).filter_by(id=meeting.id).with_for_update().one()
    milestone = _target_milestone(db, startup_id, milestone_id)
    items = [dict(i) for i in locked.action_items]
    known = {i.get("id") for i in items}
    if item_ids is not None and not set(item_ids) <= known:
        raise _validation("item_ids", "One of those action items does not exist.")
    todo = [
        i for i in items if not i.get("task_id") and (item_ids is None or i.get("id") in item_ids)
    ]
    order = (
        db.query(RoadmapTask.order)
        .filter_by(milestone_id=milestone.id)
        .order_by(RoadmapTask.order.desc())
        .limit(1)
        .scalar()
    )
    next_order = (order if order is not None else -1) + 1
    created: list[dict[str, str]] = []
    for item in todo:
        task = RoadmapTask(
            milestone_id=milestone.id,
            title=str(item["title"])[:200],
            description=f"From the meeting: {ev.title}",
            status=RoadmapStatus.todo,
            assignee_id=uuid.UUID(item["assignee_id"]) if item.get("assignee_id") else None,
            order=next_order,
        )
        db.add(task)
        db.flush()
        item["task_id"] = str(task.id)
        created.append({"item_id": str(item["id"]), "task_id": str(task.id)})
        next_order += 1
    locked.action_items = items
    recompute_milestone_progress(db, milestone)
    db.flush()
    return {"created": created, "milestone_id": str(milestone.id), "action_items": items}


def _target_milestone(
    db: Session, startup_id: uuid.UUID, milestone_id: uuid.UUID | None
) -> RoadmapMilestone:
    base = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id)
    )
    if milestone_id is not None:
        m = base.filter(RoadmapMilestone.id == milestone_id).one_or_none()
        if m is None:
            raise _validation("milestone_id", "That milestone isn't in this workspace.")
        return m
    m = (
        base.filter(RoadmapMilestone.status != RoadmapStatus.done)
        .order_by(RoadmapMilestone.due_on.asc().nullslast(), RoadmapMilestone.created_at.asc())
        .first()
    ) or base.order_by(RoadmapMilestone.created_at.asc()).first()
    if m is None:
        raise AppError(
            "NO_ROADMAP_MILESTONE",
            "Create a roadmap milestone first, then add these as tasks.",
            409,
        )
    return m


def fmt_when(ev: CalendarEvent | SimpleNamespace) -> str:
    when = ev.event_date.strftime("%a %d %b %Y")
    if ev.start_time:
        when += f", {ev.start_time.strftime('%H:%M')} ({ev.timezone})"
    return when


def esc(value: str | None) -> str:
    return html.escape(value or "")
