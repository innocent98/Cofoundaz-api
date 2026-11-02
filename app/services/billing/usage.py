"""Usage meters (PRD 24.2): AI credits, seats, storage. Credits are LLM tokens / 50 (D-SET-9),
read from `llm_usage_daily`; seats count founders and team members (mentors, investors, lawyers,
accountants and consultants are free on every plan)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.billing import BillingAddonPurchase, BillingSubscription
from app.db.models.document import DocumentFile
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership
from app.platform.events import event_bus
from app.services.billing import subscription as subs
from app.services.billing.catalog import TOKENS_PER_CREDIT

SEAT_ROLES = (MembershipRole.founder, MembershipRole.team_member)


def cycle_bounds(sub: BillingSubscription) -> tuple[date, date]:
    """[start, end) of the metering cycle: the paid period, else the calendar month."""
    if subs.is_paid(sub) and not sub.comped and sub.current_period_start and sub.current_period_end:
        return sub.current_period_start.date(), sub.current_period_end.date()
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _credits(tokens: int) -> int:
    return tokens // TOKENS_PER_CREDIT


def usage(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    sub = subs.current(db, startup_id)
    plan = subs.plan_by_key(db, sub.plan_key)
    start, end = cycle_bounds(sub)
    rows = (
        db.query(LlmUsageDaily.usage_date, LlmUsageDaily.tokens_used)
        .filter(
            LlmUsageDaily.startup_id == startup_id,
            LlmUsageDaily.usage_date >= start,
            LlmUsageDaily.usage_date < end,
        )
        .order_by(LlmUsageDaily.usage_date)
        .all()
    )
    used = _credits(sum(r[1] for r in rows))
    # Credit packs never expire (D-SET-10): what was bought, minus what earlier months used beyond
    # their plan allowance, carries into this cycle. Earlier usage is bucketed by calendar month
    # (an approximation when the paid cycle isn't a calendar month).
    packs_total = int(
        db.query(func.coalesce(func.sum(BillingAddonPurchase.credits), 0))
        .filter(BillingAddonPurchase.startup_id == startup_id)
        .scalar()
        or 0
    )
    earlier: dict[tuple[int, int], int] = {}
    for d, t in (
        db.query(LlmUsageDaily.usage_date, LlmUsageDaily.tokens_used)
        .filter(LlmUsageDaily.startup_id == startup_id, LlmUsageDaily.usage_date < start)
        .all()
    ):
        earlier[(d.year, d.month)] = earlier.get((d.year, d.month), 0) + t
    overage = sum(max(0, _credits(t) - plan.ai_credits) for t in earlier.values())
    packs = max(0, packs_total - overage)
    limit = plan.ai_credits + packs
    seats_used = (
        db.query(func.count(Membership.id))
        .filter(
            Membership.startup_id == startup_id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_(SEAT_ROLES),
        )
        .scalar()
    )
    storage_bytes = (
        db.query(func.coalesce(func.sum(DocumentFile.size_bytes), 0))
        .filter(DocumentFile.startup_id == startup_id)
        .scalar()
    )
    pct = round(100 * used / limit) if limit else 0
    return {
        "cycle": {"starts_on": start.isoformat(), "resets_on": end.isoformat()},
        "ai_credits": {
            "used": used,
            "limit": limit,
            "percent": pct,
            "from_packs": packs,
            "tokens_per_credit": TOKENS_PER_CREDIT,
        },
        "seats": {"used": int(seats_used or 0), "limit": plan.seats},
        "storage": {
            "used_mb": round(int(storage_bytes or 0) / (1024 * 1024), 1),
            "limit_mb": plan.storage_mb,
        },
        "daily": [{"date": d.isoformat(), "credits": _credits(t)} for d, t in rows],
        "breakdown": [],  # per-feature usage is not recorded yet (follow-up)
    }


def maybe_alert(db: Session, startup_id: uuid.UUID, data: dict[str, Any]) -> None:
    """billing.usage.threshold once per cycle when AI credits pass 90% (PRD 24 events)."""
    sub = subs.get_or_create(db, startup_id)
    cycle = data["cycle"]["starts_on"]
    if data["ai_credits"]["percent"] >= 90 and sub.usage_alert_cycle != cycle:
        sub.usage_alert_cycle = cycle
        db.flush()
        event_bus.publish(
            db,
            "billing.usage.threshold",
            {"startup_id": str(startup_id), "percent": data["ai_credits"]["percent"]},
        )
