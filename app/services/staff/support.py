"""Staff Support console: the ticket queue, thread, notes, canned replies and the actions.

D-ADM-9: a ticket shows the founder's name and email and the workspace's name and plan, and
nothing else about the account: no route here reaches documents, journal or AI chats. Every
mutation locks the ticket row, writes one audit row (ids and field changes, never message text)
in the same commit, and sends mail only after that commit.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import NotFound
from app.db.models.billing import BillingSubscription
from app.db.models.enums import TicketCategory, TicketPriority, TicketStatus, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.startup import Startup
from app.db.models.support import (
    SupportCannedResponse,
    SupportTicket,
    SupportTicketMessage,
    SupportTicketNote,
)
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit
from app.services.staff.like import contains_pattern
from app.services.support import core
from app.services.support.emails import resolved_email, staff_reply_email

TABS = ("open", "mine", "resolved")


def _audit(
    db: Session,
    ctx: StaffContext,
    action: str,
    t: SupportTicket,
    *,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None,
    ua: str | None,
) -> None:
    write_audit(
        db,
        action,
        actor_user_id=ctx.user.id,
        startup_id=t.startup_id,
        entity_type="support_ticket",
        entity_id=t.id,
        ip=ip,
        user_agent=ua,
        reason=reason,
        details={"ticket": core.number(t), **(details or {})},
    )


# ---------------------------------------------------------------- reads


def _row(
    t: SupportTicket, ws_name: str | None, plan: str | None, people: dict[uuid.UUID, dict[str, Any]]
) -> dict[str, Any]:
    now = core.now_utc()
    return {
        "id": str(t.id),
        "number": core.number(t),
        "subject": t.subject,
        "category": t.category.value,
        "priority": t.priority.value,
        "status": t.status.value,
        "escalated": t.escalated,
        "last_author": t.last_author,
        "founder": people.get(t.created_by) if t.created_by else None,
        "workspace": {"id": str(t.startup_id), "name": ws_name, "plan_key": plan or "starter"},
        "assignee": people.get(t.assigned_to) if t.assigned_to else None,
        "sla": {"due_at": core.iso(t.sla_due_at), "state": core.sla_state(t, now)},
        "created_at": core.iso(t.created_at),
        "last_message_at": core.iso(t.last_message_at),
        "first_response_at": core.iso(t.first_response_at),
        "resolved_at": core.iso(t.resolved_at),
    }


def _people(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, dict[str, Any]]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = (
        db.query(User.id, User.email, UserProfile.full_name)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.id.in_(wanted))
        .all()
    )
    return {
        uid: {"id": str(uid), "name": name or email, "email": email} for uid, email, name in rows
    }


def _base(db: Session):  # type: ignore[no-untyped-def]
    return (
        db.query(SupportTicket, Startup.name, BillingSubscription.plan_key)
        .join(Startup, Startup.id == SupportTicket.startup_id)
        .outerjoin(BillingSubscription, BillingSubscription.startup_id == Startup.id)
    )


def list_tickets(
    db: Session,
    ctx: StaffContext,
    *,
    tab: str,
    priority: TicketPriority | None,
    category: TicketCategory | None,
    sla: str | None,
    escalated: bool | None,
    unassigned: bool,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = _base(db)
    if tab == "resolved":
        q = q.filter(SupportTicket.status == TicketStatus.resolved)
    else:
        q = q.filter(SupportTicket.status == TicketStatus.open)
        if tab == "mine":
            q = q.filter(SupportTicket.assigned_to == ctx.user.id)
    if priority:
        q = q.filter(SupportTicket.priority == priority)
    if category:
        q = q.filter(SupportTicket.category == category)
    if escalated is not None:
        q = q.filter(SupportTicket.escalated.is_(escalated))
    if unassigned:
        q = q.filter(SupportTicket.assigned_to.is_(None))
    if sla in ("breached", "due_soon"):
        now = core.now_utc()
        q = q.filter(
            SupportTicket.status == TicketStatus.open, SupportTicket.first_response_at.is_(None)
        )
        if sla == "breached":
            q = q.filter(SupportTicket.sla_due_at < now)
        else:
            q = q.filter(
                SupportTicket.sla_due_at >= now, SupportTicket.sla_due_at <= now + core.DUE_SOON
            )
    if query:
        term = query.strip()
        pat = contains_pattern(term)
        digits = term.upper().removeprefix("T-")
        creator = (
            db.query(User.id)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .filter(
                or_(
                    User.email.ilike(pat, escape="\\"),
                    UserProfile.full_name.ilike(pat, escape="\\"),
                )
            )
        )
        conds: list[ColumnElement[bool]] = [
            SupportTicket.subject.ilike(pat, escape="\\"),
            Startup.name.ilike(pat, escape="\\"),
            SupportTicket.created_by.in_(creator),
        ]
        if digits.isdigit() and len(digits) <= 9:
            conds.append(SupportTicket.ticket_no == int(digits))
        q = q.filter(or_(*conds))
    if cursor:
        ts, rid = core.decode_cursor(cursor)
        q = q.filter(
            or_(
                SupportTicket.last_message_at < ts,
                and_(SupportTicket.last_message_at == ts, SupportTicket.id < rid),
            )
        )
    rows = (
        q.order_by(SupportTicket.last_message_at.desc(), SupportTicket.id.desc())
        .limit(limit + 1)
        .all()
    )
    page = rows[:limit]
    people = _people(db, {x for t, _, _ in page for x in (t.created_by, t.assigned_to)})
    items = [_row(t, ws, plan, people) for t, ws, plan in page]
    nxt = (
        core.encode_cursor(page[-1][0].last_message_at, page[-1][0].id)
        if len(rows) > limit
        else None
    )
    return items, nxt


def counts(db: Session, ctx: StaffContext) -> dict[str, int]:
    now = core.now_utc()
    open_ = SupportTicket.status == TicketStatus.open

    def n(*c: ColumnElement[bool]) -> int:
        return int(db.query(func.count(SupportTicket.id)).filter(*c).scalar() or 0)

    return {
        "open": n(open_),
        "mine": n(open_, SupportTicket.assigned_to == ctx.user.id),
        "resolved": n(SupportTicket.status == TicketStatus.resolved),
        "breached": n(
            open_, SupportTicket.first_response_at.is_(None), SupportTicket.sla_due_at < now
        ),
        "unassigned": n(open_, SupportTicket.assigned_to.is_(None)),
    }


def attention(db: Session) -> dict[str, int]:
    """For the dashboard's Needs attention card (A9)."""
    now = core.now_utc()
    open_ = SupportTicket.status == TicketStatus.open
    total = db.query(func.count(SupportTicket.id)).filter(open_).scalar() or 0
    late = (
        db.query(func.count(SupportTicket.id))
        .filter(open_, SupportTicket.first_response_at.is_(None), SupportTicket.sla_due_at < now)
        .scalar()
        or 0
    )
    return {"open": int(total), "breached": int(late)}


def _load(db: Session, ticket_id: uuid.UUID, *, lock: bool = False) -> SupportTicket:
    q = db.query(SupportTicket).filter(SupportTicket.id == ticket_id)
    if lock:
        q = q.with_for_update()
    t = q.first()
    if t is None:
        raise NotFound()
    return t


def detail(db: Session, ticket_id: uuid.UUID) -> dict[str, Any]:
    row = _base(db).filter(SupportTicket.id == ticket_id).first()
    if row is None:
        raise NotFound()
    t, ws, plan = row
    messages = (
        db.query(SupportTicketMessage)
        .filter(SupportTicketMessage.ticket_id == t.id)
        .order_by(SupportTicketMessage.created_at.asc(), SupportTicketMessage.id.asc())
        .all()
    )
    notes = (
        db.query(SupportTicketNote)
        .filter(SupportTicketNote.ticket_id == t.id)
        .order_by(SupportTicketNote.created_at.asc(), SupportTicketNote.id.asc())
        .all()
    )
    people = _people(
        db,
        {t.created_by, t.assigned_to, t.resolved_by}
        | {m.author_user_id for m in messages}
        | {n.author_user_id for n in notes},
    )

    def who(uid: uuid.UUID | None) -> dict[str, Any] | None:
        return people.get(uid) if uid else None

    return {
        **_row(t, ws, plan, people),
        "escalation_reason": t.escalation_reason,
        "resolved_by": who(t.resolved_by),
        "messages": [
            {
                "id": str(m.id),
                "author_kind": m.author_kind,
                "author": who(m.author_user_id),
                "body": m.body,
                "created_at": core.iso(m.created_at),
            }
            for m in messages
        ],
        "notes": [
            {
                "id": str(n.id),
                "author": who(n.author_user_id),
                "body": n.body,
                "created_at": core.iso(n.created_at),
            }
            for n in notes
        ],
    }


def assignable_staff(db: Session) -> list[dict[str, Any]]:
    """Who a ticket can go to: active staff who can do support (every Super Admin, and Admins
    with the `support` scope)."""
    rows = (
        db.query(PlatformStaff, User.email, UserProfile.full_name)
        .join(User, User.id == PlatformStaff.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(PlatformStaff.revoked_at.is_(None), User.status == UserStatus.active)
        .order_by(User.email.asc())
        .all()
    )
    return [
        {"id": str(s.user_id), "name": name or email, "email": email, "role": s.role.value}
        for s, email, name in rows
        if s.role.value == "super_admin" or "support" in s.scopes
    ]


# ---------------------------------------------------------------- actions


def _open_only(t: SupportTicket) -> None:
    if t.status != TicketStatus.open:
        raise core.err("TICKET_RESOLVED", "This ticket is resolved. Reopen it first.", 409)


def assign(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    assignee: uuid.UUID | None,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    _open_only(t)
    if assignee is not None and str(assignee) not in {s["id"] for s in assignable_staff(db)}:
        raise core.err("ASSIGNEE_NOT_ALLOWED", "That person can't take support tickets.", 422)
    before = t.assigned_to
    if before == assignee:
        raise core.err("NO_CHANGE", "The ticket is already assigned that way.", 422)
    t.assigned_to = assignee
    _audit(
        db,
        ctx,
        "ticket.assigned",
        t,
        details={
            "from": str(before) if before else None,
            "to": str(assignee) if assignee else None,
        },
        ip=ip,
        ua=ua,
    )
    db.commit()
    return detail(db, ticket_id)


def reply(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    body: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    _open_only(t)
    now = core.now_utc()
    msg = core.add_message(db, t, author=ctx.user, kind="staff", body=body, now=now)
    if t.first_response_at is None:
        t.first_response_at = now
    if t.assigned_to is None:
        t.assigned_to = ctx.user.id  # answering a ticket takes it
    _audit(db, ctx, "ticket.replied", t, details={"message_id": str(msg.id)}, ip=ip, ua=ua)
    db.commit()
    _notify(db, t, lambda to: staff_reply_email(to, t.ticket_no, t.subject, body, t.id))
    return detail(db, ticket_id)


def _notify(db: Session, t: SupportTicket, build: Any) -> None:
    owner = db.query(User).filter(User.id == t.created_by).first() if t.created_by else None
    if owner is None or owner.status != UserStatus.active:
        return
    core.send_after_commit(lambda: build(owner.email))


def add_note(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    body: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    note = SupportTicketNote(
        ticket_id=t.id, author_user_id=ctx.user.id, body=body, created_at=core.now_utc()
    )
    db.add(note)
    db.flush()
    _audit(db, ctx, "ticket.note_added", t, details={"note_id": str(note.id)}, ip=ip, ua=ua)
    db.commit()
    return detail(db, ticket_id)


def set_priority(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    priority: TicketPriority,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    _open_only(t)
    if t.priority == priority:
        raise core.err("NO_CHANGE", "The ticket already has that priority.", 422)
    before = t.priority
    t.priority = priority
    if t.first_response_at is None:
        t.sla_due_at = core.sla_due(priority, t.created_at)
    _audit(
        db,
        ctx,
        "ticket.priority_changed",
        t,
        details={"from": before.value, "to": priority.value},
        ip=ip,
        ua=ua,
    )
    db.commit()
    return detail(db, ticket_id)


def escalate(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    _open_only(t)
    if t.escalated:
        raise core.err("ALREADY_ESCALATED", "This ticket is already escalated.", 409)
    now = core.now_utc()
    t.escalated = True
    t.escalation_reason = reason
    before = t.priority
    t.priority = TicketPriority.p1
    if t.first_response_at is None:
        t.sla_due_at = min(t.sla_due_at, now + timedelta(hours=core.SLA_HOURS[TicketPriority.p1]))
    db.add(
        SupportTicketNote(
            ticket_id=t.id,
            author_user_id=ctx.user.id,
            body=f"Escalated to P1: {reason}",
            created_at=now,
        )
    )
    _audit(
        db,
        ctx,
        "ticket.escalated",
        t,
        reason=reason,
        details={"priority_from": before.value},
        ip=ip,
        ua=ua,
    )
    db.commit()
    return detail(db, ticket_id)


def resolve(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    message: str | None,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    _open_only(t)
    now = core.now_utc()
    if message:
        core.add_message(db, t, author=ctx.user, kind="staff", body=message, now=now)
        if t.first_response_at is None:
            t.first_response_at = now
    t.status = TicketStatus.resolved
    t.resolved_at = now
    t.resolved_by = ctx.user.id
    if t.assigned_to is None:
        t.assigned_to = ctx.user.id
    _audit(db, ctx, "ticket.resolved", t, details={"with_message": bool(message)}, ip=ip, ua=ua)
    db.commit()
    _notify(db, t, lambda to: resolved_email(to, t.ticket_no, t.subject, message, t.id))
    return detail(db, ticket_id)


def reopen(
    db: Session,
    ctx: StaffContext,
    ticket_id: uuid.UUID,
    *,
    reason: str | None,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    t = _load(db, ticket_id, lock=True)
    if t.status != TicketStatus.resolved:
        raise core.err("TICKET_NOT_RESOLVED", "Only a resolved ticket can be reopened.", 409)
    core.restart_clock(t, core.now_utc())
    _audit(db, ctx, "ticket.reopened", t, reason=reason, ip=ip, ua=ua)
    db.commit()
    return detail(db, ticket_id)


# ---------------------------------------------------------------- canned responses


def _canned(c: SupportCannedResponse) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "title": c.title,
        "body": c.body,
        "created_at": core.iso(c.created_at),
        "updated_at": core.iso(c.updated_at),
    }


def _canned_audit(
    db: Session,
    ctx: StaffContext,
    action: str,
    c: SupportCannedResponse,
    ip: str | None,
    ua: str | None,
) -> None:
    write_audit(
        db,
        action,
        actor_user_id=ctx.user.id,
        entity_type="support_canned_response",
        entity_id=c.id,
        ip=ip,
        user_agent=ua,
        details={"title": c.title},
    )


def list_canned(db: Session) -> list[dict[str, Any]]:
    rows = db.query(SupportCannedResponse).order_by(func.lower(SupportCannedResponse.title)).all()
    return [_canned(c) for c in rows]


def create_canned(
    db: Session, ctx: StaffContext, *, title: str, body: str, ip: str | None, ua: str | None
) -> dict[str, Any]:
    c = SupportCannedResponse(title=title, body=body, created_by=ctx.user.id)
    db.add(c)
    db.flush()
    _canned_audit(db, ctx, "canned.created", c, ip, ua)
    db.commit()
    return _canned(c)


def _canned_get(db: Session, canned_id: uuid.UUID) -> SupportCannedResponse:
    c = (
        db.query(SupportCannedResponse)
        .filter(SupportCannedResponse.id == canned_id)
        .with_for_update()
        .first()
    )
    if c is None:
        raise NotFound()
    return c


def update_canned(
    db: Session,
    ctx: StaffContext,
    canned_id: uuid.UUID,
    *,
    title: str | None,
    body: str | None,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    c = _canned_get(db, canned_id)
    if title is None and body is None:
        raise core.err("NO_CHANGE", "Send a new title or body.", 422)
    if title is not None:
        c.title = title
    if body is not None:
        c.body = body
    db.flush()
    _canned_audit(db, ctx, "canned.updated", c, ip, ua)
    db.commit()
    return _canned(c)


def delete_canned(
    db: Session, ctx: StaffContext, canned_id: uuid.UUID, *, ip: str | None, ua: str | None
) -> dict[str, Any]:
    c = _canned_get(db, canned_id)
    _canned_audit(db, ctx, "canned.deleted", c, ip, ua)
    db.delete(c)
    db.commit()
    return {"id": str(canned_id), "deleted": True}
