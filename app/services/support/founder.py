"""The founder side of support: open a ticket from Settings Help, list, read, reply.

A founder reaches only their own workspace's tickets: the ones they opened, plus every ticket of
the workspace when they hold its founder role. Everything else is a 404 (not a 403, so an id from
another workspace tells them nothing). Internal notes and staff identities never appear here.
"""

import uuid
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import MembershipRole, TicketStatus
from app.db.models.membership import Membership
from app.db.models.support import SupportTicket, SupportTicketMessage
from app.db.models.user import User
from app.services.support import core

TICKETS_PER_HOUR = 5  # T5
TICKETS_PER_DAY = 20
REPLIES_PER_HOUR = 30


def _visible(db: Session, user: User, m: Membership):  # type: ignore[no-untyped-def]
    q = db.query(SupportTicket).filter(SupportTicket.startup_id == m.startup_id)
    if m.role != MembershipRole.founder:
        q = q.filter(SupportTicket.created_by == user.id)
    return q


def _public(t: SupportTicket) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "number": core.number(t),
        "category": t.category.value,
        "subject": t.subject,
        "status": t.status.value,
        "last_author": t.last_author,
        "created_at": core.iso(t.created_at),
        "last_message_at": core.iso(t.last_message_at),
        "resolved_at": core.iso(t.resolved_at),
    }


def _thread(db: Session, user: User, t: SupportTicket) -> list[dict[str, Any]]:
    rows = (
        db.query(SupportTicketMessage)
        .filter(SupportTicketMessage.ticket_id == t.id)
        .order_by(SupportTicketMessage.created_at.asc(), SupportTicketMessage.id.asc())
        .all()
    )
    names = core.display_names(db, (r.author_user_id for r in rows if r.author_kind == "founder"))
    return [
        {
            "id": str(r.id),
            "author_kind": r.author_kind,
            "author_name": (
                (names.get(r.author_user_id) if r.author_user_id else None) or "Former teammate"
                if r.author_kind == "founder"
                else core.STAFF_NAME
            ),
            "is_mine": r.author_user_id == user.id,
            "body": r.body,
            "created_at": core.iso(r.created_at),
        }
        for r in rows
    ]


def _detail(db: Session, user: User, t: SupportTicket) -> dict[str, Any]:
    return {**_public(t), "messages": _thread(db, user, t)}


def create_ticket(
    db: Session, user: User, m: Membership, *, category: Any, subject: str, body: str
) -> dict[str, Any]:
    core.claim(
        f"support_rl:ticket_h:{user.id}",
        TICKETS_PER_HOUR,
        3600,
        "You've opened several tickets recently. Please wait a little before opening another.",
    )
    core.claim(
        f"support_rl:ticket_d:{user.id}",
        TICKETS_PER_DAY,
        86400,
        "You've reached today's limit for new tickets. Reply on an existing ticket instead.",
    )
    now = core.now_utc()
    priority = core.default_priority(category)
    ticket = SupportTicket(
        startup_id=m.startup_id,
        created_by=user.id,
        category=category,
        subject=subject,
        priority=priority,
        status=TicketStatus.open,
        sla_due_at=core.sla_due(priority, now),
        last_message_at=now,
        last_author="founder",
    )
    db.add(ticket)
    db.flush()
    core.add_message(db, ticket, author=user, kind="founder", body=body, now=now)
    detail = _detail(db, user, ticket)
    db.commit()
    return detail


def list_tickets(
    db: Session,
    user: User,
    m: Membership,
    *,
    status: TicketStatus | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = _visible(db, user, m)
    if status is not None:
        q = q.filter(SupportTicket.status == status)
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
    nxt = core.encode_cursor(page[-1].last_message_at, page[-1].id) if len(rows) > limit else None
    return [_public(t) for t in page], nxt


def _get(db: Session, user: User, m: Membership, ticket_id: uuid.UUID, *, lock: bool = False):  # type: ignore[no-untyped-def]
    q = _visible(db, user, m).filter(SupportTicket.id == ticket_id)
    if lock:
        q = q.with_for_update()
    ticket = q.first()
    if ticket is None:
        raise NotFound()
    return ticket


def get_ticket(db: Session, user: User, m: Membership, ticket_id: uuid.UUID) -> dict[str, Any]:
    return _detail(db, user, _get(db, user, m, ticket_id))


def reply(
    db: Session, user: User, m: Membership, ticket_id: uuid.UUID, *, body: str
) -> dict[str, Any]:
    ticket = _get(db, user, m, ticket_id, lock=True)
    now = core.now_utc()
    if ticket.status == TicketStatus.resolved:
        # T7: a reply within 14 days reopens the ticket; after that a new one is needed.
        if ticket.resolved_at is None or now - ticket.resolved_at > core.REOPEN_WINDOW:
            raise core.err(
                "TICKET_CLOSED",
                "This ticket was resolved more than 14 days ago. Please open a new one.",
                409,
            )
    core.claim(
        f"support_rl:reply_h:{user.id}",
        REPLIES_PER_HOUR,
        3600,
        "You're replying very quickly. Please wait a moment.",
    )
    if ticket.status == TicketStatus.resolved:
        core.restart_clock(ticket, now)
    core.add_message(db, ticket, author=user, kind="founder", body=body, now=now)
    detail = _detail(db, user, ticket)
    db.commit()
    return detail
