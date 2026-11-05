"""Staff side of listing review (design §4, D-MP-L8, D-MP-L19). Callers have passed
`require_staff("moderation")` and a reason. Approve / reject / request changes are working steps
(reason only); unlist and relist change what founders can buy, so they also carry the staff
member's own fresh authenticator code. Staff cannot act on their own listings."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core import pagination as core
from app.db.models.audit import AuditLog
from app.db.models.enums import ListingStatus, ProviderStatus
from app.db.models.marketplace import MarketplaceListing, MarketplaceProvider
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.email import EmailMessage
from app.services.marketplace import emails
from app.services.marketplace.listings import err, public_name, refresh_prescreen
from app.services.marketplace.providers import badge
from app.services.staff.like import contains_pattern

REJECT_TEMPLATES: dict[str, str] = {
    "unclear_scope": "The scope or deliverables aren't clear enough",
    "pricing": "The price doesn't match the scope",
    "prohibited_content": "The listing includes content we can't allow",
    "contact_details": "The listing includes contact details or off-platform dealing",
    "other": "The listing didn't meet our requirements",
}


def _audit_ctx(ctx: StaffContext, ip: str | None, ua: str | None) -> dict[str, Any]:
    return {"actor_user_id": ctx.user.id, "ip": ip, "user_agent": ua}


def _lock(db: Session, listing_id: uuid.UUID) -> tuple[MarketplaceListing, MarketplaceProvider]:
    row = (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.id == listing_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise err("LISTING_NOT_FOUND", "No such listing.", 404)
    provider = db.get(MarketplaceProvider, row.provider_id)
    if provider is None:  # unreachable: the FK cascades
        raise err("LISTING_NOT_FOUND", "No such listing.", 404)
    return row, provider


def _not_self(ctx: StaffContext, provider: MarketplaceProvider) -> None:
    if provider.user_id is not None and provider.user_id == ctx.user.id:
        raise err("CANNOT_ACT_ON_SELF", "You can't do that on your own listing.", 422)


def exists_or_404(db: Session, listing_id: uuid.UUID) -> None:
    """Checked before an authenticator code is spent, so a typo never burns one."""
    if db.query(MarketplaceListing.id).filter(MarketplaceListing.id == listing_id).first() is None:
        raise err("LISTING_NOT_FOUND", "No such listing.", 404)


def has_flag(row: MarketplaceListing) -> bool:
    """A live listing whose at-once edit (tags, price) tripped the pre-screen."""
    return row.status in (ListingStatus.active, ListingStatus.paused) and bool(
        (row.prescreen_json or {}).get("review")
    )


def has_revision(row: MarketplaceListing) -> bool:
    return row.status in (ListingStatus.active, ListingStatus.paused) and bool(
        row.pending_revision_json
    )


def summary(row: MarketplaceListing, provider: MarketplaceProvider) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider_id": str(provider.id),
        "provider_name": provider.display_name,
        "provider_status": provider.status.value,
        "type": row.type.value,
        "title": row.title,
        "category": row.category,
        "price_minor": row.price_minor,
        "status": row.status.value,
        "has_revision": has_revision(row),
        "has_flag": has_flag(row),
        "flag": (row.prescreen_json or {}).get("review") if has_flag(row) else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "revision_submitted_at": (
            row.revision_submitted_at.isoformat() if row.revision_submitted_at else None
        ),
        "prescreen_flags": (row.prescreen_json or {}).get("flags", []),
        "created_at": row.created_at.isoformat(),
    }


def _diff(row: MarketplaceListing) -> list[dict[str, Any]]:
    out = []
    for key, new in (row.pending_revision_json or {}).items():
        out.append({"field": public_name(key), "from": getattr(row, key), "to": new})
    return out


def detail(db: Session, listing_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(MarketplaceListing, listing_id)
    if row is None:
        raise err("LISTING_NOT_FOUND", "No such listing.", 404)
    provider = db.get(MarketplaceProvider, row.provider_id)
    if provider is None:  # unreachable
        raise err("LISTING_NOT_FOUND", "No such listing.", 404)
    history = (
        db.query(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .filter(AuditLog.entity_type == "marketplace_listing", AuditLog.entity_id == row.id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        **summary(row, provider),
        "summary": row.summary,
        "description": row.description,
        "tags": list(row.tags),
        "duration_min": row.duration_min,
        "delivery_days": row.delivery_days,
        "deliverables": list(row.deliverables_json),
        "revisions_included": row.revisions_included,
        "meeting_url": row.meeting_url,
        "review_message": row.review_message,
        "unlisted_reason": row.unlisted_reason,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "pending_revision": (
            {public_name(k): v for k, v in row.pending_revision_json.items()}
            if row.pending_revision_json
            else None
        ),
        "diff": _diff(row),
        "provider": {
            "id": str(provider.id),
            "display_name": provider.display_name,
            "category": provider.category,
            "status": provider.status.value,
            "badge": badge(provider),
        },
        "history": [
            {
                "action": a.action,
                "at": a.created_at.isoformat(),
                "actor_email": e,
                "reason": a.reason,
            }
            for a, e in history
        ],
    }


def counts(db: Session) -> dict[str, Any]:
    rows = db.query(MarketplaceListing.status, func.count(MarketplaceListing.id)).group_by(
        MarketplaceListing.status
    )
    by_status = {row[0]: int(row[1]) for row in rows}
    revisions = (
        db.query(func.count(MarketplaceListing.id))
        .filter(
            MarketplaceListing.status.in_([ListingStatus.active, ListingStatus.paused]),
            MarketplaceListing.pending_revision_json.isnot(None),
        )
        .scalar()
        or 0
    )
    flagged = (
        db.query(func.count(MarketplaceListing.id))
        .filter(
            MarketplaceListing.status.in_([ListingStatus.active, ListingStatus.paused]),
            MarketplaceListing.prescreen_json.has_key("review"),  # noqa: W601
        )
        .scalar()
        or 0
    )
    return {
        "by_status": {s.value: by_status.get(s, 0) for s in ListingStatus},
        "pending_revisions": int(revisions),
        "flagged_edits": int(flagged),
    }


def list_listings(
    db: Session,
    *,
    status: ListingStatus | None,
    queue: bool,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = db.query(MarketplaceListing, MarketplaceProvider).join(
        MarketplaceProvider, MarketplaceProvider.id == MarketplaceListing.provider_id
    )
    if status:
        q = q.filter(MarketplaceListing.status == status)
    if queue:  # what needs a decision: first submissions and edits to live listings
        q = q.filter(
            or_(
                MarketplaceListing.status == ListingStatus.pending_review,
                (MarketplaceListing.status.in_([ListingStatus.active, ListingStatus.paused]))
                & (
                    MarketplaceListing.pending_revision_json.isnot(None)
                    | MarketplaceListing.prescreen_json.has_key("review")  # noqa: W601
                ),
            )
        )
    if query and query.strip():
        pat = contains_pattern(query.strip())
        q = q.filter(
            or_(
                MarketplaceListing.title.ilike(pat, escape="\\"),
                MarketplaceProvider.display_name.ilike(pat, escape="\\"),
            )
        )
    if cursor:
        ts, rid = core.decode_cursor(cursor)
        q = q.filter(
            or_(
                MarketplaceListing.created_at > ts,
                (MarketplaceListing.created_at == ts) & (MarketplaceListing.id > rid),
            )
        )
    rows = (
        q.order_by(MarketplaceListing.created_at.asc(), MarketplaceListing.id.asc())
        .limit(limit + 1)
        .all()
    )
    nxt = (
        core.encode_cursor(rows[limit - 1][0].created_at, rows[limit - 1][0].id)
        if len(rows) > limit
        else None
    )
    return [summary(r, p) for r, p in rows[:limit]], nxt


def _reviewable(row: MarketplaceListing) -> str:
    if row.status == ListingStatus.pending_review:
        return "first"
    if has_revision(row):
        return "revision"
    if has_flag(row):
        return "flag"
    raise err("INVALID_STATE", "There's nothing waiting for review on this listing.")


def approve(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceListing, list[EmailMessage]]:
    row, provider = _lock(db, listing_id)
    _not_self(ctx, provider)
    kind = _reviewable(row)
    if provider.status != ProviderStatus.approved:
        raise err(
            "PROVIDER_NOT_APPROVED", "The expert isn't approved, so nothing of theirs can go live."
        )
    if kind == "first":
        row.status = ListingStatus.active
        row.published_at = datetime.now(UTC)
    else:  # an edit, or a flagged at-once edit: approving accepts everything now live
        for key, value in (row.pending_revision_json or {}).items():
            setattr(row, key, value)
        row.pending_revision_json = None
        row.revision_submitted_at = None
    row.review_message = None
    refresh_prescreen(row)
    # What staff approved is the new baseline: only flags beyond it reach the queue again.
    row.prescreen_json = {
        **{k: v for k, v in row.prescreen_json.items() if k != "review"},
        "accepted": row.prescreen_json.get("flags", []),
    }
    write_audit(
        db,
        "marketplace.listing.approved",
        entity_type="marketplace_listing",
        entity_id=row.id,
        reason=reason,
        details={"kind": kind},
        **_audit_ctx(ctx, ip, ua),
    )
    db.flush()
    return row, [
        emails.listing_approved_email(
            provider.email, name=provider.display_name, title=row.title, revision=kind == "revision"
        )
    ]


def _send_back(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    action: str,
    first_status: ListingStatus,
    message: str,
    reason: str,
    headline: str,
    ip: str | None,
    ua: str | None,
    details: dict[str, Any] | None = None,
) -> tuple[MarketplaceListing, list[EmailMessage]]:
    row, provider = _lock(db, listing_id)
    _not_self(ctx, provider)
    kind = _reviewable(row)
    if kind == "first":
        row.status = first_status
    elif kind == "revision":  # the live version stays; only the edit is discarded
        row.pending_revision_json = None
        row.revision_submitted_at = None
    else:
        # A flagged at-once edit is already live. The message goes to the expert and the queue
        # item closes; to take the listing down staff unlist it (reason + authenticator code).
        row.prescreen_json = {k: v for k, v in (row.prescreen_json or {}).items() if k != "review"}
    row.review_message = message
    refresh_prescreen(row)
    write_audit(
        db,
        action,
        entity_type="marketplace_listing",
        entity_id=row.id,
        reason=reason,
        details={"kind": kind, **(details or {})},
        **_audit_ctx(ctx, ip, ua),
    )
    db.flush()
    mail = emails.listing_feedback_email(
        provider.email,
        name=provider.display_name,
        title=row.title,
        headline=headline,
        detail=message,
    )
    return row, [mail]


def reject(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    template: str,
    message: str | None,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceListing, list[EmailMessage]]:
    return _send_back(
        db,
        ctx,
        listing_id,
        action="marketplace.listing.rejected",
        first_status=ListingStatus.rejected,
        message=message or REJECT_TEMPLATES[template],
        reason=reason,
        headline="We couldn't approve your listing",
        ip=ip,
        ua=ua,
        details={"template": template},
    )


def request_changes(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    message: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceListing, list[EmailMessage]]:
    return _send_back(
        db,
        ctx,
        listing_id,
        action="marketplace.listing.changes_requested",
        first_status=ListingStatus.draft,
        message=message,
        reason=message,
        headline="Changes needed on your listing",
        ip=ip,
        ua=ua,
    )


def unlist(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> MarketplaceListing:
    row, provider = _lock(db, listing_id)
    _not_self(ctx, provider)
    if row.status not in (ListingStatus.active, ListingStatus.paused):
        raise err("INVALID_STATE", "Only a live listing can be unlisted.")
    row.status = ListingStatus.unlisted
    row.unlisted_by, row.unlisted_reason = ctx.user.id, reason
    write_audit(
        db,
        "marketplace.listing.unlisted",
        entity_type="marketplace_listing",
        entity_id=row.id,
        reason=reason,
        **_audit_ctx(ctx, ip, ua),
    )
    db.flush()
    return row


def relist(
    db: Session,
    ctx: StaffContext,
    listing_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> MarketplaceListing:
    row, provider = _lock(db, listing_id)
    _not_self(ctx, provider)
    if row.status != ListingStatus.unlisted:
        raise err("INVALID_STATE", "Only an unlisted listing can be relisted.")
    row.status = ListingStatus.active
    row.unlisted_by, row.unlisted_reason = None, None
    write_audit(
        db,
        "marketplace.listing.relisted",
        entity_type="marketplace_listing",
        entity_id=row.id,
        reason=reason,
        **_audit_ctx(ctx, ip, ua),
    )
    db.flush()
    return row
