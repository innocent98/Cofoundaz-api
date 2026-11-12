"""Who is who across the two sides of the Marketplace."""

import uuid

from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.orm import Session

from app.db.models.enums import BookingStatus, MembershipStatus
from app.db.models.marketplace import MarketplaceBooking, MarketplaceOrder, MarketplaceProvider
from app.db.models.membership import Membership
from app.db.models.user import User


def is_own_provider(db: Session, provider: MarketplaceProvider, startup_id: uuid.UUID) -> bool:
    """D-MP-L16: an expert can't deal with their own workspace: same user, or the same email as
    any active member."""
    rows = (
        db.query(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )
    emails_ = {e.lower() for _, e in rows}
    return (provider.user_id in {uid for uid, _ in rows}) or provider.email.lower() in emails_


# A booking "was paid" when it reached `awaiting_provider` or later. The statuses below are always
# past that point; a closed one (cancelled, declined, expired, refunded) counts only if its order
# took the money AND it was not closed for a reason that means it never went live (a checkout
# that lapsed, a payment under review, or a late payment that was refunded straight back).
LIVE_AFTER_PAYMENT = (
    BookingStatus.awaiting_provider,
    BookingStatus.confirmed,
    BookingStatus.in_progress,
    BookingStatus.delivered,
    BookingStatus.disputed,
    BookingStatus.completed,
    BookingStatus.partially_refunded,
)
CLOSED_STATUSES = (
    BookingStatus.cancelled,
    BookingStatus.declined,
    BookingStatus.expired,
    BookingStatus.refunded,
)
NEVER_LIVE_REASONS = (
    "payment_not_completed",
    "payment_review",
    "slot_no_longer_available",
    "booking_closed",
)


def paid_clause() -> ColumnElement[bool]:
    """SQL form of `was_paid`, for the expert-side queries."""
    took_money = exists().where(
        MarketplaceOrder.booking_id == MarketplaceBooking.id,
        MarketplaceOrder.paid_at.isnot(None),
    )
    return or_(
        MarketplaceBooking.status.in_(LIVE_AFTER_PAYMENT),
        and_(
            MarketplaceBooking.status.in_(CLOSED_STATUSES),
            or_(
                MarketplaceBooking.closed_reason.is_(None),
                MarketplaceBooking.closed_reason.notin_(NEVER_LIVE_REASONS),
            ),
            took_money,
        ),
    )


def was_paid(db: Session, b: MarketplaceBooking) -> bool:
    if b.status in LIVE_AFTER_PAYMENT:
        return True
    if b.status not in CLOSED_STATUSES or b.closed_reason in NEVER_LIVE_REASONS:
        return False
    return (
        db.query(MarketplaceOrder.id)
        .filter(MarketplaceOrder.booking_id == b.id, MarketplaceOrder.paid_at.isnot(None))
        .first()
        is not None
    )
