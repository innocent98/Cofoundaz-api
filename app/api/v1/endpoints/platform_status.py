"""Public, unauthenticated platform status: the app polls it to show a maintenance banner."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.db.session import get_db
from app.platform.state import maintenance_state

router = APIRouter()


@router.get("/status")
def platform_status(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    return success_response({"maintenance": maintenance_state(db)})
