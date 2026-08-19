import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import NotFound
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
from app.services.health_score.config import (
    DIMENSION_LABELS,
    DIMENSION_WEIGHTS,
    HEALTH_CONFIG_VERSION,
    MIN_COHORT_SIZE,
)
from app.services.health_score.scoring import band_for, weighted_overall


def latest_completed_result(db: Session, startup_id: uuid.UUID) -> AssessmentResult | None:
    return db.execute(
        select(AssessmentResult)
        .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
        .where(Assessment.startup_id == startup_id,
               Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _delta_7d(db: Session, startup_id: uuid.UUID, current: int, now: datetime) -> int:
    cutoff = now - timedelta(days=7)
    baseline = db.execute(
        select(HealthScoreHistory.score)
        .where(HealthScoreHistory.startup_id == startup_id,
               HealthScoreHistory.created_at <= cutoff)
        .order_by(HealthScoreHistory.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if baseline is None:  # nothing older than 7d — fall back to earliest point
        baseline = db.execute(
            select(HealthScoreHistory.score)
            .where(HealthScoreHistory.startup_id == startup_id)
            .order_by(HealthScoreHistory.created_at.asc()).limit(1)
        ).scalar_one_or_none()
    return current - baseline if baseline is not None else 0


def recompute_health_score(db: Session, startup: Startup, *,
                           trigger: str = "assessment_complete") -> HealthScore | None:
    result = latest_completed_result(db, startup.id)
    if result is None:
        return None

    now = datetime.now(UTC)
    dim_scores = {d.value: int(result.dimension_scores.get(d.value, 50)) for d in Dimension}
    overall = weighted_overall(dim_scores)
    band = band_for(overall)

    prev = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    previous_score = prev.score if prev else None
    # Read prior_max BEFORE appending this recompute's history row, so it
    # reflects only points that existed before this call — the first-ever
    # score therefore has no prior_max and never emits a record.
    prior_max = db.execute(
        select(HealthScoreHistory.score).where(HealthScoreHistory.startup_id == startup.id)
        .order_by(HealthScoreHistory.score.desc()).limit(1)
    ).scalar_one_or_none()

    # 1. Replace the signal set
    db.query(HealthSignal).filter_by(startup_id=startup.id).delete()
    for d in Dimension:
        v = dim_scores[d.value]
        db.add(HealthSignal(startup_id=startup.id, dimension=d.value,
                            key=f"assessment.{d.value}", value=v,
                            contribution=round(v * DIMENSION_WEIGHTS[d.value], 2),
                            source_ref=f"assessment:{result.assessment_id}"))

    # 2. Upsert the current score
    stmt = pg_insert(HealthScore).values(
        startup_id=startup.id, score=overall, band=band, dimension_scores=dim_scores,
        source="assessment", config_version=HEALTH_CONFIG_VERSION,
    ).on_conflict_do_update(
        index_elements=["startup_id"],
        set_={"score": overall, "band": band, "dimension_scores": dim_scores,
              "config_version": HEALTH_CONFIG_VERSION},
    )
    db.execute(stmt)

    # 3. Append history
    delta = _delta_7d(db, startup.id, overall, now)
    db.add(HealthScoreHistory(startup_id=startup.id, score=overall, dimension_scores=dim_scores,
                              delta=delta, trigger=trigger, config_version=HEALTH_CONFIG_VERSION))

    # 4. Recommendations
    from app.services.health_score.recommendations import generate_recommendations
    generate_recommendations(db, startup.id, dim_scores)

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
    event_bus.publish("healthscore.updated", {
        "startup_id": str(startup.id), "score": overall, "previous_score": previous_score,
        "band": band, "delta_7d": delta, "computed_at": now.isoformat(),
        "config_version": HEALTH_CONFIG_VERSION})
    if delta <= -5:
        event_bus.publish("healthscore.dropped", {
            "startup_id": str(startup.id), "score": overall, "previous_score": previous_score,
            "delta_7d": delta, "computed_at": now.isoformat()})
    if prior_max is not None and overall > prior_max:
        event_bus.publish("healthscore.record", {
            "startup_id": str(startup.id), "score": overall, "previous_max": prior_max,
            "computed_at": now.isoformat()})
    return hs


def get_overview(db: Session, startup: Startup) -> dict:
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    if hs is None:
        # lazy-on-read: compute if a completed assessment exists, else pending
        if latest_completed_result(db, startup.id) is not None:
            hs = recompute_health_score(db, startup, trigger="lazy_read")
            db.commit()
    if hs is None:
        return {
            "status": "pending_assessment", "score": None, "band": None,
            "message": "Complete your kickoff assessment to generate your Health Score.",
            "dimensions": [], "top_recommendations": [],
        }
    now = datetime.now(UTC)
    delta = _delta_7d(db, startup.id, hs.score, now)
    dims = [
        {"key": k, "label": DIMENSION_LABELS[k], "score": v, "band": band_for(v)}
        for k, v in hs.dimension_scores.items()
    ]
    recs = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
        .order_by(HealthRecommendation.priority.asc())
        .limit(3)
        .all()
    )
    weakest = min(hs.dimension_scores, key=lambda k: hs.dimension_scores[k])
    summary = (
        f"Your Health Score is {hs.score} ({hs.band.replace('_', ' ')}). "
        f"Your weakest area is {DIMENSION_LABELS[weakest]}."
    )
    return {
        "status": "ok", "score": hs.score, "band": hs.band, "delta_7d": delta,
        "computed_at": hs.updated_at.isoformat(), "config_version": hs.config_version,
        "dimensions": dims,
        "top_recommendations": [_serialize_rec(r) for r in recs], "summary": summary,
    }


def _serialize_rec(r: HealthRecommendation) -> dict:
    return {
        "id": str(r.id), "dimension": r.dimension, "key": r.key, "title": r.title,
        "body": r.body, "estimated_lift": r.estimated_lift, "effort": r.effort.value,
        "status": r.status.value, "priority": r.priority,
    }


_RANGES: dict[str, int | None] = {"7d": 7, "30d": 30, "90d": 90, "all": None}


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
        "signals": [
            {
                "key": s.key,
                "value": float(s.value),
                "contribution": float(s.contribution),
                "source_ref": s.source_ref,
            }
            for s in signals
        ],
        "trend": trend,
        "recommendations": [_serialize_rec(r) for r in recs],
    }


def get_benchmarks(db: Session, startup: Startup) -> dict:
    cohort = {
        "stage": startup.stage.value if startup.stage else None,
        "industry": startup.industry,
    }
    # Cohort size counts peer startups sharing stage+industry that have a health score.
    # Real percentile aggregation is deferred to a later iteration; for now the
    # cohort-size gate is exercised but both branches return the insufficient_data
    # shape until that aggregation exists.
    peers = (
        db.query(HealthScore)
        .join(Startup, Startup.id == HealthScore.startup_id)
        .filter(Startup.stage == startup.stage, Startup.industry == startup.industry)
        .count()
    )
    if peers < MIN_COHORT_SIZE:
        return {
            "status": "insufficient_data", "cohort": cohort,
            "min_cohort_size": MIN_COHORT_SIZE, "percentiles": None,
        }
    return {
        "status": "insufficient_data", "cohort": cohort,
        "min_cohort_size": MIN_COHORT_SIZE, "percentiles": None,
    }


def list_recommendations(db: Session, startup: Startup, status_filter: str | None) -> list[dict]:
    q = db.query(HealthRecommendation).filter_by(startup_id=startup.id)
    if status_filter:
        q = q.filter(HealthRecommendation.status == RecommendationStatus(status_filter))
    else:
        q = q.filter(HealthRecommendation.status == RecommendationStatus.pending)
    rows = q.order_by(HealthRecommendation.priority.asc()).all()
    return [_serialize_rec(r) for r in rows]


def get_history(db: Session, startup: Startup, range_key: str) -> list[dict]:
    days = _RANGES[range_key]
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
        }
        for h in rows
    ]
