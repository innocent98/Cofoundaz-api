import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.services.onboarding.workspace import is_untouched_draft

router = APIRouter()

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MePatch(BaseModel):
    """What a member may change about themselves. Only the display name for now (certificates,
    comments and invitations print it); unknown fields are ignored, never applied."""

    model_config = ConfigDict(extra="ignore")

    full_name: str

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # PydanticCustomError, not ValueError: pydantic prefixes ValueError text with "Value error, ".
        if _CONTROL.search(v):
            raise PydanticCustomError("name_chars", "Use letters, spaces and punctuation only.")
        v = " ".join(v.split())
        if not v:
            raise PydanticCustomError("name_required", "Tell us your name.")
        if len(v) > 120:
            raise PydanticCustomError("name_too_long", "Keep it to 120 characters.")
        return v


@router.get("/me")
def me(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(_me_payload(db, user))


@router.patch("/me")
def update_me(
    body: MePatch,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    user.profile.full_name = body.full_name
    db.commit()
    return success_response(_me_payload(db, user))


def _me_payload(db: Session, user: User) -> dict[str, Any]:
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
    # Hide an untouched onboarding draft when the user belongs to another workspace: it was
    # created for an invited member by mistake (before the 2026-10-19 fix) or abandoned.
    pairs = [(m, s) for m, s in rows]
    if len(pairs) > 1:
        pairs = [(m, s) for m, s in pairs if not is_untouched_draft(s)] or pairs
    memberships = [
        {"startup_id": str(s.id), "name": s.name, "role": m.role.value} for m, s in pairs
    ]
    profile = user.profile
    return {
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
