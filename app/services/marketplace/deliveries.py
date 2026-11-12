"""Delivery and acceptance (design §3.4, §5; gaps 26-28).

  package       in_progress --deliver--> delivered --accept--> completed
                                              |--request changes (<= revisions included)--> in_progress
  consultation  confirmed --(slot ended) complete / timer--> delivered --accept--> completed

A `delivered` booking that nobody answers is completed by the timer (7 days for a package, 48 hours
after a consultation ended) with the delivery recorded as `auto_released`.

This slice moves the BOOKING only. Money is not released here: accepting (or auto-releasing)
records the decision and publishes `marketplace.delivery.accepted`, the hook slice E's release and
payout logic subscribes to. Lock discipline is slice C's: the booking row is locked first and every
transition re-reads its status under that lock, so a replay or a race changes nothing twice. These
paths never take the order lock."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.provider_deps import ProviderContext
from app.core.errors import NotFound
from app.db.models.enums import BookingStatus, ListingType, MarketplaceFilePurpose
from app.db.models.marketplace import MarketplaceBooking, MarketplaceDelivery, MarketplaceProvider
from app.db.models.membership import Membership
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.email import EmailMessage
from app.platform.events import event_bus
from app.services.marketplace import closing, emails, files, outbox
from app.services.marketplace.constants import (
    AUTO_RELEASE_CONSULTATION,
    AUTO_RELEASE_PACKAGE,
    MAX_DELIVERY_FILES,
)
from app.services.marketplace.listings import err

FOUNDER_FILES = "/api/v1/marketplace/files"
PROVIDER_FILES = "/api/v1/provider/files"


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ reads


def serialize(db: Session, d: MarketplaceDelivery, *, files_base: str) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "round": d.round,
        "message": d.message,
        "files": files.serialize_many(db, list(d.file_ids), base=files_base),
        "submitted_at": d.submitted_at.isoformat(),
        "decision": d.decision,
        "decision_note": d.decision_note,
        "decided_at": d.decided_at.isoformat() if d.decided_at else None,
    }


def list_view(db: Session, booking_id: uuid.UUID, *, files_base: str) -> list[dict[str, Any]]:
    rows = (
        db.query(MarketplaceDelivery)
        .filter(MarketplaceDelivery.booking_id == booking_id)
        .order_by(MarketplaceDelivery.round)
        .all()
    )
    return [serialize(db, d, files_base=files_base) for d in rows]


def _open_delivery(db: Session, booking_id: uuid.UUID) -> MarketplaceDelivery | None:
    return (
        db.query(MarketplaceDelivery)
        .filter(
            MarketplaceDelivery.booking_id == booking_id, MarketplaceDelivery.decision.is_(None)
        )
        .order_by(MarketplaceDelivery.round.desc())
        .first()
    )


def _last_delivery(db: Session, booking_id: uuid.UUID) -> MarketplaceDelivery | None:
    return (
        db.query(MarketplaceDelivery)
        .filter(MarketplaceDelivery.booking_id == booking_id)
        .order_by(MarketplaceDelivery.round.desc())
        .first()
    )


def _next_round(db: Session, booking_id: uuid.UUID) -> int:
    top = (
        db.query(func.max(MarketplaceDelivery.round))
        .filter(MarketplaceDelivery.booking_id == booking_id)
        .scalar()
    )
    return int(top or 0) + 1


def _payload(db: Session, b: MarketplaceBooking, **extra: Any) -> dict[str, Any]:
    """Event payload. Carries what the founder notifications quote, never the brief or a message."""
    provider = db.get(MarketplaceProvider, b.provider_id)
    return {
        "booking_id": str(b.id),
        "startup_id": str(b.startup_id),
        "provider_id": str(b.provider_id),
        "title": b.title_snap,
        "provider_name": provider.display_name if provider else None,
        "type": b.type,
        **extra,
    }


# ------------------------------------------------------------------ the expert delivers


def _lock(db: Session, booking_id: uuid.UUID, **where: Any) -> MarketplaceBooking:
    q = db.query(MarketplaceBooking).filter(MarketplaceBooking.id == booking_id)
    for col, value in where.items():
        q = q.filter(getattr(MarketplaceBooking, col) == value)
    row = q.with_for_update().first()
    if row is None or row.status == BookingStatus.pending_payment:
        raise NotFound()
    return row


def _hand_over(
    db: Session,
    b: MarketplaceBooking,
    *,
    t: datetime,
    message: str,
    file_rows: list[Any],
    auto_release_at: datetime,
    actor: uuid.UUID | None,
) -> MarketplaceDelivery:
    d = MarketplaceDelivery(
        booking_id=b.id,
        round=_next_round(db, b.id),
        message=message,
        file_ids=[str(f.id) for f in file_rows],
        submitted_at=t,
    )
    db.add(d)
    b.status = BookingStatus.delivered
    b.delivered_at = t
    b.auto_release_at = auto_release_at
    db.flush()
    write_audit(
        db,
        "marketplace.delivery.submitted",
        actor_user_id=actor,
        startup_id=b.startup_id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"round": d.round, "files": len(file_rows), "type": b.type},
    )
    event_bus.publish(
        db,
        "marketplace.delivery.submitted",
        _payload(
            db, b, delivery_id=str(d.id), round=d.round, auto_release_at=auto_release_at.isoformat()
        ),
    )
    return d


def deliver(
    db: Session,
    ctx: ProviderContext,
    booking_id: uuid.UUID,
    *,
    message: str,
    file_ids: list[uuid.UUID],
) -> tuple[MarketplaceBooking, MarketplaceDelivery]:
    """A package's work, for the first time or after a change request."""
    b = _lock(db, booking_id, provider_id=ctx.provider.id)
    if b.type != ListingType.package.value:
        raise err(
            "INVALID_STATE", "Consultations are marked complete after the session, not delivered."
        )
    if b.status != BookingStatus.in_progress:
        raise err("INVALID_STATE", f"Can't deliver a booking that is '{b.status.value}'.")
    t = now()
    rows = files.attach(
        db,
        owner_user_id=ctx.user.id,
        file_ids=file_ids,
        purposes=frozenset({MarketplaceFilePurpose.delivery}),
        limit=MAX_DELIVERY_FILES,
        booking_id=b.id,
    )
    d = _hand_over(
        db,
        b,
        t=t,
        message=message,
        file_rows=rows,
        auto_release_at=t + AUTO_RELEASE_PACKAGE,
        actor=ctx.user.id,
    )
    return b, d


def mark_consultation_delivered(
    db: Session, b: MarketplaceBooking, t: datetime, *, actor: uuid.UUID | None
) -> MarketplaceDelivery:
    """`b` is locked, a confirmed consultation whose slot has ended. Used by the expert's
    `complete` and by the timer for sessions nobody marked."""
    assert b.ends_at is not None  # nosec B101 - a confirmed consultation always has a slot
    return _hand_over(
        db,
        b,
        t=t,
        message="",
        file_rows=[],
        auto_release_at=b.ends_at + AUTO_RELEASE_CONSULTATION,
        actor=actor,
    )


def complete_consultation(
    db: Session, ctx: ProviderContext, booking_id: uuid.UUID
) -> tuple[MarketplaceBooking, MarketplaceDelivery]:
    b = _lock(db, booking_id, provider_id=ctx.provider.id)
    if b.type != ListingType.consultation.value:
        raise err("INVALID_STATE", "Packages are delivered with their files, not marked complete.")
    if b.status != BookingStatus.confirmed:
        raise err("INVALID_STATE", f"Can't complete a booking that is '{b.status.value}'.")
    t = now()
    if b.ends_at is None or b.ends_at > t:
        raise err(
            "SESSION_NOT_ENDED",
            "You can mark this consultation complete once the session ends.",
            409,
        )
    return b, mark_consultation_delivered(db, b, t, actor=ctx.user.id)


# ------------------------------------------------------------------ the founder answers


def _provider_mail(db: Session, b: MarketplaceBooking, build: Any) -> EmailMessage | None:
    provider = db.get(MarketplaceProvider, b.provider_id)
    if provider is None:
        return None
    return build(provider.email, provider.display_name)  # type: ignore[no-any-return]


def _finish(
    db: Session,
    b: MarketplaceBooking,
    d: MarketplaceDelivery,
    *,
    decision: str,
    t: datetime,
    actor: uuid.UUID | None,
) -> None:
    """Close a delivered booking as completed (accepted by the founder, or auto-released)."""
    d.decision, d.decided_at = decision, t
    b.status = BookingStatus.completed
    b.auto_release_at = None
    closing.on_closed(db, b, drop_calendar=False)
    auto = decision == "auto_released"
    write_audit(
        db,
        "marketplace.delivery.auto_released" if auto else "marketplace.delivery.accepted",
        actor_user_id=actor,
        startup_id=b.startup_id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"round": d.round},
    )
    payload = _payload(db, b, delivery_id=str(d.id), round=d.round, auto_released=auto)
    if auto:
        event_bus.publish(db, "marketplace.delivery.auto_released", payload)
    # Slice E's hook for releasing the money: one event whichever way the delivery was accepted.
    event_bus.publish(db, "marketplace.delivery.accepted", payload)
    outbox.queue(
        db,
        _provider_mail(
            db,
            b,
            lambda to, name: emails.delivery_accepted_email(
                to, name=name, title=b.title_snap, auto_released=auto
            ),
        ),
    )


def accept(
    db: Session, user: User, m: Membership, booking_id: uuid.UUID
) -> tuple[MarketplaceBooking, bool]:
    """Returns (booking, replayed). A second accept of an already accepted delivery is a no-op."""
    b = _lock(db, booking_id, startup_id=m.startup_id)
    if b.status == BookingStatus.completed:
        last = _last_delivery(db, b.id)
        if last is not None and last.decision == "accepted":
            return b, True
    if b.status != BookingStatus.delivered:
        raise err("INVALID_STATE", f"Can't accept a booking that is '{b.status.value}'.")
    d = _open_delivery(db, b.id)
    if d is None:  # unreachable: every delivered booking has an open delivery
        raise err("INVALID_STATE", "There is no delivery to accept.")
    _finish(db, b, d, decision="accepted", t=now(), actor=user.id)
    return b, False


def request_changes(
    db: Session, user: User, m: Membership, booking_id: uuid.UUID, reason: str
) -> tuple[MarketplaceBooking, bool]:
    b = _lock(db, booking_id, startup_id=m.startup_id)
    if b.type != ListingType.package.value:
        raise err(
            "NOT_APPLICABLE",
            "Consultations have no revision rounds. Use 'Report a problem' if it went wrong.",
            422,
        )
    if b.status == BookingStatus.in_progress:
        last = _last_delivery(db, b.id)
        if (
            last is not None
            and last.decision == "changes_requested"
            and last.decision_note == reason
        ):
            return b, True  # the same request again: already recorded
    if b.status != BookingStatus.delivered:
        raise err(
            "INVALID_STATE", f"Can't request changes on a booking that is '{b.status.value}'."
        )
    if b.revisions_used >= b.revisions_included_snap:
        raise err(
            "REVISION_LIMIT_REACHED",
            f"All {b.revisions_included_snap} included revisions are used. If the work still "
            "isn't right, use 'Report a problem' and our team will look at it.",
            409,
        )
    d = _open_delivery(db, b.id)
    if d is None:  # unreachable
        raise err("INVALID_STATE", "There is no delivery to review.")
    t = now()
    d.decision, d.decision_note, d.decided_at = "changes_requested", reason, t
    b.revisions_used += 1
    b.status = BookingStatus.in_progress
    b.auto_release_at = None
    write_audit(
        db,
        "marketplace.delivery.changes_requested",
        actor_user_id=user.id,
        startup_id=b.startup_id,
        entity_type="marketplace_booking",
        entity_id=b.id,
        details={"round": d.round, "revisions_used": b.revisions_used},
    )
    event_bus.publish(
        db,
        "marketplace.delivery.changes_requested",
        _payload(db, b, delivery_id=str(d.id), round=d.round, revisions_used=b.revisions_used),
    )
    outbox.queue(
        db,
        _provider_mail(
            db,
            b,
            lambda to, name: emails.delivery_changes_email(
                to, name=name, title=b.title_snap, reason=reason, round_no=d.round
            ),
        ),
    )
    return b, False


def auto_release(db: Session, b: MarketplaceBooking, t: datetime) -> bool:
    """Timer: `b` is locked. Completes a delivery nobody answered once its window has passed."""
    if b.status != BookingStatus.delivered or b.auto_release_at is None or b.auto_release_at > t:
        return False
    d = _open_delivery(db, b.id)
    if d is None:
        return False
    _finish(db, b, d, decision="auto_released", t=t, actor=None)
    return True
