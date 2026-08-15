from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.envelope import success_response
from app.db.session import get_db
from app.schemas.auth import RefreshRequest
from app.services.auth.sessions import (
    clear_refresh_cookie,
    revoke_session,
    rotate_refresh,
    set_refresh_cookie,
)

router = APIRouter()


def _read_refresh(request: Request, payload: RefreshRequest | None) -> str:
    body_token = payload.refresh_token if payload else None
    return request.cookies.get(settings.REFRESH_COOKIE_NAME) or (body_token or "")


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    raw = _read_refresh(request, payload)
    if not raw:
        raise Unauthorized()
    access, new_refresh, _ = rotate_refresh(
        db,
        raw,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    set_refresh_cookie(response, new_refresh)
    return success_response({"access_token": access, "refresh_token": new_refresh})


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    raw = _read_refresh(request, payload)
    if raw:
        revoke_session(db, raw)
        db.commit()
    clear_refresh_cookie(response)
    return success_response({"logged_out": True})
