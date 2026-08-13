from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose
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
_GENERIC_SENT_MESSAGE = "If that email has an account, a reset link is on its way."


@router.post("/forgot")
def forgot(
    payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is not None:
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
    # Same response either way — no enumeration.
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
