from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.onboarding import InvitesRequest
from app.services.onboarding.invites import create_invitations
from app.services.onboarding.workspace import ensure_draft, resolve_or_create_workspace

router = APIRouter()


@router.post("/invites")
def post_invites(
    payload: InvitesRequest,
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = resolve_or_create_workspace(db, user)
    ensure_draft(startup)
    result = create_invitations(db, startup, user, [i.model_dump() for i in payload.invites])
    db.commit()
    return success_response(result)
