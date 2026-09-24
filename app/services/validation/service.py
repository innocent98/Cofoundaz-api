"""Validation Hub service (Module 09) — assumptions, experiments, interviews and surveys.

Services flush; the endpoints commit (spec section 5). Everything here takes ``startup_id`` from
the caller's membership, never from a request body.
"""

import secrets
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)
from app.db.models.validation import Assumption, Experiment, Interview, Survey, SurveyResponse
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token
from app.services.validation.questions import validate_questions

# Only these two transitions are worth telling the rest of the system about (spec D8).
_EVENT_BY_STATUS = {
    AssumptionStatus.validated: "validation.assumption.validated",
    AssumptionStatus.invalidated: "validation.assumption.invalidated",
}


def link_ids(db: Session, startup_id: uuid.UUID, assumption_ids: Any) -> list[str]:
    """Clean a list of assumption links, rejecting anything outside this workspace (spec D9)."""
    if assumption_ids is None:
        return []
    if not isinstance(assumption_ids, list):
        raise AppError("VALIDATION_ERROR", "Assumption links must be a list.", 422)
    wanted = [str(value) for value in assumption_ids]
    if not wanted:
        return []
    known = {str(row.id) for row in db.query(Assumption.id).filter_by(startup_id=startup_id).all()}
    if [value for value in wanted if value not in known]:
        raise AppError("VALIDATION_ERROR", "Unknown assumption link.", 422)
    return wanted


# --- assumptions ---------------------------------------------------------------------------


def list_assumptions(
    db: Session,
    startup_id: uuid.UUID,
    *,
    status: AssumptionStatus | None = None,
    risk: RiskLevel | None = None,
) -> list[Assumption]:
    query = db.query(Assumption).filter_by(startup_id=startup_id)
    if status is not None:
        query = query.filter(Assumption.status == status)
    if risk is not None:
        query = query.filter(Assumption.risk == risk)
    return query.order_by(Assumption.created_at.desc(), Assumption.id.desc()).all()


def get_assumption(db: Session, startup_id: uuid.UUID, assumption_id: Any) -> Assumption:
    row = db.query(Assumption).filter_by(id=assumption_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_assumption(
    db: Session,
    startup_id: uuid.UUID,
    *,
    statement: str,
    risk: RiskLevel,
    status: AssumptionStatus | None = None,
) -> Assumption:
    row = Assumption(
        startup_id=startup_id,
        statement=statement.strip(),
        risk=risk,
        status=status or AssumptionStatus.untested,
    )
    db.add(row)
    db.flush()
    return row


def update_assumption(
    db: Session,
    assumption: Assumption,
    *,
    actor_id: uuid.UUID,
    statement: str | None = None,
    risk: RiskLevel | None = None,
    status: AssumptionStatus | None = None,
) -> Assumption:
    """Edit an assumption.

    Any status may follow any other; only an actual move into validated or invalidated publishes
    an event, so a repeated save cannot double-fire it.
    """
    if statement is not None:
        assumption.statement = statement.strip()
    if risk is not None:
        assumption.risk = risk
    event = None
    if status is not None and status != assumption.status:
        assumption.status = status
        event = _EVENT_BY_STATUS.get(status)
    db.flush()
    if event is not None:
        event_bus.publish(
            db,
            event,
            {
                "startup_id": str(assumption.startup_id),
                "assumption_id": str(assumption.id),
                "status": assumption.status.value,
                "actor_id": str(actor_id),
            },
        )
    return assumption


def evidence_counts(
    db: Session, startup_id: uuid.UUID, assumptions: list[Assumption]
) -> dict[str, int]:
    """How many experiments and interviews link to each assumption (spec D2).

    Derived on read so it can never disagree with the rows it counts. Only this workspace's
    experiments and interviews are looked at.
    """
    counts = {str(row.id): 0 for row in assumptions}
    if not counts:
        return counts
    linked = [
        row.assumption_ids
        for row in db.query(Experiment.assumption_ids).filter_by(startup_id=startup_id).all()
    ] + [
        row.assumption_ids
        for row in db.query(Interview.assumption_ids).filter_by(startup_id=startup_id).all()
    ]
    for ids in linked:
        for value in ids or []:
            if str(value) in counts:
                counts[str(value)] += 1
    return counts


def serialize_assumption(assumption: Assumption, evidence_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(assumption.id),
        "statement": assumption.statement,
        "risk": assumption.risk.value,
        "status": assumption.status.value,
        "evidence_count": evidence_count,
        "created_at": assumption.created_at.isoformat(),
        "updated_at": assumption.updated_at.isoformat(),
    }


# --- experiments ---------------------------------------------------------------------------


def list_experiments(
    db: Session,
    startup_id: uuid.UUID,
    *,
    type: ExperimentType | None = None,
    status: ExperimentStatus | None = None,
) -> list[Experiment]:
    query = db.query(Experiment).filter_by(startup_id=startup_id)
    if type is not None:
        query = query.filter(Experiment.type == type)
    if status is not None:
        query = query.filter(Experiment.status == status)
    return query.order_by(Experiment.created_at.desc(), Experiment.id.desc()).all()


def get_experiment(db: Session, startup_id: uuid.UUID, experiment_id: Any) -> Experiment:
    row = db.query(Experiment).filter_by(id=experiment_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_experiment(
    db: Session,
    startup_id: uuid.UUID,
    *,
    name: str,
    type: ExperimentType,
    config: dict[str, Any] | None = None,
    status: ExperimentStatus | None = None,
    metrics: dict[str, Any] | None = None,
    assumption_ids: Any = None,
) -> Experiment:
    row = Experiment(
        startup_id=startup_id,
        name=name.strip(),
        type=type,
        config=config or {},
        status=status or ExperimentStatus.draft,
        metrics=metrics or {},
        assumption_ids=link_ids(db, startup_id, assumption_ids),
    )
    db.add(row)
    db.flush()
    return row


def update_experiment(
    db: Session,
    experiment: Experiment,
    *,
    name: str | None = None,
    type: ExperimentType | None = None,
    config: dict[str, Any] | None = None,
    status: ExperimentStatus | None = None,
    metrics: dict[str, Any] | None = None,
    assumption_ids: Any = None,
) -> Experiment:
    if name is not None:
        experiment.name = name.strip()
    if type is not None:
        experiment.type = type
    if config is not None:
        experiment.config = config
    if status is not None:
        experiment.status = status
    if metrics is not None:
        experiment.metrics = metrics
    if assumption_ids is not None:
        experiment.assumption_ids = link_ids(db, experiment.startup_id, assumption_ids)
    db.flush()
    return experiment


def serialize_experiment(experiment: Experiment) -> dict[str, Any]:
    return {
        "id": str(experiment.id),
        "name": experiment.name,
        "type": experiment.type.value,
        "status": experiment.status.value,
        "config": experiment.config,
        "metrics": experiment.metrics,
        "assumption_ids": experiment.assumption_ids,
        "created_at": experiment.created_at.isoformat(),
        "updated_at": experiment.updated_at.isoformat(),
    }


def _counter(value: Any) -> int:
    """Read one metric as a count; anything odd (missing, text, negative) reads as 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def smoke_test_stats(db: Session, startup_id: uuid.UUID, experiment_id: Any) -> dict[str, Any]:
    """Funnel read for a smoke test.

    404 for anything that is not one, so the route cannot be used to discover other experiment
    types.
    """
    experiment = get_experiment(db, startup_id, experiment_id)
    if experiment.type != ExperimentType.smoke_test:
        raise NotFound()
    metrics = experiment.metrics or {}
    visits = _counter(metrics.get("visits"))
    signups = _counter(metrics.get("signups"))
    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status.value,
        "visits": visits,
        "signups": signups,
        "conversion": round(100 * signups / visits, 1) if visits else 0.0,
    }


# --- interviews ----------------------------------------------------------------------------


def _quotes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError("VALIDATION_ERROR", "Key quotes must be a list.", 422)
    if any(not isinstance(quote, str) or not quote.strip() for quote in value):
        raise AppError("VALIDATION_ERROR", "Each key quote must be non-empty text.", 422)
    return [quote.strip() for quote in value]


def list_interviews(
    db: Session,
    startup_id: uuid.UUID,
    *,
    segment: str | None = None,
    verdict: InterviewVerdict | None = None,
    assumption_id: str | None = None,
) -> list[Interview]:
    query = db.query(Interview).filter_by(startup_id=startup_id)
    if segment is not None:
        query = query.filter(Interview.segment == segment)
    if verdict is not None:
        query = query.filter(Interview.verdict == verdict)
    rows = query.order_by(Interview.held_on.desc(), Interview.created_at.desc()).all()
    if assumption_id is not None:
        rows = [row for row in rows if str(assumption_id) in (row.assumption_ids or [])]
    return rows


def get_interview(db: Session, startup_id: uuid.UUID, interview_id: Any) -> Interview:
    row = db.query(Interview).filter_by(id=interview_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_interview(
    db: Session,
    startup_id: uuid.UUID,
    *,
    interviewee: str,
    held_on: date,
    verdict: InterviewVerdict,
    segment: str | None = None,
    notes: str = "",
    key_quotes: Any = None,
    assumption_ids: Any = None,
) -> Interview:
    row = Interview(
        startup_id=startup_id,
        interviewee=interviewee.strip(),
        segment=segment,
        held_on=held_on,
        notes=notes or "",
        key_quotes=_quotes(key_quotes),
        verdict=verdict,
        assumption_ids=link_ids(db, startup_id, assumption_ids),
    )
    db.add(row)
    db.flush()
    return row


def update_interview(
    db: Session,
    interview: Interview,
    *,
    interviewee: str | None = None,
    segment: str | None = None,
    held_on: date | None = None,
    notes: str | None = None,
    key_quotes: Any = None,
    verdict: InterviewVerdict | None = None,
    assumption_ids: Any = None,
) -> Interview:
    if interviewee is not None:
        interview.interviewee = interviewee.strip()
    if segment is not None:
        interview.segment = segment
    if held_on is not None:
        interview.held_on = held_on
    if notes is not None:
        interview.notes = notes
    if key_quotes is not None:
        interview.key_quotes = _quotes(key_quotes)
    if verdict is not None:
        interview.verdict = verdict
    if assumption_ids is not None:
        interview.assumption_ids = link_ids(db, interview.startup_id, assumption_ids)
    db.flush()
    return interview


def serialize_interview(interview: Interview) -> dict[str, Any]:
    return {
        "id": str(interview.id),
        "interviewee": interview.interviewee,
        "segment": interview.segment,
        "held_on": interview.held_on.isoformat(),
        "notes": interview.notes,
        "key_quotes": interview.key_quotes,
        "verdict": interview.verdict.value,
        "assumption_ids": interview.assumption_ids,
        "created_at": interview.created_at.isoformat(),
        "updated_at": interview.updated_at.isoformat(),
    }


# --- surveys -------------------------------------------------------------------------------


def list_surveys(db: Session, startup_id: uuid.UUID) -> list[Survey]:
    return (
        db.query(Survey)
        .filter_by(startup_id=startup_id)
        .order_by(Survey.created_at.desc(), Survey.id.desc())
        .all()
    )


def get_survey(db: Session, startup_id: uuid.UUID, survey_id: Any) -> Survey:
    row = db.query(Survey).filter_by(id=survey_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_survey(
    db: Session, startup_id: uuid.UUID, *, title: str, questions: Any = None
) -> Survey:
    row = Survey(
        startup_id=startup_id,
        title=title.strip(),
        questions=validate_questions(questions or []),
    )
    db.add(row)
    db.flush()
    return row


def update_survey(
    db: Session,
    survey: Survey,
    *,
    title: str | None = None,
    questions: Any = None,
    status: SurveyStatus | None = None,
) -> tuple[Survey, str | None]:
    """Edit a survey.

    Opening it for the first time mints the public token and returns the raw value **once**; only
    its hash is stored, and re-opening never hands it out again (spec section 4).
    """
    raw: str | None = None
    if title is not None:
        survey.title = title.strip()
    if questions is not None:
        survey.questions = validate_questions(questions)
    if status is not None:
        survey.status = status
        if status == SurveyStatus.open and survey.token_hash is None:
            raw = secrets.token_urlsafe(32)
            survey.token_hash = hash_token(raw)
    db.flush()
    return survey, raw


def response_counts(db: Session, startup_id: uuid.UUID, surveys: list[Survey]) -> dict[str, int]:
    counts = {str(row.id): 0 for row in surveys}
    if not counts:
        return counts
    rows = (
        db.query(SurveyResponse.survey_id, func.count(SurveyResponse.id))
        .filter(SurveyResponse.startup_id == startup_id)
        .group_by(SurveyResponse.survey_id)
        .all()
    )
    for survey_id, count in rows:
        if str(survey_id) in counts:
            counts[str(survey_id)] = count
    return counts


def serialize_survey(survey: Survey, response_count: int = 0) -> dict[str, Any]:
    """The owner's view of a survey. The token never appears here, in any form."""
    return {
        "id": str(survey.id),
        "title": survey.title,
        "status": survey.status.value,
        "questions": survey.questions,
        "response_count": response_count,
        "has_link": survey.token_hash is not None,
        "created_at": survey.created_at.isoformat(),
        "updated_at": survey.updated_at.isoformat(),
    }


def survey_analytics(db: Session, startup_id: uuid.UUID, survey_id: Any) -> dict[str, Any]:
    """Per-question counts and the completion rate (spec section 5).

    Open answers are counted, never listed: reading raw responses is a follow-up.
    """
    survey = get_survey(db, startup_id, survey_id)
    rows = db.query(SurveyResponse).filter_by(survey_id=survey.id).all()
    total = len(rows)
    questions = survey.questions or []
    required = [question["id"] for question in questions if question.get("required")]
    complete = sum(1 for row in rows if all(key in (row.answers or {}) for key in required))
    out: list[dict[str, Any]] = []
    for question in questions:
        given = [
            (row.answers or {})[question["id"]]
            for row in rows
            if question["id"] in (row.answers or {})
        ]
        entry: dict[str, Any] = {
            "id": question["id"],
            "type": question["type"],
            "prompt": question["prompt"],
            "answered": len(given),
        }
        if question["type"] == "choice":
            entry["counts"] = {
                option: sum(1 for value in given if value == option)
                for option in question.get("options", [])
            }
        elif question["type"] in ("scale", "nps"):
            numbers = [
                value for value in given if isinstance(value, int) and not isinstance(value, bool)
            ]
            entry["counts"] = {str(value): numbers.count(value) for value in sorted(set(numbers))}
            entry["average"] = round(sum(numbers) / len(numbers), 1) if numbers else 0.0
        out.append(entry)
    return {
        "survey_id": str(survey.id),
        "title": survey.title,
        "status": survey.status.value,
        "responses": total,
        "completion_rate": round(100 * complete / total) if total else 0,
        "questions": out,
    }
