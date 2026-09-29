import uuid

from sqlalchemy.orm import Session

from app.db.models.marketing import Campaign, MarketingMetric
from app.schemas.marketing import MetricsIngest
from app.services.marketing.service import _validation


def ingest_metrics(db: Session, *, startup_id: uuid.UUID, data: MetricsIngest) -> int:
    # Validate every referenced campaign belongs to this startup before inserting anything.
    campaign_ids = {p.campaign_id for p in data.points if p.campaign_id is not None}
    if campaign_ids:
        owned = {
            row.id
            for row in db.query(Campaign.id)
            .filter(Campaign.startup_id == startup_id, Campaign.id.in_(campaign_ids))
            .all()
        }
        missing = campaign_ids - owned
        if missing:
            raise _validation(
                "campaign_id", "campaign_id must reference a campaign in this workspace."
            )
    for p in data.points:
        db.add(
            MarketingMetric(
                startup_id=startup_id,
                ts=p.ts,
                channel=p.channel,
                campaign_id=p.campaign_id,
                metric=p.metric,
                value=p.value,
            )
        )
    db.flush()
    return len(data.points)
