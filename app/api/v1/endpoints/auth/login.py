from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import InvalidCredentials
from app.core.security import verify_password
from app.db.models.enums import MfaType, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import LoginRequest
from app.services.auth.mfa import issue_mfa_ticket
from app.services.auth.ratelimit import (
    assert_login_allowed,
    clear_login_failures,
    enforce,
    record_login_failure,
)
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie, token_body

router = APIRouter()


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    now = datetime.now(UTC)
    ip = request.client.host if request.client else None
    if ip:
        # Credential stuffing across many emails from one address: counts every attempt.
        enforce(
            "login_ip",
            ip,
            limit=settings.AUTH_LOGIN_PER_IP_15MIN,
            window_seconds=15 * 60,
        )
    # Lockout is per (email, IP) and identical for an address with no account (gap 1): the
    # counters are keyed on the typed email, never on whether a user row exists.
    assert_login_allowed(payload.email, ip)
    user = db.query(User).filter(User.email == payload.email).first()

    if (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        record_login_failure(payload.email, ip)
        write_audit(db, "auth.login.failed", actor_user_id=user.id if user else None, ip=ip)
        db.commit()
        raise InvalidCredentials()
    if user.status == UserStatus.disabled:
        # Counted like a wrong password, so a suspended account never looks different (it would
        # otherwise be the one address that can never be locked).
        record_login_failure(payload.email, ip)
        raise InvalidCredentials()

    clear_login_failures(payload.email, ip)

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
    return success_response(token_body(request, access, refresh, mfa_required=False))
