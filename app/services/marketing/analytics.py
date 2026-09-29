import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Case, and_, case, func
from sqlalchemy.orm import Session

from app.db.models.enums import (
    MarketingGenerationKind,
    MarketingGenerationStatus,
    MarketingMetricName,
)
from app.db.models.marketing import Campaign, MarketingAiGeneration, MarketingMetric
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


_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _range_cutoff(range_key: str) -> date:
    if range_key not in _RANGE_DAYS:
        raise _validation("range", "range must be one of 7d, 30d, 90d.")
    return datetime.now(UTC).date() - timedelta(days=_RANGE_DAYS[range_key])


def _rate(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def get_analytics(db: Session, *, startup_id: uuid.UUID, range_key: str) -> dict:
    cutoff = _range_cutoff(range_key)
    base = db.query(MarketingMetric).filter(
        MarketingMetric.startup_id == startup_id, MarketingMetric.ts >= cutoff
    )

    def _sum(metric: MarketingMetricName) -> Case[int]:
        return case((MarketingMetric.metric == metric, MarketingMetric.value), else_=0)

    # traffic by ISO week
    week = func.date_trunc("week", MarketingMetric.ts)
    traffic_rows = (
        base.filter(MarketingMetric.metric == MarketingMetricName.visits)
        .with_entities(week.label("wk"), func.coalesce(func.sum(MarketingMetric.value), 0))
        .group_by(week)
        .order_by(week)
        .all()
    )
    traffic_by_week = [{"week_start": wk.date(), "visits": int(v)} for wk, v in traffic_rows]

    # CAC by channel (channel-scoped rows only)
    cac_rows = (
        base.filter(MarketingMetric.channel.isnot(None))
        .with_entities(
            MarketingMetric.channel,
            func.sum(_sum(MarketingMetricName.spend)),
            func.sum(_sum(MarketingMetricName.conversions)),
        )
        .group_by(MarketingMetric.channel)
        .all()
    )
    cac_by_channel = []
    for channel, spend, conversions in cac_rows:
        spend, conversions = int(spend or 0), int(conversions or 0)
        cac_by_channel.append(
            {
                "channel": channel.value,
                "spend": spend,
                "conversions": conversions,
                "cac": round(spend / conversions) if conversions else None,
            }
        )

    # funnel totals
    totals = base.with_entities(
        func.sum(_sum(MarketingMetricName.impressions)),
        func.sum(_sum(MarketingMetricName.clicks)),
        func.sum(_sum(MarketingMetricName.conversions)),
    ).one()
    impressions, clicks, conversions = (int(x or 0) for x in totals)
    funnel = {
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "click_through_rate": _rate(clicks, impressions),
        "conversion_rate": _rate(conversions, clicks),
    }

    # leaderboard (campaign-scoped rows, joined to campaign name)
    lb_rows = (
        base.filter(MarketingMetric.campaign_id.isnot(None))
        .join(
            Campaign,
            and_(Campaign.id == MarketingMetric.campaign_id, Campaign.startup_id == startup_id),
        )
        .with_entities(
            Campaign.id,
            Campaign.name,
            func.sum(_sum(MarketingMetricName.clicks)),
            func.sum(_sum(MarketingMetricName.conversions)),
            func.sum(_sum(MarketingMetricName.spend)),
        )
        .group_by(Campaign.id, Campaign.name)
        .order_by(func.sum(_sum(MarketingMetricName.conversions)).desc(), Campaign.name)
        .all()
    )
    leaderboard = []
    for cid, name, clk, cnv, spd in lb_rows:
        clk, cnv, spd = int(clk or 0), int(cnv or 0), int(spd or 0)
        leaderboard.append(
            {
                "campaign_id": cid,
                "name": name,
                "clicks": clk,
                "conversions": cnv,
                "spend": spd,
                "cac": round(spd / cnv) if cnv else None,
            }
        )

    return {
        "range": range_key,
        "traffic_by_week": traffic_by_week,
        "cac_by_channel": cac_by_channel,
        "funnel": funnel,
        "leaderboard": leaderboard,
    }


def top_channel_by_conversions(db: Session, *, startup_id: uuid.UUID) -> str | None:
    row = (
        db.query(MarketingMetric.channel, func.sum(MarketingMetric.value).label("c"))
        .filter(
            MarketingMetric.startup_id == startup_id,
            MarketingMetric.metric == MarketingMetricName.conversions,
            MarketingMetric.channel.isnot(None),
        )
        .group_by(MarketingMetric.channel)
        .order_by(func.sum(MarketingMetric.value).desc())
        .first()
    )
    return row[0].value if row is not None else None


def count_ai_content_ideas(db: Session, *, startup_id: uuid.UUID) -> int:
    rows = (
        db.query(MarketingAiGeneration.output)
        .filter(
            MarketingAiGeneration.startup_id == startup_id,
            MarketingAiGeneration.kind == MarketingGenerationKind.content_gap,
            MarketingAiGeneration.status == MarketingGenerationStatus.ready,
        )
        .all()
    )
    return sum(len((out or {}).get("gaps", []) or []) for (out,) in rows)
