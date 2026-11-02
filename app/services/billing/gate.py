"""The plan gate. `require_feature("sales_outreach")` is the reusable dependency every "locked by
plan" route uses. It is dark by default: with PLAN_ENFORCEMENT off every feature is allowed, so
shipping Billing blocks nothing (D-SET-3, D-SAL-11) until the flag is switched on."""

import uuid
from collections.abc import Callable

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.billing import BillingPlan
from app.db.models.membership import Membership
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services.billing import subscription as subs


class PlanRequired(AppError):  # noqa: N818
    code, http_status = "PLAN_REQUIRED", 402
    message = "Upgrade your plan to use this."


def effective_plan(db: Session, startup_id: uuid.UUID) -> BillingPlan:
    return subs.plan_by_key(db, subs.current(db, startup_id).plan_key)


def has_feature(db: Session, startup_id: uuid.UUID, feature: str) -> bool:
    if not settings.PLAN_ENFORCEMENT:
        return True
    return feature in (effective_plan(db, startup_id).modules or [])


def cheapest_plan_with(db: Session, feature: str) -> BillingPlan | None:
    for plan in subs.plans(db):
        if feature in (plan.modules or []):
            return plan
    return None


def assert_feature(db: Session, startup_id: uuid.UUID, feature: str) -> None:
    if has_feature(db, startup_id, feature):
        return
    needed = cheapest_plan_with(db, feature)
    name = needed.name if needed else "a paid plan"
    err = PlanRequired(message=f"Upgrade to {name} to use this.")
    err.field_errors = [{"field": "plan", "message": needed.key if needed else "growth"}]
    raise err


def require_feature(feature: str) -> Callable[..., Membership]:
    def _dep(
        membership: Membership = Depends(require_workspace),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ) -> Membership:
        assert_feature(db, membership.startup_id, feature)
        return membership

    return _dep
