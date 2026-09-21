import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.assessment import Assessment, AssessmentAnswer, AssessmentResult
from app.db.models.enums import AssessmentStatus, AssessmentType
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.assessment.bank import ASSESSMENT_BANK, Question
from app.services.assessment.engine import next_question, validate_answer
from app.services.assessment.scoring import score


def answered_map(db: Session, assessment: Assessment) -> dict[str, Any]:
    rows = db.query(AssessmentAnswer).filter(AssessmentAnswer.assessment_id == assessment.id).all()
    return {r.question_key: r.value_json for r in rows}


def serialize_question(q: Question | None) -> dict | None:
    if q is None:
        return None
    return {
        "key": q.key,
        "dimension": q.dimension.value,
        "section": q.section,
        "qtype": q.qtype,
        "options": q.options,
    }


def start_or_resume(db: Session, startup: Startup, user: User) -> Assessment:
    existing = (
        db.query(Assessment)
        .filter(
            Assessment.startup_id == startup.id,
            Assessment.status == AssessmentStatus.in_progress,
        )
        .first()
    )
    if existing is not None:
        return existing
    has_completed = (
        db.query(Assessment)
        .filter(
            Assessment.startup_id == startup.id,
            Assessment.status == AssessmentStatus.completed,
        )
        .first()
        is not None
    )
    # Insert-or-get: a plain read-then-insert here is a TOCTOU race -- two concurrent
    # POST /assessments for the SAME startup both see no in_progress assessment above,
    # both INSERT, and the loser trips the partial unique index
    # uq_assessments_startup_in_progress as an uncaught IntegrityError (500). The intended
    # behavior is RESUME, not error: wrap the INSERT in a SAVEPOINT so a unique violation
    # only unwinds the savepoint (not the whole transaction), then re-select the
    # now-committed winner's row.
    try:
        with db.begin_nested():
            a = Assessment(
                startup_id=startup.id,
                created_by=user.id,
                type=AssessmentType.quarterly if has_completed else AssessmentType.initial,
                status=AssessmentStatus.in_progress,
                bank_version=ASSESSMENT_BANK.version,
            )
            db.add(a)
            db.flush()
        return a
    except IntegrityError:
        # A concurrent caller won the race -- resume their in_progress assessment.
        return (
            db.query(Assessment)
            .filter(
                Assessment.startup_id == startup.id,
                Assessment.status == AssessmentStatus.in_progress,
            )
            .one()
        )


def submit_answer(
    db: Session, assessment: Assessment, startup: Startup, question_key: str, value: Any
) -> Question | None:
    if assessment.status != AssessmentStatus.in_progress:
        raise AppError("INVALID_ANSWER", "This assessment is not in progress.", 422)
    answers = answered_map(db, assessment)
    current = next_question(ASSESSMENT_BANK, answers, startup)
    if current is None or current.key != question_key:
        raise AppError("INVALID_ANSWER", "That isn't the current question.", 422)
    validate_answer(current, value)
    # Atomic upsert: a query-then-insert/update here would be a TOCTOU race — two
    # concurrent submits of the SAME current question both pass the gate above, both
    # try to INSERT, and the loser trips the (assessment_id, question_key) unique
    # constraint as an uncaught IntegrityError (500). ON CONFLICT DO UPDATE pushes the
    # dedup down to Postgres, so the race resolves to one row with no exception either
    # side of the connection pool sees.
    stmt = (
        pg_insert(AssessmentAnswer)
        .values(assessment_id=assessment.id, question_key=question_key, value_json=value)
        .on_conflict_do_update(
            index_elements=["assessment_id", "question_key"],
            set_={"value_json": value},
        )
    )
    db.execute(stmt)
    db.flush()
    return next_question(ASSESSMENT_BANK, answered_map(db, assessment), startup)


def _result_dict(
    assessment_id: uuid.UUID,
    dimension_scores: dict[str, int],
    overall_provisional: int,
    narrative: str,
) -> dict[str, Any]:
    return {
        "assessment_id": str(assessment_id),
        "status": "completed",
        "dimension_scores": dimension_scores,
        "overall_provisional": overall_provisional,
        "narrative": narrative,
    }


def complete_assessment(
    db: Session, assessment: Assessment, startup: Startup
) -> tuple[dict[str, Any], bool]:
    """Complete `assessment`, returning `(result, claimed)`.

    `claimed` is True only for the caller whose UPDATE actually won the atomic
    in_progress -> completed transition below; it is False when this call found the
    assessment already completed (by a prior call or a concurrent racer that won).
    `result` is the same scored-result dict either way. Callers that must fire a
    side effect exactly once per real completion (e.g. writing an activity-log row)
    should gate on `claimed`, not on a pre-call status read -- see the TOCTOU note
    on the atomic claim below.
    """
    answers = answered_map(db, assessment)
    nq = next_question(ASSESSMENT_BANK, answers, startup)
    if nq is not None:
        raise AppError(
            "ASSESSMENT_INCOMPLETE",
            f"'{nq.key}' still needs an answer before this assessment can be completed.",
            422,
            field_errors=[{"field": nq.key, "message": "Please answer this question."}],
        )

    now = datetime.now(UTC)
    # Atomic claim: consume the in_progress -> completed transition only if this call is
    # the one that finds it in_progress. A plain read-then-write here (check
    # assessment.status, then write) is the same TOCTOU race as rotate_refresh
    # (app/services/auth/sessions.py) -- two concurrent completions of the same fully
    # answered assessment would both pass the check before either commits, and both would
    # score + enqueue jobs + publish the event. Conditioning the UPDATE itself on
    # status == in_progress makes only one concurrent caller's UPDATE match (Postgres
    # row-locks the row for the transaction; the loser's UPDATE blocks until the winner
    # commits, then re-evaluates against the now-committed row and matches zero rows).
    claimed = (
        db.query(Assessment)
        .filter(Assessment.id == assessment.id, Assessment.status == AssessmentStatus.in_progress)
        .update(
            {"status": AssessmentStatus.completed, "completed_at": now}, synchronize_session=False
        )
    )
    if claimed == 0:
        # Already completed -- by us on a prior call, or by a concurrent caller that just
        # won the race. Either way, return the stored result without re-scoring or
        # re-enqueuing: the side effects below must fire exactly once.
        stored = db.query(AssessmentResult).filter_by(assessment_id=assessment.id).first()
        if stored is None:
            # Lost the race but the winner hasn't committed its AssessmentResult yet from
            # this transaction's point of view. Shouldn't happen with the blocking UPDATE
            # above (the winner's commit is what unblocks us), but fail loudly rather than
            # silently returning an empty result if it ever does.
            raise AppError(
                "ASSESSMENT_INCOMPLETE",
                "This assessment is being completed by another request. Try again shortly.",
                409,
            )
        return (
            _result_dict(
                assessment.id,
                stored.dimension_scores,
                stored.overall_provisional,
                stored.narrative,
            ),
            False,
        )

    # We won the claim -- keep the in-memory object in sync with what we just committed.
    assessment.status = AssessmentStatus.completed
    assessment.completed_at = now

    scored = score(ASSESSMENT_BANK, answers, startup)
    db.add(
        AssessmentResult(
            assessment_id=assessment.id,
            dimension_scores=scored["dimension_scores"],
            overall_provisional=scored["overall_provisional"],
            narrative=scored["narrative"],
        )
    )
    if assessment.type == AssessmentType.initial:
        startup.profile.assessment_pending = False

    job_payload = {"startup_id": str(startup.id), "assessment_id": str(assessment.id)}
    from app.services.health_score.service import recompute_health_score

    # Flush the just-added AssessmentResult before recomputing: SessionLocal is
    # autoflush=False (app/db/session.py), so recompute_health_score's first query
    # (latest_completed_result, a SELECT joining Assessment -> AssessmentResult) would
    # otherwise not see this unflushed row, bail, and produce no HealthScore/recommendations/
    # ai.health.recommendations job from this request -- masked only by get_overview's
    # lazy-on-read recompute on a later GET /health-score.
    db.flush()
    recompute_health_score(db, startup, trigger="assessment_complete")
    job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)
    job_dispatcher.enqueue(db, "ai.assessment.narrative", job_payload, startup.id)
    event_bus.publish(
        db,
        "assessment.completed",
        {
            "assessment_id": str(assessment.id),
            "startup_id": str(startup.id),
            "dimension_scores": scored["dimension_scores"],
        },
    )
    db.flush()
    return (
        _result_dict(
            assessment.id,
            scored["dimension_scores"],
            scored["overall_provisional"],
            scored["narrative"],
        ),
        True,
    )
