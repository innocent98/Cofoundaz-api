"""Marketplace side of the Paystack webhook. The existing billing webhook route verifies the
HMAC-SHA512 signature over the raw body (fail closed) and then asks `owns()`: events whose
reference starts with `mp_` come here, everything else stays with billing, untouched.

A `charge.success` body is never trusted for money: we re-verify the reference with Paystack and
apply what Paystack says (amount, currency, status) in `orders.apply_payment`. Every event is
recorded under a key so a replay (or the same event delivered twice) is answered 200 and applied
once; the ledger's own uniqueness is the second line of defence."""

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.marketplace import MarketplaceOrder, MarketplaceWebhookEvent
from app.services.billing import paystack
from app.services.marketplace import orders

PREFIX = "mp_"
HANDLED = frozenset({"charge.success", "refund.processed", "refund.failed", "refund.pending"})
NON_FINAL = frozenset({"unverified"})
RETRYABLE = frozenset({"payment_not_successful"})


def _reference(event: str, data: dict[str, Any]) -> str | None:
    if event.startswith("refund."):
        ref = data.get("transaction_reference")
        if ref is None and isinstance(data.get("transaction"), dict):
            ref = data["transaction"].get("reference")
    else:
        ref = data.get("reference")
    return ref if isinstance(ref, str) else None


def owns(event: str, data: dict[str, Any]) -> bool:
    ref = _reference(event, data)
    return event in HANDLED and ref is not None and ref.startswith(PREFIX)


class RetryLater(AppError):  # noqa: N818
    """Answer non-2xx so Paystack redelivers: we couldn't settle this event yet."""

    code, http_status = "RETRY_LATER", 409
    message = "Not confirmed yet. Please retry."


def _verified(reference: str) -> dict[str, Any] | None:
    """Paystack's own answer for the reference. Only a definitive 404 ("no such transaction") is a
    normal outcome (`None`). An outage, a 429 or any other refusal propagates as an error, so the
    webhook answers non-2xx and Paystack retries instead of the payment being orphaned."""
    try:
        return paystack.verify(reference)
    except paystack.PaystackNotFound:
        return None


def process(db: Session, event: str, data: dict[str, Any]) -> str:
    reference = _reference(event, data)
    if reference is None:
        return "ignored_malformed"
    tx = _verified(reference) if event == "charge.success" else None
    suffix = (
        str(data.get("refund_reference") or data.get("amount") or "")
        if event != "charge.success"
        else ""
    )
    key = f"mp:{event}:{reference}:{suffix}"[:160]
    fresh = db.execute(
        insert(MarketplaceWebhookEvent)
        .values(event_key=key, type=event[:60])
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(MarketplaceWebhookEvent.id)
    ).first()
    if fresh is None:
        return "duplicate"
    if event == "charge.success":
        outcome = orders.apply_payment(db, reference, tx) if tx is not None else "unverified"
    elif event == "refund.failed":
        outcome = _refund_failed(db, reference, data)
    elif event == "refund.processed":
        outcome = _refund_processed(db, reference, data)
    else:  # refund.pending
        outcome = "refund_noted"
    row = db.query(MarketplaceWebhookEvent).filter_by(event_key=key).one()
    if outcome in RETRYABLE:
        # A signed charge.success that Paystack itself doesn't (yet) confirm: ask for a redelivery.
        # Forget the event so the redelivery is processed afresh (the error also rolls back).
        db.delete(row)
        db.flush()
        raise RetryLater()
    if outcome in NON_FINAL:
        db.delete(row)  # not a verdict (e.g. an unknown reference): nothing to remember
    else:
        row.outcome = outcome[:60]
    return outcome


def _refund_processed(db: Session, reference: str, data: dict[str, Any]) -> str:
    order_id = (
        db.query(MarketplaceOrder.id)
        .filter(MarketplaceOrder.paystack_reference == reference)
        .scalar()
    )
    amount = data.get("amount")
    ref = data.get("refund_reference") or data.get("id")
    if order_id is None or not isinstance(amount, int) or not ref:
        return "refund_noted"
    return orders.settle_from_webhook(db, order_id, amount, str(ref))


def _refund_failed(db: Session, reference: str, data: dict[str, Any]) -> str:
    order_id = (
        db.query(MarketplaceOrder.id)
        .filter(MarketplaceOrder.paystack_reference == reference)
        .scalar()
    )
    amount = data.get("amount")
    if order_id is None or not isinstance(amount, int):
        return "ignored_unknown_reference"
    return orders.reverse_failed_refund(
        db, order_id, amount, str(data.get("refund_reference") or amount)
    )
