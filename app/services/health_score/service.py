import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import NotFound, RecommendationResolved
from app.db.models.activity import ActivityLog
from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.enums import AssessmentStatus, Dimension, RecommendationStatus
from app.db.models.health_score import (
    HealthRecommendation,
    HealthScore,
    HealthScoreHistory,
    HealthSignal,
)
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.health_score.config import (
    DIMENSION_LABELS,
    DIMENSION_WEIGHTS,
    HEALTH_CONFIG_VERSION,
    MIN_COHORT_SIZE,
)
from app.services.health_score.live_signals import SIGNALS_BY_KEY, SignalReading, read_signals
from app.services.health_score.scoring import band_for, weighted_overall


def latest_completed_result(db: Session, startup_id: uuid.UUID) -> AssessmentResult | None:
    return db.execute(
        select(AssessmentResult)
        .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
        .where(Assessment.startup_id == startup_id, Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _delta_7d(db: Session, startup_id: uuid.UUID, current: int, now: datetime) -> int:
    cutoff = now - timedelta(days=7)
    baseline = db.execute(
        select(HealthScoreHistory.score)
        .where(HealthScoreHistory.startup_id == startup_id, HealthScoreHistory.created_at <= cutoff)
        .order_by(HealthScoreHistory.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if baseline is None:  # nothing older than 7d — fall back to earliest point
        baseline = db.execute(
            select(HealthScoreHistory.score)
            .where(HealthScoreHistory.startup_id == startup_id)
            .order_by(HealthScoreHistory.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
    return current - baseline if baseline is not None else 0


def recompute_health_score(
    db: Session, startup: Startup, *, trigger: str = "assessment_complete"
) -> HealthScore | None:
    result = latest_completed_result(db, startup.id)
    if result is None:
        return None

    now = datetime.now(UTC)
    baseline = {d.value: int(result.dimension_scores.get(d.value, 50)) for d in Dimension}
    readings = read_signals(db, startup)
    share = live_share(_first_assessed_at(db, startup.id), now)
    dim_scores = blend(baseline, readings, share)
    overall = weighted_overall(dim_scores)
    band = band_for(overall)

    prev = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    previous_score = prev.score if prev else None
    # Read prior_max BEFORE appending this recompute's history row, so it
    # reflects only points that existed before this call — the first-ever
    # score therefore has no prior_max and never emits a record.
    prior_max = db.execute(
        select(HealthScoreHistory.score)
        .where(HealthScoreHistory.startup_id == startup.id)
        .order_by(HealthScoreHistory.score.desc())
        .limit(1)
    ).scalar_one_or_none()

    # 1. Replace the signal set: the assessment baseline per dimension + every live signal,
    # each with the points it contributes to the overall score (so every point traces back).
    db.query(HealthSignal).filter_by(startup_id=startup.id).delete()
    by_dim = _readings_by_dim(readings)
    for d in Dimension:
        live = by_dim.get(d.value, [])
        dim_share = share if live else 0.0
        w = DIMENSION_WEIGHTS[d.value]
        db.add(
            HealthSignal(
                startup_id=startup.id,
                dimension=d.value,
                key=f"assessment.{d.value}",
                value=baseline[d.value],
                contribution=round(baseline[d.value] * w * (1 - dim_share), 2),
                source_ref=f"assessment:{result.assessment_id}",
            )
        )
        for r in live:
            db.add(
                HealthSignal(
                    startup_id=startup.id,
                    dimension=d.value,
                    key=r.signal.key,
                    value=min(r.value, 9999.0),
                    contribution=round(r.score * w * dim_share / len(live), 2),
                    source_ref=r.signal.href,
                )
            )

    # 2. Upsert the current score
    stmt = (
        pg_insert(HealthScore)
        .values(
            startup_id=startup.id,
            score=overall,
            band=band,
            dimension_scores=dim_scores,
            source="live",
            config_version=HEALTH_CONFIG_VERSION,
        )
        .on_conflict_do_update(
            index_elements=["startup_id"],
            set_={
                "score": overall,
                "band": band,
                "dimension_scores": dim_scores,
                "config_version": HEALTH_CONFIG_VERSION,
                # ON CONFLICT DO UPDATE bypasses the ORM's onupdate: set it explicitly, or the
                # 15-minute throttle and `computed_at` would read the first-ever timestamp.
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)

    # 3. Append history (a point per change; assessment completions always get one)
    delta = _delta_7d(db, startup.id, overall, now)
    changed = prev is None or prev.score != overall or prev.dimension_scores != dim_scores
    if changed or trigger == "assessment_complete":
        db.add(
            HealthScoreHistory(
                startup_id=startup.id,
                score=overall,
                dimension_scores=dim_scores,
                delta=delta,
                trigger=trigger,
                config_version=HEALTH_CONFIG_VERSION,
            )
        )

    # 4. Recommendations
    from app.services.health_score.recommendations import generate_recommendations

    created = generate_recommendations(db, startup.id, dim_scores, live_recommendations(readings))
    # The AI rewrite of recommendation text costs LLM tokens: only when there is new text to
    # write (live recomputes run on every relevant change).
    if created or trigger == "assessment_complete":
        job_dispatcher.enqueue(
            db, "ai.health.recommendations", {"startup_id": str(startup.id)}, startup.id
        )

    db.flush()
    # populate_existing=True forces a refresh from the DB row we just upserted via
    # raw Core (pg_insert), instead of silently returning the stale ORM-identity-mapped
    # object that `prev` (queried earlier, pre-upsert) left in the session's identity map.
    hs = (
        db.query(HealthScore)
        .filter_by(startup_id=startup.id)
        .execution_options(populate_existing=True)
        .first()
    )

    # 5. Events
    event_bus.publish(
        db,
        "healthscore.updated",
        {
            "startup_id": str(startup.id),
            "score": overall,
            "previous_score": previous_score,
            "band": band,
            "delta_7d": delta,
            "computed_at": now.isoformat(),
            "config_version": HEALTH_CONFIG_VERSION,
        },
    )
    if delta <= -5:
        event_bus.publish(
            db,
            "healthscore.dropped",
            {
                "startup_id": str(startup.id),
                "score": overall,
                "previous_score": previous_score,
                "delta_7d": delta,
                "computed_at": now.isoformat(),
            },
        )
    if prior_max is not None and overall > prior_max:
        event_bus.publish(
            db,
            "healthscore.record",
            {
                "startup_id": str(startup.id),
                "score": overall,
                "previous_max": prior_max,
                "computed_at": now.isoformat(),
            },
        )
    return hs


LIVE_SHARE = 0.5  # D-HEA-1: each dimension = 50% assessment baseline + 50% live signals
# D-HEA-1b: the live share ramps in from 0 on the first assessment day to LIVE_SHARE after
# RAMP_DAYS, so a just-assessed founder isn't halved by signals they've had no time to move.
RAMP_DAYS = 14


def live_share(first_assessed_at: datetime | None, now: datetime) -> float:
    if first_assessed_at is None:
        return 0.0
    days = max(0.0, (now - first_assessed_at).total_seconds() / 86400)
    return round(LIVE_SHARE * min(days / RAMP_DAYS, 1.0), 3)


def _first_assessed_at(db: Session, startup_id: uuid.UUID) -> datetime | None:
    return db.execute(
        select(Assessment.completed_at)
        .where(Assessment.startup_id == startup_id, Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.asc())
        .limit(1)
    ).scalar_one_or_none()


MIN_RECOMPUTE_INTERVAL = timedelta(minutes=15)
REC_LIMIT = 6


def _readings_by_dim(readings: list[SignalReading]) -> dict[str, list[SignalReading]]:
    out: dict[str, list[SignalReading]] = {}
    for r in readings:
        out.setdefault(r.signal.dimension, []).append(r)
    return out


def blend(
    baseline: dict[str, int], readings: list[SignalReading], share: float = LIVE_SHARE
) -> dict[str, int]:
    """Dimension scores: the assessment baseline blended with the live signals (D-HEA-1).
    A dimension with no applicable live signal keeps its baseline."""
    by_dim = _readings_by_dim(readings)
    out = {}
    for dim, base in baseline.items():
        live = by_dim.get(dim)
        if not live:
            out[dim] = base
            continue
        live_avg = sum(r.score for r in live) / len(live)
        out[dim] = round(base * (1 - share) + live_avg * share)
    return out


def live_recommendations(readings: list[SignalReading], share: float = LIVE_SHARE) -> list[dict]:
    """Catalog-shaped recommendations for below-target signals, biggest potential lift first
    (D-HEA-4). Lift = overall points gained by hitting the target."""
    by_dim = _readings_by_dim(readings)
    recs: list[dict[str, Any]] = []
    for r in readings:
        if r.score >= 100:
            continue
        n = len(by_dim[r.signal.dimension])
        # Lift at full weight: what hitting the target is worth once the ramp completes.
        lift = (100 - r.score) * DIMENSION_WEIGHTS[r.signal.dimension] * max(share, LIVE_SHARE) / n
        recs.append(
            {
                "key": f"live.{r.signal.key}",
                "dimension": r.signal.dimension,
                "title": r.signal.rec_title,
                "body": r.signal.rec_body,
                "estimated_lift": max(1, round(lift)),
                "effort": r.signal.rec_effort,
                "href": r.signal.href,
            }
        )
    recs.sort(key=lambda x: int(x["estimated_lift"]), reverse=True)
    return recs[:REC_LIMIT]


def refresh_health_score(
    db: Session, startup: Startup, *, trigger: str, force: bool = False
) -> HealthScore | None:
    """Recompute from live signals unless it ran in the last 15 minutes (PRD: "min interval
    15 min"). No-op before the kickoff assessment (the baseline doesn't exist yet)."""
    if latest_completed_result(db, startup.id) is None:
        return None
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    if (
        not force
        and hs is not None
        and hs.updated_at is not None
        and datetime.now(UTC) - hs.updated_at < MIN_RECOMPUTE_INTERVAL
    ):
        return hs
    return recompute_health_score(db, startup, trigger=trigger)


_BAND_WORDS = {
    "thriving": "thriving",
    "healthy": "healthy",
    "needs_work": "finding your footing",
    "at_risk": "at risk",
}
SPARK_POINTS = 8


def summary_line(hs: HealthScore, stage: str | None) -> str:
    """PRD 06.1: "You're {band} for {stage} stage. {strongest} is carrying you; {weakest} is
    holding you back." Ties break on the fixed dimension order, so the line is stable."""
    scores = hs.dimension_scores
    strongest = max(scores, key=lambda k: scores[k])
    weakest = min(scores, key=lambda k: scores[k])
    lead = f"You're {_BAND_WORDS.get(hs.band, hs.band)} for {stage or 'your'} stage."
    if scores[strongest] == scores[weakest]:
        return f"{lead} Your five dimensions are evenly balanced."
    return (
        f"{lead} {DIMENSION_LABELS[strongest]} is carrying you; "
        f"{DIMENSION_LABELS[weakest]} is holding you back."
    )


def _dimension_trends(db: Session, startup_id: uuid.UUID) -> dict[str, list[int]]:
    rows = (
        db.query(HealthScoreHistory.dimension_scores)
        .filter(HealthScoreHistory.startup_id == startup_id)
        .order_by(HealthScoreHistory.created_at.desc())
        .limit(SPARK_POINTS)
        .all()
    )
    out: dict[str, list[int]] = {d.value: [] for d in Dimension}
    for (scores,) in reversed(rows):
        for d in out:
            if d in scores:
                out[d].append(int(scores[d]))
    return out


def get_overview(db: Session, startup: Startup) -> dict:
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    if latest_completed_result(db, startup.id) is not None:
        # Lazy-on-read: compute the first time, and refresh live signals when stale (>15 min).
        hs = refresh_health_score(db, startup, trigger="lazy_read", force=hs is None) or hs
        # Commit unconditionally: a refresh returns the same identity-mapped row it updated,
        # so an identity check can't tell whether a recompute happened (it would roll back).
        db.commit()
    if hs is None:
        return {
            "status": "pending_assessment",
            "score": None,
            "band": None,
            "message": "Complete your kickoff assessment to generate your Health Score.",
            "dimensions": [],
            "top_recommendations": [],
        }
    now = datetime.now(UTC)
    delta = _delta_7d(db, startup.id, hs.score, now)
    trends = _dimension_trends(db, startup.id)
    dims = [
        {
            "key": k,
            "label": DIMENSION_LABELS[k],
            "score": v,
            "band": band_for(v),
            # Oldest -> newest dimension scores from the last 8 recomputes (sparkline).
            "trend": trends.get(k, []),
        }
        for k, v in hs.dimension_scores.items()
    ]
    recs = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
        .order_by(HealthRecommendation.priority.asc())
        .limit(3)
        .all()
    )
    return {
        "status": "ok",
        "score": hs.score,
        "band": hs.band,
        "delta_7d": delta,
        "computed_at": hs.updated_at.isoformat(),
        "config_version": hs.config_version,
        "dimensions": dims,
        "top_recommendations": [_serialize_rec(r) for r in recs],
        "summary": summary_line(hs, startup.stage.value if startup.stage else None),
    }


_DIMENSION_HREFS = {
    "product": "/roadmap",
    "market": "/validation",
    "money": "/finance",
    "legal": "/documents",
    "team": "/team",
}


def _rec_href(r: HealthRecommendation) -> str:
    """Where "Do it" / "Start" takes the founder: the signal's module for live recs, else the
    dimension's home module."""
    if r.key.startswith("live."):
        sig = SIGNALS_BY_KEY.get(r.key.removeprefix("live."))
        if sig is not None:
            return sig.href
    return _DIMENSION_HREFS.get(r.dimension, "/health")


def _serialize_rec(r: HealthRecommendation) -> dict:
    return {
        "id": str(r.id),
        "dimension": r.dimension,
        "key": r.key,
        "title": r.title,
        "body": r.body,
        "estimated_lift": r.estimated_lift,
        "effort": r.effort.value,
        "status": r.status.value,
        "priority": r.priority,
        "href": _rec_href(r),
    }


HISTORY_RANGES: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "365d": 365,  # PRD 06.3 range picker: 30 / 90 / 365 days
    "all": None,
}

# PRD 06.3 annotated event markers: notable workspace events, from the activity log.
MARKER_LABELS = {
    "assessment.completed": "Assessment completed",
    "business.plan.generated": "Business plan generated",
    "roadmap.milestone.completed": "Milestone completed",
    "roadmap.replanned": "Roadmap re-planned",
    "workspace.member.joined": "Teammate joined",
    "document.signature.completed": "Agreement signed",
}


def _ordered(signals: list[HealthSignal]) -> list[HealthSignal]:
    return sorted(signals, key=lambda x: (not x.key.startswith("assessment."), x.key))


def _serialize_signal(sig: HealthSignal, startup: Startup) -> dict:
    """PRD 06.2 row: name, current value, target for stage, contribution (pts), source link."""
    live = SIGNALS_BY_KEY.get(sig.key)
    if live is not None:
        return {
            "key": sig.key,
            "label": live.label,
            "value": float(sig.value),
            "unit": live.unit,
            "target": live.target(startup.stage),
            "contribution": float(sig.contribution),
            "source": "live",
            # Runway stores 999 for a startup that isn't burning cash (full marks).
            "status": (
                "not_burning"
                if sig.key == "money.runway_live" and float(sig.value) >= 999
                else "ok"
            ),
            "href": live.href,
            "source_ref": sig.source_ref,
        }
    return {
        "key": sig.key,
        "label": "Kickoff assessment",
        "value": float(sig.value),
        "unit": "score",
        "target": 100,
        "contribution": float(sig.contribution),
        "source": "assessment",
        "href": "/assessment",
        "source_ref": sig.source_ref,
    }


def get_dimension(db: Session, startup: Startup, dim: str) -> dict:
    if dim not in DIMENSION_LABELS:
        raise NotFound()
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    score = hs.dimension_scores.get(dim) if hs else None
    signals = db.query(HealthSignal).filter_by(startup_id=startup.id, dimension=dim).all()
    recs = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=startup.id, dimension=dim, status=RecommendationStatus.pending)
        .order_by(HealthRecommendation.priority.asc())
        .all()
    )
    trend = [
        {"score": h.dimension_scores.get(dim), "computed_at": h.created_at.isoformat()}
        for h in db.query(HealthScoreHistory)
        .filter_by(startup_id=startup.id)
        .order_by(HealthScoreHistory.created_at.asc())
        .all()
    ]
    return {
        "key": dim,
        "label": DIMENSION_LABELS[dim],
        "score": score,
        "band": band_for(score) if score is not None else None,
        "signals": [_serialize_signal(sig, startup) for sig in _ordered(signals)],
        "trend": trend,
        "recommendations": [_serialize_rec(r) for r in recs],
    }


def _percentile(sorted_vals: list[int], p: float) -> int:
    """Nearest-rank percentile of an ascending list."""
    idx = max(0, min(len(sorted_vals) - 1, round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def get_benchmarks(db: Session, startup: Startup) -> dict:
    """Anonymised cohort comparison (PRD 06.4): peers sharing stage + industry with a score.
    Below MIN_COHORT_SIZE nothing is shown, so no single startup can be reverse-identified;
    only aggregates (p25/p50/p75) and the caller's own percentile rank are returned."""
    cohort = {
        "stage": startup.stage.value if startup.stage else None,
        "industry": startup.industry,
    }
    peers = (
        db.query(HealthScore)
        .join(Startup, Startup.id == HealthScore.startup_id)
        .filter(Startup.stage == startup.stage, Startup.industry == startup.industry)
        .all()
    )
    if len(peers) < MIN_COHORT_SIZE:
        return {
            "status": "insufficient_data",
            "cohort": cohort,
            "min_cohort_size": MIN_COHORT_SIZE,
            "percentiles": None,
        }
    mine = next((p for p in peers if p.startup_id == startup.id), None)

    def stats(values: list[int], own: int | None) -> dict:
        vals = sorted(values)
        rank = None
        if own is not None:
            rank = round(100 * sum(1 for v in vals if v < own) / len(vals))
        return {
            "p25": _percentile(vals, 0.25),
            "p50": _percentile(vals, 0.5),
            "p75": _percentile(vals, 0.75),
            "you": own,
            "your_percentile": rank,
        }

    percentiles = {"overall": stats([p.score for p in peers], mine.score if mine else None)}
    for d in Dimension:
        percentiles[d.value] = stats(
            [int(p.dimension_scores.get(d.value, 0)) for p in peers],
            int(mine.dimension_scores.get(d.value, 0)) if mine else None,
        )
    return {
        "status": "ok",
        "cohort": {**cohort, "size": len(peers)},
        "min_cohort_size": MIN_COHORT_SIZE,
        "percentiles": percentiles,
    }


def list_recommendations(db: Session, startup: Startup, status_filter: str | None) -> list[dict]:
    q = db.query(HealthRecommendation).filter_by(startup_id=startup.id)
    if status_filter:
        q = q.filter(HealthRecommendation.status == RecommendationStatus(status_filter))
    else:
        q = q.filter(HealthRecommendation.status == RecommendationStatus.pending)
    rows = q.order_by(HealthRecommendation.priority.asc()).all()
    return [_serialize_rec(r) for r in rows]


def resolve_recommendation(
    db: Session, startup: Startup, rec_id: uuid.UUID, target: RecommendationStatus
) -> dict:
    row = db.query(HealthRecommendation).filter_by(id=rec_id, startup_id=startup.id).first()
    if row is None:
        raise NotFound()  # uniform 404 -- never leak cross-tenant existence
    if row.status == target:
        return _serialize_rec(row)  # idempotent
    if row.status != RecommendationStatus.pending:
        raise RecommendationResolved()  # accepted<->dismissed cross-transition
    row.status = target
    row.resolved_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _serialize_rec(row)


def get_history(db: Session, startup: Startup, range_key: str) -> list[dict]:
    days = HISTORY_RANGES[range_key]
    q = db.query(HealthScoreHistory).filter_by(startup_id=startup.id)
    if days is not None:
        q = q.filter(HealthScoreHistory.created_at >= datetime.now(UTC) - timedelta(days=days))
    rows = q.order_by(HealthScoreHistory.created_at.asc()).all()
    return [
        {
            "score": h.score,
            "dimension_scores": h.dimension_scores,
            "delta": h.delta,
            "computed_at": h.created_at.isoformat(),
            "trigger": h.trigger,
        }
        for h in rows
    ]


def get_history_markers(db: Session, startup: Startup, range_key: str) -> list[dict]:
    """Event markers for the trend chart, oldest first (PRD 06.3)."""
    days = HISTORY_RANGES[range_key]
    q = db.query(ActivityLog).filter(
        ActivityLog.startup_id == startup.id, ActivityLog.action.in_(list(MARKER_LABELS))
    )
    if days is not None:
        q = q.filter(ActivityLog.created_at >= datetime.now(UTC) - timedelta(days=days))
    return [
        {"at": a.created_at.isoformat(), "label": MARKER_LABELS[a.action], "action": a.action}
        for a in q.order_by(ActivityLog.created_at.asc()).limit(50).all()
    ]
