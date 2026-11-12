"""Orders that need a person (`/admin/marketplace/orders/attention`, scope `moderation`): a payment
whose amount didn't match what we asked Paystack for (money taken that no booking can claim), and
refunds Paystack keeps refusing. Staff can mark one resolved (reason required, audited); slice E adds
the money actions (manual refund, payout holds) and the Super Admin money console. Staff see ids, amounts, references and reasons, never the
brief or the workspace."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_staff
from app.core.envelope import success_response
from app.db.models.marketplace import MarketplaceOrder
from app.db.session import get_db
from app.schemas.marketplace import ReasonRequest
from app.services.marketplace import orders

router = APIRouter()

_Moderation = Annotated[StaffContext, Depends(require_staff("moderation"))]
_Db = Annotated[Session, Depends(get_db)]


def _row(o: MarketplaceOrder) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "booking_id": str(o.booking_id),
        "reference": o.paystack_reference,
        "status": o.status.value,
        "currency": o.currency,
        "amount_minor": o.amount_minor,
        "refunded_minor": o.refunded_minor,
        "refund_due_minor": o.refund_due_minor,
        "attention_reason": o.attention_reason,
        "attention_at": o.attention_at.isoformat() if o.attention_at else None,
        "paystack_reported_minor": o.attention_amount_minor,
        "refund_attempts": o.refund_attempts,
        "refund_failed_at": o.refund_failed_at.isoformat() if o.refund_failed_at else None,
        "resolved_at": o.attention_resolved_at.isoformat() if o.attention_resolved_at else None,
    }


@router.get("/orders/attention")
def orders_needing_attention(
    ctx: _Moderation,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    rows = (
        db.query(MarketplaceOrder)
        .filter(
            and_(
                MarketplaceOrder.attention_resolved_at.is_(None),
                or_(
                    MarketplaceOrder.attention_reason.isnot(None),
                    MarketplaceOrder.refund_failed_at.isnot(None),
                ),
            )
        )
        .order_by(MarketplaceOrder.attention_at.desc().nullslast(), MarketplaceOrder.id)
        .limit(limit)
        .all()
    )
    return success_response({"orders": [_row(o) for o in rows]})


@router.post("/orders/{order_id}/attention/resolve")
def resolve_attention(
    order_id: uuid.UUID, payload: ReasonRequest, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    """Staff say a flagged order has been dealt with. Needs a reason (10+ characters), which is
    audited; the order leaves the queue until something new is flagged on it."""
    order = orders.resolve_attention(db, order_id, staff_user_id=ctx.user.id, reason=payload.reason)
    db.commit()
    return success_response(_row(order))
