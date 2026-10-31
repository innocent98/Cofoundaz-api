"""Staff Workspaces console (read-only, D-ADM-7) and the Dashboard KPIs.

D-ADM-9: staff never read documents, journal or AI chats. These queries name every column and read
only metadata: the document table is touched for a SUM of sizes, never a name or body.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Date, cast, func, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.billing import BillingPlan, BillingSubscription
from app.db.models.document import DocumentFile
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.finance_connection import FinanceConnection
from app.db.models.health_score import HealthScore
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup, StartupProfile
from app.db.models.user import User, UserProfile
from app.services.billing.catalog import TOKENS_PER_CREDIT
from app.services.staff.like import contains_pattern
from app.services.staff.users import _decode_cursor, _encode_cursor

SEAT_ROLES = (MembershipRole.founder, MembershipRole.team_member)
WINDOW_DAYS = 30
ACTIVATION_WITHIN = timedelta(days=14)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- list


def search_workspaces(
    db: Session,
    *,
    query: str | None,
    stage: StartupStage | None,
    billing_status: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    owner = User.__table__.alias("owner")
    owner_profile = UserProfile.__table__.alias("owner_profile")
    q = (
        db.query(
            Startup.id,
            Startup.name,
            Startup.stage,
            Startup.country,
            Startup.created_at,
            owner.c.email.label("owner_email"),
            owner_profile.c.full_name.label("owner_name"),
            BillingSubscription.plan_key,
            BillingSubscription.status.label("billing_status"),
            HealthScore.score,
            HealthScore.band,
        )
        .join(owner, owner.c.id == Startup.created_by)
        .outerjoin(owner_profile, owner_profile.c.user_id == owner.c.id)
        .outerjoin(BillingSubscription, BillingSubscription.startup_id == Startup.id)
        .outerjoin(HealthScore, HealthScore.startup_id == Startup.id)
        .filter(Startup.deleted_at.is_(None))
    )
    if query:
        term = query.strip()
        pat = contains_pattern(term)
        conds: list[ColumnElement[bool]] = [
            Startup.name.ilike(pat, escape="\\"),
            owner.c.email.ilike(pat, escape="\\"),
        ]
        try:
            conds.append(Startup.id == uuid.UUID(term))
        except ValueError:
            pass
        q = q.filter(or_(*conds))
    if stage:
        q = q.filter(Startup.stage == stage)
    if billing_status:
        if billing_status == "free":
            q = q.filter(
                or_(BillingSubscription.id.is_(None), BillingSubscription.status == "free")
            )
        else:
            q = q.filter(BillingSubscription.status == billing_status)
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (Startup.created_at < c_ts) | ((Startup.created_at == c_ts) & (Startup.id < c_id))
        )
    rows = q.order_by(Startup.created_at.desc(), Startup.id.desc()).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)
    ids = [r.id for r in rows]
    seats: dict[uuid.UUID, int] = {}
    members: dict[uuid.UUID, int] = {}
    last_active: dict[uuid.UUID, datetime | None] = {}
    if ids:
        for sid, role, n in (
            db.query(Membership.startup_id, Membership.role, func.count(Membership.id))
            .filter(Membership.startup_id.in_(ids), Membership.status == MembershipStatus.active)
            .group_by(Membership.startup_id, Membership.role)
            .all()
        ):
            members[sid] = members.get(sid, 0) + n
            if role in SEAT_ROLES:
                seats[sid] = seats.get(sid, 0) + n
        last_active = dict(
            db.query(Membership.startup_id, func.max(User.last_login_at))
            .join(User, User.id == Membership.user_id)
            .filter(Membership.startup_id.in_(ids), Membership.status == MembershipStatus.active)
            .group_by(Membership.startup_id)
            .tuples()
            .all()
        )
    items = [
        {
            "id": str(r.id),
            "name": r.name,
            "owner_email": r.owner_email,
            "owner_name": r.owner_name,
            "stage": r.stage.value if r.stage else None,
            "country": r.country,
            "created_at": r.created_at.isoformat(),
            "seats": seats.get(r.id, 0),
            "members": members.get(r.id, 0),
            "plan_key": r.plan_key or "starter",
            "billing_status": r.billing_status or "free",
            "health_score": r.score,
            "health_band": r.band,
            "last_active_at": _iso(last_active.get(r.id)),
        }
        for r in rows
    ]
    return items, next_cursor


# ---------------------------------------------------------------- detail


def workspace_detail(db: Session, workspace_id: uuid.UUID) -> dict[str, Any]:
    owner = User.__table__.alias("owner")
    base = (
        db.query(
            Startup.id,
            Startup.name,
            Startup.industry,
            Startup.stage,
            Startup.country,
            Startup.created_at,
            owner.c.email.label("owner_email"),
            StartupProfile.onboarding_completed_at,
            HealthScore.score,
            HealthScore.band,
        )
        .join(owner, owner.c.id == Startup.created_by)
        .outerjoin(StartupProfile, StartupProfile.startup_id == Startup.id)
        .outerjoin(HealthScore, HealthScore.startup_id == Startup.id)
        .filter(Startup.id == workspace_id, Startup.deleted_at.is_(None))
        .first()
    )
    if base is None:
        raise AppError("WORKSPACE_NOT_FOUND", "No such workspace.", 404)
    members = [
        {
            "user_id": str(uid),
            "email": email,
            "full_name": name,
            "role": role.value,
            "status": status.value,
            "joined_at": _iso(joined),
        }
        for uid, email, name, role, status, joined in (
            db.query(
                User.id,
                User.email,
                UserProfile.full_name,
                Membership.role,
                Membership.status,
                Membership.joined_at,
            )
            .join(Membership, Membership.user_id == User.id)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .filter(Membership.startup_id == workspace_id)
            .order_by(Membership.created_at.asc(), Membership.id.asc())
            .all()
        )
    ]
    sub = (
        db.query(
            BillingSubscription.plan_key,
            BillingSubscription.interval,
            BillingSubscription.status,
            BillingSubscription.trial_ends_at,
            BillingSubscription.current_period_end,
            BillingSubscription.cancel_at_period_end,
            BillingSubscription.grace_ends_at,
        )
        .filter(BillingSubscription.startup_id == workspace_id)
        .first()
    )
    plan = (
        db.query(
            BillingPlan.name, BillingPlan.ai_credits, BillingPlan.seats, BillingPlan.storage_mb
        )
        .filter(BillingPlan.key == (sub.plan_key if sub else "starter"))
        .first()
    )
    since = date.today() - timedelta(days=WINDOW_DAYS)
    tokens = int(
        db.query(func.coalesce(func.sum(LlmUsageDaily.tokens_used), 0))
        .filter(LlmUsageDaily.startup_id == workspace_id, LlmUsageDaily.usage_date >= since)
        .scalar()
        or 0
    )
    storage_bytes = int(
        db.query(func.coalesce(func.sum(DocumentFile.size_bytes), 0))
        .filter(DocumentFile.startup_id == workspace_id)
        .scalar()
        or 0
    )
    integrations = [
        {"provider": provider, "status": status}
        for provider, status in db.query(FinanceConnection.provider, FinanceConnection.status)
        .filter(FinanceConnection.startup_id == workspace_id)
        .order_by(FinanceConnection.provider)
        .all()
    ]
    seats_used = sum(
        1 for m in members if m["status"] == "active" and m["role"] in {r.value for r in SEAT_ROLES}
    )
    return {
        "id": str(base.id),
        "name": base.name,
        "industry": base.industry,
        "stage": base.stage.value if base.stage else None,
        "country": base.country,
        "created_at": base.created_at.isoformat(),
        "owner_email": base.owner_email,
        "onboarding_completed_at": _iso(base.onboarding_completed_at),
        "health_score": base.score,
        "health_band": base.band,
        "members": members,
        "subscription": {
            "plan_key": sub.plan_key if sub else "starter",
            "plan_name": plan.name if plan else None,
            "interval": sub.interval if sub else "monthly",
            "status": sub.status if sub else "free",
            "trial_ends_at": _iso(sub.trial_ends_at) if sub else None,
            "current_period_end": _iso(sub.current_period_end) if sub else None,
            "cancel_at_period_end": bool(sub.cancel_at_period_end) if sub else False,
            "grace_ends_at": _iso(sub.grace_ends_at) if sub else None,
        },
        "usage": {
            "ai_credits_30d": tokens // TOKENS_PER_CREDIT,
            "ai_credits_limit": plan.ai_credits if plan else None,
            "seats_used": seats_used,
            "seats_limit": plan.seats if plan else None,
            "storage_mb": round(storage_bytes / (1024 * 1024), 1),
            "storage_limit_mb": plan.storage_mb if plan else None,
        },
        "integrations": integrations,
    }


# ---------------------------------------------------------------- dashboard KPIs


def _rate(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def _window_counts(db: Session, start: datetime, end: datetime) -> tuple[int, int, int]:
    """Workspaces created in [start, end), of which finished onboarding within 14 days, and users
    who signed up in the window."""
    created = (
        db.query(func.count(Startup.id))
        .filter(Startup.deleted_at.is_(None), Startup.created_at >= start, Startup.created_at < end)
        .scalar()
        or 0
    )
    activated = (
        db.query(func.count(Startup.id))
        .join(StartupProfile, StartupProfile.startup_id == Startup.id)
        .filter(
            Startup.deleted_at.is_(None),
            Startup.created_at >= start,
            Startup.created_at < end,
            StartupProfile.onboarding_completed_at.isnot(None),
            StartupProfile.onboarding_completed_at <= Startup.created_at + ACTIVATION_WITHIN,
        )
        .scalar()
        or 0
    )
    signups = (
        db.query(func.count(User.id))
        .filter(User.deleted_at.is_(None), User.created_at >= start, User.created_at < end)
        .scalar()
        or 0
    )
    return int(created), int(activated), int(signups)


def dashboard_kpis(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    cur_start = now - timedelta(days=WINDOW_DAYS)
    prev_start = now - timedelta(days=2 * WINDOW_DAYS)
    created, activated, _ = _window_counts(db, cur_start, now)
    p_created, p_activated, _ = _window_counts(db, prev_start, cur_start)
    total = int(db.query(func.count(Startup.id)).filter(Startup.deleted_at.is_(None)).scalar() or 0)
    paying = int(
        db.query(func.count(BillingSubscription.id))
        .join(Startup, Startup.id == BillingSubscription.startup_id)
        .filter(
            Startup.deleted_at.is_(None),
            BillingSubscription.status == "active",
            BillingSubscription.plan_key != "starter",
        )
        .scalar()
        or 0
    )
    mrr = 0
    for interval, monthly, annual, n in (
        db.query(
            BillingSubscription.interval,
            BillingPlan.price_monthly_minor,
            BillingPlan.price_annual_minor,
            func.count(BillingSubscription.id),
        )
        .join(BillingPlan, BillingPlan.key == BillingSubscription.plan_key)
        .join(Startup, Startup.id == BillingSubscription.startup_id)
        .filter(
            Startup.deleted_at.is_(None),
            BillingSubscription.status == "active",
            BillingSubscription.plan_key != "starter",
        )
        .group_by(
            BillingSubscription.interval,
            BillingPlan.price_monthly_minor,
            BillingPlan.price_annual_minor,
        )
        .all()
    ):
        mrr += n * (annual // 12 if interval == "annual" else monthly)
    day = cast(func.timezone("UTC", User.created_at), Date)
    signups_by_day = dict(
        db.query(day, func.count(User.id))
        .filter(User.deleted_at.is_(None), User.created_at >= cur_start)
        .group_by(day)
        .tuples()
        .all()
    )
    act_day = cast(func.timezone("UTC", StartupProfile.onboarding_completed_at), Date)
    activations_by_day = dict(
        db.query(act_day, func.count(StartupProfile.startup_id))
        .join(Startup, Startup.id == StartupProfile.startup_id)
        .filter(Startup.deleted_at.is_(None), StartupProfile.onboarding_completed_at >= cur_start)
        .group_by(act_day)
        .tuples()
        .all()
    )
    series = []
    for i in range(WINDOW_DAYS):
        d = (cur_start + timedelta(days=i + 1)).astimezone(UTC).date()
        series.append(
            {
                "date": d.isoformat(),
                "signups": int(signups_by_day.get(d, 0)),
                "activations": int(activations_by_day.get(d, 0)),
            }
        )
    return {
        "window_days": WINDOW_DAYS,
        "generated_at": now.isoformat(),
        "total_workspaces": {
            "value": total,
            "new_in_window": created,
            "new_in_previous_window": p_created,
        },
        "activation_rate": {
            "percent": _rate(activated, created),
            "previous_percent": _rate(p_activated, p_created),
            "cohort": created,
            "activated": activated,
        },
        "paid_conversion": {
            "percent": _rate(paying, total),
            "paying_workspaces": paying,
            "previous_percent": None,
        },
        "mrr": {"minor": int(mrr), "currency": "NGN", "previous_minor": None},
        "signups_series": series,
    }
