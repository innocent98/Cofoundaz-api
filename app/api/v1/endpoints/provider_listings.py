"""The expert's listings and availability (`/provider/listings`, `/provider/availability`).
Approved experts only: a suspended expert can still read their record but can't sell."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext, require_provider
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.session import get_db
from app.schemas.marketplace import AvailabilityPut, ListingCreate, ListingPatch
from app.services.marketplace import availability, listings

router = APIRouter()

_Approved = Annotated[ProviderContext, Depends(require_provider(approved=True))]
_Db = Annotated[Session, Depends(get_db)]
_REQUIRED = ("title", "summary", "category", "price_minor")


@router.get("/listings")
def list_mine(ctx: _Approved, db: _Db) -> dict[str, Any]:
    rows = listings.list_for_provider(db, ctx.provider.id)
    return success_response(
        {"listings": [listings.serialize_for_provider(r, ctx.provider) for r in rows]}
    )


@router.post("/listings", status_code=status.HTTP_201_CREATED)
def create(payload: ListingCreate, ctx: _Approved, db: _Db) -> dict[str, Any]:
    row = listings.create_listing(db, ctx, payload.model_dump(exclude_unset=True))
    db.commit()
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.get("/listings/{listing_id}")
def get_one(listing_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    row = listings.get_for_provider(db, ctx.provider.id, listing_id)
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.patch("/listings/{listing_id}")
def update(listing_id: uuid.UUID, payload: ListingPatch, ctx: _Approved, db: _Db) -> dict[str, Any]:
    sent = payload.model_fields_set
    if not sent:
        raise AppError("VALIDATION_ERROR", "Nothing to update.", 422)
    changes: dict[str, Any] = {}
    for field in sent:
        value = getattr(payload, field)
        if value is None and field in _REQUIRED:
            raise AppError("VALIDATION_ERROR", f"'{field}' can't be empty.", 422)
        if value is None and field in ("tags", "deliverables"):
            value = []
        if value is None and field == "revisions_included":
            raise AppError("VALIDATION_ERROR", "'revisions_included' can't be empty.", 422)
        changes[field] = value
    row = listings.update_listing(db, ctx, listing_id, changes)
    db.commit()
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.delete("/listings/{listing_id}")
def delete(listing_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    listings.delete_listing(db, ctx, listing_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/listings/{listing_id}/submit")
def submit(listing_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    row = listings.submit_listing(db, ctx, listing_id)
    db.commit()
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.post("/listings/{listing_id}/pause")
def pause(listing_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    row = listings.set_paused(db, ctx, listing_id, pause=True)
    db.commit()
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.post("/listings/{listing_id}/resume")
def resume(listing_id: uuid.UUID, ctx: _Approved, db: _Db) -> dict[str, Any]:
    row = listings.set_paused(db, ctx, listing_id, pause=False)
    db.commit()
    return success_response(listings.serialize_for_provider(row, ctx.provider))


@router.get("/availability")
def get_availability(ctx: _Approved, db: _Db) -> dict[str, Any]:
    return success_response(availability.get_availability(db, ctx.provider))


@router.put("/availability")
def put_availability(payload: AvailabilityPut, ctx: _Approved, db: _Db) -> dict[str, Any]:
    out = availability.put_availability(
        db,
        ctx.provider,
        ctx.user.id,
        {
            "min_notice_h": payload.min_notice_h,
            "buffer_min": payload.buffer_min,
            "rules": [r.model_dump() | {"start": r.start, "end": r.end} for r in payload.rules],
            "exceptions": [
                {"on_date": e.on_date, "kind": e.kind, "start": e.start, "end": e.end}
                for e in payload.exceptions
            ],
        },
    )
    db.commit()
    return success_response(out)
