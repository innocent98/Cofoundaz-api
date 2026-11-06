"""The Marketplace timers (D-MP-L7), run once a minute by the worker.

1. A checkout hold that lapsed unpaid: the booking `expired`, the order `failed`, no charge.
2. A paid booking the expert hasn't answered in 48 hours (or whose slot has started): `expired`
   and the card refunded in full.
3. Refunds that are due (a request made earlier, or a Paystack outage): retried with backoff.

Every booking is row-locked with SKIP LOCKED (a request already working on it wins and the timer
picks it up next minute), and booking is always locked before its order, as everywhere else."""

import uuid
from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logger import log
from app.db.models.enums import BookingStatus, OrderStatus
from app.db.models.marketplace import MarketplaceBooking, MarketplaceOrder, MarketplaceProvider
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.billing import paystack
from app.services.marketplace import emails, orders, outbox
from app.services.marketplace.constants import (
    MAX_REFUND_ATTEMPTS,
    TIMER_BATCH,
    UNVERIFIED_HOLD_GRACE,
)


def _expire_hold(db: Session, booking_id: uuid.UUID, *, unverified: bool) -> int:
    """Close one lapsed hold (booking locked first, then order). Returns 1 if it expired."""
    b = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == booking_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if b is None or b.status != BookingStatus.pending_payment:
        return 0
    order = orders.lock_order(db, db.query(MarketplaceOrder.id).filter_by(booking_id=b.id).scalar())
    if order.status == OrderStatus.pending:
        order.status = OrderStatus.failed
    b.status = BookingStatus.expired
    b.closed_reason = "payment_not_completed"
    write_audit(
        db,
        "marketplace.booking.expired",
        startup_id=b.startup_id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"why": "payment_not_completed", "verified": not unverified},
    )
    if unverified:
        log.warning(f"marketplace timers: closed hold {b.id} Paystack could not answer for")
        booker = db.get(User, b.booked_by)
        if booker is not None:
            outbox.queue(
                db,
                emails.payment_problem_email(booker.email, title=b.title_snap, kind="unverified"),
            )
    return 1


def expire_unpaid(db: Session, t: datetime) -> int:
    """Expire lapsed checkout holds, but never before asking Paystack: a charge that actually
    went through (the founder closed the tab, the webhook was lost) is applied, not failed.

    Each hold is its own transaction (committed, then its emails sent) and its own failure domain:
    no expert lock is held across candidates or across the network call, and one bad hold can't
    abort the tick. A hold Paystack can't answer for is retried every minute until
    UNVERIFIED_HOLD_GRACE past its expiry, then closed (a payment that turns up later still
    revives or refunds it). Orders flagged for staff are skipped."""
    candidates = [
        (bid, ref, exp)
        for bid, ref, exp in db.query(
            MarketplaceBooking.id,
            MarketplaceOrder.paystack_reference,
            MarketplaceBooking.slot_hold_expires_at,
        )
        .join(MarketplaceOrder, MarketplaceOrder.booking_id == MarketplaceBooking.id)
        .filter(
            MarketplaceBooking.status == BookingStatus.pending_payment,
            MarketplaceBooking.slot_hold_expires_at < t,
            MarketplaceOrder.attention_reason.is_(None),
        )
        .order_by(MarketplaceBooking.slot_hold_expires_at)
        .limit(TIMER_BATCH)
        .all()
    ]
    db.commit()  # the read above is done; hold nothing while we talk to Paystack
    expired = 0
    for booking_id, reference, hold_expired_at in candidates:
        try:
            unverified = False
            try:
                tx = paystack.verify(reference)
            except paystack.PaystackNotFound:
                tx = None  # Paystack never saw this payment: it really is unpaid
            except AppError:
                if hold_expired_at is None or t - hold_expired_at < UNVERIFIED_HOLD_GRACE:
                    continue  # can't tell yet: leave the hold, try again next minute
                tx, unverified = None, True  # out of patience: close it, never retry forever
            if tx is not None and tx.get("status") == "success":
                orders.apply_payment(db, reference, tx)  # money arrived: revive or refund
            else:
                expired += _expire_hold(db, booking_id, unverified=unverified)
            db.commit()
            outbox.flush(db)
        except Exception as exc:  # noqa: BLE001 - one bad hold must not stop the rest
            db.rollback()
            log.warning(f"marketplace timers: hold {booking_id} failed: {exc}")
    return expired


def expire_unanswered(db: Session, t: datetime) -> list[uuid.UUID]:
    """Returns the ids of orders now owed a full refund."""
    rows = (
        db.query(MarketplaceBooking)
        .filter(
            MarketplaceBooking.status == BookingStatus.awaiting_provider,
            or_(
                MarketplaceBooking.accept_by < t,
                and_(MarketplaceBooking.starts_at.isnot(None), MarketplaceBooking.starts_at <= t),
            ),
        )
        .order_by(MarketplaceBooking.accept_by)
        .limit(TIMER_BATCH)
        .with_for_update(skip_locked=True)
        .all()
    )
    owed: list[uuid.UUID] = []
    for b in rows:
        order = orders.lock_order(
            db, db.query(MarketplaceOrder.id).filter_by(booking_id=b.id).scalar()
        )
        slot_gone = b.starts_at is not None and b.starts_at <= t
        b.status = BookingStatus.expired
        b.closed_reason = (
            "slot_passed"
            if slot_gone and not (b.accept_by and b.accept_by < t)
            else "provider_no_response"
        )
        orders.request_refund(
            db,
            order,
            order.amount_minor - order.refunded_minor - order.refund_due_minor,
            b.closed_reason,
        )
        write_audit(
            db,
            "marketplace.booking.expired",
            startup_id=b.startup_id,
            entity_type="marketplace_booking",
            entity_id=b.id,
            details={"why": b.closed_reason},
        )
        event_bus.publish(db, "marketplace.booking.expired", {"booking_id": str(b.id)})
        booker = db.get(User, b.booked_by)
        provider = db.get(MarketplaceProvider, b.provider_id)
        if booker is not None:
            outbox.queue(
                db,
                emails.booking_closed_email(
                    booker.email,
                    provider=provider.display_name if provider else None,
                    title=b.title_snap,
                    reason=b.closed_reason,
                ),
            )
        owed.append(order.id)
    return owed


def due_refund_ids(db: Session, t: datetime) -> list[uuid.UUID]:
    rows = (
        db.query(MarketplaceOrder.id)
        .filter(
            MarketplaceOrder.refund_due_minor > 0,
            MarketplaceOrder.refund_attempts < MAX_REFUND_ATTEMPTS,
            or_(
                MarketplaceOrder.refund_next_attempt_at.is_(None),
                MarketplaceOrder.refund_next_attempt_at <= t,
            ),
        )
        .order_by(MarketplaceOrder.refund_next_attempt_at)
        .limit(TIMER_BATCH)
        .all()
    )
    return [r[0] for r in rows]


def tick(db: Session, t: datetime) -> dict[str, int]:
    """One pass. Commits as it goes (state first, then each refund on its own) and sends the
    queued emails only after the commit that made them true."""
    unpaid = expire_unpaid(db, t)
    unanswered = expire_unanswered(db, t)
    db.commit()
    outbox.flush(db)
    refunded = 0
    for order_id in due_refund_ids(db, t):
        try:
            outcome = orders.process_refund(db, order_id, at=t)
            db.commit()
            refunded += outcome == "refunded"
        except Exception as exc:  # noqa: BLE001 - one bad refund must not stop the rest
            db.rollback()
            log.warning(f"marketplace timers: refund for order {order_id} failed: {exc}")
    return {"expired_unpaid": unpaid, "expired_unanswered": len(unanswered), "refunded": refunded}
