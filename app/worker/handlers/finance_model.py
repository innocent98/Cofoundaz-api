import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.enums import FinancialModelStatus
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.llm_budget import metered_complete_json
from app.services.finance.cashflow import cash_flow_summary, trailing_monthly_flows
from app.services.finance.model_engine import project_three_statements
from app.services.finance.model_prompt import (
    ASSUMPTIONS_SCHEMA,
    build_model_messages,
    validate_assumptions,
)
from app.worker.runner import register_handler

MODEL_MAX_TOKENS = 800


def handle_finance_model(db: Session, job: Job) -> None:
    """Build the 3-statement model: actuals -> LLM assumptions -> engine -> store. No commit."""
    model = db.get(FinancialModel, uuid.UUID(str(job.payload["model_id"])))
    if model is None or model.status != FinancialModelStatus.generating:
        return  # benign no-op (missing / already resolved)
    startup = db.get(Startup, model.startup_id)
    if startup is None:
        return
    summary = cash_flow_summary(db, startup_id=startup.id)
    revenue, costs = trailing_monthly_flows(db, startup_id=startup.id)
    starting = {
        "revenue_0": int(revenue),
        "monthly_costs_0": int(costs),
        "cash_on_hand": int(summary["cash_on_hand"]),
        "currency": summary["currency"],
    }
    raw = metered_complete_json(
        db,
        startup.id,
        build_model_messages(starting),
        schema=ASSUMPTIONS_SCHEMA,
        max_tokens=MODEL_MAX_TOKENS,
    )
    if raw is None:  # over budget: metered_complete_json skips the LLM call and returns None
        model.status = FinancialModelStatus.failed
        model.error = "AI budget exceeded"
        db.flush()
        return
    assumptions = validate_assumptions(raw)
    stmts = project_three_statements(
        starting,
        assumptions,
        today=datetime.now(UTC).date(),
        horizon=model.horizon_months,
    )
    # `months` lives inside each stored statement: serialize_model reads it from there.
    # Only the display statements are stored, never the *_raw arrays.
    months = stmts["months"]
    model.assumptions = assumptions
    model.pnl = {"months": months, **stmts["pnl"]}
    model.cash_flow = {"months": months, **stmts["cash_flow"]}
    model.balance_sheet = {"months": months, **stmts["balance_sheet"]}
    model.currency = starting["currency"] or model.currency
    model.status = FinancialModelStatus.complete
    model.generated_at = datetime.now(UTC)
    db.flush()


register_handler("ai.finance.model", handle_finance_model)
