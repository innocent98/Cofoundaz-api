"""The expert's side of listings (design §3.2, D-MP-L5, D-MP-L8, D-MP-L9).

A new listing is a draft, goes to staff review on submit, and is `active` once approved. Edits to a
live listing split in two: price, duration, delivery days, revisions, meeting link and tags apply at
once (price changes only ever affect new bookings); title, summary, description, category and
deliverables wait in `pending_revision_json` while the approved version stays live. Every mutation
row-locks the listing."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext
from app.core.errors import AppError, NotFound
from app.db.models.enums import ListingStatus, ListingType
from app.db.models.marketplace import MarketplaceListing, MarketplaceProvider
from app.platform.audit import write_audit
from app.services.marketplace.constants import (
    DEFAULT_COMMISSION_BPS,
    MAX_LISTINGS_PER_PROVIDER,
    REGULATED,
)
from app.services.marketplace.prescreen import prescreen_listing

REVIEWED = ("title", "summary", "description", "category", "deliverables_json")
IMMEDIATE = (
    "price_minor",
    "duration_min",
    "delivery_days",
    "revisions_included",
    "meeting_url",
    "tags",
)
DRAFTISH = frozenset({ListingStatus.draft, ListingStatus.rejected})
LIVE = frozenset({ListingStatus.active, ListingStatus.paused})


def err(code: str, message: str, status: int = 409) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def _column(name: str) -> str:
    return "deliverables_json" if name == "deliverables" else name


def commission_for(provider: MarketplaceProvider, price_minor: int) -> dict[str, int]:
    """What the expert would keep (D-MP-5): the commission comes off their side, rounded half up
    to the kobo, and net + commission always equals the price."""
    bps = provider.commission_bps if provider.commission_bps is not None else DEFAULT_COMMISSION_BPS
    commission = (price_minor * bps + 5000) // 10000
    return {
        "commission_bps": bps,
        "commission_minor": commission,
        "net_minor": price_minor - commission,
    }


def check_category(provider: MarketplaceProvider, category: str) -> None:
    """Legal and Accounting listings need that category as the expert's vetted main category
    (design §1.3.6). Any other category must be the main one or one of their secondaries."""
    allowed = {provider.category, *provider.categories}
    if category in REGULATED:
        allowed = {provider.category}
    if category not in allowed:
        raise err(
            "CATEGORY_NOT_ALLOWED",
            "You can only list services in the categories you were vetted for.",
            422,
        )


def _text_view(row: MarketplaceListing, pending: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {
        "title": row.title,
        "summary": row.summary,
        "description": row.description,
        "deliverables": row.deliverables_json,
        "tags": row.tags,
        **{_k(k): v for k, v in (pending or {}).items()},
    }
    return merged


def _k(name: str) -> str:
    return "deliverables" if name == "deliverables_json" else name


def refresh_prescreen(row: MarketplaceListing) -> None:
    row.prescreen_json = prescreen_listing(
        _text_view(row, row.pending_revision_json), row.price_minor
    )


def lock_listing(db: Session, provider_id: uuid.UUID, listing_id: uuid.UUID) -> MarketplaceListing:
    row = (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.id == listing_id, MarketplaceListing.provider_id == provider_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def serialize_for_provider(
    row: MarketplaceListing, provider: MarketplaceProvider
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.type.value,
        "status": row.status.value,
        "title": row.title,
        "summary": row.summary,
        "description": row.description,
        "category": row.category,
        "tags": list(row.tags),
        "price_minor": row.price_minor,
        "currency": "NGN",
        "duration_min": row.duration_min,
        "delivery_days": row.delivery_days,
        "deliverables": list(row.deliverables_json),
        "revisions_included": row.revisions_included,
        "meeting_url": row.meeting_url,
        "pending_revision": (
            {_k(k): v for k, v in row.pending_revision_json.items()}
            if row.pending_revision_json
            else None
        ),
        "review_message": row.review_message,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "unlisted_reason": row.unlisted_reason if row.status == ListingStatus.unlisted else None,
        "earnings_preview": commission_for(provider, row.price_minor),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def list_for_provider(db: Session, provider_id: uuid.UUID) -> list[MarketplaceListing]:
    return (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.provider_id == provider_id)
        .order_by(MarketplaceListing.created_at.desc(), MarketplaceListing.id)
        .all()
    )


def get_for_provider(
    db: Session, provider_id: uuid.UUID, listing_id: uuid.UUID
) -> MarketplaceListing:
    row = (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.id == listing_id, MarketplaceListing.provider_id == provider_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def _apply(row: MarketplaceListing, changes: dict[str, Any]) -> None:
    for name, value in changes.items():
        setattr(row, name, list(value) if isinstance(value, list) else value)


def create_listing(db: Session, ctx: ProviderContext, data: dict[str, Any]) -> MarketplaceListing:
    provider = ctx.provider
    count = (
        db.query(func.count(MarketplaceListing.id))
        .filter(MarketplaceListing.provider_id == provider.id)
        .scalar()
        or 0
    )
    if count >= MAX_LISTINGS_PER_PROVIDER:
        raise err(
            "LISTING_LIMIT_REACHED", f"You can have up to {MAX_LISTINGS_PER_PROVIDER} listings."
        )
    check_category(provider, data["category"])
    row = MarketplaceListing(
        provider_id=provider.id,
        type=ListingType(data["type"]),
        title=data["title"],
        summary=data.get("summary") or "",
        description=data.get("description"),
        category=data["category"],
        tags=list(data.get("tags") or []),
        price_minor=data["price_minor"],
        duration_min=data.get("duration_min"),
        delivery_days=data.get("delivery_days"),
        deliverables_json=list(data.get("deliverables") or []),
        revisions_included=data.get("revisions_included", 2),
        meeting_url=data.get("meeting_url"),
        status=ListingStatus.draft,
        prescreen_json={},
    )
    db.add(row)
    db.flush()
    refresh_prescreen(row)
    write_audit(
        db,
        "marketplace.listing.created",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_listing",
        entity_id=row.id,
        details={"type": row.type.value, "category": row.category},
    )
    return row


def update_listing(
    db: Session, ctx: ProviderContext, listing_id: uuid.UUID, raw: dict[str, Any]
) -> MarketplaceListing:
    row = lock_listing(db, ctx.provider.id, listing_id)
    changes = {_column(k): v for k, v in raw.items()}
    if "category" in changes and changes["category"] != row.category:
        check_category(ctx.provider, changes["category"])
    if row.status in DRAFTISH:
        _apply(row, changes)
        refresh_prescreen(row)
        db.flush()
        return row
    if row.status == ListingStatus.pending_review:
        raise err(
            "LISTING_LOCKED", "This listing is under review. You can edit it after the decision."
        )
    if row.status == ListingStatus.unlisted:
        raise err("LISTING_UNLISTED", "This listing was taken down. Contact support.", 403)
    _edit_live(db, ctx, row, changes)
    return row


def _edit_live(
    db: Session, ctx: ProviderContext, row: MarketplaceListing, changes: dict[str, Any]
) -> None:
    """A live listing: immediate fields apply now, reviewed fields wait for staff (D-MP-L8)."""
    _apply(row, {k: v for k, v in changes.items() if k in IMMEDIATE})
    reviewed = {k: v for k, v in changes.items() if k in REVIEWED}
    pending = dict(row.pending_revision_json or {})
    pending.update(reviewed)
    # An edit that restores the live value is not a change.
    pending = {k: v for k, v in pending.items() if v != getattr(row, k)}
    started = row.pending_revision_json is None and bool(pending)
    row.pending_revision_json = pending or None
    if not pending:
        row.revision_submitted_at = None
    elif started or row.revision_submitted_at is None:
        row.revision_submitted_at = datetime.now(UTC)
    if reviewed:
        row.review_message = None
    refresh_prescreen(row)
    db.flush()
    if started:
        write_audit(
            db,
            "marketplace.listing.revision_submitted",
            actor_user_id=ctx.user.id,
            entity_type="marketplace_listing",
            entity_id=row.id,
            details={"fields": sorted(_k(k) for k in pending)},
        )


def submit_blockers(row: MarketplaceListing) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []

    def need(field: str, message: str) -> None:
        missing.append({"field": field, "message": message})

    if len(row.summary.strip()) < 20:
        need("summary", "Write a summary of at least 20 characters.")
    if not row.description or len(row.description.strip()) < 50:
        need("description", "Describe the service in at least 50 characters.")
    if row.type == ListingType.consultation:
        if not row.duration_min:
            need("duration_min", "Choose how long a session lasts.")
        if not row.meeting_url:
            need("meeting_url", "Add the meeting link founders will join.")
    else:
        if not row.delivery_days:
            need("delivery_days", "Say how many days delivery takes.")
        if not row.deliverables_json:
            need("deliverables", "List at least one deliverable.")
    return missing


def submit_listing(db: Session, ctx: ProviderContext, listing_id: uuid.UUID) -> MarketplaceListing:
    row = lock_listing(db, ctx.provider.id, listing_id)
    if row.status not in DRAFTISH:
        raise err("INVALID_STATE", "This listing can't be submitted right now.")
    missing = submit_blockers(row)
    if missing:
        raise AppError(
            code="SUBMISSION_INCOMPLETE",
            message="A few things are still needed before you can submit.",
            http_status=422,
            field_errors=missing,
        )
    refresh_prescreen(row)
    row.status = ListingStatus.pending_review
    row.submitted_at = datetime.now(UTC)
    row.review_message = None
    db.flush()
    write_audit(
        db,
        "marketplace.listing.submitted",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_listing",
        entity_id=row.id,
    )
    return row


def set_paused(
    db: Session, ctx: ProviderContext, listing_id: uuid.UUID, *, pause: bool
) -> MarketplaceListing:
    row = lock_listing(db, ctx.provider.id, listing_id)
    want, have = (
        (ListingStatus.paused, ListingStatus.active)
        if pause
        else (
            ListingStatus.active,
            ListingStatus.paused,
        )
    )
    if row.status != have:
        raise err(
            "INVALID_STATE",
            f"Only a {have.value} listing can be {'paused' if pause else 'resumed'}.",
        )
    row.status = want
    db.flush()
    write_audit(
        db,
        "marketplace.listing.paused" if pause else "marketplace.listing.resumed",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_listing",
        entity_id=row.id,
    )
    return row


def delete_listing(db: Session, ctx: ProviderContext, listing_id: uuid.UUID) -> None:
    row = lock_listing(db, ctx.provider.id, listing_id)
    if row.status not in DRAFTISH:
        raise err(
            "INVALID_STATE", "Only a draft or rejected listing can be deleted. Pause it instead."
        )
    write_audit(
        db,
        "marketplace.listing.deleted",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_listing",
        entity_id=row.id,
    )
    db.delete(row)
    db.flush()
