import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.enums import CallPrepStatus
from app.db.models.sales_coach import SalesCallPrep
from app.platform.jobs import job_dispatcher
from app.services.sales.deals import get_deal

JOB = "ai.sales.call_prep"


def latest_prep(db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID) -> SalesCallPrep | None:
    return db.scalar(
        select(SalesCallPrep)
        .where(SalesCallPrep.startup_id == startup_id, SalesCallPrep.deal_id == deal_id)
        .order_by(SalesCallPrep.created_at.desc(), SalesCallPrep.id)
        .limit(1)
    )


def request_prep(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, deal_id: uuid.UUID
) -> SalesCallPrep:
    """Queue a call-prep sheet for a deal. One may be in flight per deal; regenerating once the
    last one finished adds a new row (the old one stays as history)."""
    # The deal row lock serializes concurrent requests, so two clicks can't both pass the check.
    get_deal(db, startup_id, deal_id, lock=True)
    current = latest_prep(db, startup_id, deal_id)
    if current is not None and current.status == CallPrepStatus.generating:
        raise AppError(
            "CALL_PREP_IN_PROGRESS", "A call prep is already being written for this deal.", 409
        )
    row = SalesCallPrep(
        startup_id=startup_id,
        deal_id=deal_id,
        requested_by=user_id,
        status=CallPrepStatus.generating,
        # Explicit, not the column default: Postgres now() is the transaction start, so two
        # preps written in one transaction would tie and "latest" would be arbitrary.
        created_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    job_dispatcher.enqueue(
        db, JOB, {"prep_id": str(row.id), "startup_id": str(startup_id)}, startup_id
    )
    return row


def serialize_prep(p: SalesCallPrep) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "deal_id": str(p.deal_id),
        "status": p.status.value,
        "output": p.output_json,
        "error": p.error,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
