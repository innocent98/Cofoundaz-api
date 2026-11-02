"""The ledger figures a financial-model projection starts from."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.enums import TransactionDirection
from app.db.models.finance import Transaction
from app.db.models.finance_runway import FinanceRunwaySettings
from app.services.finance.cashflow import cash_flow_summary, trailing_monthly_flows
from app.services.finance.categories import FUNDING_CATEGORY


def funding_total(db: Session, *, startup_id: uuid.UUID, today: date) -> int:
    """Everything the ledger recorded as funding in: the model's paid-in capital."""
    total = (
        db.query(func.coalesce(func.sum(Transaction.amount_minor), 0))
        .filter(
            Transaction.startup_id == startup_id,
            Transaction.direction == TransactionDirection.inflow,
            Transaction.date <= today,
            func.lower(Transaction.category) == FUNDING_CATEGORY.casefold(),
        )
        .scalar()
    )
    return int(total or 0)


def build_starting(db: Session, *, startup_id: uuid.UUID) -> tuple[dict[str, Any], str]:
    """(starting figures, revenue source). Real actuals win; the Business Builder plan only
    fills a pre-revenue gap (PRD 12.6 / 08.8)."""
    today = datetime.now(UTC).date()
    summary = cash_flow_summary(db, startup_id=startup_id)
    revenue, costs = trailing_monthly_flows(db, startup_id=startup_id)
    starting: dict[str, Any] = {
        "revenue_0": int(revenue),
        "monthly_costs_0": int(costs),
        "cash_on_hand": int(summary["cash_on_hand"]),
        "currency": summary["currency"],
        "funding_total": funding_total(db, startup_id=startup_id, today=today),
        "as_of": today.isoformat(),
    }
    revenue_source = "actuals"
    settings_row = db.query(FinanceRunwaySettings).filter_by(startup_id=startup_id).first()
    drivers = (settings_row.revenue_drivers or {}) if settings_row else {}
    if starting["revenue_0"] <= 0 and int(drivers.get("total_monthly_minor") or 0) > 0:
        starting["revenue_0"] = int(drivers["total_monthly_minor"])
        starting["revenue_plan"] = [s.get("name") for s in drivers.get("streams") or []]
        revenue_source = "business_builder"
    return starting, revenue_source
