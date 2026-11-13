import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.legal import LegalFormationProgress
from app.services.legal.catalog import (
    ESTIMATE_DISCLAIMER,
    JURISDICTIONS,
    FormationStepDef,
    steps_for,
)


def _require_jurisdiction(jurisdiction: str) -> tuple[FormationStepDef, ...]:
    steps = steps_for(jurisdiction)
    if steps is None:
        raise NotFound()
    return steps


def checklist(db: Session, *, startup_id: uuid.UUID, jurisdiction: str) -> dict[str, Any]:
    """The catalog for one jurisdiction, merged with this workspace's progress.

    One query for progress, then a merge in catalog order. `completed_at` is returned as an ISO
    string, the same way the other derived reads in this codebase serialise timestamps.
    """
    steps = _require_jurisdiction(jurisdiction)
    done: dict[str, Any] = {
        row.step_key: row.completed_at
        for row in db.query(LegalFormationProgress)
        .filter_by(startup_id=startup_id, jurisdiction=jurisdiction)
        .all()
    }
    return {
        "jurisdiction": {"code": jurisdiction, "name": JURISDICTIONS[jurisdiction]},
        # Said out loud so no client can present the catalog figures as verified fact (spec D6).
        "estimates": True,
        "disclaimer": ESTIMATE_DISCLAIMER,
        "steps": [
            {
                "key": step.key,
                "title": step.title,
                "authority": step.authority,
                "est_days": step.est_days,
                "est_cost_minor": step.est_cost_minor,
                "est_cost_currency": step.est_cost_currency,
                "completed": step.key in done,
                "completed_at": done[step.key].isoformat() if step.key in done else None,
            }
            for step in steps
        ],
    }


def set_step(
    db: Session,
    *,
    startup_id: uuid.UUID,
    jurisdiction: str,
    step_key: str,
    completed: bool,
    actor_id: uuid.UUID | None,
) -> None:
    """Tick or untick one step. A row's existence IS the tick (spec D7).

    The jurisdiction and key are validated against the catalog **before** any write, so a
    rejected key never leaves a half-written row.
    """
    steps = _require_jurisdiction(jurisdiction)
    if step_key not in {step.key for step in steps}:
        raise NotFound()

    if not completed:
        # Unticking deletes the row. Unticking something never ticked is a harmless no-op.
        db.query(LegalFormationProgress).filter_by(
            startup_id=startup_id, jurisdiction=jurisdiction, step_key=step_key
        ).delete(synchronize_session=False)
        db.flush()
        return

    try:
        # Inside a SAVEPOINT: a duplicate tick hits uq_legal_formation_progress_step, and only
        # this insert is rolled back, leaving the caller's transaction usable.
        with db.begin_nested():
            db.add(
                LegalFormationProgress(
                    startup_id=startup_id,
                    jurisdiction=jurisdiction,
                    step_key=step_key,
                    completed_by=actor_id,
                )
            )
            db.flush()
    except IntegrityError:
        # Already ticked. Idempotent by design: the FE sends the desired state, not a delta.
        return
