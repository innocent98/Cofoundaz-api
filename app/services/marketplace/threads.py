"""Messaging (design §3.7, gap 20): one conversation per workspace-and-expert pair before any
booking, attached to a booking once one is paid.

Sides. The founder side is the workspace: a founder and team members READ, only a founder POSTS
(D-MP-L1). The expert side is the expert. Each side has one read stamp per message, so a team
member opening the thread reads it for the whole workspace. Staff never read a thread here
(D-MP-L11: only inside a dispute, slice E, and audited).

Who starts a conversation: only a founder. An expert can reply to a conversation that exists (a
founder wrote, or a booking is paid) but cannot open one, so an expert can't cold-message the
whole directory. A thread with no message yet and no booking is invisible to the expert.

Messages are plain text. They are never put in an email (the expert gets "you have a new message",
once per thread per 10 minutes) and a founder's in-app notification names the expert but quotes no
text, since a notification can be emailed. The thread list's last-message preview is API only. Locking: posting locks the THREAD row only (to serialise the digest stamp);
it reads the booking without a lock, so it can never deadlock with `apply_payment`, which locks
booking -> order -> expert -> thread."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext
from app.core.errors import NotFound
from app.db.models.enums import BookingStatus, MarketplaceFilePurpose, ProviderStatus
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceMessage,
    MarketplaceProvider,
    MarketplaceThread,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.marketplace import emails, files, identity, outbox
from app.services.marketplace.constants import (
    MAX_MESSAGE_FILES,
    MAX_NEW_THREADS_PER_DAY,
    MESSAGE_COLLAPSE,
)
from app.services.marketplace.listings import err

FOUNDER, PROVIDER = "founder", "provider"
# A closed booking's thread is read-only. `completed` stays open for follow-up questions.
CLOSED = (
    BookingStatus.declined,
    BookingStatus.expired,
    BookingStatus.cancelled,
    BookingStatus.refunded,
)
FOUNDER_FILES = "/api/v1/marketplace/files"
PROVIDER_FILES = "/api/v1/provider/files"
PREVIEW_CHARS = 80


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ creating threads


def _pair_thread(
    db: Session, startup_id: uuid.UUID, provider_id: uuid.UUID
) -> MarketplaceThread | None:
    return (
        db.query(MarketplaceThread)
        .filter(
            MarketplaceThread.startup_id == startup_id,
            MarketplaceThread.provider_id == provider_id,
            MarketplaceThread.booking_id.is_(None),
        )
        .first()
    )


def open_thread(
    db: Session, user: User, m: Membership, provider_id: uuid.UUID
) -> tuple[MarketplaceThread, bool]:
    """Get or create the pre-booking thread with an approved expert. Returns (thread, created)."""
    provider = db.get(MarketplaceProvider, provider_id)
    if provider is None or provider.status != ProviderStatus.approved:
        raise NotFound()
    if identity.is_own_provider(db, provider, m.startup_id):
        raise err("CANNOT_MESSAGE_OWN", "You can't message your own expert profile.", 422)
    existing = _pair_thread(db, m.startup_id, provider.id)
    if existing is not None:
        return existing, False
    since = now() - timedelta(days=1)
    fresh = (
        db.query(func.count(MarketplaceThread.id))
        .filter(
            MarketplaceThread.startup_id == m.startup_id,
            MarketplaceThread.booking_id.is_(None),
            MarketplaceThread.created_at >= since,
        )
        .scalar()
    )
    if (fresh or 0) >= MAX_NEW_THREADS_PER_DAY:
        raise err(
            "THREAD_LIMIT_REACHED",
            "You've started a lot of conversations today. Try again tomorrow, or reply in one "
            "you already have.",
            429,
        )
    thread = MarketplaceThread(startup_id=m.startup_id, provider_id=provider.id)
    try:
        with db.begin_nested():
            db.add(thread)
            db.flush()
    except IntegrityError:  # a concurrent request created it first
        again = _pair_thread(db, m.startup_id, provider.id)
        if again is None:
            raise
        return again, False
    return thread, True


def attach_booking(db: Session, b: MarketplaceBooking) -> MarketplaceThread:
    """The booking was just paid (caller holds the booking lock, and the expert's where needed):
    attach it to the pair's pre-booking thread if there is one, else open a thread for it. Safe to
    call twice. Refuses (404) a booking that was never paid."""
    if not identity.was_paid(db, b):
        raise NotFound()
    existing = db.query(MarketplaceThread).filter(MarketplaceThread.booking_id == b.id).first()
    if existing is not None:
        return existing
    pre = (
        db.query(MarketplaceThread)
        .filter(
            MarketplaceThread.startup_id == b.startup_id,
            MarketplaceThread.provider_id == b.provider_id,
            MarketplaceThread.booking_id.is_(None),
        )
        .with_for_update()
        .first()
    )
    if pre is not None:
        pre.booking_id = b.id
        db.flush()
        return pre
    thread = MarketplaceThread(startup_id=b.startup_id, provider_id=b.provider_id, booking_id=b.id)
    try:
        with db.begin_nested():
            db.add(thread)
            db.flush()
    except IntegrityError:
        found = db.query(MarketplaceThread).filter(MarketplaceThread.booking_id == b.id).first()
        if found is None:
            raise
        return found
    return thread


# ------------------------------------------------------------------ finding threads (per side)


def _founder_thread(db: Session, m: Membership, thread_id: uuid.UUID) -> MarketplaceThread:
    row = (
        db.query(MarketplaceThread)
        .filter(MarketplaceThread.id == thread_id, MarketplaceThread.startup_id == m.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def _provider_visible():  # type: ignore[no-untyped-def]
    """A thread the expert may see: a booking exists, or the founder has written."""
    return or_(
        MarketplaceThread.booking_id.isnot(None), MarketplaceThread.last_message_at.isnot(None)
    )


def _provider_thread(db: Session, ctx: ProviderContext, thread_id: uuid.UUID) -> MarketplaceThread:
    row = (
        db.query(MarketplaceThread)
        .filter(
            MarketplaceThread.id == thread_id,
            MarketplaceThread.provider_id == ctx.provider.id,
            _provider_visible(),
        )
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def _founder_booking(db: Session, m: Membership, booking_id: uuid.UUID) -> MarketplaceBooking:
    b = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == booking_id, MarketplaceBooking.startup_id == m.startup_id)
        .first()
    )
    if b is None:
        raise NotFound()
    return b


def booking_thread_founder_read(
    db: Session, m: Membership, booking_id: uuid.UUID
) -> MarketplaceThread | None:
    """READ path: the booking's thread if it has one, else None. Never creates or locks."""
    b = _founder_booking(db, m, booking_id)
    return db.query(MarketplaceThread).filter(MarketplaceThread.booking_id == b.id).first()


def booking_thread_founder_post(
    db: Session, m: Membership, booking_id: uuid.UUID
) -> MarketplaceThread:
    """POST path (a founder): the booking's thread, created on first use for a booking paid
    before threads existed. A checkout still waiting for payment has none yet (409); a booking
    that never went live (a lapsed hold, a payment under review, a refunded late payment) never
    gets one (404), so the pair's live pre-booking thread is never attached to it."""
    b = _founder_booking(db, m, booking_id)
    if b.status == BookingStatus.pending_payment:
        raise err("THREAD_NOT_AVAILABLE", "Messaging opens once the booking is paid.", 409)
    if not identity.was_paid(db, b):
        raise NotFound()
    return attach_booking(db, b)


def booking_thread_provider(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID
) -> MarketplaceThread:
    """The expert never creates a thread: it exists (payment made one) or this is a 404."""
    b = (
        db.query(MarketplaceBooking)
        .filter(
            MarketplaceBooking.id == booking_id,
            MarketplaceBooking.provider_id == ctx.provider.id,
            identity.paid_clause(),
        )
        .first()
    )
    thread = (
        db.query(MarketplaceThread).filter(MarketplaceThread.booking_id == b.id).first()
        if b is not None
        else None
    )
    if thread is None:
        raise NotFound()
    return thread


# ------------------------------------------------------------------ views


def _counts(db: Session, thread_ids: list[uuid.UUID], side: str) -> dict[uuid.UUID, int]:
    """Unread messages per thread for `side`: what the OTHER side sent that this side hasn't read."""
    if not thread_ids:
        return {}
    other, read_col = (
        (PROVIDER, MarketplaceMessage.read_at_founder_side)
        if side == FOUNDER
        else (FOUNDER, MarketplaceMessage.read_at_provider_side)
    )
    rows = (
        db.query(MarketplaceMessage.thread_id, func.count(MarketplaceMessage.id))
        .filter(
            MarketplaceMessage.thread_id.in_(thread_ids),
            MarketplaceMessage.sender_side == other,
            read_col.is_(None),
        )
        .group_by(MarketplaceMessage.thread_id)
        .all()
    )
    return {tid: int(n) for tid, n in rows}


def _last_message(db: Session, thread_id: uuid.UUID) -> MarketplaceMessage | None:
    return (
        db.query(MarketplaceMessage)
        .filter(MarketplaceMessage.thread_id == thread_id)
        .order_by(MarketplaceMessage.created_at.desc(), MarketplaceMessage.id.desc())
        .first()
    )


def _thread_view(db: Session, t: MarketplaceThread, side: str, unread: int) -> dict[str, Any]:
    last = _last_message(db, t.id)
    booking = db.get(MarketplaceBooking, t.booking_id) if t.booking_id else None
    out: dict[str, Any] = {
        "id": str(t.id),
        "booking_id": str(t.booking_id) if t.booking_id else None,
        "booking_title": booking.title_snap if booking else None,
        "booking_status": booking.status.value if booking else None,
        "read_only": bool(booking and booking.status in CLOSED),
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "last_message_preview": last.body[:PREVIEW_CHARS] if last else None,
        "unread_count": unread,
        "created_at": t.created_at.isoformat(),
    }
    if side == FOUNDER:
        provider = db.get(MarketplaceProvider, t.provider_id)
        out["provider"] = {
            "id": str(t.provider_id),
            "display_name": provider.display_name if provider else None,
        }
    else:
        client = db.get(User, booking.booked_by) if booking else None
        out["client_name"] = client.profile.full_name if client and client.profile else None
    return out


def thread_view(db: Session, t: MarketplaceThread, side: str) -> dict[str, Any]:
    return _thread_view(db, t, side, _counts(db, [t.id], side).get(t.id, 0))


def list_founder(
    db: Session, m: Membership, *, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int, int]:
    q = db.query(MarketplaceThread).filter(MarketplaceThread.startup_id == m.startup_id)
    total = q.count()
    rows = (
        q.order_by(
            MarketplaceThread.last_message_at.desc().nullslast(),
            MarketplaceThread.created_at.desc(),
            MarketplaceThread.id,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return _views(db, rows, FOUNDER), total, _unread_total(db, FOUNDER, startup_id=m.startup_id)


def list_provider(
    db: Session, ctx: ProviderContext, *, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int, int]:
    q = db.query(MarketplaceThread).filter(
        MarketplaceThread.provider_id == ctx.provider.id, _provider_visible()
    )
    total = q.count()
    rows = (
        q.order_by(
            MarketplaceThread.last_message_at.desc().nullslast(),
            MarketplaceThread.created_at.desc(),
            MarketplaceThread.id,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return (
        _views(db, rows, PROVIDER),
        total,
        _unread_total(db, PROVIDER, provider_id=ctx.provider.id),
    )


def _views(db: Session, rows: list[MarketplaceThread], side: str) -> list[dict[str, Any]]:
    counts = _counts(db, [r.id for r in rows], side)
    return [_thread_view(db, r, side, counts.get(r.id, 0)) for r in rows]


def _unread_total(
    db: Session,
    side: str,
    *,
    startup_id: uuid.UUID | None = None,
    provider_id: uuid.UUID | None = None,
) -> int:
    other, read_col = (
        (PROVIDER, MarketplaceMessage.read_at_founder_side)
        if side == FOUNDER
        else (FOUNDER, MarketplaceMessage.read_at_provider_side)
    )
    q = (
        db.query(func.count(MarketplaceMessage.id))
        .join(MarketplaceThread, MarketplaceThread.id == MarketplaceMessage.thread_id)
        .filter(MarketplaceMessage.sender_side == other, read_col.is_(None))
    )
    if startup_id is not None:
        q = q.filter(MarketplaceThread.startup_id == startup_id)
    if provider_id is not None:
        q = q.filter(MarketplaceThread.provider_id == provider_id, _provider_visible())
    return int(q.scalar() or 0)


def get_founder(db: Session, m: Membership, thread_id: uuid.UUID) -> dict[str, Any]:
    return thread_view(db, _founder_thread(db, m, thread_id), FOUNDER)


def get_provider(db: Session, ctx: ProviderContext, thread_id: uuid.UUID) -> dict[str, Any]:
    return thread_view(db, _provider_thread(db, ctx, thread_id), PROVIDER)


def serialize_message(db: Session, msg: MarketplaceMessage, side: str) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "thread_id": str(msg.thread_id),
        "sender_side": msg.sender_side,
        "mine": msg.sender_side == side,
        "body": msg.body,
        "files": files.serialize_many(
            db, list(msg.file_ids), base=FOUNDER_FILES if side == FOUNDER else PROVIDER_FILES
        ),
        "created_at": msg.created_at.isoformat(),
        "read": (
            (msg.read_at_provider_side if side == FOUNDER else msg.read_at_founder_side) is not None
            if msg.sender_side == side
            else True
        ),
    }


# ------------------------------------------------------------------ reading messages


def _read_page(
    db: Session, t: MarketplaceThread, side: str, *, limit: int, before: uuid.UUID | None
) -> tuple[list[dict[str, Any]], bool]:
    """The newest `limit` messages (older than `before` when paging back), oldest first. Reading
    marks everything the other side sent in this thread as read by this side."""
    q = db.query(MarketplaceMessage).filter(MarketplaceMessage.thread_id == t.id)
    if before is not None:
        anchor = (
            db.query(MarketplaceMessage)
            .filter(MarketplaceMessage.id == before, MarketplaceMessage.thread_id == t.id)
            .first()
        )
        if anchor is None:
            raise NotFound()
        q = q.filter(
            or_(
                MarketplaceMessage.created_at < anchor.created_at,
                and_(
                    MarketplaceMessage.created_at == anchor.created_at,
                    MarketplaceMessage.id < anchor.id,
                ),
            )
        )
    rows = (
        q.order_by(MarketplaceMessage.created_at.desc(), MarketplaceMessage.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = list(reversed(rows[:limit]))
    other, read_col = (
        (PROVIDER, MarketplaceMessage.read_at_founder_side)
        if side == FOUNDER
        else (FOUNDER, MarketplaceMessage.read_at_provider_side)
    )
    col_name = "read_at_founder_side" if side == FOUNDER else "read_at_provider_side"
    db.execute(
        update(MarketplaceMessage)
        .where(
            MarketplaceMessage.thread_id == t.id,
            MarketplaceMessage.sender_side == other,
            read_col.is_(None),
        )
        .values({col_name: now()})
        .execution_options(synchronize_session=False)
    )
    out = [serialize_message(db, r, side) for r in page]
    return out, has_more


def read_founder(
    db: Session, m: Membership, thread_id: uuid.UUID, *, limit: int, before: uuid.UUID | None
) -> tuple[list[dict[str, Any]], bool]:
    return _read_page(db, _founder_thread(db, m, thread_id), FOUNDER, limit=limit, before=before)


def read_provider(
    db: Session, ctx: ProviderContext, thread_id: uuid.UUID, *, limit: int, before: uuid.UUID | None
) -> tuple[list[dict[str, Any]], bool]:
    return _read_page(
        db, _provider_thread(db, ctx, thread_id), PROVIDER, limit=limit, before=before
    )


# ------------------------------------------------------------------ posting


def _lock_thread(db: Session, thread_id: uuid.UUID) -> MarketplaceThread:
    row = (
        db.query(MarketplaceThread)
        .filter(MarketplaceThread.id == thread_id)
        .with_for_update()
        .one()
    )
    return row


def _check_open(db: Session, t: MarketplaceThread) -> MarketplaceBooking | None:
    booking = db.get(MarketplaceBooking, t.booking_id) if t.booking_id else None
    if booking is not None and booking.status in CLOSED:
        raise err("THREAD_CLOSED", "This booking is closed, so the conversation is read-only.", 409)
    return booking


def _post(
    db: Session,
    t: MarketplaceThread,
    *,
    side: str,
    sender: User,
    body: str,
    file_ids: list[uuid.UUID],
) -> MarketplaceMessage:
    """`t` is locked and open."""
    stamp = now()
    attached = files.attach(
        db,
        owner_user_id=sender.id,
        file_ids=file_ids,
        purposes=frozenset({MarketplaceFilePurpose.message}),
        limit=MAX_MESSAGE_FILES,
        booking_id=t.booking_id,
        thread_id=None if t.booking_id else t.id,
    )
    msg = MarketplaceMessage(
        thread_id=t.id,
        sender_user_id=sender.id,
        sender_side=side,
        body=body,
        file_ids=[str(f.id) for f in attached],
        created_at=stamp,  # explicit: the server default is the transaction's start, not "now"
    )
    if side == FOUNDER:
        msg.read_at_founder_side = stamp
    else:
        msg.read_at_provider_side = stamp
    db.add(msg)
    t.last_message_at = stamp
    db.flush()
    return msg


def post_founder(
    db: Session,
    user: User,
    m: Membership,
    thread_id: uuid.UUID,
    *,
    body: str,
    file_ids: list[uuid.UUID],
) -> MarketplaceMessage:
    _founder_thread(db, m, thread_id)  # 404 for a thread of another workspace, before any lock
    t = _lock_thread(db, thread_id)
    _check_open(db, t)
    msg = _post(db, t, side=FOUNDER, sender=user, body=body, file_ids=file_ids)
    _tell_provider(db, t, user)
    write_audit(
        db,
        "marketplace.message.sent",
        actor_user_id=user.id,
        startup_id=t.startup_id,
        entity_type="marketplace_thread",
        entity_id=t.id,
        details={"side": FOUNDER, "files": len(msg.file_ids)},
    )
    return msg


def post_provider(
    db: Session,
    ctx: ProviderContext,
    thread_id: uuid.UUID,
    *,
    body: str,
    file_ids: list[uuid.UUID],
) -> MarketplaceMessage:
    _provider_thread(db, ctx, thread_id)
    t = _lock_thread(db, thread_id)
    booking = _check_open(db, t)
    msg = _post(db, t, side=PROVIDER, sender=ctx.user, body=body, file_ids=file_ids)
    event_bus.publish(
        db,
        "marketplace.message.new",
        {
            "startup_id": str(t.startup_id),
            "thread_id": str(t.id),
            "booking_id": str(t.booking_id) if t.booking_id else None,
            "provider_id": str(t.provider_id),
            "provider_name": ctx.provider.display_name,
            "service": booking.title_snap if booking else None,
        },
    )
    write_audit(
        db,
        "marketplace.message.sent",
        actor_user_id=ctx.user.id,
        startup_id=t.startup_id,
        entity_type="marketplace_thread",
        entity_id=t.id,
        details={"side": PROVIDER, "files": len(msg.file_ids)},
    )
    return msg


def _tell_provider(db: Session, t: MarketplaceThread, sender: User) -> None:
    """One email per thread per MESSAGE_COLLAPSE window, however many messages arrive in it."""
    stamp = now()
    if t.provider_emailed_at is not None and stamp - t.provider_emailed_at < MESSAGE_COLLAPSE:
        return
    provider = db.get(MarketplaceProvider, t.provider_id)
    if provider is None or provider.status != ProviderStatus.approved:
        return
    t.provider_emailed_at = stamp
    outbox.queue(
        db,
        emails.new_message_email(
            provider.email,
            name=provider.display_name,
            sender=sender.profile.full_name if sender.profile else None,
        ),
    )
