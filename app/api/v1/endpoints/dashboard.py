import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.services.dashboard.briefing import accept_action
from app.services.dashboard.service import BRIEFING_ROLES, get_activity, get_summary

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


@router.get("/summary")
def dashboard_summary(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    data = get_summary(db, startup, user, role=membership.role)
    db.commit()  # get_or_generate_today may have lazily created today's mission
    return success_response(data)


@router.get("/activity")
def dashboard_activity(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    return success_response(get_activity(db, startup, cursor=cursor, limit=limit))


@router.post("/briefing/{briefing_id}/actions/{index}/accept")
def accept_briefing_action(
    briefing_id: uuid.UUID,
    index: int,
    membership: Membership = Depends(require_role(*BRIEFING_ROLES)),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """ "Do it" on an AI briefing action (PRD 02.1). A `task` action is added to today's
    mission; a `navigate` action returns its route. Idempotent per action."""
    startup = _startup(db, membership)
    data = accept_action(db, startup, user, briefing_id, index)
    db.commit()
    return success_response(data)
