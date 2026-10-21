"""Weekly AI readout of an active campaign (PRD 10: "AI weekly readout card").

The prompt is built from numbers only: no campaign name, no free text from the founder.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.enums import MarketingMetricName
from app.db.models.marketing import Campaign, CampaignReadout, MarketingMetric
from app.schemas.marketing import ReadoutResponse
from app.services.marketing.analytics import _stats


def serialize_readout(r: CampaignReadout) -> ReadoutResponse:
    return ReadoutResponse.model_validate(r, from_attributes=True)


def list_readouts(
    db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID, limit: int = 12
) -> list[CampaignReadout]:
    return (
        db.query(CampaignReadout)
        .filter_by(startup_id=startup_id, campaign_id=campaign_id)
        .order_by(CampaignReadout.week_start.desc())
        .limit(limit)
        .all()
    )


def _window(
    db: Session, campaign_id: uuid.UUID, start: date | None, end: date | None
) -> dict[str, Any]:
    q = db.query(MarketingMetric.metric, func.sum(MarketingMetric.value)).filter(
        MarketingMetric.campaign_id == campaign_id
    )
    if start is not None:
        q = q.filter(MarketingMetric.ts >= start)
    if end is not None:
        q = q.filter(MarketingMetric.ts < end)
    totals: dict[MarketingMetricName, int] = {
        m: int(v or 0) for m, v in q.group_by(MarketingMetric.metric)
    }
    return _stats(totals)


def readout_facts(db: Session, campaign: Campaign, today: date) -> dict[str, Any]:
    """Everything the model is told about the campaign: its objective, its plan, and the last
    seven days against the seven before them."""
    return {
        "objective": campaign.objective.value,
        "status": campaign.status.value,
        "budget": campaign.budget,
        "channel_mix_percent": campaign.channel_mix,
        "days_live": (today - campaign.launched_at.date()).days if campaign.launched_at else None,
        "last_7_days": _window(db, campaign.id, today - timedelta(days=7), today),
        "previous_7_days": _window(
            db, campaign.id, today - timedelta(days=14), today - timedelta(days=7)
        ),
        "all_time": _window(db, campaign.id, None, None),
    }
