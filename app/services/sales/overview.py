import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.enums import StageKind
from app.db.models.sales import Deal
from app.services.sales.stages import ensure_stages

CURRENCY = "NGN"


def overview(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    """Board header numbers. Seeds the default stages on a fresh workspace (lazy seed)."""
    stages = ensure_stages(db, startup_id)
    sums = {
        stage_id: (count, total)
        for stage_id, count, total in db.execute(
            select(Deal.stage_id, func.count(Deal.id), func.coalesce(func.sum(Deal.value_minor), 0))
            .where(Deal.startup_id == startup_id)
            .group_by(Deal.stage_id)
        )
    }
    rows: list[dict[str, Any]] = []
    pipeline = 0
    weighted_x100 = 0
    won = lost = 0
    for st in stages:
        count, total = sums.get(st.id, (0, 0))
        count, total = int(count), int(total)
        rows.append(
            {
                "stage_id": str(st.id),
                "name": st.name,
                "kind": st.kind.value,
                "deal_count": count,
                "value_minor": total,
            }
        )
        if st.kind == StageKind.open:
            pipeline += total
            weighted_x100 += total * st.win_probability
        elif st.kind == StageKind.won:
            won += count
        else:
            lost += count
    closed = won + lost
    return {
        "total_pipeline_minor": pipeline,
        "weighted_forecast_minor": (weighted_x100 + 50) // 100,
        "win_rate": round(won * 100 / closed, 1) if closed else None,
        "currency": CURRENCY,
        "stages": rows,
    }


def pipeline_kpi(db: Session, startup_id: uuid.UUID) -> int | None:
    """Open-stage deal value for the Dashboard tile; None when the workspace has no deals at all."""
    if db.scalar(select(Deal.id).where(Deal.startup_id == startup_id).limit(1)) is None:
        return None
    return int(overview(db, startup_id)["total_pipeline_minor"])
