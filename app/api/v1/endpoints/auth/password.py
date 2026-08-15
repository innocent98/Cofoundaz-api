from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.redis import get_redis
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.email import EmailMessage, get_email_sender
from app.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth.password import validate_password_strength
from app.services.auth.sessions import revoke_all_for_user
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
            invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.password_reset)
            raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, _RESET_TTL)
            get_email_sender().send(
                EmailMessage(
                    to=user.email,
                    subject="Reset your password",
                    html=f"<p>Reset your password — token: <code>{raw}</code></p>",
                )
            )
            write_audit(
                db,
                "auth.password.reset_requested",
                actor_user_id=user.id,
                ip=request.client.host if request.client else None,
            )
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
