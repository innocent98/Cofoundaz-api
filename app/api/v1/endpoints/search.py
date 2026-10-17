from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.core.rate_limit import limiter
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services.search.service import search

router = APIRouter()


@router.get("")
@limiter.limit("60/minute")
def global_search(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    q: str = Query(default="", max_length=200),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """⌘K global search (PRD §2.1). Results are scoped to the workspace and to what the
    caller's role can open; fewer than 2 characters returns no results."""
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if startup is None:
        raise NotFound()
    return success_response(search(db, startup, user, membership.role, q))
