import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import FinancialModelStatus
from app.db.models.financial_model import FinancialModel
from app.platform.jobs import job_dispatcher
from app.schemas.financial_model import FinancialModelResponse, ModelStatement

MODEL_JOB_TYPE = "ai.finance.model"
# A `generating` row older than this is a lost job (worker down, payload dropped), not a running
# one; it is failed so the workspace is never stuck unable to generate.
STALE_AFTER = timedelta(minutes=15)


class ModelAlreadyGenerating(AppError):  # noqa: N818
    code, http_status = "MODEL_ALREADY_GENERATING", 409
    message = "A financial model is already being generated. Wait for it to finish."


def create_model(db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID) -> FinancialModel:
    """Start a generation, or raise 409 while one is `generating` for the workspace.

    Two layers: the row lock serialises concurrent callers through the check, and the partial
    unique index `uq_financial_models_one_generating` is the backstop that makes a second
    `generating` row impossible even if a caller bypasses this function.
    """
    running = (
        db.query(FinancialModel)
        .filter_by(startup_id=startup_id, status=FinancialModelStatus.generating)
        .with_for_update()
        .all()
    )
    now = datetime.now(UTC)
    for old in running:
        if now - old.created_at > STALE_AFTER:
            old.status = FinancialModelStatus.failed
            old.error = "Generation timed out"
        else:
            raise ModelAlreadyGenerating()
    db.flush()
    row = FinancialModel(
        startup_id=startup_id,
        created_by=created_by,
        status=FinancialModelStatus.generating,
        horizon_months=12,
        currency="NGN",
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        raise ModelAlreadyGenerating() from None
    db.flush()
    job_dispatcher.enqueue(db, MODEL_JOB_TYPE, {"model_id": str(row.id)}, startup_id)
    return row


def get_model(db: Session, *, startup_id: uuid.UUID, model_id: uuid.UUID) -> FinancialModel:
    row = db.query(FinancialModel).filter_by(id=model_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def latest_model(db: Session, *, startup_id: uuid.UUID) -> FinancialModel | None:
    return (
        db.query(FinancialModel)
        .filter_by(startup_id=startup_id)
        .order_by(FinancialModel.created_at.desc(), FinancialModel.id.desc())
        .first()
    )


def _statement(data: dict[str, Any] | None) -> ModelStatement | None:
    return ModelStatement.model_validate(data) if data else None


def serialize_model(row: FinancialModel) -> dict[str, Any]:
    # `months` lives inside the stored statement JSON (worker stores the engine output).
    months = next(
        (s["months"] for s in (row.pnl, row.cash_flow, row.balance_sheet) if s and s.get("months")),
        None,
    )
    resp = FinancialModelResponse(
        id=row.id,
        status=row.status.value,
        horizon_months=row.horizon_months,
        currency=row.currency,
        assumptions=row.assumptions,
        pnl=_statement(row.pnl),
        cash_flow=_statement(row.cash_flow),
        balance_sheet=_statement(row.balance_sheet),
        months=months,
        error=row.error,
        generated_at=row.generated_at,
        created_at=row.created_at,
    )
    return resp.model_dump(mode="json")
