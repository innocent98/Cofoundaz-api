"""Runway orchestration: settings persistence + scenario payload assembly.

Import graph (acyclic): runway -> cashflow, runway_math, scenario_config. Neither cashflow nor
runway_math may import this module.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.finance import Transaction
from app.db.models.finance_runway import FinanceRunwaySettings
from app.db.models.health_score import HealthSignal
from app.platform.events import event_bus
from app.schemas.finance_runway import AssumptionsUpdate
from app.services.finance.cashflow import cash_flow_summary, trailing_monthly_flows
from app.services.finance.runway_math import _runway_is_low, project_scenarios
from app.services.finance.scenario_config import RUNWAY_HORIZON_MONTHS


def get_or_default_settings(db: Session, *, startup_id: uuid.UUID) -> FinanceRunwaySettings | None:
    """The startup's settings row, or None. Never creates a row on read."""
    return db.scalars(
        select(FinanceRunwaySettings).where(FinanceRunwaySettings.startup_id == startup_id)
    ).one_or_none()


def _get_or_create_settings(db: Session, *, startup_id: uuid.UUID) -> FinanceRunwaySettings:
    row = get_or_default_settings(db, startup_id=startup_id)
    if row is None:
        # add() INSIDE begin_nested: a concurrent first writer loses the unique(startup_id) race,
        # the savepoint rolls back cleanly, and we adopt the winner's row instead of a 500.
        try:
            with db.begin_nested():
                row = FinanceRunwaySettings(startup_id=startup_id)
                db.add(row)
        except IntegrityError:
            row = get_or_default_settings(db, startup_id=startup_id)
            if row is None:  # pragma: no cover - the conflicting row vanished mid-request
                raise
    return row


def upsert_assumptions(
    db: Session, *, startup_id: uuid.UUID, data: AssumptionsUpdate
) -> FinanceRunwaySettings:
    row = _get_or_create_settings(db, startup_id=startup_id)
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


def evaluate_runway_alert(db: Session, *, startup_id: uuid.UUID) -> None:
    """Fire `finance.runway.low` once per transition into danger; re-arm on recovery.

    `alert_is_low` is the dedup state: it flips True when the event fires and back to False when
    runway recovers, so a still-low startup never re-notifies on every transaction.
    """
    summary = cash_flow_summary(db, startup_id=startup_id)
    monthly_burn = int(summary["monthly_burn"])
    low_now = _runway_is_low(monthly_burn, summary["runway_months"])
    row = _get_or_create_settings(db, startup_id=startup_id)
    if low_now and not row.alert_is_low:
        row.alert_is_low = True
        row.alert_last_fired_at = datetime.now(UTC)
        db.flush()
        event_bus.publish(
            db,
            "finance.runway.low",
            {
                "startup_id": str(startup_id),
                "runway_months": summary["runway_months"],
                "monthly_burn": monthly_burn,
                "currency": summary["currency"],
            },
        )
    elif not low_now and row.alert_is_low:
        row.alert_is_low = False
        db.flush()


RUNWAY_SIGNAL_KEY = "money.runway_live"


def build_runway_signal(db: Session, *, startup_id: uuid.UUID) -> dict[str, Any] | None:
    """HealthSignal fields for the informational live-runway signal, or None with no finance data.

    Display-only: `contribution` is always 0 and Health Score scoring never reads signals, so this
    cannot move the overall score. A cash-positive startup (runway_months None) stores value 0; the
    key/source_ref distinguish it from a real assessment signal.
    """
    has_transactions = db.scalars(
        select(Transaction.id).where(Transaction.startup_id == startup_id).limit(1)
    ).first()
    if has_transactions is None:
        return None
    summary = cash_flow_summary(db, startup_id=startup_id)
    runway_months = summary["runway_months"]
    value = min(999.99, round(runway_months, 2)) if runway_months is not None else 0
    return {
        "startup_id": startup_id,
        "dimension": "money",
        "key": RUNWAY_SIGNAL_KEY,
        "value": value,
        "contribution": 0.00,
        "source_ref": "finance:cash-flow",
    }


def upsert_runway_signal(db: Session, *, startup_id: uuid.UUID) -> None:
    """Refresh the startup's `money.runway_live` signal only; assessment signals are untouched."""
    db.execute(
        delete(HealthSignal).where(
            HealthSignal.startup_id == startup_id, HealthSignal.key == RUNWAY_SIGNAL_KEY
        )
    )
    fields = build_runway_signal(db, startup_id=startup_id)
    if fields is not None:
        db.add(HealthSignal(**fields))
    db.flush()
