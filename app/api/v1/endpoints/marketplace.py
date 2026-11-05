"""The founder-facing Marketplace (`/marketplace/*`), slice B: browse only. Founders and team
members read (D-MP-L1); nothing here books, pays or messages yet. Responses come from the explicit
allow-list payloads in `services/marketplace/directory.py` (no staff or vetting fields)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.api.v1.endpoints.provider import file_headers
from app.core.envelope import success_response
from app.core.errors import FileNotClean, NotFound
from app.db.models.enums import (
    MARKETPLACE_ROLES,
    MarketplaceFilePurpose,
    ProviderStatus,
    ScanStatus,
)
from app.db.models.marketplace import (
    MarketplaceFile,
    MarketplaceProvider,
    MarketplaceProviderPortfolio,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.services.marketplace import directory, files

router = APIRouter()

_Member = Annotated[Membership, Depends(require_role(*MARKETPLACE_ROLES))]
_User = Annotated[User, Depends(get_verified_user)]
_Db = Annotated[Session, Depends(get_db)]
_Page = Annotated[int, Query(ge=1, le=1000)]


def _meta_response(items_key: str, items: list[Any], meta: dict[str, int]) -> dict[str, Any]:
    """Page-numbered lists carry their paging inside `data.pagination` (the envelope's `meta` is
    for cursor lists)."""
    return success_response({items_key: items, "pagination": meta})


@router.get("/providers")
def providers(
    m: _Member,
    u: _User,
    db: _Db,
    category: Annotated[str | None, Query(max_length=20)] = None,
    min_price: Annotated[int | None, Query(ge=0)] = None,
    max_price: Annotated[int | None, Query(ge=0)] = None,
    min_rating: Annotated[float | None, Query(ge=0, le=5)] = None,
    language: Annotated[str | None, Query(max_length=30)] = None,
    available: bool = False,
    q: Annotated[str | None, Query(max_length=120)] = None,
    sort: str = "relevance",
    page: _Page = 1,
) -> dict[str, Any]:
    items, meta = directory.list_providers(
        db,
        category=category,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        language=language,
        available=available,
        q=q,
        sort=sort,
        page=page,
    )
    return _meta_response("providers", items, meta)


@router.get("/providers/{provider_id}")
def provider_profile(provider_id: uuid.UUID, m: _Member, u: _User, db: _Db) -> dict[str, Any]:
    return success_response(directory.get_provider(db, provider_id))


def _visible_provider(db: Session, provider_id: uuid.UUID) -> MarketplaceProvider:
    p = db.get(MarketplaceProvider, provider_id)
    if p is None or p.status != ProviderStatus.approved:
        raise NotFound()
    return p


def _serve(
    db: Session, owner: uuid.UUID, file_id: uuid.UUID, purpose: MarketplaceFilePurpose
) -> Response:
    f = (
        db.query(MarketplaceFile)
        .filter(
            MarketplaceFile.id == file_id,
            MarketplaceFile.owner_user_id == owner,
            MarketplaceFile.purpose == purpose,
            MarketplaceFile.deleted_at.is_(None),
        )
        .first()
    )
    if f is None:
        raise NotFound()
    if f.scan_status != ScanStatus.clean:
        raise FileNotClean()
    return Response(
        content=files.read_bytes(f), media_type=f.mime, headers=file_headers(f.filename)
    )


@router.get("/providers/{provider_id}/avatar")
def provider_avatar(provider_id: uuid.UUID, m: _Member, u: _User, db: _Db) -> Response:
    p = _visible_provider(db, provider_id)
    if p.avatar_file_id is None or p.user_id is None:
        raise NotFound()
    return _serve(db, p.user_id, p.avatar_file_id, MarketplaceFilePurpose.avatar)


@router.get("/providers/{provider_id}/portfolio/{item_id}/file")
def portfolio_file(
    provider_id: uuid.UUID, item_id: uuid.UUID, m: _Member, u: _User, db: _Db
) -> Response:
    p = _visible_provider(db, provider_id)
    item = (
        db.query(MarketplaceProviderPortfolio)
        .filter(
            MarketplaceProviderPortfolio.id == item_id,
            MarketplaceProviderPortfolio.provider_id == p.id,
        )
        .first()
    )
    if item is None or item.file_id is None or p.user_id is None:
        raise NotFound()
    return _serve(db, p.user_id, item.file_id, MarketplaceFilePurpose.portfolio)


@router.get("/listings")
def listings_(
    m: _Member,
    u: _User,
    db: _Db,
    type: Annotated[str | None, Query(pattern="^(consultation|package)$")] = None,  # noqa: A002
    category: Annotated[str | None, Query(max_length=20)] = None,
    min_price: Annotated[int | None, Query(ge=0)] = None,
    max_price: Annotated[int | None, Query(ge=0)] = None,
    max_delivery_days: Annotated[int | None, Query(ge=1, le=90)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    sort: str = "relevance",
    page: _Page = 1,
) -> dict[str, Any]:
    items, meta = directory.list_listings(
        db,
        type_=type,
        category=category,
        min_price=min_price,
        max_price=max_price,
        max_delivery_days=max_delivery_days,
        q=q,
        sort=sort,
        page=page,
    )
    return _meta_response("listings", items, meta)


@router.get("/listings/{listing_id}")
def listing(listing_id: uuid.UUID, m: _Member, u: _User, db: _Db) -> dict[str, Any]:
    return success_response(directory.listing_detail(db, listing_id))


@router.get("/listings/{listing_id}/slots")
def slots(
    listing_id: uuid.UUID,
    m: _Member,
    u: _User,
    db: _Db,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> dict[str, Any]:
    return success_response(directory.listing_slots(db, listing_id, from_, to))
