"""The expert's booking inbox (`/provider/bookings*`): list, read, accept, decline (slice C);
deliver, mark a consultation complete, the shared documents and the booking's conversation
(slice D). Approved experts only."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext, require_provider
from app.core.envelope import success_response
from app.core.rate_limit import limiter
from app.db.models.enums import BookingStatus
from app.db.session import get_db
from app.schemas.marketplace import DeliverRequest, MessageCreate
from app.services.marketplace import (
    booking_documents,
    bookings,
    deliveries,
    emails,
    orders,
    outbox,
    threads,
    views,
)

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
        views.provider_detail(db, bookings.get_for_provider(db, ctx, booking_id))
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


@router.post("/bookings/{booking_id}/deliver")
def deliver(
    booking_id: uuid.UUID, payload: DeliverRequest, ctx: _Approved, db: _Db
) -> dict[str, Any]:
    """Hand over a package's work (a message and up to 10 files uploaded with purpose
    `delivery`). The client then has 7 days to accept or ask for changes."""
    b, _ = deliveries.deliver(
        db, ctx, booking_id, message=payload.message, file_ids=payload.file_ids
    )
    db.commit()
    return success_response(views.provider_detail(db, b))


@router.post("/bookings/{booking_id}/complete")
def complete(booking_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    """Mark a consultation done, once its session has ended. The client then has 48 hours."""
    b, _ = deliveries.complete_consultation(db, ctx, booking_id)
    db.commit()
    return success_response(views.provider_detail(db, b))


@router.get("/bookings/{booking_id}/documents")
def list_documents(booking_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    b = bookings.get_for_provider(db, ctx, booking_id)
    return success_response({"documents": views.provider_detail(db, b)["documents"]})


@router.get("/bookings/{booking_id}/documents/{document_id}")
def read_document(
    booking_id: uuid.UUID, document_id: uuid.UUID, ctx: _Approved, db: _Db
) -> dict[str, Any]:
    """One document the client shared for this booking, read-only, while the share is live."""
    return success_response(booking_documents.read_for_provider(db, ctx, booking_id, document_id))


@router.get("/bookings/{booking_id}/messages")
def booking_messages(
    booking_id: uuid.UUID,
    ctx: _Approved,
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: uuid.UUID | None = None,
) -> dict[str, Any]:
    thread = threads.booking_thread_provider(db, ctx, booking_id)
    items, more = threads.read_provider(db, ctx, thread.id, limit=limit, before=before)
    db.commit()
    return success_response({"thread_id": str(thread.id), "messages": items, "has_more": more})


@router.post("/bookings/{booking_id}/messages", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def post_booking_message(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    booking_id: uuid.UUID,
    payload: MessageCreate,
    ctx: _Approved,
    db: _Db,
) -> dict[str, Any]:
    thread = threads.booking_thread_provider(db, ctx, booking_id)
    msg = threads.post_provider(db, ctx, thread.id, body=payload.body, file_ids=payload.file_ids)
    db.commit()
    outbox.flush(db)
    return success_response(threads.serialize_message(db, msg, threads.PROVIDER))
