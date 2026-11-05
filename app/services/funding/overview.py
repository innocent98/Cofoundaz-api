"""The header bar, stage counts and the suggested target (a hint, never auto-applied)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.enums import InvestorStage
from app.db.models.funding import Investor
from app.services.finance.cashflow import cash_flow_summary
from app.services.funding import investors as inv_svc
from app.services.funding import rounds as rounds_svc

SUGGESTED_TARGET_MONTHS = 18  # D-FUND-L1


def _suggested_target(db: Session, startup_id: uuid.UUID, currency: str) -> dict[str, Any] | None:
    """18 months of net burn from Finance. Finance is NGN only, so a USD round gets no hint."""
    if currency != "NGN":
        return None
    burn = int(cash_flow_summary(db, startup_id=startup_id).get("monthly_burn") or 0)
    if burn <= 0:
        return None
    return {
        "amount_minor": burn * SUGGESTED_TARGET_MONTHS,
        "currency": "NGN",
        "months": SUGGESTED_TARGET_MONTHS,
        "basis": "monthly_net_burn",
        "monthly_burn_minor": burn,
    }


def overview(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    rnd = rounds_svc.latest_round(db, startup_id)
    if rnd is None:
        return {
            "round": None,
            "empty": True,
            "stages": [{"stage": s, "count": 0} for s in inv_svc.STAGE_ORDER],
            "totals": None,
            "suggested_target": _suggested_target(db, startup_id, "NGN"),
            "counts": {"investors": 0, "warm": 0, "overdue_next_steps": 0},
        }
    counts: dict[str, int] = {
        str(stage): int(n)
        for stage, n in db.query(Investor.stage, func.count(Investor.id))
        .filter(Investor.startup_id == startup_id, Investor.round_id == rnd.id)
        .group_by(Investor.stage)
        .all()
    }
    total = sum(counts.values())
    warm = (
        db.query(func.count(Investor.id))
        .filter(Investor.round_id == rnd.id, Investor.startup_id == startup_id, Investor.warm)
        .scalar()
    )
    today = datetime.now(UTC).date()
    overdue = (
        db.query(func.count(Investor.id))
        .filter(
            Investor.round_id == rnd.id,
            Investor.startup_id == startup_id,
            Investor.next_step_on < today,
            Investor.stage.notin_([InvestorStage.committed.value, InvestorStage.passed.value]),
        )
        .scalar()
    )
    return {
        "round": rounds_svc.serialize(rnd),
        "empty": total == 0,
        "stages": [{"stage": s, "count": int(counts.get(s, 0))} for s in inv_svc.STAGE_ORDER],
        "totals": inv_svc.totals(db, rnd),
        "suggested_target": (
            _suggested_target(db, startup_id, rnd.currency) if rnd.status != "closed" else None
        ),
        "counts": {
            "investors": total,
            "warm": int(warm or 0),
            "overdue_next_steps": int(overdue or 0),
        },
    }
