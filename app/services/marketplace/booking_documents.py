"""\"Attach from Documents\" (D-MP-L10, gap 18): the founder lets the expert read ONE document for
30 days, through the booking.

Why not `document_grants`: those grants never expire, would list the document in the expert's
Shared-with-me page and name the founder's workspace to them, which the Marketplace promises never
to do. A booking-scoped link row is narrower: it expires, it is revoked when the booking closes,
and the expert's read route returns the document and nothing else about the workspace."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext
from app.core.errors import NotFound
from app.db.models.document import Document
from app.db.models.enums import BookingStatus
from app.db.models.marketplace import MarketplaceBooking, MarketplaceBookingDocument
from app.db.models.membership import Membership
from app.platform.audit import write_audit
from app.services.marketplace.constants import BOOKING_DOCUMENT_TTL, MAX_BOOKING_DOCUMENTS
from app.services.marketplace.listings import err

SHAREABLE = (
    BookingStatus.awaiting_provider,
    BookingStatus.confirmed,
    BookingStatus.in_progress,
    BookingStatus.delivered,
)
PROVIDER_READABLE = (*SHAREABLE, BookingStatus.disputed)


def _now() -> datetime:
    return datetime.now(UTC)


def _live_links(db: Session, booking_id: uuid.UUID) -> list[MarketplaceBookingDocument]:
    return (
        db.query(MarketplaceBookingDocument)
        .filter(
            MarketplaceBookingDocument.booking_id == booking_id,
            MarketplaceBookingDocument.revoked_at.is_(None),
            MarketplaceBookingDocument.expires_at > _now(),
        )
        .order_by(MarketplaceBookingDocument.created_at)
        .all()
    )


def attach(
    db: Session, m: Membership, booking: MarketplaceBooking, user_id: uuid.UUID, document_id: Any
) -> MarketplaceBookingDocument:
    """`booking` must be locked and belong to `m`'s workspace. Idempotent: attaching a document
    that is already shared renews nothing and returns the same link; attaching one whose link
    lapsed or was revoked starts a fresh 30 days."""
    if booking.status not in SHAREABLE:
        raise err("INVALID_STATE", "Documents can't be attached to this booking now.", 409)
    doc = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.startup_id == m.startup_id,
            Document.trashed_at.is_(None),
        )
        .first()
    )
    if doc is None:
        raise NotFound()
    t = _now()
    link = (
        db.query(MarketplaceBookingDocument)
        .filter_by(booking_id=booking.id, document_id=doc.id)
        .with_for_update()
        .first()
    )
    if link is not None and link.revoked_at is None and link.expires_at > t:
        return link
    if len(_live_links(db, booking.id)) >= MAX_BOOKING_DOCUMENTS:
        raise err(
            "DOCUMENT_LIMIT_REACHED",
            f"Share up to {MAX_BOOKING_DOCUMENTS} documents per booking.",
            409,
        )
    if link is None:
        link = MarketplaceBookingDocument(
            booking_id=booking.id, startup_id=m.startup_id, document_id=doc.id
        )
        db.add(link)
    link.granted_by, link.revoked_at = user_id, None
    link.expires_at = t + BOOKING_DOCUMENT_TTL
    db.flush()
    write_audit(
        db,
        "marketplace.booking.document_shared",
        actor_user_id=user_id,
        startup_id=m.startup_id,
        entity_type="marketplace_booking",
        entity_id=booking.id,
        details={"document_id": str(doc.id), "expires_at": link.expires_at.isoformat()},
    )
    return link


def revoke(
    db: Session, m: Membership, booking: MarketplaceBooking, user_id: uuid.UUID, document_id: Any
) -> None:
    link = (
        db.query(MarketplaceBookingDocument)
        .filter_by(booking_id=booking.id, startup_id=m.startup_id, document_id=document_id)
        .first()
    )
    if link is None:
        raise NotFound()
    if link.revoked_at is None:
        link.revoked_at = _now()
        write_audit(
            db,
            "marketplace.booking.document_revoked",
            actor_user_id=user_id,
            startup_id=m.startup_id,
            entity_type="marketplace_booking",
            entity_id=booking.id,
            details={"document_id": str(link.document_id)},
        )


def revoke_all(db: Session, booking: MarketplaceBooking) -> int:
    """The booking reached an end state: every link stops working."""
    t = _now()
    rows = (
        db.query(MarketplaceBookingDocument)
        .filter(
            MarketplaceBookingDocument.booking_id == booking.id,
            MarketplaceBookingDocument.revoked_at.is_(None),
        )
        .all()
    )
    for r in rows:
        r.revoked_at = t
    return len(rows)


def serialize_link(db: Session, link: MarketplaceBookingDocument) -> dict[str, Any]:
    doc = db.get(Document, link.document_id)
    t = _now()
    return {
        "document_id": str(link.document_id),
        "title": doc.title if doc else None,
        "kind": doc.kind.value if doc else None,
        "expires_at": link.expires_at.isoformat(),
        "status": (
            "revoked" if link.revoked_at else ("expired" if link.expires_at <= t else "active")
        ),
    }


def list_for_booking(
    db: Session, booking: MarketplaceBooking, *, live_only: bool
) -> list[dict[str, Any]]:
    q = db.query(MarketplaceBookingDocument).filter_by(booking_id=booking.id)
    rows = (
        _live_links(db, booking.id)
        if live_only
        else q.order_by(MarketplaceBookingDocument.created_at).all()
    )
    return [serialize_link(db, r) for r in rows]


def read_for_provider(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID, document_id: uuid.UUID
) -> dict[str, Any]:
    """One shared document, read-only, for the expert of THAT booking while the link is live."""
    b = (
        db.query(MarketplaceBooking)
        .filter(
            MarketplaceBooking.id == booking_id,
            MarketplaceBooking.provider_id == ctx.provider.id,
            MarketplaceBooking.status.in_(PROVIDER_READABLE),
        )
        .first()
    )
    link = (
        db.query(MarketplaceBookingDocument)
        .filter(
            MarketplaceBookingDocument.booking_id == booking_id,
            MarketplaceBookingDocument.document_id == document_id,
            MarketplaceBookingDocument.revoked_at.is_(None),
            MarketplaceBookingDocument.expires_at > _now(),
        )
        .first()
        if b is not None
        else None
    )
    doc = db.get(Document, document_id) if link is not None else None
    if doc is None or doc.trashed_at is not None:
        raise NotFound()
    return {
        "id": str(doc.id),
        "title": doc.title,
        "kind": doc.kind.value,
        "version": doc.version,
        "sections": doc.sections,
        "expires_at": link.expires_at.isoformat() if link else None,
    }
