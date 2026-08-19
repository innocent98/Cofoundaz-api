import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.enums import AssessmentStatus, Dimension
from app.db.models.health_score import HealthScore, HealthScoreHistory, HealthSignal
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.health_score.config import DIMENSION_WEIGHTS, HEALTH_CONFIG_VERSION
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

    # 4. Recommendations — added in Task 5:
    #    generate_recommendations(db, startup.id, dim_scores)

    db.flush()
    hs = db.query(HealthScore).filter_by(startup_id=startup.id).first()

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
