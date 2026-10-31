"""Billing flows: checkout, finalising a Paystack charge (shared by the webhook, the callback
verify route and synchronous charges), plan changes with proration, cancel and resume, cards,
add-ons and invoices.

Every money-moving step is keyed by a Paystack reference stored in `billing_operations` first;
`finalize_charge` locks that row, so the webhook, the verify route and a retry can all arrive
together and the effect happens once.
"""

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logger import log
from app.db.models.billing import (
    BillingAddonPurchase,
    BillingInvoice,
    BillingOperation,
    BillingPaymentMethod,
    BillingPlan,
    BillingSubscription,
)
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.billing import paystack
from app.services.billing import subscription as subs
from app.services.billing.catalog import ADDONS_BY_KEY


def _err(code: str, message: str, status: int = 409) -> AppError:
    return AppError(code, message, status)


def callback_url() -> str:
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    return f"{base}/settings?tab=billing"


def reference_for(kind: str, startup_id: uuid.UUID, idem_key: str | None) -> str:
    if idem_key:
        digest = hashlib.sha256(f"{startup_id}:{kind}:{idem_key}".encode()).hexdigest()[:20]
    else:
        digest = uuid.uuid4().hex[:20]
    return f"cfz_{kind}_{digest}"


def _prorated_credit(sub: BillingSubscription, plan: BillingPlan) -> int:
    """Unused part of what was paid for the current period, in kobo."""
    if not (sub.status == "active" and sub.current_period_start and sub.current_period_end):
        return 0
    total = (sub.current_period_end - sub.current_period_start).total_seconds()
    left = (sub.current_period_end - subs.now()).total_seconds()
    if total <= 0 or left <= 0:
        return 0
    return int(subs.price(plan, sub.interval) * min(1.0, left / total))


def ensure_plan_code(db: Session, plan: BillingPlan, interval: str) -> str:
    attr = "paystack_plan_code_annual" if interval == "annual" else "paystack_plan_code_monthly"
    code = getattr(plan, attr)
    if code:
        return str(code)
    code = paystack.create_plan(
        name=f"Cofoundaz {plan.name} ({interval})",
        amount_minor=subs.price(plan, interval),
        interval=interval,
    )
    setattr(plan, attr, code)
    db.flush()
    return code


def _existing_op(db: Session, reference: str) -> BillingOperation | None:
    return db.query(BillingOperation).filter_by(reference=reference).first()


def _new_op(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User | None,
    reference: str,
    kind: str,
    amount_minor: int,
    params: dict[str, Any],
) -> BillingOperation:
    op = BillingOperation(
        startup_id=startup_id,
        reference=reference,
        kind=kind,
        amount_minor=amount_minor,
        params=params,
        initiated_by=user.id if user else None,
    )
    db.add(op)
    db.flush()
    return op


def _hosted(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    kind: str,
    amount_minor: int,
    params: dict[str, Any],
    idem_key: str | None,
    plan_code: str | None = None,
) -> dict[str, Any]:
    """Start a hosted Paystack checkout for `kind`. A replay of the same Idempotency-Key returns
    the same link instead of initialising a second transaction."""
    reference = reference_for(kind, startup_id, idem_key)
    op = _existing_op(db, reference)
    if op is not None:
        same = {k: v for k, v in op.params.items() if k != "authorization_url"} == params
        if op.startup_id != startup_id or op.kind != kind or not same:
            raise _err("IDEMPOTENCY_KEY_REUSED", "That request key was used for something else.")
        if op.status == "pending" and op.params.get("authorization_url"):
            return {"authorization_url": op.params["authorization_url"], "reference": reference}
        raise _err("ALREADY_PROCESSED", "That request already went through.")
    op = _new_op(
        db,
        startup_id=startup_id,
        user=user,
        reference=reference,
        kind=kind,
        amount_minor=amount_minor,
        params=params,
    )
    data = paystack.initialize(
        email=user.email,
        amount_minor=amount_minor,
        reference=reference,
        callback_url=callback_url(),
        plan_code=plan_code,
        metadata={"startup_id": str(startup_id), "operation": kind},
    )
    url = data.get("authorization_url")
    if not isinstance(url, str):
        raise paystack.PaystackRejected()
    op.params = {**params, "authorization_url": url}
    db.flush()
    return {"authorization_url": url, "reference": reference}


def start_checkout(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    plan_key: str,
    interval: str,
    idem_key: str | None,
) -> dict[str, Any]:
    if interval not in subs.INTERVALS:
        raise _err("VALIDATION_ERROR", "Choose monthly or annual.", 422)
    plan = subs.plan_by_key(db, plan_key)
    amount = subs.price(plan, interval)
    if amount <= 0:
        raise _err("PLAN_NOT_PURCHASABLE", "Starter is free: there's nothing to pay for.", 422)
    sub = subs.current(db, startup_id)
    if subs.is_paid(sub):
        raise _err("ALREADY_SUBSCRIBED", "You're already on a paid plan. Change plan instead.")
    code = ensure_plan_code(db, plan, interval)
    return _hosted(
        db,
        startup_id=startup_id,
        user=user,
        kind="checkout",
        amount_minor=amount,
        params={"plan_key": plan.key, "interval": interval},
        idem_key=idem_key,
        plan_code=code,
    )


# ---- invoices ----------------------------------------------------------------------------------


def _next_number(db: Session, startup_id: uuid.UUID) -> str:
    n = db.query(BillingInvoice).filter_by(startup_id=startup_id).count() + 1
    return f"CFZ-{subs.now().year}-{n:04d}"


def record_invoice(
    db: Session,
    *,
    startup_id: uuid.UUID,
    kind: str,
    description: str,
    amount_minor: int,
    reference: str,
    status: str,
    plan_key: str | None,
    paid_at: datetime | None,
) -> BillingInvoice | None:
    existing = db.query(BillingInvoice).filter_by(reference=reference).first()
    if existing is not None:
        if status == "paid" and existing.status != "paid":
            existing.status, existing.paid_at = "paid", paid_at
        return existing
    inv = BillingInvoice(
        startup_id=startup_id,
        number=_next_number(db, startup_id),
        kind=kind,
        description=description,
        amount_minor=amount_minor,
        status=status,
        reference=reference,
        plan_key=plan_key,
        paid_at=paid_at,
    )
    db.add(inv)
    db.flush()
    return inv


# ---- cards -------------------------------------------------------------------------------------


def store_authorization(
    db: Session, startup_id: uuid.UUID, auth: dict[str, Any] | None
) -> BillingPaymentMethod | None:
    """Keep a reusable card authorization Paystack reported. Only brand/last4/expiry are kept for
    display; the authorization code is the token we charge with, never a card number."""
    if not isinstance(auth, dict) or not auth.get("reusable") or auth.get("channel") != "card":
        return None
    signature = str(auth.get("signature") or auth.get("authorization_code") or "")
    code = auth.get("authorization_code")
    if not signature or not isinstance(code, str):
        return None
    pm = (
        db.query(BillingPaymentMethod).filter_by(startup_id=startup_id, signature=signature).first()
    )
    if pm is None:
        has_default = (
            db.query(BillingPaymentMethod)
            .filter(
                BillingPaymentMethod.startup_id == startup_id,
                BillingPaymentMethod.removed_at.is_(None),
                BillingPaymentMethod.is_default.is_(True),
            )
            .first()
            is not None
        )
        pm = BillingPaymentMethod(
            startup_id=startup_id, signature=signature, is_default=not has_default
        )
        db.add(pm)
    pm.authorization_code = code
    pm.brand = str(auth.get("card_type") or auth.get("brand") or "card").strip()[:20]
    pm.last4 = str(auth.get("last4") or "")[:4]
    pm.exp_month = str(auth.get("exp_month") or "")[:2]
    pm.exp_year = str(auth.get("exp_year") or "")[:4]
    pm.bank = str(auth["bank"])[:80] if auth.get("bank") else None
    pm.removed_at = None
    db.flush()
    return pm


def default_method(db: Session, startup_id: uuid.UUID) -> BillingPaymentMethod | None:
    q = db.query(BillingPaymentMethod).filter(
        BillingPaymentMethod.startup_id == startup_id, BillingPaymentMethod.removed_at.is_(None)
    )
    return q.order_by(
        BillingPaymentMethod.is_default.desc(), BillingPaymentMethod.created_at
    ).first()


def start_add_card(
    db: Session, *, startup_id: uuid.UUID, user: User, idem_key: str | None
) -> dict[str, Any]:
    """Paystack has no SetupIntent: a hosted ₦50 charge tokenises the card and is refunded at once."""
    return _hosted(
        db,
        startup_id=startup_id,
        user=user,
        kind="card",
        amount_minor=50_00,
        params={},
        idem_key=idem_key,
    )


def remove_method(db: Session, startup_id: uuid.UUID, method_id: uuid.UUID) -> None:
    pm = db.query(BillingPaymentMethod).filter_by(id=method_id, startup_id=startup_id).first()
    if pm is None or pm.removed_at is not None:
        raise AppError("NOT_FOUND", "Not found.", 404)
    others = (
        db.query(BillingPaymentMethod)
        .filter(
            BillingPaymentMethod.startup_id == startup_id,
            BillingPaymentMethod.removed_at.is_(None),
            BillingPaymentMethod.id != pm.id,
        )
        .count()
    )
    sub = subs.get_or_create(db, startup_id)
    if others == 0 and subs.is_paid(sub):
        raise _err("LAST_PAYMENT_METHOD", "Add another method before removing this one.")
    pm.removed_at = subs.now()
    if pm.is_default:
        pm.is_default = False
        nxt = default_method(db, startup_id)
        if nxt is not None:
            nxt.is_default = True
    db.flush()


def set_default_method(db: Session, startup_id: uuid.UUID, method_id: uuid.UUID) -> None:
    pm = db.query(BillingPaymentMethod).filter_by(id=method_id, startup_id=startup_id).first()
    if pm is None or pm.removed_at is not None:
        raise AppError("NOT_FOUND", "Not found.", 404)
    db.query(BillingPaymentMethod).filter_by(startup_id=startup_id).update({"is_default": False})
    pm.is_default = True
    db.flush()


# ---- finalising a charge ----------------------------------------------------------------------


def _activate(
    db: Session,
    sub: BillingSubscription,
    *,
    plan_key: str,
    interval: str,
    customer_code: str | None,
    when: datetime,
) -> None:
    before = sub.plan_key
    sub.plan_key, sub.interval, sub.status = plan_key, interval, "active"
    sub.current_period_start = when
    sub.current_period_end = subs.add_interval(when, interval)
    sub.trial_ends_at = None
    sub.cancel_at_period_end = False
    sub.canceled_at = None
    sub.cancel_reason = None
    sub.pending_plan_key = sub.pending_interval = None
    sub.payment_failed_at = sub.grace_ends_at = None
    if customer_code:
        sub.paystack_customer_code = customer_code
    db.flush()
    if before != plan_key:
        event_bus.publish(
            db,
            "billing.plan.changed",
            {"startup_id": str(sub.startup_id), "from": before, "to": plan_key},
        )


def _adopt_active_subscription(sub: BillingSubscription, plan_code: str | None) -> bool:
    """Store the customer's active Paystack subscription on `sub`. True when there is one."""
    if not sub.paystack_customer_code:
        return False
    for row in paystack.list_subscriptions(sub.paystack_customer_code):
        plan = subs.as_dict(row.get("plan"))
        if row.get("status") == "active" and (not plan_code or plan.get("plan_code") == plan_code):
            sub.paystack_subscription_code = row.get("subscription_code")
            sub.paystack_email_token = row.get("email_token")
            return True
    return False


def _link_paystack_subscription(sub: BillingSubscription, plan_code: str | None) -> None:
    """Best effort: remember the Paystack subscription code and its email token so we can cancel
    it later. Paystack creates the subscription just after the charge, so it can be missing here;
    the `subscription.create` webhook fills the same fields, and cancel and plan changes look again.
    """
    try:
        _adopt_active_subscription(sub, plan_code)
    except AppError as exc:
        log.warning(f"billing: couldn't read the Paystack subscription ({exc.code})")


def _paid_when(data: dict[str, Any]) -> datetime:
    raw = data.get("paid_at") or data.get("paidAt")
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=subs.now().tzinfo)
        except ValueError:
            pass
    return subs.now()


def finalize_charge(db: Session, data: dict[str, Any]) -> str:
    """Apply a successful Paystack transaction exactly once. Returns a short outcome string."""
    if data.get("status") != "success":
        return "not_successful"
    reference = str(data.get("reference") or "")
    amount = data.get("amount")
    op = (
        db.query(BillingOperation).filter_by(reference=reference).with_for_update().first()
        if reference
        else None
    )
    if op is None:
        return _renewal(db, data)
    if op.status == "paid":
        return "already_processed"
    if data.get("currency") != "NGN" or amount != op.amount_minor:
        # Never grant what wasn't paid for: a wrong amount or currency is recorded and ignored.
        op.status = "failed"
        db.flush()
        log.warning(f"billing: amount/currency mismatch on {reference}")
        return "amount_mismatch"
    when = _paid_when(data)
    customer = subs.as_dict(data.get("customer"))
    store_authorization(db, op.startup_id, data.get("authorization"))
    op.status = "paid"
    sub = subs.get_or_create(db, op.startup_id)
    if op.kind == "checkout" and subs.is_paid(sub):
        # A second checkout link was paid after the workspace already subscribed: record it, give
        # back the money, and don't start a second Paystack subscription.
        record_invoice(
            db,
            startup_id=op.startup_id,
            kind="subscription",
            description="Duplicate payment (refunded)",
            amount_minor=op.amount_minor,
            reference=reference,
            status="refunded",
            plan_key=None,
            paid_at=when,
        )
        try:
            paystack.refund(reference)
        except AppError as exc:
            log.warning(
                f"billing: duplicate-payment refund failed ({exc.code}); refund by hand in Paystack"
            )
        db.flush()
        return "duplicate_payment_refunded"
    if op.kind == "checkout":
        plan_key, interval = op.params["plan_key"], op.params["interval"]
        _activate(
            db,
            sub,
            plan_key=plan_key,
            interval=interval,
            customer_code=customer.get("customer_code"),
            when=when,
        )
        plan_obj = subs.as_dict(data.get("plan_object"))
        _link_paystack_subscription(sub, plan_obj.get("plan_code"))
        record_invoice(
            db,
            startup_id=op.startup_id,
            kind="subscription",
            description=f"{subs.plan_by_key(db, plan_key).name} plan, {interval}",
            amount_minor=op.amount_minor,
            reference=reference,
            status="paid",
            plan_key=plan_key,
            paid_at=when,
        )
    elif op.kind == "card":
        try:
            paystack.refund(reference)
        except AppError as exc:
            log.warning(
                f"billing: card-check refund failed ({exc.code}); refund by hand in Paystack"
            )
    elif op.kind == "addon":
        addon = ADDONS_BY_KEY[op.params["addon_key"]]
        db.add(
            BillingAddonPurchase(
                startup_id=op.startup_id,
                addon_key=addon["key"],
                credits=addon["credits"],
                amount_minor=op.amount_minor,
                reference=reference,
            )
        )
        record_invoice(
            db,
            startup_id=op.startup_id,
            kind="addon",
            description=addon["name"],
            amount_minor=op.amount_minor,
            reference=reference,
            status="paid",
            plan_key=None,
            paid_at=when,
        )
    elif op.kind == "upgrade":
        _finish_upgrade(db, sub, op, when=when)
    db.flush()
    return "applied"


def _unique_by_customer(db: Session, code: Any) -> BillingSubscription | None:
    """The one subscription for a Paystack customer code, or None. A customer code is per email, so
    one founder with two workspaces shares it: refusing is safer than guessing whose money it is."""
    rows = (
        db.query(BillingSubscription)
        .filter_by(paystack_customer_code=code)
        .with_for_update()
        .limit(2)
        .all()
    )
    if len(rows) > 1:
        log.warning(
            "billing: customer code matches several workspaces; event left for manual review"
        )
        return None
    return rows[0] if rows else None


def _plan_from_code(db: Session, plan_code: Any) -> tuple[BillingPlan, str] | None:
    for plan in subs.plans(db):
        if plan.paystack_plan_code_monthly == plan_code:
            return plan, "monthly"
        if plan.paystack_plan_code_annual == plan_code:
            return plan, "annual"
    return None


def _renewal(db: Session, data: dict[str, Any]) -> str:
    """A charge Paystack made by itself (a subscription renewal): no reference of ours."""
    customer = subs.as_dict(data.get("customer"))
    code = customer.get("customer_code")
    plan_obj = subs.as_dict(data.get("plan"))
    if not code or not plan_obj:
        return "ignored_unknown_reference"
    sub = _unique_by_customer(db, code)
    if sub is None:
        return "ignored_no_subscription"
    reference = str(data.get("reference") or "")
    if db.query(BillingInvoice).filter_by(reference=reference).first() is not None:
        return "already_processed"
    when = _paid_when(data)
    if sub.status in ("active", "past_due"):
        plan_key = sub.pending_plan_key or sub.plan_key
        interval = sub.pending_interval or sub.interval
    else:
        # Grace ran out but Paystack's retry finally charged: take the plan from the Paystack plan code.
        found = _plan_from_code(db, plan_obj.get("plan_code"))
        if found is None:
            return "ignored_no_subscription"
        plan_key, interval = found[0].key, found[1]
    # Anchor the new period on the payment itself: reading the subscription after the period end may
    # already have rolled it forward, and a renewal must not add a second period for one charge.
    _activate(db, sub, plan_key=plan_key, interval=interval, customer_code=None, when=when)
    record_invoice(
        db,
        startup_id=sub.startup_id,
        kind="subscription",
        description=f"{subs.plan_by_key(db, plan_key).name} plan, {interval}",
        amount_minor=int(data.get("amount") or 0),
        reference=reference,
        status="paid",
        plan_key=plan_key,
        paid_at=when,
    )
    return "renewed"


# ---- plan changes ------------------------------------------------------------------------------


def preview_change(
    db: Session, startup_id: uuid.UUID, plan_key: str, interval: str
) -> dict[str, Any]:
    if interval not in subs.INTERVALS:
        raise _err("VALIDATION_ERROR", "Choose monthly or annual.", 422)
    target = subs.plan_by_key(db, plan_key)
    sub = subs.current(db, startup_id)
    new_price = subs.price(target, interval)
    current = subs.plan_by_key(db, sub.plan_key)
    if not subs.is_paid(sub):
        return {
            "kind": "free" if target.key == "starter" else "checkout",
            "amount_due_minor": new_price,
            "credit_minor": 0,
            "effective": "now",
            "currency": "NGN",
            "keeps_until": None,
        }
    if (target.key, interval) == (sub.plan_key, sub.interval):
        return {
            "kind": "same",
            "amount_due_minor": 0,
            "credit_minor": 0,
            "effective": "now",
            "currency": "NGN",
            "keeps_until": None,
        }
    if target.key == "starter":  # paid -> free is a cancel at period end
        return {
            "kind": "cancel",
            "amount_due_minor": 0,
            "credit_minor": 0,
            "effective": "period_end",
            "currency": "NGN",
            "keeps_until": sub.current_period_end.isoformat() if sub.current_period_end else None,
        }
    upgrade = (target.rank, new_price) > (current.rank, subs.price(current, sub.interval)) and (
        target.rank > current.rank or (target.rank == current.rank and interval == "annual")
    )
    if upgrade:
        credit = _prorated_credit(sub, current)
        return {
            "kind": "upgrade",
            "amount_due_minor": max(new_price - credit, 0),
            "credit_minor": credit,
            "effective": "now",
            "currency": "NGN",
            "keeps_until": None,
        }
    return {
        "kind": "downgrade",
        "amount_due_minor": 0,
        "credit_minor": 0,
        "effective": "period_end",
        "currency": "NGN",
        "keeps_until": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


def _switch_paystack_plan(
    db: Session,
    sub: BillingSubscription,
    plan: BillingPlan,
    interval: str,
    user: User,
) -> None:
    """Stop the old Paystack subscription and, for a paid target, schedule the new one to start when
    the current period ends (so the card isn't charged twice for the same days)."""
    if not sub.paystack_subscription_code:
        _adopt_active_subscription(sub, None)
    if sub.paystack_subscription_code and sub.paystack_email_token:
        paystack.disable_subscription(
            code=sub.paystack_subscription_code, email_token=sub.paystack_email_token
        )
    sub.paystack_subscription_code = sub.paystack_email_token = None
    pm = default_method(db, sub.startup_id)
    if (
        plan.key != "starter"
        and pm is not None
        and sub.paystack_customer_code
        and sub.current_period_end
    ):
        created = paystack.create_subscription(
            customer_code=sub.paystack_customer_code,
            plan_code=ensure_plan_code(db, plan, interval),
            authorization_code=pm.authorization_code,
            start_date_iso=sub.current_period_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        sub.paystack_subscription_code = created.get("subscription_code")
        sub.paystack_email_token = created.get("email_token")


def _finish_upgrade(
    db: Session, sub: BillingSubscription, op: BillingOperation, *, when: datetime
) -> None:
    plan_key, interval = op.params["plan_key"], op.params["interval"]
    before = sub.plan_key
    sub.plan_key, sub.interval = plan_key, interval
    sub.pending_plan_key = sub.pending_interval = None
    sub.status = "active"
    # The charge was for a whole new period starting now (new price minus the unused credit), so
    # the period restarts here; any credit above the new price is forgone, never turned into free time.
    sub.current_period_start = when
    sub.current_period_end = subs.add_interval(when, interval)
    sub.cancel_at_period_end = False
    sub.payment_failed_at = sub.grace_ends_at = None
    if before != plan_key:
        event_bus.publish(
            db,
            "billing.plan.changed",
            {"startup_id": str(sub.startup_id), "from": before, "to": plan_key},
        )
    record_invoice(
        db,
        startup_id=op.startup_id,
        kind="upgrade",
        description=f"Upgrade to {subs.plan_by_key(db, plan_key).name}, {interval} (prorated)",
        amount_minor=op.amount_minor,
        reference=op.reference,
        status="paid",
        plan_key=plan_key,
        paid_at=when,
    )


def _assert_no_upgrade_in_flight(db: Session, startup_id: uuid.UUID) -> None:
    """The row lock ends at the pre-charge commit, so a second upgrade is refused while one is pending."""
    in_flight = (
        db.query(BillingOperation)
        .filter(
            BillingOperation.startup_id == startup_id,
            BillingOperation.kind == "upgrade",
            BillingOperation.status == "pending",
            BillingOperation.created_at > subs.now() - timedelta(minutes=10),
        )
        .first()
    )
    if in_flight is not None:
        raise _err(
            "UPGRADE_IN_PROGRESS",
            "Another plan change is still being processed. Try again in a minute.",
        )


def change_plan(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user: User,
    plan_key: str,
    interval: str,
    idem_key: str | None,
) -> dict[str, Any]:
    # A replay of an upgrade that already went through answers the same, before anything is recomputed
    # (the plan has changed by now, so a fresh preview would say "no change").
    done = _existing_op(db, reference_for("upgrade", startup_id, idem_key)) if idem_key else None
    if done is not None and done.kind == "upgrade" and done.status == "paid":
        return {"result": "applied", "amount_charged_minor": done.amount_minor}
    db.query(BillingSubscription).filter_by(startup_id=startup_id).with_for_update().first()
    prev = preview_change(db, startup_id, plan_key, interval)
    sub = subs.current(db, startup_id)
    target = subs.plan_by_key(db, plan_key)
    kind = prev["kind"]
    if kind == "same":
        raise _err("NO_CHANGE", "You're already on that plan.")
    if kind == "checkout":
        return {
            "result": "checkout",
            **start_checkout(
                db,
                startup_id=startup_id,
                user=user,
                plan_key=plan_key,
                interval=interval,
                idem_key=idem_key,
            ),
        }
    if kind in ("free", "cancel"):
        return {"result": "scheduled", **_cancel(db, sub, reason=None)}
    if kind == "downgrade":
        _switch_paystack_plan(db, sub, target, interval, user)
        sub.pending_plan_key, sub.pending_interval = target.key, interval
        sub.cancel_at_period_end = False
        db.flush()
        return {"result": "scheduled", "effective_at": prev["keeps_until"]}
    # upgrade
    reference = reference_for("upgrade", startup_id, idem_key)
    op = _existing_op(db, reference)
    if op is not None:
        if op.status == "paid":
            return {"result": "applied", "amount_charged_minor": op.amount_minor}
        raise _err("ALREADY_PROCESSED", "That request already went through.")
    _assert_no_upgrade_in_flight(db, startup_id)
    due = prev["amount_due_minor"]
    pm = default_method(db, startup_id)
    if due > 0 and pm is None:
        raise _err("NO_PAYMENT_METHOD", "Add a card before upgrading.")
    op = _new_op(
        db,
        startup_id=startup_id,
        user=user,
        reference=reference,
        kind="upgrade",
        amount_minor=due,
        params={"plan_key": target.key, "interval": interval},
    )
    # The operation is durable BEFORE the card is charged: if anything after the charge fails, the
    # reference still exists, so the charge.success webhook (or a retry) finishes the upgrade
    # instead of the money being taken with no trace.
    db.commit()
    if due > 0 and pm is not None:
        charged = paystack.charge_authorization(
            authorization_code=pm.authorization_code,
            email=user.email,
            amount_minor=due,
            reference=reference,
            metadata={"startup_id": str(startup_id), "operation": "upgrade"},
        )
        if charged.get("status") == "failed":
            op.status = "failed"
            db.commit()
            raise _err("CHARGE_FAILED", "Your card was declined. Nothing changed.", 402)
        if charged.get("status") != "success" or charged.get("amount") != due:
            # Pending / needs authorisation: left pending, a later success webhook completes it.
            db.commit()
            raise _err(
                "CHARGE_PENDING",
                "Your bank hasn't confirmed the charge yet. Check back in a minute.",
                402,
            )
    op.status = "paid"
    _finish_upgrade(db, sub, op, when=subs.now())
    db.commit()
    try:
        _switch_paystack_plan(db, sub, target, interval, user)
    except (
        AppError
    ) as exc:  # the plan already changed and was paid for; renewal sync is retried by hand
        log.warning(f"billing: upgraded {startup_id} but couldn't re-point Paystack ({exc.code})")
    db.flush()
    return {"result": "applied", "amount_charged_minor": due}


def _cancel(db: Session, sub: BillingSubscription, *, reason: str | None) -> dict[str, Any]:
    if not subs.is_paid(sub):
        raise _err("NOT_SUBSCRIBED", "There's no paid plan to cancel.")
    # Not linked yet: find it first, or the card keeps being charged after we show "canceled".
    if not sub.paystack_subscription_code:
        _adopt_active_subscription(sub, None)
    if sub.paystack_subscription_code and sub.paystack_email_token:
        paystack.disable_subscription(
            code=sub.paystack_subscription_code, email_token=sub.paystack_email_token
        )
        sub.paystack_subscription_code = sub.paystack_email_token = None
    sub.cancel_at_period_end = True
    sub.canceled_at = subs.now()
    sub.cancel_reason = (reason or "")[:80] or None
    sub.pending_plan_key = sub.pending_interval = None
    db.flush()
    return {
        "cancel_at_period_end": True,
        "ends_at": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


def cancel(db: Session, startup_id: uuid.UUID, reason: str | None) -> dict[str, Any]:
    return _cancel(db, subs.current(db, startup_id), reason=reason)


def resume(db: Session, startup_id: uuid.UUID) -> None:
    sub = subs.current(db, startup_id)
    if not (sub.status == "active" and sub.cancel_at_period_end):
        raise _err("NOT_CANCELED", "This plan isn't set to cancel.")
    plan = subs.plan_by_key(db, sub.plan_key)
    pm = default_method(db, startup_id)
    if pm is None or not sub.paystack_customer_code or not sub.current_period_end:
        raise _err("NO_PAYMENT_METHOD", "Add a card before resuming your plan.")
    plan_code = ensure_plan_code(db, plan, sub.interval)
    # One still active at Paystack keeps renewing, and a second one is refused as "already in place".
    if not _adopt_active_subscription(sub, plan_code):
        created = paystack.create_subscription(
            customer_code=sub.paystack_customer_code,
            plan_code=plan_code,
            authorization_code=pm.authorization_code,
            start_date_iso=sub.current_period_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        sub.paystack_subscription_code = created.get("subscription_code")
        sub.paystack_email_token = created.get("email_token")
    sub.cancel_at_period_end = False
    sub.canceled_at = None
    sub.cancel_reason = None
    db.flush()


# ---- add-ons -----------------------------------------------------------------------------------


def purchase_addon(
    db: Session, *, startup_id: uuid.UUID, user: User, addon_key: str, idem_key: str | None
) -> dict[str, Any]:
    addon = ADDONS_BY_KEY.get(addon_key)
    if addon is None:
        raise AppError("NOT_FOUND", "Not found.", 404)
    return _hosted(
        db,
        startup_id=startup_id,
        user=user,
        kind="addon",
        amount_minor=addon["price_minor"],
        params={"addon_key": addon_key},
        idem_key=idem_key,
    )


def verify_reference(db: Session, startup_id: uuid.UUID, reference: str) -> dict[str, Any]:
    """The callback return: ask Paystack for the truth about one of OUR references and apply it.
    Never trusts query-string status; another workspace's reference is a 404."""
    paystack.check_reference(reference)
    op = _existing_op(db, reference)
    if op is None or op.startup_id != startup_id:
        raise AppError("NOT_FOUND", "Not found.", 404)
    if op.status != "paid":
        outcome = finalize_charge(db, paystack.verify(reference))
    else:
        outcome = "already_processed"
    db.refresh(op)
    return {"reference": reference, "kind": op.kind, "status": op.status, "outcome": outcome}
