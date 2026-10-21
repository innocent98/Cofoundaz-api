"""Sales analytics (spec §4, §11.6): everything is computed here from the CRM tables, never by
the model.

Ranges are inclusive calendar days in UTC. Money is kobo (NGN). Deals are only ever counted by
the stage they are in *now* (won / lost) or by the history in `deal_stage_changes` (funnel).
"""

import uuid
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.enums import SALES_ROLES, MembershipStatus, StageKind
from app.db.models.membership import Membership
from app.db.models.sales import Deal, DealStageChange, PipelineStage, SalesActivity
from app.db.models.user import User, UserProfile
from app.services.sales.errors import _validation
from app.services.sales.stages import ensure_stages

CURRENCY = "NGN"
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
REVENUE_MONTHS = 12


def resolve_range(start: date | None, end: date | None, *, today: date | None = None) -> tuple:
    today = today or datetime.now(UTC).date()
    end = end or today
    start = start or end - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if start > end:
        raise _validation("from", "The start date must be on or before the end date.")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise _validation("to", f"Pick a range of at most {MAX_RANGE_DAYS} days.")
    return start, end


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """[start 00:00 UTC, day after end 00:00 UTC)."""
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC),
    )


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def funnel(db: Session, startup_id: uuid.UUID, lo: datetime, hi: datetime) -> dict[str, Any]:
    """Of the deals created in range, how many reached each stage. "Reached" is by stage
    position (reached X or any later stage) from `deal_stage_changes`, which includes the
    initial row written at creation. The lost stage is not a rung: a lost deal counts up to the
    furthest non-lost stage it ever reached."""
    stages = [
        s
        for s in ensure_stages(db, startup_id)
        if s.kind != StageKind.lost and s.archived_at is None
    ]
    furthest: dict[uuid.UUID, int] = {
        deal_id: int(pos)
        for deal_id, pos in db.execute(
            select(DealStageChange.deal_id, func.max(PipelineStage.position))
            .join(Deal, Deal.id == DealStageChange.deal_id)
            .join(PipelineStage, PipelineStage.id == DealStageChange.to_stage_id)
            .where(
                Deal.startup_id == startup_id,
                Deal.created_at >= lo,
                Deal.created_at < hi,
                PipelineStage.kind != StageKind.lost,
            )
            .group_by(DealStageChange.deal_id)
        )
    }
    cohort = (
        db.scalar(
            select(func.count())
            .select_from(Deal)
            .where(Deal.startup_id == startup_id, Deal.created_at >= lo, Deal.created_at < hi)
        )
        or 0
    )
    rows: list[dict[str, Any]] = []
    previous: int | None = None
    for stage in stages:
        reached = sum(pos >= stage.position for pos in furthest.values())
        rows.append(
            {
                "stage_id": str(stage.id),
                "name": stage.name,
                "kind": stage.kind.value,
                "reached": reached,
                "conversion_from_previous_pct": (
                    None if previous is None else _pct(reached, previous)
                ),
            }
        )
        previous = reached
    return {"deals_created": cohort, "stages": rows}


def _closed_in_range(
    db: Session, startup_id: uuid.UUID, kind: StageKind, lo: datetime, hi: datetime
) -> Any:
    return (
        select(Deal)
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .where(
            Deal.startup_id == startup_id,
            PipelineStage.kind == kind,
            Deal.closed_at >= lo,
            Deal.closed_at < hi,
        )
    )


def won_and_cycle(db: Session, startup_id: uuid.UUID, lo: datetime, hi: datetime) -> dict:
    deals = list(db.scalars(_closed_in_range(db, startup_id, StageKind.won, lo, hi)))
    days = [(d.closed_at - d.created_at).total_seconds() / 86400 for d in deals if d.closed_at]
    return {
        "won_count": len(deals),
        "won_value_minor": sum(d.value_minor for d in deals),
        "avg_cycle_days": round(sum(days) / len(days), 1) if days else None,
    }


def win_loss(
    db: Session, startup_id: uuid.UUID, lo: datetime, hi: datetime, *, won_count: int
) -> dict[str, Any]:
    lost = list(db.scalars(_closed_in_range(db, startup_id, StageKind.lost, lo, hi)))
    by_reason: Counter[str] = Counter()
    value_by_reason: Counter[str] = Counter()
    for d in lost:
        key = d.closed_reason.value if d.closed_reason else "other"
        by_reason[key] += 1
        value_by_reason[key] += d.value_minor
    reasons = [
        {"reason": r, "count": n, "value_minor": value_by_reason[r]}
        for r, n in sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "won": won_count,
        "lost": len(lost),
        "win_rate_pct": _pct(won_count, won_count + len(lost)),
        "loss_reasons": reasons,
    }


def leaderboard(
    db: Session, startup_id: uuid.UUID, lo: datetime, hi: datetime
) -> list[dict[str, Any]]:
    """Activities logged per current founder / team member in range (members with none show 0)."""
    members = db.execute(
        select(User.id, UserProfile.full_name, User.email)
        .join(Membership, Membership.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(
            Membership.startup_id == startup_id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_(SALES_ROLES),
        )
    ).all()
    counts: dict[uuid.UUID, Counter[str]] = {}
    for author_id, kind, n in db.execute(
        select(SalesActivity.author_id, SalesActivity.kind, func.count())
        .where(
            SalesActivity.startup_id == startup_id,
            SalesActivity.occurred_at >= lo,
            SalesActivity.occurred_at < hi,
            SalesActivity.author_id.is_not(None),
        )
        .group_by(SalesActivity.author_id, SalesActivity.kind)
    ):
        counts.setdefault(author_id, Counter())[kind.value] += n
    rows = []
    for user_id, full_name, email in members:
        c = counts.get(user_id, Counter())
        rows.append(
            {
                "user_id": str(user_id),
                "name": full_name or email,
                "total": sum(c.values()),
                "calls": c["call"],
                "emails": c["email"],
                "meetings": c["meeting"],
                "notes": c["note"],
            }
        )
    rows.sort(key=lambda r: (-r["total"], str(r["name"]).lower()))
    return rows


def _month_keys(now: datetime) -> list[tuple[int, int]]:
    y, m = now.year, now.month
    keys = []
    for _ in range(REVENUE_MONTHS):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return list(reversed(keys))


def monthly_revenue(
    db: Session, startup_id: uuid.UUID, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Closed-won value per month for the last 12 months including this one, oldest first, zero
    filled. Counts deals currently in the won stage by the month they closed (UTC)."""
    now = now or datetime.now(UTC)
    keys = _month_keys(now)
    first = datetime(keys[0][0], keys[0][1], 1, tzinfo=UTC)
    totals: Counter[tuple[int, int]] = Counter()
    counts: Counter[tuple[int, int]] = Counter()
    for closed_at, value in db.execute(
        select(Deal.closed_at, Deal.value_minor)
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .where(
            Deal.startup_id == startup_id,
            PipelineStage.kind == StageKind.won,
            Deal.closed_at >= first,
        )
    ):
        k = (closed_at.astimezone(UTC).year, closed_at.astimezone(UTC).month)
        totals[k] += value
        counts[k] += 1
    return [
        {"month": f"{y:04d}-{m:02d}", "value_minor": totals[(y, m)], "deal_count": counts[(y, m)]}
        for y, m in keys
    ]


def analytics(
    db: Session,
    startup_id: uuid.UUID,
    start: date | None,
    end: date | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    start, end = resolve_range(start, end)
    lo, hi = _bounds(start, end)
    won = won_and_cycle(db, startup_id, lo, hi)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "currency": CURRENCY,
        "funnel": funnel(db, startup_id, lo, hi),
        "avg_cycle_days": won["avg_cycle_days"],
        "won": {"count": won["won_count"], "value_minor": won["won_value_minor"]},
        "win_loss": win_loss(db, startup_id, lo, hi, won_count=won["won_count"]),
        "leaderboard": leaderboard(db, startup_id, lo, hi),
        "monthly_revenue": monthly_revenue(db, startup_id, now=now),
    }
