"""Orders, the ledger and refunds (design §1.4, D-MP-3 a, D-MP-4, D-MP-5). All money is integer
kobo. Every function here that changes an order expects the caller to hold it row-locked
(`lock_order`), so two requests (a webhook and a founder's "verify", a timer and a decline) can
never both act on the same payment.

Ledger rule: `marketplace_ledger_entries` is append-only (database trigger) and unique per
(order, kind, external_ref), so a replay can't write a movement twice. The ledger's sum for an
order is the money still held: charge minus refunds. `order.refunded_minor` must always equal
what the ledger says was refunded (`ledger_balance`, asserted in tests)."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.enums import BookingStatus, LedgerKind, OrderStatus
from app.db.models.marketplace import (
    ACTIVE_BOOKING,
    MarketplaceBooking,
    MarketplaceLedgerEntry,
    MarketplaceOrder,
    MarketplaceProvider,
    receipt_seq,
)
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.billing import paystack
from app.services.marketplace import outbox
from app.services.marketplace.constants import (
    ACCEPT_WINDOW,
    MAX_REFUND_ATTEMPTS,
    REFUND_RETRY_BACKOFF_MIN,
)
from app.services.marketplace.listings import err


def reference_for(booking_id: uuid.UUID) -> str:
    """`mp_` + 20 hex, derived from the booking id: stable, unguessable enough for a reference and
    matching `paystack.REFERENCE_RE`."""
    return "mp_" + hashlib.sha256(f"marketplace:{booking_id}".encode()).hexdigest()[:20]


def now() -> datetime:
    return datetime.now(UTC)


def lock_order(db: Session, order_id: uuid.UUID) -> MarketplaceOrder:
    row = (
        db.query(MarketplaceOrder).filter(MarketplaceOrder.id == order_id).with_for_update().first()
    )
    if row is None:
        raise err("ORDER_NOT_FOUND", "No such order.", 404)
    return row


def write_ledger(
    db: Session,
    order: MarketplaceOrder,
    provider_id: uuid.UUID,
    kind: LedgerKind,
    amount_minor: int,
    external_ref: str,
    *,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Append one movement. False when this exact (order, kind, external_ref) already exists."""
    inserted = db.execute(
        insert(MarketplaceLedgerEntry)
        .values(
            id=uuid.uuid4(),
            order_id=order.id,
            provider_id=provider_id,
            kind=kind,
            amount_minor=amount_minor,
            currency=order.currency,
            external_ref=external_ref[:80],
            note=(note or "")[:200] or None,
            actor_id=actor_id,
        )
        .on_conflict_do_nothing(constraint="uq_marketplace_ledger_once")
        .returning(MarketplaceLedgerEntry.id)
    ).first()
    return inserted is not None


def ledger_balance(db: Session, order_id: uuid.UUID) -> int:
    """Money still held for the order: the signed sum of its ledger entries."""
    total = (
        db.query(func.coalesce(func.sum(MarketplaceLedgerEntry.amount_minor), 0))
        .filter(MarketplaceLedgerEntry.order_id == order_id)
        .scalar()
    )
    return int(total or 0)


def _next_receipt_number(db: Session) -> str:
    n = db.execute(receipt_seq.next_value()).scalar()
    return f"MPR-{now().year}-{n}"


# ------------------------------------------------------------------ taking payment


def _paid_at(tx: dict[str, Any]) -> datetime:
    raw = tx.get("paid_at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return now()


def slot_still_free(db: Session, booking: MarketplaceBooking) -> bool:
    """Used when a payment lands after the checkout hold lapsed: may the booking be revived? A
    live booking OR another founder's unexpired hold on an overlapping time says no. The caller
    holds the expert's row lock so two late payments can't both revive."""
    if booking.starts_at is None or booking.ends_at is None:
        return True
    t = now()
    if booking.starts_at <= t:
        return False
    clash = (
        db.query(MarketplaceBooking.id)
        .filter(
            MarketplaceBooking.provider_id == booking.provider_id,
            MarketplaceBooking.id != booking.id,
            or_(
                MarketplaceBooking.status.in_(ACTIVE_BOOKING),
                and_(
                    MarketplaceBooking.status == BookingStatus.pending_payment,
                    MarketplaceBooking.slot_hold_expires_at > t,
                ),
            ),
            MarketplaceBooking.starts_at < booking.ends_at,
            MarketplaceBooking.ends_at > booking.starts_at,
        )
        .first()
    )
    return clash is None


def apply_payment(db: Session, reference: str, tx: dict[str, Any]) -> str:
    """Apply a payment Paystack has CONFIRMED for `reference` (`tx` is the answer of Paystack's
    verify call, never the webhook body). Idempotent: a second call finds the order paid and does
    nothing. Money that arrives for a booking that can no longer proceed (cancelled, or its slot
    taken after the hold lapsed) is refunded in full automatically."""
    # Lock order is ALWAYS booking first, then order (decline, cancel and the timers do the same),
    # so none of them can deadlock with a payment arriving. Find the ids without a lock first.
    ids = (
        db.query(MarketplaceOrder.id, MarketplaceOrder.booking_id)
        .filter(MarketplaceOrder.paystack_reference == reference)
        .first()
    )
    if ids is None:
        return "ignored_unknown_reference"
    booking = (
        db.query(MarketplaceBooking)
        .filter(MarketplaceBooking.id == ids.booking_id)
        .with_for_update()
        .one()
    )
    order = lock_order(db, ids.id)
    if order.status not in (OrderStatus.pending, OrderStatus.failed):
        return "already_applied"
    if tx.get("status") != "success":
        return "payment_not_successful"
    if (
        tx.get("reference") != reference
        or tx.get("currency") != order.currency
        or not isinstance(tx.get("amount"), int)
        or tx["amount"] != order.amount_minor
    ):
        log.warning(f"marketplace: payment for {reference} doesn't match its order; not applied")
        return "payment_mismatch"
    t = now()
    order.status = OrderStatus.paid
    order.paid_at = _paid_at(tx)
    order.receipt_number = _next_receipt_number(db)
    write_ledger(
        db,
        order,
        booking.provider_id,
        LedgerKind.charge,
        order.amount_minor,
        reference,
        note="Card payment received",
    )
    write_audit(
        db,
        "marketplace.order.paid",
        actor_user_id=booking.booked_by,
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={"amount_minor": order.amount_minor, "booking_id": str(booking.id)},
    )
    revivable = booking.status == BookingStatus.pending_payment or (
        booking.status == BookingStatus.expired and booking.closed_reason == "payment_not_completed"
    )
    if revivable and booking.starts_at is not None:
        # Booking -> order -> expert: the expert lock is last, and create_booking (expert first)
        # never holds a booking lock, so the two can't deadlock.
        db.query(MarketplaceProvider.id).filter(
            MarketplaceProvider.id == booking.provider_id
        ).with_for_update().one()
    if revivable and slot_still_free(db, booking):
        booking.status = BookingStatus.awaiting_provider
        booking.slot_hold_expires_at = None
        booking.accept_by = t + ACCEPT_WINDOW
        booking.closed_reason = None
        write_audit(
            db,
            "marketplace.booking.created",
            actor_user_id=booking.booked_by,
            startup_id=booking.startup_id,
            entity_type="marketplace_booking",
            entity_id=booking.id,
            details={
                "provider_id": str(booking.provider_id),
                "listing_id": str(booking.listing_id),
            },
        )
        event_bus.publish(
            db,
            "marketplace.booking.created",
            {"booking_id": str(booking.id), "provider_id": str(booking.provider_id)},
        )
        event_bus.publish(db, "marketplace.order.paid", {"order_id": str(order.id)})
        outbox.queue(db, outbox.new_request_email(db, booking))
        return "paid"
    reason = "booking_closed" if not revivable else "slot_no_longer_available"
    if booking.status == BookingStatus.pending_payment:
        booking.status = BookingStatus.expired
    booking.closed_reason = booking.closed_reason or reason
    request_refund(db, order, order.amount_minor, reason)
    return "paid_then_refund_requested"


# ------------------------------------------------------------------ refunds


def request_refund(
    db: Session,
    order: MarketplaceOrder,
    amount_minor: int,
    reason: str,
    *,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Ask for `amount_minor` kobo back. Records the intent only: `process_refund` talks to
    Paystack and writes the ledger entry once Paystack accepts, and a timer retries it if Paystack
    is down. The order must be locked and paid. False if nothing (more) can be refunded."""
    if order.status not in (OrderStatus.paid, OrderStatus.held, OrderStatus.partially_refunded):
        return False
    remaining = order.amount_minor - order.refunded_minor - order.refund_due_minor
    if amount_minor <= 0 or amount_minor > remaining:
        return False
    order.refund_due_minor += amount_minor
    order.refund_reason = reason[:60]
    order.refund_attempts = 0
    order.refund_next_attempt_at = now()
    order.refund_failed_at = None
    write_audit(
        db,
        "marketplace.order.refund_requested",
        actor_user_id=actor_id,
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={"amount_minor": amount_minor, "reason": reason},
    )
    return True


def record_refund_sent(
    db: Session, order: MarketplaceOrder, booking: MarketplaceBooking, amount: int, refund_ref: str
) -> None:
    """Paystack has taken (or confirmed) a refund of `amount`: write the ledger entry, keyed by
    Paystack's refund id so the same refund can never be booked twice, and move the order. The
    order must be locked. Raises if the ledger already holds this refund id (a bug, not a retry)."""
    if not write_ledger(
        db,
        order,
        booking.provider_id,
        LedgerKind.refund,
        -amount,
        f"refund:{refund_ref}",
        note=order.refund_reason,
    ):
        raise err("LEDGER_CONFLICT", f"Refund {refund_ref} is already on the ledger.", 500)
    order.refunded_minor += amount
    order.refund_due_minor = max(0, order.refund_due_minor - amount)
    if order.refund_due_minor == 0:
        order.refund_next_attempt_at = None
    order.status = (
        OrderStatus.refunded
        if order.refunded_minor == order.amount_minor
        else OrderStatus.partially_refunded
    )
    write_audit(
        db,
        "marketplace.order.refunded",
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={
            "amount_minor": amount,
            "refunded_total_minor": order.refunded_minor,
            "reason": order.refund_reason,
            "refund_ref": refund_ref,
        },
    )
    event_bus.publish(db, "marketplace.order.refunded", {"order_id": str(order.id)})


def refund_key(data: dict[str, Any]) -> str | None:
    """Paystack's identifier for a refund it just answered: its numeric `id`, else its
    `refund_reference`. Used only to key the ledger entry of a refund WE sent; reconciliation does
    not match rows by identifier (two views of one refund may carry different ones) but by totals.
    """
    rid = data.get("id") or data.get("refund_reference")
    return str(rid) if rid else None


def _held_by_paystack(order: MarketplaceOrder) -> int:
    """Kobo Paystack holds as accepted (not failed or reversed) refunds of THIS transaction. Rows
    that don't name this transaction are ignored whatever the query returned."""
    total = 0
    for r in paystack.list_refunds(order.paystack_reference):
        ref = r.get("transaction_reference")
        if ref is None and isinstance(r.get("transaction"), dict):
            ref = r["transaction"].get("reference")
        if ref != order.paystack_reference:
            continue
        if str(r.get("status", "")).lower() in ("failed", "reversed"):
            continue
        amount = r.get("amount")
        if isinstance(amount, int) and amount > 0:
            total += amount
    return total


def reconcile_with_paystack(
    db: Session, order: MarketplaceOrder, booking: MarketplaceBooking
) -> bool:
    """Book money Paystack has already refunded that our books don't show: an earlier attempt that
    was accepted but whose answer we lost (timeout, crash before commit), or a `refund.processed`
    we are told about. Count-based on purpose: Paystack's accepted total for this transaction
    minus what the order already records. Only up to what is currently due, so a refund Paystack
    made earlier can never be booked as a later one. The order must be locked."""
    gap = _held_by_paystack(order) - order.refunded_minor
    if gap <= 0 or order.refund_due_minor <= 0:
        return False
    amount = min(gap, order.refund_due_minor)
    record_refund_sent(db, order, booking, amount, f"reconcile:{order.refunded_minor + amount}")
    return True


def process_refund(db: Session, order_id: uuid.UUID) -> str:
    """Send a due refund to Paystack. Row-locks the order for the whole call so a timer and a
    request can't both refund it. Before RE-sending (any earlier attempt may have been accepted
    without our seeing the answer) it asks Paystack what it already holds and only sends the
    difference. On success: a ledger `refund` entry keyed by Paystack's refund id, `refunded_minor`
    up. On a Paystack error: backoff and retry; after MAX_REFUND_ATTEMPTS it is flagged
    (`refund_failed_at`) for staff and left in `refund_due`."""
    order = lock_order(db, order_id)
    due = order.refund_due_minor
    if due <= 0:
        return "nothing_due"
    booking = db.get(MarketplaceBooking, order.booking_id)
    if booking is None:  # unreachable: the FK is RESTRICT
        return "ignored"
    t = now()
    try:
        # Always reconcile first: an earlier attempt, even one that crashed before committing, may
        # have been accepted by Paystack.
        reconcile_with_paystack(db, order, booking)
        due = order.refund_due_minor
        if due <= 0:
            return "refunded"
        sent = paystack.refund(order.paystack_reference, amount_minor=due)
    except (paystack.PaystackUnavailable, paystack.PaystackRejected) as exc:
        order.refund_attempts += 1
        step = min(order.refund_attempts, len(REFUND_RETRY_BACKOFF_MIN)) - 1
        order.refund_next_attempt_at = t + timedelta(minutes=REFUND_RETRY_BACKOFF_MIN[step])
        if order.refund_attempts >= MAX_REFUND_ATTEMPTS and order.refund_failed_at is None:
            order.refund_failed_at = t
            write_audit(
                db,
                "marketplace.order.refund_failed",
                startup_id=order.startup_id,
                entity_type="marketplace_order",
                entity_id=order.id,
                details={"attempts": order.refund_attempts, "due_minor": due},
            )
        log.warning(
            f"marketplace: refund of {order.paystack_reference} not accepted: {type(exc).__name__}"
        )
        return "retry_scheduled"
    record_refund_sent(
        db,
        order,
        booking,
        due,
        refund_key(sent) or f"sent-{uuid.uuid4().hex[:12]}",
    )
    return "refunded"


def settle_from_webhook(db: Session, order_id: uuid.UUID) -> str:
    """Paystack says a refund was processed. If our books still show money as due that Paystack
    has in fact refunded, book it (by totals, not by identifier); otherwise it is already known."""
    order = lock_order(db, order_id)
    booking = db.get(MarketplaceBooking, order.booking_id)
    if booking is None or order.refund_due_minor <= 0:
        return "refund_noted"
    return "refund_settled" if reconcile_with_paystack(db, order, booking) else "refund_noted"


def reverse_failed_refund(db: Session, order_id: uuid.UUID, amount_minor: int, ref: str) -> str:
    """Paystack reported a refund we had recorded as sent has failed: put the money back on the
    books (ledger `adjustment`, positive) and flag the order for staff. Idempotent per `ref`."""
    order = lock_order(db, order_id)
    booking = db.get(MarketplaceBooking, order.booking_id)
    if booking is None or amount_minor <= 0 or amount_minor > order.refunded_minor:
        return "ignored"
    if not write_ledger(
        db,
        order,
        booking.provider_id,
        LedgerKind.adjustment,
        amount_minor,
        f"refund_failed:{ref}",
        note="Refund failed at Paystack",
    ):
        return "duplicate"
    order.refunded_minor -= amount_minor
    order.refund_failed_at = now()
    order.status = OrderStatus.partially_refunded if order.refunded_minor else OrderStatus.paid
    write_audit(
        db,
        "marketplace.order.refund_reversed",
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={"amount_minor": amount_minor, "ref": ref},
    )
    return "refund_reversed"


# ------------------------------------------------------------------ receipts


def receipt(
    order: MarketplaceOrder, booking: MarketplaceBooking, seller_name: str | None
) -> dict[str, Any]:
    """What the founder paid for. Both lines are shown (the expert is the seller of the service;
    Cofoundaz's fee comes out of the price), and it is explicitly NOT a tax invoice until counsel
    and an accountant have reviewed it (design §1.3.5)."""
    return {
        "receipt_number": order.receipt_number,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "currency": order.currency,
        "reference": order.paystack_reference,
        "total_minor": order.amount_minor,
        "refunded_minor": order.refunded_minor,
        "lines": [
            {
                "label": f"{booking.title_snap} (service by {seller_name or 'the expert'})",
                "amount_minor": order.provider_net_minor,
            },
            {"label": "Cofoundaz platform fee", "amount_minor": order.commission_minor},
        ],
        "is_tax_invoice": False,
        "note": (
            "This receipt is not a tax invoice. VAT and withholding tax treatment is being "
            "reviewed and a tax invoice will follow."
        ),
    }


__all__ = [
    "apply_payment",
    "ledger_balance",
    "lock_order",
    "process_refund",
    "receipt",
    "reference_for",
    "request_refund",
    "reverse_failed_refund",
    "slot_still_free",
    "write_ledger",
]
