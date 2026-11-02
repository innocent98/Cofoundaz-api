"""Invite and cancellation emails for external meeting attendees (PRD 19.3), with an .ics.

Subject: "{Founder} invited you: {title} · {date}". Internal members are notified in-app and
never emailed. Each attendee row remembers the SEQUENCE it was last sent, so a repeated job
never double-sends and an edit re-invites with a higher SEQUENCE.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.calendar import CalendarAttendee, CalendarEvent
from app.db.models.user import User, UserProfile
from app.platform.email import EmailAttachment, EmailMessage, get_email_sender
from app.services.calendar.ics import build_ics
from app.services.calendar.meetings import esc, fmt_when


def _organizer(db: Session, user_id: uuid.UUID | None) -> tuple[str, str]:
    user = db.get(User, user_id) if user_id else None
    if user is None:
        return "Cofoundaz", "no-reply@cofoundaz.com"
    profile = db.query(UserProfile).filter_by(user_id=user.id).one_or_none()
    name = (profile.full_name if profile and profile.full_name else None) or str(user.email)
    return name, str(user.email)


def _html(intro: str, ev: CalendarEvent | SimpleNamespace, extra: str = "") -> str:
    rows = [f"<p>{esc(intro)}</p>", f"<p><strong>{esc(ev.title)}</strong><br>{esc(fmt_when(ev))}"]
    if ev.location:
        rows[-1] += f"<br>{esc(ev.location)}"
    rows[-1] += "</p>"
    if ev.video_url and ev.video_url.startswith(("https://", "http://")):
        rows.append(f'<p><a href="{esc(ev.video_url)}">Join the call</a></p>')
    if ev.description:
        rows.append(f"<p>{esc(ev.description).replace(chr(10), '<br>')}</p>")
    return "".join(rows) + extra


def send_pending(
    db: Session, *, event_id: uuid.UUID, organizer_id: uuid.UUID | None, cancel_emails: list[str]
) -> dict[str, int]:
    ev = db.get(CalendarEvent, event_id)
    if ev is None:
        return {"sent": 0, "cancelled": 0}
    name, org_email = _organizer(db, organizer_id or ev.created_by)
    sender = get_email_sender()
    rows = db.query(CalendarAttendee).filter_by(event_id=ev.id).all()
    seq = max([a.invite_sequence for a in rows] + [-1]) + 1
    sent = cancelled = 0
    for a in rows:
        if a.invite_state != "pending" or a.user_id is not None:
            continue
        ics = build_ics(
            ev,
            method="REQUEST",
            sequence=seq,
            organizer_email=org_email,
            organizer_name=name,
            attendee_email=a.email,
            attendee_name=a.name,
        )
        try:
            sender.send(
                EmailMessage(
                    to=a.email,
                    subject=f"{name} invited you: {ev.title} · {ev.event_date.strftime('%d %b %Y')}",
                    html=_html(f"{name} invited you to a meeting.", ev),
                    attachments=[
                        EmailAttachment("invite.ics", ics, "text/calendar; method=REQUEST")
                    ],
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad address must not block the rest
            log.warning(f"[calendar] invite to attendee {a.id} failed: {exc}")
            a.invite_state = "failed"
            continue
        a.invite_state, a.invite_sequence = "sent", seq
        sent += 1
    for email in cancel_emails:
        cancelled += _cancel(ev, email, name, org_email, seq)
    db.flush()
    return {"sent": sent, "cancelled": cancelled}


def _cancel(
    ev: CalendarEvent | SimpleNamespace, email: str, name: str, org_email: str, seq: int
) -> int:
    ics = build_ics(
        ev,
        method="CANCEL",
        sequence=seq,
        organizer_email=org_email,
        organizer_name=name,
        attendee_email=email,
    )
    try:
        get_email_sender().send(
            EmailMessage(
                to=email,
                subject=f"Cancelled: {ev.title} · {ev.event_date.strftime('%d %b %Y')}",
                html=_html(f"{name} cancelled this meeting.", ev),
                attachments=[EmailAttachment("cancelled.ics", ics, "text/calendar; method=CANCEL")],
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[calendar] cancellation failed: {exc}")
        return 0
    return 1


def send_cancel_snapshot(db: Session, snap: dict, organizer_id: uuid.UUID | None) -> int:
    """A deleted meeting: build a stand-in from the job's snapshot and cancel each invitee."""
    ev = SimpleNamespace(
        id=uuid.UUID(snap["event_id"]),
        title=snap["title"],
        event_date=date.fromisoformat(snap["date"]),
        end_date=date.fromisoformat(snap["end_date"]) if snap.get("end_date") else None,
        start_time=time.fromisoformat(snap["start_time"]) if snap.get("start_time") else None,
        end_time=time.fromisoformat(snap["end_time"]) if snap.get("end_time") else None,
        timezone=snap["timezone"],
        location=snap.get("location"),
        video_url=None,
        description=None,
    )
    name, org_email = _organizer(db, organizer_id)
    return sum(_cancel(ev, e, name, org_email, 9999) for e in snap["emails"])
