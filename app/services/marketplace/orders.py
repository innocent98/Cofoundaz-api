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
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.billing import paystack
from app.services.marketplace import emails, outbox, threads
from app.services.marketplace.constants import (
    ACCEPT_WINDOW,
    MAX_REFUND_ATTEMPTS,
    REFUND_INFLIGHT_WAIT,
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


_REASON_WORDS = {
    "payment_mismatch": "payment did not match",
    "unverified_close": "hold closed without a Paystack answer",
}


def _reopen(order: MarketplaceOrder) -> None:
    """A new problem on an order staff had marked resolved puts it back in front of them."""
    order.attention_resolved_at = None
    order.attention_resolved_by = None


def flag_attention(
    db: Session,
    order: MarketplaceOrder,
    booking: MarketplaceBooking,
    reason: str,
    amount: object,
) -> None:
    """Put an order in front of staff (idempotent: one flag and one audit row per reason)."""
    if order.attention_reason == reason and order.attention_resolved_at is None:
        return
    order.attention_reason = reason
    order.attention_at = now()
    order.attention_amount_minor = amount if isinstance(amount, int) else None
    _reopen(order)
    write_audit(
        db,
        f"marketplace.order.{reason}",
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={
            "expected_minor": order.amount_minor,
            "paystack_minor": order.attention_amount_minor,
            "booking_id": str(booking.id),
            "reference": order.paystack_reference,
        },
    )
    # The wording depends on the reason: an unverified close has no Paystack amount to quote.
    if order.attention_amount_minor is not None:
        detail = f"expected {order.amount_minor}, Paystack reports {order.attention_amount_minor}"
    else:
        detail = f"expected {order.amount_minor}, Paystack gave no amount"
    log.error(
        f"marketplace: {_REASON_WORDS.get(reason, reason)} on {order.paystack_reference}: "
        f"{detail}. Needs staff."
    )


def auto_resolve_attention(db: Session, order: MarketplaceOrder, *, why: str) -> bool:
    """An `unverified_close` flag exists because money MAY have been captured behind a hold we
    closed. Once the order is paid and revived, or fully refunded, nobody needs to look. The flag
    stays on the row as the record; only its resolution is stamped. Other reasons (a mismatching
    amount, a refund Paystack refuses) always need a person."""
    if order.attention_reason != "unverified_close" or order.attention_resolved_at is not None:
        return False
    order.attention_resolved_at = now()
    write_audit(
        db,
        "marketplace.order.attention_auto_resolved",
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        details={"reason": order.attention_reason, "why": why},
    )
    return True


MONEY_REASONS = ("payment_mismatch", "refund_failed")


def needs_step_up(order: MarketplaceOrder) -> bool:
    """A flag about money we hold or owe (a wrong amount, a refund Paystack keeps refusing) needs
    the staff member's fresh authenticator code to dismiss; an unverified close does not."""
    return order.attention_reason in MONEY_REASONS or order.refund_failed_at is not None


def resolve_attention(
    db: Session,
    order_id: uuid.UUID,
    *,
    staff_user_id: uuid.UUID,
    reason: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> MarketplaceOrder:
    """Staff mark a flagged order as dealt with (they handled it outside the app). Audited with
    their reason; the queue stops listing it until a new problem is flagged."""
    order = lock_order(db, order_id)
    flagged = order.attention_reason is not None or order.refund_failed_at is not None
    if not flagged or order.attention_resolved_at is not None:
        raise err("NOT_FLAGGED", "This order has nothing waiting for review.", 409)
    order.attention_resolved_at = now()
    order.attention_resolved_by = staff_user_id
    write_audit(
        db,
        "marketplace.order.attention_resolved",
        actor_user_id=staff_user_id,
        startup_id=order.startup_id,
        entity_type="marketplace_order",
        entity_id=order.id,
        reason=reason,
        ip=ip,
        user_agent=user_agent,
        details={"attention_reason": order.attention_reason},
    )
    return order


def close_for_payment_review(db: Session, booking: MarketplaceBooking) -> None:
    """Booking must be locked. `expired` + `closed_reason: payment_review` (no new enum value).
    Also used for a mismatching payment that lands on a hold already closed as unpaid."""
    booking.status = BookingStatus.expired
    booking.closed_reason = "payment_review"
    booking.slot_hold_expires_at = None
    write_audit(
        db,
        "marketplace.booking.expired",
        startup_id=booking.startup_id,
        entity_type="marketplace_booking",
        entity_id=booking.id,
        details={"why": "payment_review"},
    )
    booker = db.get(User, booking.booked_by)
    if booker is not None:
        outbox.queue(
            db,
            emails.payment_problem_email(
                booker.email, title=booking.title_snap, kind="payment_review"
            ),
        )


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
        # Paystack took real money that doesn't match what we asked for (fees passed on, a
        # different currency, a tampered request). We can't book it against this order, and we
        # must not lose it: flag the order for staff with what Paystack says it took.
        flag_attention(db, order, booking, "payment_mismatch", tx.get("amount"))
        if booking.status == BookingStatus.pending_payment or (
            booking.status == BookingStatus.expired
            and booking.closed_reason == "payment_not_completed"
        ):
            # Terminal for the checkout: release the slot, stop the timer re-verifying it every
            # minute, and tell the founder. The order stays pending and flagged for staff.
            close_for_payment_review(db, booking)
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
        auto_resolve_attention(db, order, why="paid_and_revived")
        threads.attach_booking(db, booking)
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
    booker = db.get(User, booking.booked_by)
    if booker is not None:
        outbox.queue(
            db,
            emails.booking_closed_email(
                booker.email, provider=None, title=booking.title_snap, reason=reason
            ),
        )
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
    if order.status == OrderStatus.refunded:
        auto_resolve_attention(db, order, why="fully_refunded")
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


def process_refund(db: Session, order_id: uuid.UUID, *, at: datetime | None = None) -> str:
    """Send a due refund to Paystack, safely retryable.

    The order is row-locked, and BEFORE the call a `refund_inflight_since` marker is committed. If
    anything then goes wrong without a verdict (timeout, 5xx, a crash after Paystack accepted), the
    marker stays, and the next attempt first reconciles with Paystack's own list (books what it
    already refunded, by totals) and only re-sends once REFUND_INFLIGHT_WAIT has passed with
    nothing found there. A definitive rejection clears the marker (nothing was accepted). A first
    send never depends on the list API. On success: a ledger `refund` entry keyed by Paystack's
    refund id and the order moves; retries back off and, after MAX_REFUND_ATTEMPTS, are flagged
    (`refund_failed_at`) for staff and left in `refund_due`."""
    order = lock_order(db, order_id)
    if order.refund_due_minor <= 0:
        order.refund_inflight_since = None
        return "nothing_due"
    booking = db.get(MarketplaceBooking, order.booking_id)
    if booking is None:  # unreachable: the FK is RESTRICT
        return "ignored"
    t = at or now()  # `at` lets the timer (and its tests) say what time it is
    inflight = order.refund_inflight_since
    fresh = inflight is None  # no earlier send left without a verdict
    if inflight is not None:
        try:
            reconcile_with_paystack(db, order, booking)
        except (paystack.PaystackUnavailable, paystack.PaystackRejected):
            return _refund_retry(db, order, t)
        if order.refund_due_minor <= 0:
            order.refund_inflight_since = None
            db.flush()
            return "refunded"
        if t - inflight < REFUND_INFLIGHT_WAIT:
            return _refund_retry(db, order, t, outcome="awaiting_reconcile")
    order.refund_inflight_since = t
    db.commit()  # the marker is durable before Paystack hears about the refund
    order = lock_order(db, order_id)
    due = order.refund_due_minor
    if due <= 0:  # settled by a webhook while we committed
        order.refund_inflight_since = None
        db.flush()
        return "refunded"
    try:
        sent = paystack.refund(order.paystack_reference, amount_minor=due)
    except paystack.PaystackRejected:
        if fresh:
            order.refund_inflight_since = None  # a definitive no: nothing was accepted
        # else an earlier send had no verdict and may have been accepted: the "no" may well mean
        # "already refunded", so the marker stays and the next attempt reconciles first
        return _refund_retry(db, order, t)
    except paystack.PaystackUnavailable:
        return _refund_retry(db, order, t)  # no verdict: the marker stays
    record_refund_sent(db, order, booking, due, refund_key(sent) or f"sent-{uuid.uuid4().hex[:12]}")
    order.refund_inflight_since = None
    db.flush()
    return "refunded"


def _refund_retry(
    db: Session, order: MarketplaceOrder, t: datetime, *, outcome: str = "retry_scheduled"
) -> str:
    order.refund_attempts += 1
    step = min(order.refund_attempts, len(REFUND_RETRY_BACKOFF_MIN)) - 1
    order.refund_next_attempt_at = t + timedelta(minutes=REFUND_RETRY_BACKOFF_MIN[step])
    if order.refund_attempts >= MAX_REFUND_ATTEMPTS and order.refund_failed_at is None:
        order.refund_failed_at = t
        if order.attention_reason is None or order.attention_resolved_at is not None:
            order.attention_reason, order.attention_at = "refund_failed", t
        _reopen(order)
        write_audit(
            db,
            "marketplace.order.refund_failed",
            startup_id=order.startup_id,
            entity_type="marketplace_order",
            entity_id=order.id,
            details={"attempts": order.refund_attempts, "due_minor": order.refund_due_minor},
        )
    log.warning(f"marketplace: refund of {order.paystack_reference} pending ({outcome})")
    db.flush()
    return outcome


def settle_from_webhook(
    db: Session,
    order_id: uuid.UUID,
    amount: int,
    refund_ref: str,
    other_ref: str | None = None,
) -> str:
    """Paystack's signed `refund.processed` says it refunded `amount` on this transaction. The
    only case that needs it is a send we never saw answered (the in-flight marker is set): then it
    books exactly that amount, keyed by Paystack's `refund_reference` (idempotent), up to what is
    due. With no unanswered send the refund is already on our books and it is only noted, so a
    late webhook for an earlier refund can never be booked as a later one.

    A refund we sent and saw answered is on the ledger under Paystack's `id` or its
    `refund_reference` (whichever the answer carried), so the event is matched against BOTH. The
    one residual risk: an earlier refund that our books only learned of through the list
    reconcile (ledger key `reconcile:N`, no Paystack reference stored), whose late
    `refund.processed` arrives while a LATER send of the same or a larger amount is unanswered.
    It would be booked as that later send; if the later send then failed, `refunded_minor` would
    over-state what was refunded until staff see `refund.failed`/`refund_failed_at`."""
    order = lock_order(db, order_id)
    booking = db.get(MarketplaceBooking, order.booking_id)
    if (
        booking is None
        or order.refund_inflight_since is None
        or not 0 < amount <= order.refund_due_minor
    ):
        return "refund_noted"
    key = f"refund:{refund_ref}"[:80]
    known = [key] + ([f"refund:{other_ref}"[:80]] if other_ref and other_ref != refund_ref else [])
    if (
        db.query(MarketplaceLedgerEntry.id)
        .filter(
            MarketplaceLedgerEntry.order_id == order.id,
            MarketplaceLedgerEntry.kind == LedgerKind.refund,
            MarketplaceLedgerEntry.external_ref.in_(known),
        )
        .first()
        is not None
    ):
        return "refund_noted"
    record_refund_sent(db, order, booking, amount, refund_ref)
    if order.refund_due_minor == 0:
        order.refund_inflight_since = None
    return "refund_settled"


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
    _reopen(order)
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
