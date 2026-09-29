"""Runway orchestration: settings persistence + scenario payload assembly.

Import graph (acyclic): runway -> cashflow, runway_math, scenario_config. Neither cashflow nor
runway_math may import this module.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.finance_runway import FinanceRunwaySettings
from app.schemas.finance_runway import AssumptionsUpdate
from app.services.finance.cashflow import cash_flow_summary, trailing_monthly_flows
from app.services.finance.runway_math import project_scenarios
from app.services.finance.scenario_config import RUNWAY_HORIZON_MONTHS


def get_or_default_settings(db: Session, *, startup_id: uuid.UUID) -> FinanceRunwaySettings | None:
    """The startup's settings row, or None. Never creates a row on read."""
    return db.scalars(
        select(FinanceRunwaySettings).where(FinanceRunwaySettings.startup_id == startup_id)
    ).one_or_none()


def upsert_assumptions(
    db: Session, *, startup_id: uuid.UUID, data: AssumptionsUpdate
) -> FinanceRunwaySettings:
    row = get_or_default_settings(db, startup_id=startup_id)
    if row is None:
        # add() INSIDE begin_nested: a concurrent first PUT loses the unique(startup_id) race,
        # the savepoint rolls back cleanly, and we adopt the winner's row instead of a 500.
        try:
            with db.begin_nested():
                row = FinanceRunwaySettings(startup_id=startup_id)
                db.add(row)
        except IntegrityError:
            row = get_or_default_settings(db, startup_id=startup_id)
            if row is None:  # pragma: no cover - the conflicting row vanished mid-request
                raise
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    db.flush()
    return row


def runway_payload(db: Session, *, startup_id: uuid.UUID) -> dict[str, Any]:
    row = get_or_default_settings(db, startup_id=startup_id)
    assumptions = {
        "mom_growth_percent": row.mom_growth_percent if row else 0,
        "hiring_spend_minor": row.hiring_spend_minor if row else 0,
        "one_off_costs_minor": row.one_off_costs_minor if row else 0,
    }
    summary = cash_flow_summary(db, startup_id=startup_id)
    # The projection needs the true (unclamped) monthly costs; summary's monthly_burn is clamped
    # at 0 for profitable startups and is display-only.
    monthly_revenue, monthly_costs = trailing_monthly_flows(db, startup_id=startup_id)
    scenarios = project_scenarios(
        {
            "cash_on_hand": summary["cash_on_hand"],
            "monthly_revenue": monthly_revenue,
            "monthly_costs": monthly_costs,
            "currency": summary["currency"],
        },
        assumptions,
        today=datetime.now(UTC).date(),
    )
    return {
        "assumptions": assumptions,
        "baseline": {
            "cash_on_hand": summary["cash_on_hand"],
            "monthly_burn": summary["monthly_burn"],
            "monthly_revenue": summary["monthly_revenue"],
            "currency": summary["currency"],
        },
        "horizon_months": RUNWAY_HORIZON_MONTHS,
        "scenarios": scenarios,
    }
