"""Listing review for staff (`/admin/marketplace/listings/*`, design §4). Scope `moderation`.
Approve, reject and request-changes take a reason only; unlist and relist also take the staff
member's own fresh authenticator code."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext, require_staff
from app.core.envelope import Meta, success_response
from app.db.models.enums import ListingStatus
from app.db.session import get_db
from app.schemas.marketplace import (
    ListingChangesRequest,
    ListingRejectRequest,
    ReasonRequest,
    StepUpReasonRequest,
)
from app.services.marketplace import emails
from app.services.marketplace import listing_moderation as mod
from app.services.staff.stepup import verify_step_up

router = APIRouter()

_Moderation = Annotated[StaffContext, Depends(require_staff("moderation"))]
_Db = Annotated[Session, Depends(get_db)]


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.get("/listings/counts")
def counts(ctx: _Moderation, db: _Db) -> dict[str, Any]:
    return success_response(mod.counts(db))


@router.get("/listings")
def list_listings(
    ctx: _Moderation,
    db: _Db,
    status_: Annotated[ListingStatus | None, Query(alias="status")] = None,
    queue: bool = False,
    query: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, nxt = mod.list_listings(
        db, status=status_, queue=queue, query=query, limit=limit, cursor=cursor
    )
    return success_response({"listings": items}, Meta(next_cursor=nxt))


@router.get("/listings/{listing_id}")
def get_listing(listing_id: uuid.UUID, ctx: _Moderation, db: _Db) -> dict[str, Any]:
    return success_response(mod.detail(db, listing_id))


@router.post("/listings/{listing_id}/approve")
def approve(
    listing_id: uuid.UUID, payload: ReasonRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    _, outbox = mod.approve(db, ctx, listing_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(mod.detail(db, listing_id))


@router.post("/listings/{listing_id}/reject")
def reject(
    listing_id: uuid.UUID,
    payload: ListingRejectRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    ip, ua = _client(request)
    _, outbox = mod.reject(
        db,
        ctx,
        listing_id,
        template=payload.template,
        message=payload.message,
        reason=payload.reason,
        ip=ip,
        ua=ua,
    )
    db.commit()
    emails.deliver(outbox)
    return success_response(mod.detail(db, listing_id))


@router.post("/listings/{listing_id}/request-changes")
def request_changes(
    listing_id: uuid.UUID,
    payload: ListingChangesRequest,
    request: Request,
    ctx: _Moderation,
    db: _Db,
) -> dict[str, Any]:
    ip, ua = _client(request)
    _, outbox = mod.request_changes(db, ctx, listing_id, message=payload.message, ip=ip, ua=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(mod.detail(db, listing_id))


@router.post("/listings/{listing_id}/unlist")
def unlist(
    listing_id: uuid.UUID, payload: StepUpReasonRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    mod.exists_or_404(db, listing_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    mod.unlist(db, ctx, listing_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    return success_response(mod.detail(db, listing_id))


@router.post("/listings/{listing_id}/relist")
def relist(
    listing_id: uuid.UUID, payload: StepUpReasonRequest, request: Request, ctx: _Moderation, db: _Db
) -> dict[str, Any]:
    mod.exists_or_404(db, listing_id)
    verify_step_up(ctx.user, payload.totp_code)
    ip, ua = _client(request)
    mod.relist(db, ctx, listing_id, reason=payload.reason, ip=ip, ua=ua)
    db.commit()
    return success_response(mod.detail(db, listing_id))
