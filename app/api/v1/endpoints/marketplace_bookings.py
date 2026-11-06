"""Founder bookings, checkout and orders (`/marketplace/bookings*`, `/marketplace/orders*`),
slice C. Founders and team members read; only a founder books, verifies a payment or cancels
(D-MP-L1). Money is kobo throughout. Booking needs `Idempotency-Key`: the same key and body
returns the same booking (200), the same key with a different body is 409."""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import (
    MARKETPLACE_ROLES,
    MARKETPLACE_WRITE_ROLES,
    BookingStatus,
    OrderStatus,
)
from app.db.models.marketplace import MarketplaceBooking, MarketplaceOrder, MarketplaceProvider
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.marketplace import BookingCreate
from app.services.marketplace import bookings, emails, orders, outbox

router = APIRouter()

_Reader = Annotated[Membership, Depends(require_role(*MARKETPLACE_ROLES))]
_Founder = Annotated[Membership, Depends(require_role(*MARKETPLACE_WRITE_ROLES))]
_User = Annotated[User, Depends(get_verified_user)]
_Db = Annotated[Session, Depends(get_db)]
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_TABS = "^(upcoming|in_progress|delivered|closed)$"


def _idempotency_key(value: str | None) -> str:
    if value is None or not _KEY_RE.fullmatch(value):
        raise AppError(
            "VALIDATION_ERROR",
            "Send an Idempotency-Key header of 8 to 64 letters, digits or . _ : -",
            422,
            field_errors=[{"field": "Idempotency-Key", "message": "Required, 8-64 characters."}],
        )
    return value


def _page(rows: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@router.get("/checkout")
def checkout(m: _Reader, user: _User, db: _Db) -> dict[str, Any]:
    """What the booking drawer needs before it renders: are payments on, have you accepted the
    current terms, is there a saved card, and can this role book at all."""
    return success_response(bookings.checkout_status(db, user, m))


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
def create_booking(
    payload: BookingCreate,
    response: Response,
    m: _Founder,
    user: _User,
    db: _Db,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    key = _idempotency_key(idempotency_key)
    booking, order, replayed = bookings.create_booking(
        db,
        user,
        m,
        listing_id=payload.listing_id,
        starts_at=payload.starts_at,
        brief=payload.brief,
        idempotency_key=key,
        accept_terms=payload.accept_terms,
        pay_with=payload.pay_with,
    )
    db.commit()  # the hold is real before any card is touched
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif payload.pay_with == "saved_card":
        outcome = bookings.charge_saved_card(db, user, m, booking, order)
        db.commit()
        outbox.flush(db)
        if outcome == "card_error":
            raise bookings.payment_unconfirmed(booking.id)
        if outcome in ("card_declined", "no_saved_card"):
            raise AppError(
                "CARD_DECLINED",
                "Your saved card was declined. Nothing was charged. Try another card.",
                402,
            )
    return success_response({"replayed": replayed, **bookings.founder_view(db, booking)})


@router.get("/bookings")
def list_bookings(
    m: _Reader,
    db: _Db,
    tab: Annotated[str | None, Query(pattern=_TABS)] = None,
    status_: Annotated[BookingStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    rows, total = bookings.list_for_founder(
        db, m, tab=tab, status=status_, limit=limit, offset=offset
    )
    return success_response(
        {
            "bookings": [bookings.founder_view(db, b) for b in rows],
            "pagination": _page(rows, total, limit, offset),
        }
    )


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: uuid.UUID, m: _Reader, db: _Db) -> dict[str, Any]:
    return success_response(bookings.founder_view(db, bookings.get_for_founder(db, m, booking_id)))


@router.post("/bookings/{booking_id}/verify-payment")
def verify_payment(booking_id: uuid.UUID, m: _Founder, db: _Db) -> dict[str, Any]:
    """After Paystack's checkout page, ask Paystack what happened (the webhook may be seconds
    behind). Idempotent: the payment is applied once whichever of the two arrives first."""
    booking = bookings.get_for_founder(db, m, booking_id)
    order = db.query(MarketplaceOrder).filter_by(booking_id=booking.id).one()
    outcome = bookings.verify_and_apply(db, order.paystack_reference, booking.id)
    db.commit()
    outbox.flush(db)
    return success_response(
        {
            "outcome": outcome,
            **bookings.founder_view(db, bookings.get_for_founder(db, m, booking_id)),
        }
    )


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: uuid.UUID, m: _Founder, user: _User, db: _Db) -> dict[str, Any]:
    booking, mail = bookings.cancel_by_founder(db, user, m, booking_id)
    order_id = db.query(MarketplaceOrder.id).filter_by(booking_id=booking.id).scalar()
    db.commit()
    emails.deliver(mail)
    if order_id is not None:
        try:  # best effort now; the timer retries if Paystack is down
            orders.process_refund(db, order_id)
            db.commit()
        except AppError:
            db.rollback()
    return success_response(bookings.founder_view(db, bookings.get_for_founder(db, m, booking_id)))


def _own_order(db: Session, m: Membership, order_id: uuid.UUID) -> MarketplaceOrder:
    row = (
        db.query(MarketplaceOrder)
        .filter(MarketplaceOrder.id == order_id, MarketplaceOrder.startup_id == m.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row


@router.get("/orders")
def list_orders(
    m: _Reader,
    db: _Db,
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    q = (
        db.query(MarketplaceOrder, MarketplaceBooking)
        .join(MarketplaceBooking, MarketplaceBooking.id == MarketplaceOrder.booking_id)
        .filter(
            MarketplaceOrder.startup_id == m.startup_id,
            MarketplaceOrder.status != OrderStatus.pending,
        )
    )
    if status_:
        q = q.filter(MarketplaceOrder.status == status_)
    total = q.count()
    rows = (
        q.order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        {
            **bookings.order_view(o, booking_status=b.status),
            "booking_id": str(b.id),
            "title": b.title_snap,
            "provider_id": str(b.provider_id),
        }
        for o, b in rows
    ]
    return success_response({"orders": items, "pagination": _page(items, total, limit, offset)})


@router.get("/orders/{order_id}/receipt")
def get_receipt(order_id: uuid.UUID, m: _Reader, db: _Db) -> dict[str, Any]:
    order = _own_order(db, m, order_id)
    if order.receipt_number is None:
        raise NotFound()  # no payment, no receipt
    booking = db.get(MarketplaceBooking, order.booking_id)
    provider = db.get(MarketplaceProvider, booking.provider_id) if booking else None
    if booking is None:
        raise NotFound()
    return success_response(
        orders.receipt(order, booking, provider.display_name if provider else None)
    )
