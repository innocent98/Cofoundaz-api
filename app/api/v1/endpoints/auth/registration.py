from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import EmailTaken
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User, UserProfile
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.email import EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.schemas.auth import EmailRequest, SignupRequest, TokenRequest
from app.services.auth.password import validate_password_strength
from app.services.auth.tokens import consume_auth_token, issue_auth_token

router = APIRouter()
_VERIFY_TTL = timedelta(hours=24)


def _send_verification(db: Session, user: User) -> None:
    raw = issue_auth_token(db, user, AuthTokenPurpose.email_verification, _VERIFY_TTL)
    get_email_sender().send(
        EmailMessage(
            to=user.email,
            subject="Verify your email",
            html=f"<p>Verify your email — token: <code>{raw}</code></p>",
        )
    )


@router.post("/signup", status_code=201)
def signup(
    payload: SignupRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    validate_password_strength(payload.password)
    if db.query(User).filter(User.email == payload.email).first():
        raise EmailTaken()
    user = User(
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        status=UserStatus.pending_verification,
    )
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    _send_verification(db, user)
    write_audit(
        db,
        "auth.user.registered",
        actor_user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    event_bus.publish("auth.user.registered", {"user_id": str(user.id)})
    db.commit()
    return success_response(
        {"user": {"id": str(user.id), "email": user.email}, "verification_sent": True}
    )


@router.post("/verify")
def verify(payload: TokenRequest, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    user = consume_auth_token(db, AuthTokenPurpose.email_verification, payload.token)
    user.status = UserStatus.active
    user.email_verified_at = datetime.now(UTC)
    db.flush()
    event_bus.publish("auth.user.verified", {"user_id": str(user.id)})
    db.commit()
    return success_response({"verified": True})


@router.post("/verify/resend")
def resend(payload: EmailRequest, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    user = (
        db.query(User)
        .filter(User.email == payload.email, User.status == UserStatus.pending_verification)
        .first()
    )
    if user is not None:
        _send_verification(db, user)
        db.commit()
    # Generic response regardless of whether the email exists — avoids account enumeration.
    return success_response({"sent": True})
