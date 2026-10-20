import csv
import io
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Case, and_, case, func
from sqlalchemy.orm import Query, Session

from app.core.errors import NotFound
from app.db.models.enums import (
    ChannelKey,
    MarketingGenerationKind,
    MarketingGenerationStatus,
    MarketingMetricName,
)
from app.db.models.marketing import Campaign, MarketingAiGeneration, MarketingMetric
from app.schemas.marketing import MetricsIngest
from app.services.marketing.errors import _validation


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

    # CAC by channel: channel-scoped rows, plus campaign-scoped rows attributed to channels by
    # the campaign's channel_mix (D-MKT-7). The attributed part is an estimate and says so.
    cac_by_channel = _cac_by_channel(db, startup_id, base)

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


def _cac_by_channel(db: Session, startup_id: uuid.UUID, base: Query) -> list[dict]:
    direct: dict[ChannelKey, list[int]] = defaultdict(lambda: [0, 0])
    for channel, metric, total in (
        base.filter(
            MarketingMetric.channel.isnot(None),
            MarketingMetric.metric.in_(
                [MarketingMetricName.spend, MarketingMetricName.conversions]
            ),
        )
        .with_entities(
            MarketingMetric.channel, MarketingMetric.metric, func.sum(MarketingMetric.value)
        )
        .group_by(MarketingMetric.channel, MarketingMetric.metric)
        .all()
    ):
        direct[channel][0 if metric == MarketingMetricName.spend else 1] += int(total or 0)

    attributed: dict[ChannelKey, list[float]] = defaultdict(lambda: [0.0, 0.0])
    camp_rows = (
        base.filter(
            MarketingMetric.campaign_id.isnot(None),
            MarketingMetric.channel.is_(None),
            MarketingMetric.metric.in_(
                [MarketingMetricName.spend, MarketingMetricName.conversions]
            ),
        )
        .with_entities(
            MarketingMetric.campaign_id, MarketingMetric.metric, func.sum(MarketingMetric.value)
        )
        .group_by(MarketingMetric.campaign_id, MarketingMetric.metric)
        .all()
    )
    if camp_rows:
        mixes = dict(
            db.query(Campaign.id, Campaign.channel_mix)
            .filter(
                Campaign.startup_id == startup_id,
                Campaign.id.in_({r[0] for r in camp_rows}),
            )
            .tuples()
            .all()
        )
        for cid, metric, total in camp_rows:
            for channel, share in _mix_shares(mixes.get(cid) or {}).items():
                slot = 0 if metric == MarketingMetricName.spend else 1
                attributed[channel][slot] += int(total or 0) * share

    out = []
    for channel in (c for c in ChannelKey if c in direct or c in attributed):
        d_spend, d_conv = direct[channel] if channel in direct else (0, 0)
        a_spend, a_conv = (
            (round(attributed[channel][0]), round(attributed[channel][1]))
            if channel in attributed
            else (0, 0)
        )
        spend, conversions = d_spend + a_spend, d_conv + a_conv
        out.append(
            {
                "channel": channel.value,
                "spend": spend,
                "conversions": conversions,
                "cac": round(spend / conversions) if conversions else None,
                "attributed_spend": a_spend,
                "attributed_conversions": a_conv,
                "estimated": bool(a_spend or a_conv),
            }
        )
    return out


def _mix_shares(mix: dict) -> dict[ChannelKey, float]:
    """A campaign's channel_mix as fractions summing to 1 (empty when it has no weight)."""
    weights = {}
    for key, pct in mix.items():
        try:
            channel, w = ChannelKey(key), float(pct)
        except (ValueError, TypeError):
            continue
        if w > 0:
            weights[channel] = w
    total = sum(weights.values())
    return {c: w / total for c, w in weights.items()} if total else {}


_STAT_METRICS = (
    MarketingMetricName.impressions,
    MarketingMetricName.clicks,
    MarketingMetricName.conversions,
    MarketingMetricName.spend,
)


def _stats(totals: dict[MarketingMetricName, int]) -> dict:
    impressions = totals.get(MarketingMetricName.impressions, 0)
    clicks = totals.get(MarketingMetricName.clicks, 0)
    conversions = totals.get(MarketingMetricName.conversions, 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "spend": totals.get(MarketingMetricName.spend, 0),
        "click_through_rate": _rate(clicks, impressions),
        "conversion_rate": _rate(conversions, clicks),
    }


def campaign_stats_empty() -> dict:
    return _stats({})


def campaign_stats(
    db: Session, *, startup_id: uuid.UUID, campaign_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """All-time totals for each campaign, from its campaign-scoped metric rows (one query)."""
    per: dict[uuid.UUID, dict[MarketingMetricName, int]] = defaultdict(dict)
    if campaign_ids:
        for cid, metric, total in (
            db.query(
                MarketingMetric.campaign_id, MarketingMetric.metric, func.sum(MarketingMetric.value)
            )
            .filter(
                MarketingMetric.startup_id == startup_id,
                MarketingMetric.campaign_id.in_(campaign_ids),
            )
            .group_by(MarketingMetric.campaign_id, MarketingMetric.metric)
            .all()
        ):
            per[cid][metric] = int(total or 0)
    return {cid: _stats(per.get(cid, {})) for cid in campaign_ids}


def campaign_performance(
    db: Session, *, startup_id: uuid.UUID, campaign: Campaign, range_key: str
) -> dict:
    """Totals, a daily series and a by-channel split for one campaign. `range_key` is 7d, 30d,
    90d or `all`. The by-channel split is the campaign's channel_mix applied to its totals
    (campaign-scoped metrics carry no channel), so it is always labelled estimated."""
    q = db.query(MarketingMetric).filter(
        MarketingMetric.startup_id == startup_id, MarketingMetric.campaign_id == campaign.id
    )
    if range_key != "all":
        q = q.filter(MarketingMetric.ts >= _range_cutoff(range_key))
    daily: dict[date, dict[MarketingMetricName, int]] = defaultdict(dict)
    totals: dict[MarketingMetricName, int] = {}
    for ts, metric, total in (
        q.with_entities(MarketingMetric.ts, MarketingMetric.metric, func.sum(MarketingMetric.value))
        .group_by(MarketingMetric.ts, MarketingMetric.metric)
        .all()
    ):
        daily[ts][metric] = int(total or 0)
        totals[metric] = totals.get(metric, 0) + int(total or 0)
    series = [
        {"date": d, **{m.value: daily[d].get(m, 0) for m in _STAT_METRICS}} for d in sorted(daily)
    ]
    by_channel = []
    for channel, share in _mix_shares(campaign.channel_mix or {}).items():
        by_channel.append(
            {
                "channel": channel.value,
                "share_percent": round(100 * share, 1),
                **{m.value: round(totals.get(m, 0) * share) for m in _STAT_METRICS},
                "estimated": True,
            }
        )
    return {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "range": range_key,
        "totals": _stats(totals),
        "series": series,
        "by_channel": by_channel,
    }


def list_metrics(
    db: Session, *, startup_id: uuid.UUID, range_key: str, limit: int, offset: int
) -> tuple[list[dict], int]:
    q = db.query(MarketingMetric).filter(
        MarketingMetric.startup_id == startup_id, MarketingMetric.ts >= _range_cutoff(range_key)
    )
    total = q.count()
    rows = (
        q.outerjoin(Campaign, Campaign.id == MarketingMetric.campaign_id)
        .with_entities(MarketingMetric, Campaign.name)
        .order_by(MarketingMetric.ts.desc(), MarketingMetric.created_at.desc(), MarketingMetric.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [
        {
            "id": m.id,
            "ts": m.ts,
            "metric": m.metric,
            "value": m.value,
            "channel": m.channel,
            "campaign_id": m.campaign_id,
            "campaign_name": cname,
            "created_at": m.created_at,
        }
        for m, cname in rows
    ], total


def delete_metric(db: Session, *, startup_id: uuid.UUID, metric_id: uuid.UUID) -> None:
    row = db.query(MarketingMetric).filter_by(id=metric_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    db.delete(row)
    db.flush()


_EXPORT_MAX_ROWS = 50_000


def _csv_cell(value: object) -> str:
    """Neutralise spreadsheet formula injection: campaign names are user text, and a cell that
    starts with = + - @ (or a tab/CR) would run as a formula when the CSV is opened."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(db: Session, *, startup_id: uuid.UUID, range_key: str) -> str:
    """Interim export (D-MKT-6) of every recorded metric in the range, oldest first. Replaced by
    "Add to report" when the Reports module exists."""
    rows = (
        db.query(MarketingMetric, Campaign.name)
        .outerjoin(Campaign, Campaign.id == MarketingMetric.campaign_id)
        .filter(
            MarketingMetric.startup_id == startup_id, MarketingMetric.ts >= _range_cutoff(range_key)
        )
        .order_by(MarketingMetric.ts, MarketingMetric.created_at, MarketingMetric.id)
        .limit(_EXPORT_MAX_ROWS)
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "scope", "channel", "campaign", "metric", "value"])
    for m, cname in rows:
        scope = "channel" if m.channel else "campaign" if m.campaign_id else "workspace"
        w.writerow(
            [
                m.ts.isoformat(),
                scope,
                m.channel.value if m.channel else "",
                _csv_cell(cname) if cname else "",
                m.metric.value,
                m.value,
            ]
        )
    return buf.getvalue()


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
