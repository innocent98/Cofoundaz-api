import secrets
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import InvalidCredentials
from app.core.security import get_password_hash, verify_password
from app.db.models.enums import MfaType, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import LoginRequest
from app.services.auth.mfa import issue_mfa_ticket
from app.services.auth.ratelimit import (
    clear_login_failures,
    enforce,
    ip_bucket,
    record_login_failure,
    record_successful_sign_in,
    release_login_attempt,
    reserve_login_attempt,
)
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie, token_body

router = APIRouter()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real bcrypt hash of a throwaway value, built once, to burn the same time as a real check."""
    return get_password_hash(secrets.token_urlsafe(16))


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
            ip_bucket(ip) or ip,
            limit=settings.AUTH_LOGIN_PER_IP_15MIN,
            window_seconds=15 * 60,
        )
    # Lockout is per (email, IP) and identical for an address with no account (gap 1): the
    # counters are keyed on the typed email, never on whether a user row exists. The attempt is
    # reserved atomically here, before the password is looked at.
    reserve_login_attempt(payload.email, ip)
    user = db.query(User).filter(User.email == payload.email).first()

    # An unknown email or an account with no password (Google-only) still pays for one bcrypt
    # check, so response time does not say which addresses exist.
    hashed = user.password_hash if user is not None and user.password_hash else _dummy_hash()
    password_ok = verify_password(payload.password, hashed)
    if user is None or user.password_hash is None or not password_ok:
        record_login_failure(payload.email, ip)
        write_audit(db, "auth.login.failed", actor_user_id=user.id if user else None, ip=ip)
        db.commit()
        raise InvalidCredentials()
    if user.status == UserStatus.disabled:
        # Counted like a wrong password, so a suspended account never looks different (it would
        # otherwise be the one address that can never be locked).
        record_login_failure(payload.email, ip)
        raise InvalidCredentials()

    if user.mfa_type != MfaType.none:
        # Right password, 2FA still to come: not a failure, not yet a sign-in. Give the slot back;
        # the address is recorded as known only once the code is accepted (mfa.py).
        release_login_attempt(payload.email, ip)
        ticket = issue_mfa_ticket(user.id)
        db.commit()
        # nosec B105 - not a hardcoded password. Bandit pattern-matches the
        # "access_token" dict key; the value is literally None because MFA is
        # still pending and no token has been issued yet.
        return success_response(
            {"mfa_required": True, "mfa_ticket": ticket, "access_token": None}  # nosec B105
        )

    clear_login_failures(payload.email, ip)
    record_successful_sign_in(payload.email, ip)
    user.last_login_at = now
    access, refresh = issue_token_pair(
        db, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    write_audit(db, "auth.login.success", actor_user_id=user.id, ip=ip)
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response(token_body(request, access, refresh, mfa_required=False))
