"""Subscription & Billing (PRD Module 24) on Paystack.

Founder only: anyone else gets 403 "Billing is managed by your workspace founder." (PRD 24 access).
The webhook is the one public route and lives on `webhook_router`.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, Forbidden
from app.db.models.billing import (
    BillingDetails,
    BillingInvoice,
    BillingPaymentMethod,
    BillingPlan,
)
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.platform.audit import write_audit
from app.services.billing import flows, receipt, subscription, usage, webhook
from app.services.billing.catalog import ADDONS, TOKENS_PER_CREDIT

router = APIRouter()
webhook_router = APIRouter()


def require_billing(
    membership: Membership = Depends(require_workspace),  # noqa: B008
) -> Membership:
    if membership.role != MembershipRole.founder:
        raise Forbidden(message="Billing is managed by your workspace founder.")
    return membership


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str
    interval: Literal["monthly", "annual"] = "monthly"


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=80)


class DetailsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str | None = Field(default=None, max_length=160)
    tax_id: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)


def _plan_json(p: BillingPlan) -> dict[str, Any]:
    return {
        "key": p.key,
        "name": p.name,
        "tagline": p.tagline,
        "currency": "NGN",
        "price_monthly_minor": p.price_monthly_minor,
        "price_annual_minor": p.price_annual_minor,
        "ai_credits": p.ai_credits,
        "seats": p.seats,
        "storage_mb": p.storage_mb,
        "features": p.features,
        "modules": p.modules,
    }


def _pm_json(pm: BillingPaymentMethod) -> dict[str, Any]:
    return {
        "id": str(pm.id),
        "brand": pm.brand,
        "last4": pm.last4,
        "exp_month": pm.exp_month,
        "exp_year": pm.exp_year,
        "bank": pm.bank,
        "is_default": pm.is_default,
    }


def _inv_json(i: BillingInvoice) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "number": i.number,
        "kind": i.kind,
        "description": i.description,
        "amount_minor": i.amount_minor,
        "currency": i.currency,
        "status": i.status,
        "date": (i.paid_at or i.created_at).isoformat(),
    }


@router.get("/plans")
def list_plans(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"plans": [_plan_json(p) for p in subscription.plans(db)], "addons": ADDONS}
    )


@router.get("/subscription")
def get_subscription(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = subscription.serialize(db, subscription.current(db, membership.startup_id))
    db.commit()
    return success_response(out)


@router.post("/trial")
def start_trial(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    sub = subscription.get_or_create(db, membership.startup_id)
    if not subscription.start_trial(db, sub):
        raise AppError("TRIAL_UNAVAILABLE", "This workspace has already used its trial.", 409)
    write_audit(
        db, "billing.trial.started", actor_user_id=membership.user_id, startup_id=sub.startup_id
    )
    out = subscription.serialize(db, subscription.apply_transitions(db, sub))
    db.commit()
    return success_response(out)


@router.post("/subscription/checkout")
def checkout(
    body: Pick,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    membership: Membership = Depends(require_billing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.start_checkout(
        db,
        startup_id=membership.startup_id,
        user=user,
        plan_key=body.plan,
        interval=body.interval,
        idem_key=idempotency_key,
    )
    db.commit()
    return success_response(out)


@router.get("/subscription/verify")
def verify(
    reference: str,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.verify_reference(db, membership.startup_id, reference)
    db.commit()
    return success_response(out)


@router.post("/subscription/preview-change")
def preview_change(
    body: Pick,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.preview_change(db, membership.startup_id, body.plan, body.interval)
    db.commit()
    return success_response(out)


@router.post("/subscription/change")
def change(
    body: Pick,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    membership: Membership = Depends(require_billing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if not idempotency_key:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header.", 422)
    out = flows.change_plan(
        db,
        startup_id=membership.startup_id,
        user=user,
        plan_key=body.plan,
        interval=body.interval,
        idem_key=idempotency_key,
    )
    write_audit(
        db,
        "billing.plan.change_requested",
        actor_user_id=user.id,
        startup_id=membership.startup_id,
    )
    db.commit()
    return success_response(out)


@router.post("/subscription/cancel")
def cancel(
    body: CancelBody,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.cancel(db, membership.startup_id, body.reason)
    write_audit(
        db,
        "billing.subscription.canceled",
        actor_user_id=membership.user_id,
        startup_id=membership.startup_id,
    )
    db.commit()
    return success_response(out)


@router.post("/subscription/resume")
def resume(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    flows.resume(db, membership.startup_id)
    out = subscription.serialize(db, subscription.current(db, membership.startup_id))
    db.commit()
    return success_response(out)


@router.get("/usage")
def get_usage(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = usage.usage(db, membership.startup_id)
    usage.maybe_alert(db, membership.startup_id, out)
    db.commit()
    return success_response(out)


@router.get("/payment-methods")
def list_methods(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = (
        db.query(BillingPaymentMethod)
        .filter(
            BillingPaymentMethod.startup_id == membership.startup_id,
            BillingPaymentMethod.removed_at.is_(None),
        )
        .order_by(BillingPaymentMethod.is_default.desc(), BillingPaymentMethod.created_at)
        .all()
    )
    return success_response([_pm_json(r) for r in rows])


@router.post("/payment-methods")
def add_method(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    membership: Membership = Depends(require_billing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.start_add_card(
        db, startup_id=membership.startup_id, user=user, idem_key=idempotency_key
    )
    db.commit()
    return success_response(out)


@router.put("/payment-methods/{method_id}/default")
def make_default(
    method_id: uuid.UUID,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    flows.set_default_method(db, membership.startup_id, method_id)
    db.commit()
    return success_response({"default": str(method_id)})


@router.delete("/payment-methods/{method_id}")
def remove_method(
    method_id: uuid.UUID,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    flows.remove_method(db, membership.startup_id, method_id)
    db.commit()
    return success_response({"removed": True})


@router.get("/invoices")
def list_invoices(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = (
        db.query(BillingInvoice)
        .filter_by(startup_id=membership.startup_id)
        .order_by(BillingInvoice.created_at.desc(), BillingInvoice.number.desc())
        .all()
    )
    return success_response([_inv_json(r) for r in rows])


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    inv = (
        db.query(BillingInvoice).filter_by(id=invoice_id, startup_id=membership.startup_id).first()
    )
    if inv is None:
        raise AppError("NOT_FOUND", "Not found.", 404)
    details = db.query(BillingDetails).filter_by(startup_id=membership.startup_id).first()
    startup = db.query(Startup).filter_by(id=membership.startup_id).first()
    pdf = receipt.build_receipt(inv, details, startup.name if startup else None)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'},
    )


@router.get("/addons")
def list_addons(
    membership: Membership = Depends(require_billing),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"addons": ADDONS, "tokens_per_credit": TOKENS_PER_CREDIT})


@router.post("/addons/{addon_key}/purchase")
def buy_addon(
    addon_key: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    membership: Membership = Depends(require_billing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = flows.purchase_addon(
        db,
        startup_id=membership.startup_id,
        user=user,
        addon_key=addon_key,
        idem_key=idempotency_key,
    )
    db.commit()
    return success_response(out)


def _details_json(d: BillingDetails | None) -> dict[str, Any]:
    return {
        "company_name": d.company_name if d else None,
        "tax_id": d.tax_id if d else None,
        "address": d.address if d else None,
    }


@router.get("/details")
def get_details(
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        _details_json(db.query(BillingDetails).filter_by(startup_id=membership.startup_id).first())
    )


@router.put("/details")
def put_details(
    body: DetailsBody,
    membership: Membership = Depends(require_billing),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    d = db.query(BillingDetails).filter_by(startup_id=membership.startup_id).first()
    if d is None:
        d = BillingDetails(startup_id=membership.startup_id)
        db.add(d)
    d.company_name = (body.company_name or "").strip() or None
    d.tax_id = (body.tax_id or "").strip() or None
    d.address = (body.address or "").strip() or None
    db.commit()
    return success_response(_details_json(d))


# Public on purpose: authenticity is Paystack's HMAC signature over the raw body (see webhook.py).
@webhook_router.post("/billing/paystack")
async def paystack_webhook(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > webhook.MAX_BODY_BYTES:
            raise webhook.InvalidSignature()
        chunks.append(chunk)
    raw = b"".join(chunks)
    signature = request.headers.get("x-paystack-signature")

    def handle() -> dict[str, Any]:
        out = webhook.process(db, raw_body=raw, signature=signature)
        db.commit()
        return out

    return success_response(await run_in_threadpool(handle))
