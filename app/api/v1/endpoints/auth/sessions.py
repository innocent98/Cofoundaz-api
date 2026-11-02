import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized, get_current_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import RefreshRequest
from app.services.auth.sessions import (
    clear_refresh_cookie,
    family_of,
    list_active,
    revoke_all_except,
    revoke_family_for_user,
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


def _label(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    browser = next(
        (
            n
            for k, n in (
                ("edg/", "Edge"),
                ("chrome/", "Chrome"),
                ("firefox/", "Firefox"),
                ("safari/", "Safari"),
            )
            if k in ua
        ),
        "Browser",
    )
    system = next(
        (
            n
            for k, n in (
                ("iphone", "iPhone"),
                ("android", "Android"),
                ("mac os", "Mac"),
                ("windows", "Windows"),
                ("linux", "Linux"),
            )
            if k in ua
        ),
        "unknown device",
    )
    return f"{browser} on {system}"


@router.get("/sessions")
def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    current = family_of(db, user.id, request.cookies.get(settings.REFRESH_COOKIE_NAME))
    rows = list_active(db, user.id)
    return success_response(
        [
            {
                "id": str(r.family_id),
                "label": _label(r.user_agent),
                "ip": r.ip,
                "signed_in_at": r.created_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
                "current": r.family_id == current,
            }
            for r in rows
        ]
    )


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    request: Request,
    payload: RefreshRequest | None = None,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    current = family_of(db, user.id, _read_refresh(request, payload))
    if current is None:
        raise AppError(
            code="CURRENT_SESSION_UNKNOWN",
            message="We couldn't tell which sign-in is this one. Sign in again, then retry.",
            http_status=409,
        )
    n = revoke_all_except(db, user.id, current)
    write_audit(db, "auth.sessions.revoke_others", actor_user_id=user.id)
    db.commit()
    return success_response({"revoked": n})


@router.delete("/sessions/{session_id}")
def revoke_one_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if revoke_family_for_user(db, user.id, session_id) == 0:
        raise NotFound()
    write_audit(
        db,
        "auth.session.revoked",
        actor_user_id=user.id,
        entity_type="auth_session",
        entity_id=session_id,
    )
    db.commit()
    return success_response({"revoked": True})
