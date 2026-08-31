from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.platform.activity import write_activity
from app.schemas.onboarding import AcceptRequest
from app.services.onboarding.invites import accept_invitation, preview_invitation

router = APIRouter()


def _actor_name(user: User) -> str:
    return (user.profile.full_name if user.profile else None) or "A teammate"


@router.post("/accept")
def accept(
    payload: AcceptRequest,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = accept_invitation(db, user, payload.token)
    write_activity(
        db,
        startup_id=m.startup_id,
        actor_user_id=user.id,
        action="member.joined",
        entity_type="membership",
        entity_id=m.id,
        summary=f"{_actor_name(user)} joined the workspace",
    )
    db.commit()
    return success_response({"startup_id": str(m.startup_id), "role": m.role.value})


@router.get("/{token}")
def get_invitation(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(preview_invitation(db, token))
