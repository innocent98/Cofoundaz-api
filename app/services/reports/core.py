"""What founder and staff report code share: error shape, the rate limit, cursor paging, and the
one place a removal is applied to (or lifted from) the reported item.

Staff never read the thread an item lives in (D-ADM-9): the only text a staff route returns is
`ContentReport.snippet`, copied when the report was filed.
"""

import base64
import binascii
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.ai_chat import AiMessage
from app.db.models.audit import AuditLog
from app.db.models.comment import Comment
from app.db.models.content_report import ContentReport
from app.db.models.enums import ListingStatus, ReportItemType
from app.db.models.marketplace import MarketplaceListing, MarketplaceProvider
from app.db.models.notification import Notification
from app.platform.audit import write_audit

SNIPPET_MAX = 2000  # R4
REPORTS_PER_HOUR = 10  # R3
REMOVED_PLACEHOLDER = "Removed by moderation"


def err(code: str, message: str, status: int) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def claim(key: str, limit: int, ttl: int, message: str) -> None:
    """Count one use of a rate-limited action; refuse with 429 once `limit` is spent in `ttl` s."""
    redis = get_redis()
    used = int(redis.incr(key))  # type: ignore[arg-type]
    if used == 1 or int(redis.ttl(key)) < 0:  # type: ignore[arg-type]
        redis.expire(key, ttl)  # also repairs a key left without expiry by a crash
    if used > limit:
        raise err("RATE_LIMITED", message, 429)


def encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def set_removed(
    db: Session,
    report: ContentReport,
    removed: bool,
    now: datetime,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Hide (or show again) the reported item. False when the item no longer exists (the author
    deleted it): the report still closes, there is just nothing left to hide."""
    if report.item_type == ReportItemType.listing:
        return _set_listing_unlisted(db, report, removed, actor_id)
    if report.item_type == ReportItemType.provider:
        # Experts are suspended through vetting (step-up, emails), not by removing a report item.
        raise AppError(
            "USE_VETTING",
            "Suspend an expert from the vetting queue instead of removing the report's item.",
            422,
        )
    item: Comment | AiMessage | None
    if report.item_type == ReportItemType.comment:
        item = db.query(Comment).filter(Comment.id == report.item_id).with_for_update().first()
    else:
        item = db.query(AiMessage).filter(AiMessage.id == report.item_id).with_for_update().first()
    if item is None:
        return False
    item.removed_by_moderation_at = now if removed else None
    if removed and report.item_type == ReportItemType.comment:
        _scrub_mentions(db, report.item_id)
    return True


def _unlisted_by_report(db: Session, listing_id: uuid.UUID) -> bool:
    """Was the listing's CURRENT unlisting done by the report flow? Read from the audit trail (the
    latest unlist/relist row), so a listing a moderator unlisted for their own reasons is never
    relisted by restoring a report about it."""
    last = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "marketplace_listing",
            AuditLog.entity_id == listing_id,
            AuditLog.action.in_(("marketplace.listing.unlisted", "marketplace.listing.relisted")),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .first()
    )
    return (
        last is not None
        and last.action == "marketplace.listing.unlisted"
        and (last.details or {}).get("via") == "report"
    )


def _set_listing_unlisted(
    db: Session, report: ContentReport, removed: bool, actor_id: uuid.UUID | None
) -> bool:
    """Unlist (or relist) the reported listing. Active bookings are honoured; no new ones. Both
    directions are audited on the listing and carry `via: report`; a restore only relists what
    the report flow itself unlisted."""
    listing = (
        db.query(MarketplaceListing)
        .filter(MarketplaceListing.id == report.item_id)
        .with_for_update()
        .first()
    )
    if listing is None:
        return False
    if removed and listing.status in (ListingStatus.active, ListingStatus.paused):
        listing.status = ListingStatus.unlisted
        listing.unlisted_by, listing.unlisted_reason = actor_id, "Unlisted after a report."
        write_audit(
            db,
            "marketplace.listing.unlisted",
            actor_user_id=actor_id,
            entity_type="marketplace_listing",
            entity_id=listing.id,
            details={"via": "report", "report_id": str(report.id)},
        )
    elif (
        not removed
        and listing.status == ListingStatus.unlisted
        and _unlisted_by_report(db, listing.id)
    ):
        listing.status = ListingStatus.active
        listing.unlisted_by, listing.unlisted_reason = None, None
        write_audit(
            db,
            "marketplace.listing.relisted",
            actor_user_id=actor_id,
            entity_type="marketplace_listing",
            entity_id=listing.id,
            details={"via": "report", "report_id": str(report.id)},
        )
    return True


def refuse_own_marketplace_item(db: Session, report: ContentReport, actor_id: uuid.UUID) -> None:
    """Staff can't decide on a report about their own listing or expert profile."""
    owner: uuid.UUID | None = None
    if report.item_type == ReportItemType.listing:
        row = (
            db.query(MarketplaceProvider.user_id)
            .join(MarketplaceListing, MarketplaceListing.provider_id == MarketplaceProvider.id)
            .filter(MarketplaceListing.id == report.item_id)
            .first()
        )
        owner = row[0] if row else None
    elif report.item_type == ReportItemType.provider:
        row = (
            db.query(MarketplaceProvider.user_id)
            .filter(MarketplaceProvider.id == report.item_id)
            .first()
        )
        owner = row[0] if row else None
    if owner is not None and owner == actor_id:
        raise err("CANNOT_ACT_ON_SELF", "You can't decide a report about your own listing.", 422)


def _scrub_mentions(db: Session, comment_id: uuid.UUID) -> None:
    """A mention notification quotes the comment; once it is removed the quote goes too. A later
    restore does not bring the quote back (the comment itself comes back)."""
    rows = (
        db.query(Notification)
        .filter(
            Notification.type == "team.comment.mentioned",
            Notification.data["comment_id"].astext == str(comment_id),
        )
        .all()
    )
    for n in rows:
        n.title = "A comment that mentioned you was removed by moderation"
        n.body = REMOVED_PLACEHOLDER
        n.data = {**n.data, "snippet": REMOVED_PLACEHOLDER}
