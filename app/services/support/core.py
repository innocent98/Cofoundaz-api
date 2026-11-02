"""What founder and staff ticket code share: the first-reply clock, cursor paging, names, the
one place a thread message is written, and the email helper.

The founder-facing serializers never read `SupportTicketNote` or any staff identity; staff see
more in `app/services/staff/support.py`.
"""

import base64
import binascii
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import AppError, EmailDeliveryFailed
from app.core.logger import log
from app.core.redis import get_redis
from app.db.models.enums import TicketCategory, TicketPriority, TicketStatus
from app.db.models.support import SupportTicket, SupportTicketMessage
from app.db.models.user import User, UserProfile
from app.platform.email import EmailMessage, get_email_sender

# T1: hours to the first staff reply, by priority.
SLA_HOURS: dict[TicketPriority, int] = {
    TicketPriority.p1: 1,
    TicketPriority.p2: 4,
    TicketPriority.p3: 24,
    TicketPriority.p4: 72,
}
DUE_SOON = timedelta(hours=1)
REOPEN_WINDOW = timedelta(days=14)  # T7
DEFAULT_PRIORITY: dict[TicketCategory, TicketPriority] = {
    TicketCategory.billing: TicketPriority.p2,
    TicketCategory.account: TicketPriority.p2,
}
STAFF_NAME = "Cofoundaz Support"  # what a founder sees for any staff author


def err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def number(ticket: SupportTicket) -> str:
    return f"T-{ticket.ticket_no}"


def default_priority(category: TicketCategory) -> TicketPriority:
    return DEFAULT_PRIORITY.get(category, TicketPriority.p3)


def sla_due(priority: TicketPriority, start: datetime) -> datetime:
    return start + timedelta(hours=SLA_HOURS[priority])


def sla_state(t: SupportTicket, now: datetime) -> str:
    """ok | due_soon | breached while waiting for the first staff reply; met | late once answered
    (a ticket resolved with no reply counts as answered when it was resolved)."""
    answered = t.first_response_at or t.resolved_at
    if answered is not None:
        return "met" if answered <= t.sla_due_at else "late"
    if now > t.sla_due_at:
        return "breached"
    return "due_soon" if t.sla_due_at - now <= DUE_SOON else "ok"


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def display_names(db: Session, user_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = (
        db.query(User.id, UserProfile.full_name, User.email)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.id.in_(ids))
        .all()
    )
    return {uid: (name or email) for uid, name, email in rows}


def claim(key: str, limit: int, ttl: int, message: str) -> None:
    """Count one use of a rate-limited action; refuse with 429 once `limit` is spent in `ttl` s."""
    redis = get_redis()
    used = int(redis.incr(key))  # type: ignore[arg-type]
    if used == 1:
        redis.expire(key, ttl)
    if used > limit:
        raise err("RATE_LIMITED", message, 429)


def add_message(
    db: Session,
    ticket: SupportTicket,
    *,
    author: User,
    kind: str,
    body: str,
    now: datetime,
) -> SupportTicketMessage:
    msg = SupportTicketMessage(
        ticket_id=ticket.id,
        author_user_id=author.id,
        author_kind=kind,
        body=body,
        created_at=now,  # not now(): that is the transaction's start, so ties order by random id
    )
    db.add(msg)
    ticket.last_message_at = now
    ticket.last_author = kind
    db.flush()
    return msg


def restart_clock(ticket: SupportTicket, now: datetime) -> None:
    """Back to open, waiting for a staff reply: the first-reply clock starts over."""
    ticket.status = TicketStatus.open
    ticket.resolved_at = None
    ticket.resolved_by = None
    ticket.first_response_at = None
    ticket.sla_due_at = sla_due(ticket.priority, now)


def send_after_commit(build: Callable[[], EmailMessage | None]) -> None:
    """Send mail only after the commit, so no email goes out for a change that rolled back. A
    refused send is logged, never raised: the ticket change already happened."""
    try:
        msg = build()
        if msg is not None:
            get_email_sender().send(msg)
    except EmailDeliveryFailed:
        log.warning("support email failed: provider refused the send")


def now_utc() -> datetime:
    return datetime.now(UTC)
