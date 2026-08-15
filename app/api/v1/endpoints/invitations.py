from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.db.session import get_db
from app.services.onboarding.invites import preview_invitation

router = APIRouter()


@router.get("/{token}")
def get_invitation(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    return success_response(preview_invitation(db, token))
