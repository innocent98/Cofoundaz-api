import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.onboarding import OnboardingStatePatch
from app.services.onboarding.steps import apply_step
from app.services.onboarding.workspace import (
    ensure_draft,
    joined_workspace,
    resolve_or_create_workspace,
    serialize_member_state,
    serialize_state,
)

router = APIRouter()


@router.get("/state")
def get_state(
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    x_workspace_id: uuid.UUID | None = Header(None, alias="X-Workspace-Id"),  # noqa: B008
) -> dict[str, Any]:
    # An invited member (mentor, consultant, team member) is not starting a startup: answer
    # with the workspace they joined instead of creating a founder draft and sending them
    # through onboarding.
    joined = joined_workspace(db, user, x_workspace_id)
    if joined is not None:
        return success_response(serialize_member_state(joined))
    startup = resolve_or_create_workspace(db, user)
    body = serialize_state(db, startup, user)
    db.commit()
    return success_response(body)


@router.patch("/state")
def patch_state(
    payload: OnboardingStatePatch,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = resolve_or_create_workspace(db, user)
    ensure_draft(startup)
    apply_step(db, startup, user, payload)
    body = serialize_state(db, startup, user)
    db.commit()
    return success_response(body)
