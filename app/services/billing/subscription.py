"""Subscription state: one row per workspace, lazily created, with time-based transitions applied
on read (no cron needed): a trial that ran out, a paid period that ended with cancel or a
scheduled downgrade pending, a failed payment whose grace ran out."""

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.billing import BillingPlan, BillingSubscription
from app.platform.events import event_bus

INTERVALS = ("monthly", "annual")


def as_dict(value: Any) -> dict[str, Any]:
    """Paystack payloads are untrusted JSON: anything that isn't an object reads as empty."""
    return value if isinstance(value, dict) else {}


def now() -> datetime:
    return datetime.now(UTC)


def add_interval(start: datetime, interval: str) -> datetime:
    months = 1 if interval == "monthly" else 12
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def plans(db: Session) -> list[BillingPlan]:
    return (
        db.query(BillingPlan).filter(BillingPlan.active.is_(True)).order_by(BillingPlan.rank).all()
    )


def plan_by_key(db: Session, key: str) -> BillingPlan:
    plan = (
        db.query(BillingPlan).filter(BillingPlan.key == key, BillingPlan.active.is_(True)).first()
    )
    if plan is None:
        raise AppError("PLAN_NOT_FOUND", "That plan doesn't exist.", 404)
    return plan


def price(plan: BillingPlan, interval: str) -> int:
    return plan.price_annual_minor if interval == "annual" else plan.price_monthly_minor


def get_or_create(db: Session, startup_id: uuid.UUID) -> BillingSubscription:
    sub = db.query(BillingSubscription).filter_by(startup_id=startup_id).first()
    if sub is None:
        sub = BillingSubscription(startup_id=startup_id)
        db.add(sub)
        db.flush()
    return sub


def start_trial(db: Session, sub: BillingSubscription) -> bool:
    """One Growth trial per workspace, ever (D-SET-4). False when it was already used or the
    workspace is on a paid plan."""
    if sub.trial_used_at is not None or sub.status in ("active", "past_due", "trialing"):
        return False
    t = now()
    sub.status = "trialing"
    sub.plan_key = "growth"
    sub.trial_used_at = t
    sub.trial_ends_at = t + timedelta(days=settings.BILLING_TRIAL_DAYS)
    sub.current_period_start = t
    sub.current_period_end = sub.trial_ends_at
    db.flush()
    return True


def _to_free(sub: BillingSubscription, *, status: str = "free") -> None:
    sub.status = status
    sub.plan_key = "starter"
    sub.interval = "monthly"
    sub.cancel_at_period_end = False
    sub.pending_plan_key = None
    sub.pending_interval = None
    sub.grace_ends_at = None
    sub.payment_failed_at = None
    sub.paystack_subscription_code = None
    sub.paystack_email_token = None
    sub.comped = False
    sub.trial_ends_at = None
    sub.current_period_start = None
    sub.current_period_end = None


def apply_transitions(db: Session, sub: BillingSubscription) -> BillingSubscription:
    """Move the row forward to `now`. Idempotent; emits `billing.plan.changed` when the plan moves."""
    t = now()
    before = sub.plan_key
    if sub.status == "trialing" and sub.trial_ends_at and t >= sub.trial_ends_at:
        _to_free(sub)
    elif (
        sub.status in ("active", "past_due")
        and sub.current_period_end
        and t >= sub.current_period_end
    ):
        if sub.status == "active" and sub.cancel_at_period_end:
            sub.canceled_at = sub.canceled_at or t
            _to_free(sub, status="canceled")
        elif sub.status == "active" and sub.pending_plan_key:
            sub.plan_key = sub.pending_plan_key
            sub.interval = sub.pending_interval or sub.interval
            sub.pending_plan_key = None
            sub.pending_interval = None
            sub.current_period_start = sub.current_period_end
            sub.current_period_end = add_interval(sub.current_period_start, sub.interval)
    if (
        sub.status == "active"
        and sub.current_period_end
        and t >= sub.current_period_end + timedelta(days=settings.BILLING_GRACE_DAYS)
    ):
        # No renewal payment arrived within the grace window after the period ended: lapse it
        # (otherwise a plan with no card on file would stay "active" forever).
        _to_free(sub, status="canceled")
    if sub.status == "past_due" and sub.grace_ends_at and t >= sub.grace_ends_at:
        _to_free(sub, status="canceled")
    db.flush()
    if sub.plan_key != before:
        event_bus.publish(
            db,
            "billing.plan.changed",
            {"startup_id": str(sub.startup_id), "from": before, "to": sub.plan_key},
        )
    return sub


def current(db: Session, startup_id: uuid.UUID) -> BillingSubscription:
    sub = get_or_create(db, startup_id)
    if settings.PLAN_ENFORCEMENT and sub.canceled_at is None:
        start_trial(db, sub)  # existing workspaces get their one trial when the gate switches on
    return apply_transitions(db, sub)


def is_paid(sub: BillingSubscription) -> bool:
    return sub.status in ("active", "past_due") and sub.plan_key != "starter"


@dataclass(frozen=True)
class Effective:
    plan_key: str
    status: str


def effective(sub: BillingSubscription) -> Effective:
    """What the workspace is entitled to right now (a failed payment keeps the plan through its
    grace days, then `apply_transitions` has already dropped it to Starter)."""
    return Effective(sub.plan_key, sub.status)


def serialize(db: Session, sub: BillingSubscription) -> dict[str, Any]:
    plan = plan_by_key(db, sub.plan_key)
    t = now()
    pending = plan_by_key(db, sub.pending_plan_key) if sub.pending_plan_key else None
    grace_days_left = None
    if sub.status == "past_due" and sub.grace_ends_at:
        grace_days_left = max(
            0, (sub.grace_ends_at - t).days + (1 if (sub.grace_ends_at - t).seconds else 0)
        )
    return {
        "plan": {"key": plan.key, "name": plan.name},
        "interval": sub.interval,
        "status": sub.status,
        "price_minor": price(plan, sub.interval),
        "currency": "NGN",
        "current_period_start": (
            sub.current_period_start.isoformat() if sub.current_period_start else None
        ),
        "renews_at": (
            sub.current_period_end.isoformat()
            if sub.current_period_end and is_paid(sub) and not sub.cancel_at_period_end
            else None
        ),
        "ends_at": (
            sub.current_period_end.isoformat()
            if sub.current_period_end and (sub.cancel_at_period_end or sub.status == "trialing")
            else None
        ),
        "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "trial_available": sub.trial_used_at is None and not is_paid(sub),
        "comped": sub.comped,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "cancel_reason": sub.cancel_reason,
        "pending_change": (
            {"plan": {"key": pending.key, "name": pending.name}, "interval": sub.pending_interval}
            if pending
            else None
        ),
        "payment_issue": (
            {
                "failed_at": sub.payment_failed_at.isoformat() if sub.payment_failed_at else None,
                "grace_ends_at": sub.grace_ends_at.isoformat() if sub.grace_ends_at else None,
                "grace_days_left": grace_days_left,
            }
            if sub.status == "past_due"
            else None
        ),
        "enforcement": settings.PLAN_ENFORCEMENT,
    }
