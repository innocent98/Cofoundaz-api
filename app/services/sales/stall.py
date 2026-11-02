import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.enums import StageKind
from app.db.models.sales import Deal, PipelineStage
from app.platform.events import event_bus
from app.services.sales.stages import get_settings


def notify_stalled(
    db: Session, *, deal_id: uuid.UUID, startup_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Notify once that a deal went quiet. True when the event was published.

    The scan's payload is only a hint, so everything is re-checked under a row lock: the deal must
    belong to `startup_id`, still sit in a live open stage, not be notified yet, and still be past
    the workspace threshold (an activity logged since the scan wins). A second worker (or a
    duplicate job) blocks on the lock, then sees `stalled_notified_at` set and publishes nothing.
    """
    now = now or datetime.now(UTC)
    deal = db.scalar(
        select(Deal)
        .where(Deal.id == deal_id, Deal.startup_id == startup_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if deal is None or deal.stalled_notified_at is not None:
        return False
    stage = db.get(PipelineStage, deal.stage_id)
    if stage is None or stage.kind != StageKind.open or stage.archived_at is not None:
        return False
    stall_days = get_settings(db, startup_id).stall_days
    if deal.last_activity_at > now - timedelta(days=stall_days):
        return False

    deal.stalled_notified_at = now
    db.flush()
    event_bus.publish(
        db,
        "sales.deal.stalled",
        {
            "startup_id": str(startup_id),
            "deal_id": str(deal.id),
            "name": deal.name,
            "days": (now - deal.last_activity_at).days,
            "owner_id": str(deal.owner_id) if deal.owner_id else None,
            "href": f"/sales?deal={deal.id}&followup=1",
        },
    )
    return True
