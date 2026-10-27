from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, EmailDeliveryFailed
from app.core.logger import log
from app.core.redis import get_redis
from app.core.security import get_password_hash, verify_password
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.email import get_email_sender
from app.schemas.auth import ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth.emails import password_reset_email
from app.services.auth.password import validate_password_strength
from app.services.auth.sessions import family_of, revoke_all_except, revoke_all_for_user
from app.services.auth.throttle import assert_not_locked, clear, record_failure
from app.services.auth.tokens import (
    consume_auth_token,
    invalidate_unconsumed_tokens,
    issue_auth_token,
)

router = APIRouter(prefix="/password")
_RESET_TTL = timedelta(hours=1)
_FORGOT_COOLDOWN_SECONDS = 60
_GENERIC_SENT_MESSAGE = "If that email has an account, a reset link is on its way."


@router.post("/forgot")
def forgot(
    payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    cooldown_key = f"password_reset_cooldown:{payload.email}"
    # set(..., nx=True): only the first caller within the 60s window claims the key and
    # proceeds to issue+send; a throttled retry (or an unknown email) is a no-op but still
    # gets the identical generic response below -- no enumeration signal either way.
    if get_redis().set(cooldown_key, "1", ex=_FORGOT_COOLDOWN_SECONDS, nx=True):
        user = db.query(User).filter(User.email == payload.email).first()
        if user is not None and user.status != UserStatus.disabled:
            try:
                # SAVEPOINT: a failed send rolls back the token it would have delivered.
                with db.begin_nested():
                    invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.password_reset)
                    raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, _RESET_TTL)
                    get_email_sender().send(password_reset_email(user.email, raw))
                    write_audit(
                        db,
                        "auth.password.reset_requested",
                        actor_user_id=user.id,
                        ip=request.client.host if request.client else None,
                    )
            except EmailDeliveryFailed:
                # Swallowed: only a real account reaches the send, so surfacing the failure
                # would be an enumeration signal. The response below stays identical.
                log.warning("password reset email failed: email provider refused the send")
            db.commit()
    # Same response in all cases (unknown email, disabled account, active account,
    # throttled retry) — no enumeration. Only the internal side effects above are gated.
    return success_response({"sent": True, "message": _GENERIC_SENT_MESSAGE})


@router.post("/reset")
def reset(
    payload: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    validate_password_strength(payload.password)
    user = consume_auth_token(db, AuthTokenPurpose.password_reset, payload.token)
    user.password_hash = get_password_hash(payload.password)
    revoke_all_for_user(db, user.id)  # force re-login everywhere
    write_audit(
        db,
        "auth.password.reset",
        actor_user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return success_response({"reset": True})


@router.post("/change")
def change(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ip = request.client.host if request.client else None
    # Per-user throttle on wrong "current password" guesses: a stolen access token must not be a
    # free oracle for the real password.
    key = f"password_change_fail:{user.id}"
    assert_not_locked(key)
    if not user.password_hash or not verify_password(payload.current_password, user.password_hash):
        record_failure(key)
        write_audit(db, "auth.password.change.failed", actor_user_id=user.id, ip=ip)
        db.commit()
        raise AppError(
            code="CURRENT_PASSWORD_WRONG",
            message="That isn't your current password.",
            http_status=422,
        )
    validate_password_strength(payload.new_password)
    if verify_password(payload.new_password, user.password_hash):
        raise AppError(
            code="PASSWORD_UNCHANGED",
            message="Choose a password you haven't used here.",
            http_status=422,
        )
    clear(key)
    user.password_hash = get_password_hash(payload.new_password)
    raw = payload.refresh_token or request.cookies.get(settings.REFRESH_COOKIE_NAME)
    keep = family_of(db, user.id, raw)
    # Sign out everywhere else; this device stays signed in when we can tell which one it is.
    revoked = revoke_all_except(db, user.id, keep)
    write_audit(db, "auth.password.changed", actor_user_id=user.id, ip=ip)
    db.commit()
    return success_response({"changed": True, "other_sessions_signed_out": revoked})
