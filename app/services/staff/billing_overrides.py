"""Super Admin billing overrides (PRD 26.6, D-ADM-6): refund, credit, extend trial, comp.

Callers have already passed the Super Admin guard and a fresh step-up code. Nothing here calls
Paystack: a refund is an internal entry (`provider_refund="pending"` says the money has not moved
at the provider) and a comp has no provider subscription behind it.

Every override takes the workspace's subscription row lock first, so two overrides on one
workspace (or the same `request_id` sent twice) run one after the other. The `request_id` is the
caller's idempotency key: a replay returns the stored override and changes nothing.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import AppError
from app.db.models.billing import (
    BillingAddonPurchase,
    BillingInvoice,
    BillingOverride,
    BillingSubscription,
)
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.billing import subscription as subs

TRIAL_EXTEND_MAX_DAYS = 60
TRIAL_TOTAL_MAX_DAYS = 90  # from the day the trial started
COMP_MAX_DAYS = 365
CREDITS_MAX = 100_000
OVERRIDES_SHOWN = 50
ADDON_KEY = "admin_credit"


def _err(code: str, message: str, status: int = 409) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _workspace(db: Session, startup_id: uuid.UUID) -> Startup:
    ws = db.query(Startup).filter(Startup.id == startup_id, Startup.deleted_at.is_(None)).first()
    if ws is None:
        raise _err("WORKSPACE_NOT_FOUND", "No workspace with that id.", 404)
    return ws


def _locked_subscription(db: Session, startup_id: uuid.UUID) -> BillingSubscription:
    """The workspace's row (created if missing), locked, with time-based transitions applied."""
    subs.get_or_create(db, startup_id)
    sub = (
        db.query(BillingSubscription)
        .filter(BillingSubscription.startup_id == startup_id)
        .with_for_update()
        .one()
    )
    return subs.apply_transitions(db, sub)


def _begin(db: Session, startup_id: uuid.UUID, request_id: uuid.UUID) -> BillingSubscription:
    """Serialise on the request id, then on the workspace, before touching anything. The first
    stops one `request_id` sent twice at once (even for two workspaces) from racing on the unique
    key; the second covers the lazy creation of the subscription row. Both release at commit."""
    for key in (f"billing-override:request:{request_id}", f"billing-override:ws:{startup_id}"):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})
    _workspace(db, startup_id)
    return _locked_subscription(db, startup_id)


def serialize_override(row: BillingOverride, actor_email: str | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "request_id": str(row.request_id),
        "kind": row.kind,
        "reason": row.reason,
        "actor_email": actor_email,
        "invoice_id": str(row.invoice_id) if row.invoice_id else None,
        "amount_minor": row.amount_minor,
        "credits": row.credits,
        "days": row.days,
        "plan_key": row.plan_key,
        "provider_refund": row.provider_refund,
        "details": row.details,
        "created_at": row.created_at.isoformat(),
    }


def _serialize_invoice(i: BillingInvoice) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "number": i.number,
        "kind": i.kind,
        "description": i.description,
        "amount_minor": i.amount_minor,
        "refunded_minor": i.refunded_minor,
        "refundable_minor": max(0, i.amount_minor - i.refunded_minor) if i.status == "paid" else 0,
        "currency": i.currency,
        "status": i.status,
        "paid_at": _iso(i.paid_at),
    }


def _subscription_view(db: Session, sub: BillingSubscription) -> dict[str, Any]:
    return subs.serialize(db, sub)


def overview(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    """Read-only. Reads never write: no lazy row creation, no transitions."""
    ws = _workspace(db, startup_id)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup_id).first()
    invoices = (
        db.query(BillingInvoice)
        .filter(BillingInvoice.startup_id == startup_id)
        .order_by(BillingInvoice.created_at.desc(), BillingInvoice.number.desc())
        .limit(100)
        .all()
    )
    rows = (
        db.query(BillingOverride, User.email)
        .outerjoin(User, User.id == BillingOverride.actor_user_id)
        .filter(BillingOverride.startup_id == startup_id)
        .order_by(BillingOverride.created_at.desc(), BillingOverride.id.desc())
        .limit(OVERRIDES_SHOWN)
        .all()
    )
    credits = (
        db.query(func.coalesce(func.sum(BillingOverride.credits), 0))
        .filter(BillingOverride.startup_id == startup_id, BillingOverride.kind == "credit")
        .scalar()
    )
    return {
        "workspace": {"id": str(ws.id), "name": ws.name},
        "subscription": _subscription_view(db, sub) if sub else None,
        "invoices": [_serialize_invoice(i) for i in invoices],
        "credits_granted_total": int(credits or 0),
        "overrides": [serialize_override(r, email) for r, email in rows],
    }


def _replay(
    db: Session,
    request_id: uuid.UUID,
    startup_id: uuid.UUID,
    kind: str,
    actor_id: uuid.UUID,
    **fields: Any,
) -> BillingOverride | None:
    """The stored override for a repeated `request_id`, provided it is the very same request:
    same workspace, kind, staff member and values. Anything else reuses the id and is refused."""
    row = db.query(BillingOverride).filter(BillingOverride.request_id == request_id).first()
    if row is None:
        return None
    if (
        row.startup_id != startup_id
        or row.kind != kind
        or row.actor_user_id != actor_id
        or any(getattr(row, k) != v for k, v in fields.items())
    ):
        raise _err(
            "REQUEST_ID_REUSED",
            "That request id was already used for a different override. Use a new one.",
        )
    return row


def _finish(
    db: Session,
    ctx: StaffContext,
    row: BillingOverride,
    *,
    action: str,
    startup_id: uuid.UUID,
    ip: str | None,
    ua: str | None,
) -> None:
    db.add(row)
    db.flush()
    write_audit(
        db,
        action,
        actor_user_id=ctx.user.id,
        startup_id=startup_id,
        entity_type="billing_override",
        entity_id=row.id,
        reason=row.reason,
        ip=ip,
        user_agent=ua,
        details={
            "request_id": str(row.request_id),
            "invoice_id": str(row.invoice_id) if row.invoice_id else None,
            "amount_minor": row.amount_minor,
            "credits": row.credits,
            "days": row.days,
            "plan_key": row.plan_key,
            **row.details,
        },
    )


def _result(
    ctx: StaffContext, row: BillingOverride, *, replayed: bool, **extra: Any
) -> dict[str, Any]:
    return {
        "replayed": replayed,
        "override": serialize_override(row, ctx.user.email),
        **extra,
    }


def refund(
    db: Session,
    ctx: StaffContext,
    startup_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    invoice_id: uuid.UUID,
    amount_minor: int,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    _begin(db, startup_id, request_id)
    prior = _replay(
        db, request_id, startup_id, "refund", ctx.user.id,
        invoice_id=invoice_id, amount_minor=amount_minor,
    )  # fmt: skip
    inv = (
        db.query(BillingInvoice)
        .filter(BillingInvoice.id == invoice_id, BillingInvoice.startup_id == startup_id)
        .with_for_update()
        .first()
    )
    if prior is not None:
        return _result(ctx, prior, replayed=True, invoice=_serialize_invoice(inv) if inv else None)
    if inv is None:
        raise _err("INVOICE_NOT_FOUND", "No invoice with that id in this workspace.", 404)
    if inv.status != "paid":
        raise _err(
            "INVOICE_NOT_REFUNDABLE",
            "Only a paid invoice can be refunded; this one is " + inv.status + ".",
        )
    remaining = inv.amount_minor - inv.refunded_minor
    if amount_minor > remaining:
        raise AppError(
            code="REFUND_EXCEEDS_INVOICE",
            message=f"That is more than is left to refund on this invoice ({remaining} kobo).",
            http_status=422,
            field_errors=[{"field": "amount_minor", "message": f"at most {remaining}"}],
        )
    inv.refunded_minor += amount_minor
    if inv.refunded_minor == inv.amount_minor:
        inv.status = "refunded"
    row = BillingOverride(
        request_id=request_id,
        startup_id=startup_id,
        kind="refund",
        actor_user_id=ctx.user.id,
        reason=reason,
        invoice_id=inv.id,
        amount_minor=amount_minor,
        provider_refund="pending",
        details={"invoice_number": inv.number, "invoice_status_after": inv.status},
    )
    _finish(db, ctx, row, action="billing.refund.issued", startup_id=startup_id, ip=ip, ua=ua)
    db.commit()
    return _result(ctx, row, replayed=False, invoice=_serialize_invoice(inv))


def credit(
    db: Session,
    ctx: StaffContext,
    startup_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    credits: int,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    _begin(db, startup_id, request_id)
    prior = _replay(db, request_id, startup_id, "credit", ctx.user.id, credits=credits)
    if prior is not None:
        return _result(ctx, prior, replayed=True)
    row = BillingOverride(
        id=uuid.uuid4(),
        request_id=request_id,
        startup_id=startup_id,
        kind="credit",
        actor_user_id=ctx.user.id,
        reason=reason,
        credits=credits,
        details={},
    )
    # The same table the founder's Usage read sums for credit packs, so the extra credits show up
    # in `ai_credits.limit` at once and never expire (D-SET-10). No money: amount is zero.
    db.add(
        BillingAddonPurchase(
            startup_id=startup_id,
            addon_key=ADDON_KEY,
            credits=credits,
            amount_minor=0,
            reference=f"admin-credit:{row.id}",
        )
    )
    _finish(db, ctx, row, action="billing.credit.applied", startup_id=startup_id, ip=ip, ua=ua)
    db.commit()
    return _result(ctx, row, replayed=False)


def extend_trial(
    db: Session,
    ctx: StaffContext,
    startup_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    days: int,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    sub = _begin(db, startup_id, request_id)
    prior = _replay(db, request_id, startup_id, "trial", ctx.user.id, days=days)
    if prior is not None:
        return _result(ctx, prior, replayed=True, subscription=_subscription_view(db, sub))
    if sub.status != "trialing" or sub.trial_ends_at is None:
        raise _err("NOT_TRIALING", "Only a workspace that is on a trial can have it extended.")
    new_end = sub.trial_ends_at + timedelta(days=days)
    started = sub.trial_used_at or sub.current_period_start
    if started is not None and new_end > started + timedelta(days=TRIAL_TOTAL_MAX_DAYS):
        raise AppError(
            code="TRIAL_TOO_LONG",
            message=f"A trial can run at most {TRIAL_TOTAL_MAX_DAYS} days in total.",
            http_status=422,
            field_errors=[{"field": "days", "message": "too long for this trial"}],
        )
    before = sub.trial_ends_at
    sub.trial_ends_at = sub.current_period_end = new_end
    row = BillingOverride(
        request_id=request_id,
        startup_id=startup_id,
        kind="trial",
        actor_user_id=ctx.user.id,
        reason=reason,
        days=days,
        details={
            "trial_ends_at_before": before.isoformat(),
            "trial_ends_at_after": new_end.isoformat(),
        },
    )
    _finish(db, ctx, row, action="billing.trial.extended", startup_id=startup_id, ip=ip, ua=ua)
    db.commit()
    return _result(ctx, row, replayed=False, subscription=_subscription_view(db, sub))


def comp(
    db: Session,
    ctx: StaffContext,
    startup_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    plan_key: str,
    days: int,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    sub = _begin(db, startup_id, request_id)
    prior = _replay(db, request_id, startup_id, "comp", ctx.user.id, plan_key=plan_key, days=days)
    if prior is not None:
        return _result(ctx, prior, replayed=True, subscription=_subscription_view(db, sub))
    plan = subs.plan_by_key(db, plan_key)
    if plan.key == "starter" or subs.price(plan, "monthly") <= 0:
        raise _err("PLAN_NOT_COMPABLE", "Starter is already free. Choose a paid plan.", 422)
    if sub.status in ("active", "past_due") and not sub.comped:
        raise _err(
            "PAID_SUBSCRIPTION",
            "This workspace pays for a plan. Cancel it first; a comp would stop the charges "
            "from matching what they get.",
        )
    before = {"plan_key": sub.plan_key, "status": sub.status, "comped": sub.comped}
    t = subs.now()
    end = t + timedelta(days=days)
    sub.plan_key, sub.interval, sub.status = plan.key, "monthly", "active"
    sub.comped = True
    sub.current_period_start, sub.current_period_end = t, end
    # lapses on its own at `end`: apply_transitions turns a cancelling active plan into canceled/free
    sub.cancel_at_period_end, sub.cancel_reason, sub.canceled_at = True, "comp", None
    sub.trial_ends_at = None
    sub.pending_plan_key = sub.pending_interval = None
    sub.payment_failed_at = sub.grace_ends_at = None
    db.flush()
    if before["plan_key"] != plan.key:
        event_bus.publish(
            db,
            "billing.plan.changed",
            {"startup_id": str(startup_id), "from": before["plan_key"], "to": plan.key},
        )
    row = BillingOverride(
        request_id=request_id,
        startup_id=startup_id,
        kind="comp",
        actor_user_id=ctx.user.id,
        reason=reason,
        days=days,
        plan_key=plan.key,
        details={"before": before, "ends_at": end.isoformat()},
    )
    _finish(db, ctx, row, action="billing.comp.granted", startup_id=startup_id, ip=ip, ua=ua)
    db.commit()
    return _result(ctx, row, replayed=False, subscription=_subscription_view(db, sub))
