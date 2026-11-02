"""Workspace-wide do-not-email list, and the bounce-rate auto-pause (D-SAL-8)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.enums import EnrollmentStatus, SendKind, SendStatus, SuppressionReason
from app.db.models.sales_sequences import SalesSuppression, SequenceEnrollment, SequenceSend
from app.platform.events import event_bus
from app.services.sales.stages import get_settings

AUTO_PAUSE_WINDOW = 50  # the last N sequence sends of the workspace
AUTO_PAUSE_RATE = 0.10
AUTO_PAUSE_MIN_SAMPLE = 10  # one bounce out of one send is not a 100% bounce rate

_STOP_STATUS = {
    SuppressionReason.unsubscribed: EnrollmentStatus.unsubscribed,
    SuppressionReason.bounced: EnrollmentStatus.bounced,
    SuppressionReason.complained: EnrollmentStatus.bounced,
}


def is_suppressed(db: Session, startup_id: uuid.UUID, email: str) -> bool:
    return (
        db.scalar(
            select(SalesSuppression.id).where(
                SalesSuppression.startup_id == startup_id,
                SalesSuppression.email == email.strip().lower(),
            )
        )
        is not None
    )


def suppress(
    db: Session, startup_id: uuid.UUID, email: str, reason: SuppressionReason
) -> list[SequenceEnrollment]:
    """Add the address to the workspace list (first reason wins) and stop every live enrollment
    for it in that workspace. Idempotent. Returns the enrollments it stopped."""
    addr = email.strip().lower()
    db.execute(
        pg_insert(SalesSuppression)
        .values(startup_id=startup_id, email=addr, reason=reason)
        .on_conflict_do_nothing(constraint="uq_suppression_startup_email")
    )
    live = list(
        db.scalars(
            select(SequenceEnrollment)
            .where(
                SequenceEnrollment.startup_id == startup_id,
                SequenceEnrollment.email == addr,
                SequenceEnrollment.status == EnrollmentStatus.active,
            )
            .with_for_update()
        )
    )
    for e in live:
        e.status = _STOP_STATUS[reason]
        e.stopped_reason = reason.value
        e.next_send_at = None
    db.flush()
    return live


def maybe_auto_pause(db: Session, startup_id: uuid.UUID, *, now: datetime | None = None) -> bool:
    """Pause the whole workspace's sending when more than 10% of its last 50 sequence sends
    bounced. Notifies founders once (the pause itself is the guard: a paused workspace is not
    paused again). Returns True when this call paused it."""
    now = now or datetime.now(UTC)
    settings_row = get_settings(db, startup_id)
    if settings_row.paused_at is not None:
        return False
    recent = list(
        db.scalars(
            select(SequenceSend.status)
            .where(
                SequenceSend.startup_id == startup_id,
                SequenceSend.kind == SendKind.sequence,
                SequenceSend.status.in_([SendStatus.sent, SendStatus.bounced]),
            )
            .order_by(SequenceSend.reserved_at.desc(), SequenceSend.id)
            .limit(AUTO_PAUSE_WINDOW)
        )
    )
    if len(recent) < AUTO_PAUSE_MIN_SAMPLE:
        return False
    bounced = sum(1 for s in recent if s == SendStatus.bounced)
    if bounced / len(recent) <= AUTO_PAUSE_RATE:
        return False
    settings_row.paused_at = now
    settings_row.paused_reason = "bounces"
    db.flush()
    event_bus.publish(
        db,
        "sales.sending.paused",
        {
            "startup_id": str(startup_id),
            "reason": "bounces",
            "href": "/sales/sequences",
        },
    )
    return True


def sent_today(db: Session, startup_id: uuid.UUID, now: datetime) -> int:
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(SequenceSend)
            .where(
                SequenceSend.startup_id == startup_id,
                SequenceSend.reserved_at >= day_start,
                SequenceSend.status.in_(
                    [
                        SendStatus.pending,
                        SendStatus.sent,
                        SendStatus.bounced,
                        SendStatus.unknown,
                    ]
                ),
            )
        )
        or 0
    )
