import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.assessment import Assessment, AssessmentAnswer, AssessmentResult
from app.db.models.enums import AssessmentStatus, AssessmentType, Dimension
from app.db.models.health_score import HealthScore
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.assessment.bank import ASSESSMENT_BANK, QType, Question, question_by_key
from app.services.assessment.engine import next_question, progress, validate_answer
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
        "prompt": q.prompt,
        "options": q.options,
        "helper": q.helper,
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


def progress_for(db: Session, assessment: Assessment, startup: Startup) -> dict[str, int]:
    return progress(ASSESSMENT_BANK, answered_map(db, assessment), startup)


def _result_dict(
    assessment_id: uuid.UUID,
    dimension_scores: dict[str, int],
    overall_provisional: int,
    narrative: str,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "assessment_id": str(assessment_id),
        "status": "completed",
        "dimension_scores": dimension_scores,
        "overall_provisional": overall_provisional,
        "narrative": narrative,
        # None on a replayed /complete (the transition already happened; the before/after pair is
        # only knowable by the call that did the recompute).
        "health": health,
    }


def _health_change(
    previous_score: int | None, previous_dims: dict[str, int] | None, now: HealthScore | None
) -> dict[str, Any] | None:
    """D-ASM-4: the Health Score this completion produced, against the one just before it."""
    if now is None:
        return None
    prev_dims = previous_dims or {}
    dims = []
    for d in Dimension:
        score = int(now.dimension_scores.get(d.value, 0))
        prev = prev_dims.get(d.value)
        prev_i = int(prev) if prev is not None else None
        dims.append(
            {
                "key": d.value,
                "score": score,
                "previous": prev_i,
                "delta": None if prev_i is None else score - prev_i,
            }
        )
    return {
        "score": now.score,
        "previous_score": previous_score,
        "delta": None if previous_score is None else now.score - previous_score,
        "dimensions": dims,
    }


def complete_assessment(
    db: Session, assessment: Assessment, startup: Startup, actor_id: uuid.UUID | None = None
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
            {
                "status": AssessmentStatus.completed,
                "completed_at": now,
                "stage_at_time": startup.stage.value if startup.stage is not None else None,
            },
            synchronize_session=False,
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
    assessment.stage_at_time = startup.stage.value if startup.stage is not None else None

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
    # Read the before-picture BEFORE the recompute upserts over it (D-ASM-4).
    before = db.query(HealthScore).filter_by(startup_id=startup.id).first()
    previous_score = before.score if before is not None else None
    previous_dims = dict(before.dimension_scores) if before is not None else None
    health_now = recompute_health_score(db, startup, trigger="assessment_complete")
    job_dispatcher.enqueue(db, "ai.assessment.narrative", job_payload, startup.id)
    event_bus.publish(
        db,
        "assessment.completed",
        {
            "assessment_id": str(assessment.id),
            "startup_id": str(startup.id),
            "dimension_scores": scored["dimension_scores"],
            # lets the notification fan-out skip the person who just did it
            **({"actor_id": str(actor_id)} if actor_id is not None else {}),
        },
    )
    db.flush()
    return (
        _result_dict(
            assessment.id,
            scored["dimension_scores"],
            scored["overall_provisional"],
            scored["narrative"],
            _health_change(previous_score, previous_dims, health_now),
        ),
        True,
    )


def quarterly_due(db: Session, startup_id: uuid.UUID, now: datetime | None = None) -> bool:
    """True when a quarterly re-assessment is due.

    Deliberately the same rule as the cron (`app.worker.scheduler._due_quarterly`): the latest
    COMPLETED assessment is at least `QUARTERLY_REASSESS_DAYS` old and no assessment is already in
    progress. The cron additionally de-duplicates per quarter (`ScheduledRun`); that is about not
    re-notifying, not about whether the founder is due, so it is not part of this flag.
    """
    now = now or datetime.now(UTC)
    last = (
        db.query(func.max(Assessment.completed_at))
        .filter(
            Assessment.startup_id == startup_id, Assessment.status == AssessmentStatus.completed
        )
        .scalar()
    )
    if last is None or last > now - timedelta(days=settings.QUARTERLY_REASSESS_DAYS):
        return False
    in_progress = (
        db.query(Assessment.id)
        .filter(
            Assessment.startup_id == startup_id, Assessment.status == AssessmentStatus.in_progress
        )
        .first()
    )
    return in_progress is None


def answer_label(q: Question | None, value: Any) -> str:
    """The human-readable form of a stored answer (choice label, scale number, amount, text)."""
    if q is None:
        return str(value)
    labels = {o["value"]: o["label"] for o in (q.options or [])}
    if q.qtype == QType.SINGLE_CHOICE and not isinstance(value, list | dict):
        return str(labels.get(value, value))
    if q.qtype == QType.MULTI_CHOICE and isinstance(value, list):
        return ", ".join(str(labels.get(v, v)) for v in value)
    if q.qtype == QType.NUMERIC_CURRENCY and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return bool(a == b)


def previous_completed(db: Session, assessment: Assessment) -> Assessment | None:
    """The latest COMPLETED assessment of the same startup that finished before `assessment`
    (or, for one still in progress, the latest completed one)."""
    q = db.query(Assessment).filter(
        Assessment.startup_id == assessment.startup_id,
        Assessment.status == AssessmentStatus.completed,
        Assessment.id != assessment.id,
    )
    if assessment.completed_at is not None:
        q = q.filter(Assessment.completed_at < assessment.completed_at)
    return q.order_by(Assessment.completed_at.desc()).first()


def answer_groups(db: Session, assessment: Assessment) -> list[dict[str, Any]]:
    """Structured answers grouped by dimension, each with prompt, human label and change marker."""
    current = answered_map(db, assessment)
    prev = previous_completed(db, assessment)
    prev_answers = answered_map(db, prev) if prev is not None else {}

    order = {q.key: i for i, q in enumerate(ASSESSMENT_BANK.questions)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(current, key=lambda k: (order.get(k, len(order)), k)):
        q = question_by_key(ASSESSMENT_BANK, key)
        value = current[key]
        changed = key in prev_answers and not _same(prev_answers[key], value)
        groups.setdefault(q.dimension.value if q is not None else "unknown", []).append(
            {
                "question_key": key,
                "prompt": q.prompt if q is not None else None,
                "qtype": q.qtype if q is not None else None,
                "value": value,
                "answer_label": answer_label(q, value),
                "changed": changed,
                "changed_from": answer_label(q, prev_answers[key]) if changed else None,
            }
        )
    dims = [d.value for d in Dimension]
    return [{"dimension": d, "questions": groups[d]} for d in [*dims, "unknown"] if d in groups]
