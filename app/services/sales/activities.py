from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.sales import Deal


def touch_deal(db: Session, deal: Deal, *, at: datetime | None = None) -> None:
    """Record activity on a deal: bump `last_activity_at` and end any stall episode.

    Clearing `stalled_notified_at` is what lets the stall scan notify again after the next quiet
    period (the new `last_activity_at` also yields a new idempotency period key).
    """
    deal.last_activity_at = at or datetime.now(UTC)
    deal.stalled_notified_at = None
    db.flush()
