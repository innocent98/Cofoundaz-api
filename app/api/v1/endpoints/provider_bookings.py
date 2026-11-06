"""The expert's booking inbox (`/provider/bookings*`), slice C: list, read, accept, decline.
Delivery, completion and messages arrive in slice D. Approved experts only."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext, require_provider
from app.core.envelope import success_response
from app.db.models.enums import BookingStatus
from app.db.session import get_db
from app.services.marketplace import bookings, emails, orders

router = APIRouter()

_Approved = Annotated[ProviderContext, Depends(require_provider(approved=True))]
_Db = Annotated[Session, Depends(get_db)]
_TABS = "^(upcoming|in_progress|delivered|closed)$"


@router.get("/bookings")
def list_bookings(
    ctx: _Approved,
    db: _Db,
    tab: Annotated[str | None, Query(pattern=_TABS)] = None,
    status_: Annotated[BookingStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    rows, total = bookings.list_for_provider(
        db, ctx, tab=tab, status=status_, limit=limit, offset=offset
    )
    return success_response(
        {
            "bookings": [bookings.provider_view(db, b) for b in rows],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
            },
        }
    )


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    return success_response(
        bookings.provider_view(db, bookings.get_for_provider(db, ctx, booking_id))
    )


@router.post("/bookings/{booking_id}/accept")
def accept(booking_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    b = bookings.accept(db, ctx, booking_id)
    db.commit()
    return success_response(bookings.provider_view(db, b))


@router.post("/bookings/{booking_id}/decline")
def decline(booking_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    b, order_id, mail = bookings.decline(db, ctx, booking_id)
    db.commit()
    emails.deliver(mail)
    try:  # best effort now; the timer retries if Paystack is down
        orders.process_refund(db, order_id)
        db.commit()
    except Exception:  # noqa: BLE001 - the booking is already declined; the refund stays due
        db.rollback()
    return success_response(
        bookings.provider_view(db, bookings.get_for_provider(db, ctx, booking_id))
    )
