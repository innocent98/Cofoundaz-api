import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import FinancialModelStatus
from app.db.models.financial_model import FinancialModel
from app.platform.jobs import job_dispatcher
from app.schemas.financial_model import (
    BalanceCheck,
    FinancialModelResponse,
    ModelAssumptionsPatch,
    ModelStatement,
    YearlyRollup,
)
from app.services.finance.model_config import MODEL_HORIZON_MONTHS
from app.services.finance.model_engine import (
    project_three_statements,
    rollup_yearly,
    yearly_periods,
)
from app.services.finance.model_inputs import build_starting

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
        horizon_months=MODEL_HORIZON_MONTHS,
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


class ModelNotReady(AppError):  # noqa: N818
    code, http_status = "MODEL_NOT_READY", 409
    message = "The model is not ready yet. Wait for it to finish generating."


def _statement(data: dict[str, Any] | None) -> ModelStatement | None:
    return ModelStatement.model_validate(data) if data else None


def _months_of(row: FinancialModel) -> list[str] | None:
    # `months` lives inside the stored statement JSON (the engine output is stored whole).
    return next(
        (s["months"] for s in (row.pnl, row.cash_flow, row.balance_sheet) if s and s.get("months")),
        None,
    )


def _balance_check(row: FinancialModel) -> BalanceCheck | None:
    if not row.balance_sheet:
        return None
    for r in row.balance_sheet["rows"]:
        if r["label"] == "Balance check":
            worst = max((abs(v) for v in r["values"]), default=0)
            return BalanceCheck(balanced=worst == 0, max_abs_difference_minor=worst)
    return None  # generated before the balance sheet carried a check line


def _effective_assumptions(row: FinancialModel) -> dict[str, Any] | None:
    if row.assumptions is None:
        return None
    merged: dict[str, Any] = {**row.assumptions, **(row.overrides or {})}
    starting = effective_starting(row)
    if starting is not None:
        merged["revenue_0_minor"] = starting["revenue_0"]
        merged["monthly_costs_0_minor"] = starting["monthly_costs_0"]
    return merged


def effective_starting(row: FinancialModel) -> dict[str, Any] | None:
    """The stored starting figures with the founder's start-revenue / start-cost edits applied."""
    if row.starting is None:
        return None
    ov = row.overrides or {}
    out = dict(row.starting)
    if ov.get("revenue_0_minor") is not None:
        out["revenue_0"] = int(ov["revenue_0_minor"])
    if ov.get("monthly_costs_0_minor") is not None:
        out["monthly_costs_0"] = int(ov["monthly_costs_0_minor"])
    return out


def serialize_model(row: FinancialModel, warnings: list[str] | None = None) -> dict[str, Any]:
    months = _months_of(row)
    pnl, cash_flow, balance_sheet = row.pnl, row.cash_flow, row.balance_sheet
    yearly = None
    if months and row.status == FinancialModelStatus.complete:
        yearly = YearlyRollup(
            periods=yearly_periods(months),  # type: ignore[arg-type]
            pnl=_statement(rollup_yearly(pnl, months)),
            cash_flow=_statement(rollup_yearly(cash_flow, months)),
            balance_sheet=_statement(rollup_yearly(balance_sheet, months)),
        )
    resp = FinancialModelResponse(
        id=row.id,
        status=row.status.value,
        horizon_months=row.horizon_months,
        currency=row.currency,
        assumptions=_effective_assumptions(row),
        ai_assumptions=row.assumptions,
        overrides=row.overrides or None,
        stale=row.overrides_rev != row.calculated_rev,
        pnl=_statement(pnl),
        cash_flow=_statement(cash_flow),
        balance_sheet=_statement(balance_sheet),
        months=months,
        yearly=yearly,
        balance_check=_balance_check(row),
        warnings=warnings or [],
        error=row.error,
        generated_at=row.generated_at,
        recalculated_at=row.recalculated_at,
        created_at=row.created_at,
    )
    return resp.model_dump(mode="json")


def _locked_complete(db: Session, *, startup_id: uuid.UUID, model_id: uuid.UUID) -> FinancialModel:
    row = (
        db.query(FinancialModel)
        .filter_by(id=model_id, startup_id=startup_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise NotFound()
    if row.status != FinancialModelStatus.complete:
        raise ModelNotReady()
    return row


def _month_errors(patch_values: dict[str, Any], months: list[str]) -> list[dict[str, str]]:
    lo, hi = months[0], months[-1]
    errors: list[dict[str, str]] = []
    for name, key in (("hiring_plan", "start_month"), ("one_offs", "month"), ("capex", "month")):
        for n, item in enumerate(patch_values.get(name) or []):
            if not lo <= item[key] <= hi:
                errors.append(
                    {
                        "field": f"{name}[{n}].{key}",
                        "message": f"Choose a month between {lo} and {hi}.",
                    }
                )
    return errors


def update_assumptions(
    db: Session, *, startup_id: uuid.UUID, model_id: uuid.UUID, patch: ModelAssumptionsPatch
) -> FinancialModel:
    """Store the founder's edits. Nothing is recalculated here and no AI is involved."""
    row = _locked_complete(db, startup_id=startup_id, model_id=model_id)
    months = _months_of(row)
    values = patch.model_dump(exclude_unset=True)
    if months:
        errors = _month_errors(values, months)
        if errors:
            raise AppError(
                "VALIDATION_ERROR",
                "Please check the highlighted fields.",
                422,
                field_errors=errors,
            )
    overrides = dict(row.overrides or {})
    for key, value in values.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    row.overrides = overrides or None
    row.overrides_rev += 1
    db.flush()
    return row


def _anchor(row: FinancialModel, starting: dict[str, Any]) -> date:
    """The 'today' the projection months count from (month 1 is the month after it)."""
    stored = starting.get("as_of")
    if isinstance(stored, str):
        return date.fromisoformat(stored)
    months = _months_of(row)
    if months:
        year, month = (int(p) for p in months[0].split("-"))
        # months[0] is the month after the anchor
        total = year * 12 + (month - 1) - 1
        return date(total // 12, total % 12 + 1, 1)
    return datetime.now(UTC).date()


def _outside_window(overrides: dict[str, Any] | None, months: list[str]) -> list[str]:
    """Saved edits whose month is not inside the projection (hires only if they start after it)."""
    lo, hi = months[0], months[-1]
    out: list[str] = []
    for name, key, noun in (
        ("hiring_plan", "start_month", "Hire"),
        ("one_offs", "month", "One-off"),
        ("capex", "month", "Capex"),
    ):
        for item in (overrides or {}).get(name) or []:
            month = item[key]
            too_early = month < lo and name != "hiring_plan"  # an earlier hire still applies
            if too_early or month > hi:
                label = item.get("role") or item.get("label")
                out.append(
                    f"{noun} '{label}' ({month}) is outside the projection {lo} to {hi} "
                    "and is not included."
                )
    return out


def recalculate(
    db: Session, *, startup_id: uuid.UUID, model_id: uuid.UUID, refresh_actuals: bool = False
) -> tuple[FinancialModel, list[str]]:
    """Re-run the deterministic engine with the founder's edits. Never calls the AI."""
    row = _locked_complete(db, startup_id=startup_id, model_id=model_id)
    if refresh_actuals or row.starting is None:
        # A legacy model (no stored starting figures) keeps its original month labels.
        anchor = datetime.now(UTC).date() if refresh_actuals else _anchor(row, {})
        starting, source = build_starting(db, startup_id=startup_id)
        starting["as_of"] = anchor.isoformat()
        row.starting = starting
        if row.assumptions is not None:
            row.assumptions = {
                **row.assumptions,
                "revenue_source": source,
                "revenue_0_minor": starting["revenue_0"],
            }
    else:
        anchor = _anchor(row, row.starting)
    eff = effective_starting(row)
    assumptions = _effective_assumptions(row)
    if eff is None or assumptions is None:  # pragma: no cover - a complete model always has both
        raise ModelNotReady()
    stmts = project_three_statements(eff, assumptions, today=anchor, horizon=MODEL_HORIZON_MONTHS)
    months = stmts["months"]
    row.pnl = {"months": months, **stmts["pnl"]}
    row.cash_flow = {"months": months, **stmts["cash_flow"]}
    row.balance_sheet = {"months": months, **stmts["balance_sheet"]}
    row.horizon_months = MODEL_HORIZON_MONTHS
    row.calculated_rev = row.overrides_rev
    row.recalculated_at = datetime.now(UTC)
    db.flush()
    return row, _outside_window(row.overrides, months)
