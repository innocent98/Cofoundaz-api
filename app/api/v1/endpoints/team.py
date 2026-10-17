from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.team import TeamInvitesRequest
from app.services.onboarding.invites import create_invitations

router = APIRouter()


@router.post("/invitations")
def invite_members(
    payload: TeamInvitesRequest,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Invite people to the active workspace at any time (PRD 23 `POST /team/invitations`;
    the top bar's "+ Invite", founder only). Same rules as onboarding invites: an address
    that's already a member or already has a pending invite is `skipped`, not an error."""
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if startup is None:
        raise NotFound()
    items = [{"email": str(i.email), "role": i.role.value} for i in payload.invites]
    result = create_invitations(db, startup, user, items)
    db.commit()
    return success_response(result)
