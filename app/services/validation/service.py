"""Validation Hub service (Module 09) — assumptions and experiments.

Services flush; the endpoints commit (spec section 5). Everything here takes ``startup_id`` from
the caller's membership, never from a request body.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import AssumptionStatus, ExperimentStatus, ExperimentType, RiskLevel
from app.db.models.validation import Assumption, Experiment, Interview
from app.platform.events import event_bus

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
