"""The founder-facing directory (design §3.1-§3.3, D-MP-L18): approved experts, active listings,
filters, sort and slots. Plain SQL filters and a deterministic relevance score, no embeddings.

VISIBILITY RULE, applied in one place (`_visible`): an expert is shown only while `approved` (a
suspended expert disappears at once) and only through listings that are `active`. Nothing staff-only
leaves this module: no vetting data, pre-screen flags, commission, email, user id, meeting link or
review message. The founder-side payloads are built here from an explicit allow-list of fields.

The directory loads the filtered candidates and sorts and pages them in Python: supply is tens of
experts at launch (design §11). Move the sort and page into SQL if the catalogue grows past a few
hundred."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Query, Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ListingStatus, ListingType, ProviderStatus
from app.db.models.marketplace import (
    MarketplaceListing,
    MarketplaceProvider,
    MarketplaceProviderPortfolio,
)
from app.services.marketplace import availability
from app.services.marketplace.constants import CATEGORIES, DIRECTORY_PAGE_SIZE
from app.services.marketplace.providers import badge, portfolio_rows
from app.services.staff.like import contains_pattern

NEW_BELOW_REVIEWS = 3  # stars read "New" until an expert has three reviews (D-MP-7)
SORTS = ("relevance", "rating", "price_asc", "price_desc")
LISTING_SORTS = ("relevance", "rating", "price_asc", "price_desc", "newest")


def _visible(db: Session) -> Query[Any]:
    return (
        db.query(MarketplaceListing, MarketplaceProvider)
        .join(MarketplaceProvider, MarketplaceProvider.id == MarketplaceListing.provider_id)
        .filter(
            MarketplaceProvider.status == ProviderStatus.approved,
            MarketplaceListing.status == ListingStatus.active,
        )
    )


def has_live_listing(db: Session, provider_id: uuid.UUID) -> bool:
    return (
        db.query(MarketplaceListing.id)
        .filter(
            MarketplaceListing.provider_id == provider_id,
            MarketplaceListing.status == ListingStatus.active,
        )
        .first()
        is not None
    )


def _rating(p: MarketplaceProvider) -> dict[str, Any]:
    is_new = p.rating_count < NEW_BELOW_REVIEWS
    return {
        "avg": None if is_new else float(p.rating_avg),
        "count": p.rating_count,
        "is_new": is_new,
    }


def relevance(p: MarketplaceProvider, portfolio_count: int) -> float:
    """Deterministic: ratings count once there are enough of them, then profile completeness."""
    score = float(p.rating_avg) * 20 if p.rating_count >= NEW_BELOW_REVIEWS else 0.0
    score += min(portfolio_count, 5) * 2
    score += 5 if p.avatar_file_id else 0
    score += min(len((p.vetting_json or {}).get("checks", {})), 3)
    return score


def _portfolio_counts(db: Session, provider_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not provider_ids:
        return {}
    rows = (
        db.query(
            MarketplaceProviderPortfolio.provider_id, func.count(MarketplaceProviderPortfolio.id)
        )
        .filter(MarketplaceProviderPortfolio.provider_id.in_(provider_ids))
        .group_by(MarketplaceProviderPortfolio.provider_id)
        .all()
    )
    return {row[0]: int(row[1]) for row in rows}


def _avatar_url(p: MarketplaceProvider) -> str | None:
    return f"/api/v1/marketplace/providers/{p.id}/avatar" if p.avatar_file_id else None


def _card(
    p: MarketplaceProvider, listings: list[MarketplaceListing], next_at: datetime | None
) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "display_name": p.display_name,
        "headline": p.headline,
        "category": p.category,
        "categories": list(p.categories),
        "languages": list(p.languages),
        "location": p.location,
        "timezone": p.timezone,
        "avatar_url": _avatar_url(p),
        "badge": badge(p),
        "rating": _rating(p),
        "from_price_minor": min((x.price_minor for x in listings), default=None),
        "currency": "NGN",
        "listing_count": len(listings),
        "listing_types": sorted({x.type.value for x in listings}),
        "next_available_at": next_at.isoformat() if next_at else None,
    }


def _public_listing(
    row: MarketplaceListing, p: MarketplaceProvider, next_at: datetime | None = None
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.type.value,
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
        "next_available_at": next_at.isoformat() if next_at else None,
        "provider": {
            "id": str(p.id),
            "display_name": p.display_name,
            "headline": p.headline,
            "avatar_url": _avatar_url(p),
            "badge": badge(p),
            "rating": _rating(p),
        },
    }


def next_slot(
    db: Session, row: MarketplaceListing, p: MarketplaceProvider, days: int = 14
) -> datetime | None:
    if row.type != ListingType.consultation:
        return None
    tz = ZoneInfo(p.timezone)
    today = datetime.now(UTC).astimezone(tz).date()
    slots = availability.slots_for(db, row, p, today, today + timedelta(days=days - 1))
    return slots[0].starts_at if slots else None


def _page(items: list[Any], page: int, size: int) -> tuple[list[Any], dict[str, int]]:
    start = (page - 1) * size
    return items[start : start + size], {"page": page, "page_size": size, "total": len(items)}


def _check_sort(sort: str, allowed: tuple[str, ...]) -> None:
    if sort not in allowed:
        raise AppError(
            "VALIDATION_ERROR",
            "Unknown sort.",
            422,
            field_errors=[{"field": "sort", "message": f"Use one of: {', '.join(allowed)}."}],
        )


def _check_category(category: str | None) -> None:
    if category is not None and category not in CATEGORIES:
        raise AppError(
            "VALIDATION_ERROR",
            "Unknown category.",
            422,
            field_errors=[
                {"field": "category", "message": f"Use one of: {', '.join(CATEGORIES)}."}
            ],
        )


def list_providers(
    db: Session,
    *,
    category: str | None,
    min_price: int | None,
    max_price: int | None,
    min_rating: float | None,
    language: str | None,
    available: bool,
    q: str | None,
    sort: str,
    page: int,
    page_size: int = DIRECTORY_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _check_sort(sort, SORTS)
    _check_category(category)
    query = _visible(db)
    if category:
        query = query.filter(
            or_(
                MarketplaceProvider.category == category,
                MarketplaceProvider.categories.contains([category]),
            )
        )
    if min_price is not None:
        query = query.filter(MarketplaceListing.price_minor >= min_price)
    if max_price is not None:
        query = query.filter(MarketplaceListing.price_minor <= max_price)
    if min_rating is not None:
        query = query.filter(
            and_(
                MarketplaceProvider.rating_count >= NEW_BELOW_REVIEWS,
                MarketplaceProvider.rating_avg >= min_rating,
            )
        )
    if language:
        query = query.filter(
            text(
                "EXISTS (SELECT 1 FROM unnest(marketplace_providers.languages) AS l "
                "WHERE lower(l) = :lang)"
            ).bindparams(lang=language.strip().lower())
        )
    if q and q.strip():
        pat = contains_pattern(q.strip())
        query = query.filter(
            or_(
                MarketplaceProvider.display_name.ilike(pat, escape="\\"),
                MarketplaceProvider.headline.ilike(pat, escape="\\"),
                MarketplaceListing.title.ilike(pat, escape="\\"),
            )
        )
    grouped: dict[uuid.UUID, tuple[MarketplaceProvider, list[MarketplaceListing]]] = {}
    for listing, provider in query.order_by(MarketplaceListing.id).all():
        grouped.setdefault(provider.id, (provider, []))[1].append(listing)
    # Show a provider's from-price over ALL their live listings, not just the ones that matched.
    ids = list(grouped)
    all_live: dict[uuid.UUID, list[MarketplaceListing]] = {}
    if ids:
        for lst in db.query(MarketplaceListing).filter(
            MarketplaceListing.provider_id.in_(ids),
            MarketplaceListing.status == ListingStatus.active,
        ):
            all_live.setdefault(lst.provider_id, []).append(lst)
    portfolios = _portfolio_counts(db, set(ids))
    entries: list[tuple[MarketplaceProvider, list[MarketplaceListing]]] = [
        (p, all_live.get(pid, matched)) for pid, (p, matched) in grouped.items()
    ]
    next_at: dict[uuid.UUID, datetime | None] = {}
    if available:
        week_end = datetime.now(UTC) + timedelta(days=7)
        kept: list[tuple[MarketplaceProvider, list[MarketplaceListing]]] = []
        for prov, prov_listings in entries:
            first = _earliest(db, prov, prov_listings, days=7)
            if first is not None and first <= week_end:
                next_at[prov.id] = first
                kept.append((prov, prov_listings))
        entries = kept
    key = {
        "relevance": lambda e: (-relevance(e[0], portfolios.get(e[0].id, 0)), str(e[0].id)),
        "rating": lambda e: (
            -(float(e[0].rating_avg) if e[0].rating_count >= NEW_BELOW_REVIEWS else -1),
            -e[0].rating_count,
            str(e[0].id),
        ),
        "price_asc": lambda e: (min(x.price_minor for x in e[1]), str(e[0].id)),
        "price_desc": lambda e: (-min(x.price_minor for x in e[1]), str(e[0].id)),
    }[sort]
    entries.sort(key=key)
    shown, meta = _page(entries, page, page_size)
    return [_card(p, lst, next_at.get(p.id) or _earliest(db, p, lst)) for p, lst in shown], meta


def _earliest(
    db: Session, p: MarketplaceProvider, listings: list[MarketplaceListing], days: int = 14
) -> datetime | None:
    firsts = [t for t in (next_slot(db, x, p, days) for x in listings) if t is not None]
    return min(firsts) if firsts else None


def get_provider(db: Session, provider_id: uuid.UUID) -> dict[str, Any]:
    p = db.get(MarketplaceProvider, provider_id)
    if p is None or p.status != ProviderStatus.approved:
        raise NotFound()
    live = (
        db.query(MarketplaceListing)
        .filter(
            MarketplaceListing.provider_id == p.id,
            MarketplaceListing.status == ListingStatus.active,
        )
        .order_by(MarketplaceListing.published_at, MarketplaceListing.id)
        .all()
    )
    if not live:
        raise NotFound()  # no live listing: not in the directory, so no profile either
    items = [
        {
            "id": str(i.id),
            "title": i.title,
            "description": i.description,
            "url": i.url,
            "file_url": (
                f"/api/v1/marketplace/providers/{p.id}/portfolio/{i.id}/file" if i.file_id else None
            ),
        }
        for i in portfolio_rows(db, p.id)
    ]
    card = _card(p, live, _earliest(db, p, live))
    return {
        **card,
        "bio": p.bio,
        "portfolio": items,
        "listings": [_public_listing(x, p, next_slot(db, x, p)) for x in live],
    }


def list_listings(
    db: Session,
    *,
    type_: str | None,
    category: str | None,
    min_price: int | None,
    max_price: int | None,
    max_delivery_days: int | None,
    q: str | None,
    sort: str,
    page: int,
    page_size: int = DIRECTORY_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _check_sort(sort, LISTING_SORTS)
    _check_category(category)
    query = _visible(db)
    if type_:
        query = query.filter(MarketplaceListing.type == ListingType(type_))
    if category:
        query = query.filter(MarketplaceListing.category == category)
    if min_price is not None:
        query = query.filter(MarketplaceListing.price_minor >= min_price)
    if max_price is not None:
        query = query.filter(MarketplaceListing.price_minor <= max_price)
    if max_delivery_days is not None:
        query = query.filter(
            MarketplaceListing.type == ListingType.package,
            MarketplaceListing.delivery_days <= max_delivery_days,
        )
    if q and q.strip():
        pat = contains_pattern(q.strip())
        query = query.filter(
            or_(
                MarketplaceListing.title.ilike(pat, escape="\\"),
                MarketplaceListing.summary.ilike(pat, escape="\\"),
                MarketplaceProvider.display_name.ilike(pat, escape="\\"),
            )
        )
    rows = query.all()
    ids = {p.id for _, p in rows}
    portfolios = _portfolio_counts(db, ids)
    key = {
        "relevance": lambda r: (-relevance(r[1], portfolios.get(r[1].id, 0)), str(r[0].id)),
        "rating": lambda r: (
            -(float(r[1].rating_avg) if r[1].rating_count >= NEW_BELOW_REVIEWS else -1),
            str(r[0].id),
        ),
        "price_asc": lambda r: (r[0].price_minor, str(r[0].id)),
        "price_desc": lambda r: (-r[0].price_minor, str(r[0].id)),
        "newest": lambda r: (-(r[0].published_at or r[0].created_at).timestamp(), str(r[0].id)),
    }[sort]
    rows.sort(key=key)
    shown, meta = _page(rows, page, page_size)
    return [_public_listing(r, p, next_slot(db, r, p)) for r, p in shown], meta


def get_listing(
    db: Session, listing_id: uuid.UUID
) -> tuple[MarketplaceListing, MarketplaceProvider]:
    row = _visible(db).filter(MarketplaceListing.id == listing_id).first()
    if row is None:
        raise NotFound()
    return row[0], row[1]


def listing_detail(db: Session, listing_id: uuid.UUID) -> dict[str, Any]:
    row, p = get_listing(db, listing_id)
    return _public_listing(row, p, next_slot(db, row, p))


def listing_slots(
    db: Session, listing_id: uuid.UUID, from_date: Any, to_date: Any
) -> dict[str, Any]:
    row, p = get_listing(db, listing_id)
    if row.type != ListingType.consultation:
        raise AppError("NOT_A_CONSULTATION", "Only consultations have time slots.", 422)
    tz = ZoneInfo(p.timezone)
    start, end = availability.parse_range(from_date, to_date, tz, datetime.now(UTC))
    slots = availability.slots_for(db, row, p, start, end)
    return {
        "listing_id": str(row.id),
        "timezone": p.timezone,
        "duration_min": row.duration_min,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "slots": [
            {
                "starts_at": s.starts_at.isoformat(),
                "ends_at": s.ends_at.isoformat(),
                "local_start": s.starts_at.astimezone(tz).isoformat(),
            }
            for s in slots
        ],
    }
