from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AccountLocked, InvalidCredentials
from app.core.security import verify_password
from app.db.models.enums import MfaType, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import LoginRequest
from app.services.auth.mfa import issue_mfa_ticket
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie

router = APIRouter()


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    now = datetime.now(UTC)
    user = db.query(User).filter(User.email == payload.email).first()
    ip = request.client.host if request.client else None

    if user and user.locked_until and user.locked_until > now:
        raise AccountLocked()
    if (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.LOGIN_MAX_FAILS:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                user.failed_login_count = 0
        write_audit(db, "auth.login.failed", actor_user_id=user.id if user else None, ip=ip)
        db.commit()
        raise InvalidCredentials()
    if user.status == UserStatus.disabled:
        raise InvalidCredentials()

    user.failed_login_count = 0
    user.locked_until = None

    if user.mfa_type != MfaType.none:
        ticket = issue_mfa_ticket(user.id)
        db.commit()
        # nosec B105 - not a hardcoded password. Bandit pattern-matches the
        # "access_token" dict key; the value is literally None because MFA is
        # still pending and no token has been issued yet.
        return success_response(
            {"mfa_required": True, "mfa_ticket": ticket, "access_token": None}  # nosec B105
        )

    user.last_login_at = now
    access, refresh = issue_token_pair(
        db, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    write_audit(db, "auth.login.success", actor_user_id=user.id, ip=ip)
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response(
        {"access_token": access, "refresh_token": refresh, "mfa_required": False}
    )
