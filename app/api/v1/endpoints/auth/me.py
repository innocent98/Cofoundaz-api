from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter()


@router.get("/me")
def me(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = (
        db.query(Membership, Startup)
        .join(Startup, Startup.id == Membership.startup_id)
        .filter(Membership.user_id == user.id, Membership.status == MembershipStatus.active)
        # Deterministic order: earliest-created membership first, with `id` as a stable
        # tiebreaker for rows created in the same instant (e.g. same transaction, where
        # Postgres `now()` is transaction-scoped and can tie). Without this, `.all()` has
        # no defined order and `active_workspace_id` below could vary between calls.
        .order_by(Membership.created_at, Membership.id)
        .all()
    )
    memberships = [{"startup_id": str(s.id), "name": s.name, "role": m.role.value} for m, s in rows]
    profile = user.profile
    return success_response(
        {
            "user": {"id": str(user.id), "email": user.email, "status": user.status.value},
            "profile": (
                None
                if profile is None
                else {
                    "full_name": profile.full_name,
                    "role_title": profile.role_title,
                    "country": profile.country,
                    "avatar_url": profile.avatar_url,
                }
            ),
            "memberships": memberships,
            "active_workspace_id": memberships[0]["startup_id"] if memberships else None,
        }
    )
