"""Paystack webhook for Cofoundaz Billing.

Public on purpose (no bearer): authenticity is the `x-paystack-signature` HMAC-SHA512 of the raw
body under our secret key, checked in constant time and failing closed. Each verified event is
recorded under a key derived from its exact bytes, so Paystack's retries (identical bodies) are
recognised and answered 200 without re-applying anything.
"""

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logger import log
from app.db.models.billing import BillingSubscription, BillingWebhookEvent
from app.platform.events import event_bus
from app.services.billing import flows, paystack
from app.services.billing import subscription as subs

MAX_BODY_BYTES = 256 * 1024


class InvalidSignature(AppError):  # noqa: N818
    code, http_status = "INVALID_SIGNATURE", 401
    message = "Signature check failed."


def _sub_by_code(db: Session, code: Any) -> BillingSubscription | None:
    if not isinstance(code, str) or not code:
        return None
    return (
        db.query(BillingSubscription)
        .filter_by(paystack_subscription_code=code)
        .with_for_update()
        .first()
    )


def _sub_by_customer(db: Session, data: dict[str, Any]) -> BillingSubscription | None:
    return flows._unique_by_customer(  # noqa: SLF001
        db, subs.as_dict(data.get("customer")).get("customer_code")
    )


def _on_subscription_create(db: Session, data: dict[str, Any]) -> str:
    sub = _sub_by_customer(db, data)
    if sub is None:
        return "ignored_no_subscription"
    # A scheduled (future) subscription for a plan change must not overwrite a live one's token.
    if (
        sub.paystack_subscription_code
        and data.get("subscription_code") != sub.paystack_subscription_code
    ):
        return "ignored_not_current"
    sub.paystack_subscription_code = data.get("subscription_code")
    sub.paystack_email_token = data.get("email_token")
    return "linked"


def _on_not_renew(db: Session, data: dict[str, Any]) -> str:
    sub = _sub_by_code(db, data.get("subscription_code"))
    if sub is None:
        return "ignored_no_subscription"
    if sub.status == "active" and not sub.pending_plan_key:
        sub.cancel_at_period_end = True
        sub.canceled_at = sub.canceled_at or subs.now()
    return "cancel_at_period_end"


def _on_disable(db: Session, data: dict[str, Any]) -> str:
    sub = _sub_by_code(db, data.get("subscription_code"))
    if sub is None:
        return "ignored_no_subscription"
    sub.paystack_subscription_code = sub.paystack_email_token = None
    return "unlinked"


def _on_payment_failed(db: Session, data: dict[str, Any]) -> str:
    inner = subs.as_dict(data.get("subscription")) or data
    sub = _sub_by_code(db, inner.get("subscription_code")) or _sub_by_customer(db, data)
    if sub is None or sub.status not in ("active", "past_due"):
        return "ignored_no_subscription"
    if sub.status == "active":
        t = subs.now()
        sub.status = "past_due"
        sub.payment_failed_at = t
        sub.grace_ends_at = t + timedelta(days=settings.BILLING_GRACE_DAYS)
        event_bus.publish(
            db,
            "billing.payment.failed",
            {"startup_id": str(sub.startup_id), "grace_days": settings.BILLING_GRACE_DAYS},
        )
        plan_name = subs.plan_by_key(db, sub.plan_key).name
        flows.record_invoice(
            db,
            startup_id=sub.startup_id,
            kind="subscription",
            description=f"{plan_name} plan, {sub.interval}",
            amount_minor=int(data.get("amount") or 0),
            reference=f"failed_{sub.startup_id.hex[:8]}_{int(t.timestamp())}",
            status="failed",
            plan_key=sub.plan_key,
            paid_at=None,
        )
    return "past_due"


def process(db: Session, *, raw_body: bytes, signature: str | None) -> dict[str, Any]:
    if len(raw_body) > MAX_BODY_BYTES or not paystack.verify_signature(raw_body, signature):
        raise InvalidSignature()
    try:
        body = json.loads(raw_body)
    except ValueError:
        raise InvalidSignature() from None
    if not isinstance(body, dict) or not isinstance(body.get("event"), str):
        return {"received": True, "outcome": "ignored_malformed"}
    event: str = body["event"]
    data = subs.as_dict(body.get("data"))
    key = f"{event}:{hashlib.sha256(raw_body).hexdigest()}"
    inserted = db.execute(
        insert(BillingWebhookEvent)
        .values(event_key=key[:160], event=event[:60], payload=body)
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(BillingWebhookEvent.id)
    ).first()
    if inserted is None:
        return {"received": True, "outcome": "duplicate"}
    row = db.query(BillingWebhookEvent).filter_by(event_key=key[:160]).one()
    if event == "charge.success":
        outcome = flows.finalize_charge(db, data)
    elif event == "subscription.create":
        outcome = _on_subscription_create(db, data)
    elif event == "subscription.not_renew":
        outcome = _on_not_renew(db, data)
    elif event == "subscription.disable":
        outcome = _on_disable(db, data)
    elif event == "invoice.payment_failed":
        outcome = _on_payment_failed(db, data)
    else:
        outcome = "ignored_event"
    row.processed_at = subs.now()
    log.info(f"billing webhook {event}: {outcome}")
    return {"received": True, "outcome": outcome}
