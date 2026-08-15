from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.user import User
from app.db.session import get_db
from app.services.onboarding.workspace import resolve_or_create_workspace, serialize_state

router = APIRouter()


@router.get("/state")
def get_state(
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = resolve_or_create_workspace(db, user)
    body = serialize_state(db, startup, user)
    db.commit()
    return success_response(body)
