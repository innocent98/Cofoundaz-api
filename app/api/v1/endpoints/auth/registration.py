from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import AppError, EmailDeliveryFailed, EmailTaken
from app.core.logger import log
from app.core.redis import get_redis
from app.core.security import get_password_hash
from app.db.models.enums import AuthTokenPurpose, UserStatus
from app.db.models.user import User, UserProfile
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.email import get_email_sender
from app.platform.events import event_bus
from app.schemas.auth import EmailRequest, SignupRequest, TokenRequest
from app.services.auth.emails import verification_email
from app.services.auth.password import validate_password_strength
from app.services.auth.ratelimit import blocked_for, enforce
from app.services.auth.tokens import (
    consume_auth_token,
    invalidate_unconsumed_tokens,
    issue_auth_token,
)

router = APIRouter()
_VERIFY_TTL = timedelta(hours=24)
_RESEND_COOLDOWN_SECONDS = 60


def _send_verification(db: Session, user: User) -> None:
    invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.email_verification)
    raw = issue_auth_token(db, user, AuthTokenPurpose.email_verification, _VERIFY_TTL)
    get_email_sender().send(verification_email(user.email, raw))


@router.post("/signup", status_code=201)
def signup(
    payload: SignupRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    ip = request.client.host if request.client else None
    if ip:
        # D-AUTH-1 keeps the explicit "email already has an account" answer, so volume per
        # address is what stops address harvesting.
        enforce("signup_ip", ip, limit=settings.AUTH_SIGNUP_PER_IP_HOURLY, window_seconds=3600)
    if settings.SIGNUP_REQUIRE_TERMS and not payload.accepted_terms:
        raise AppError(
            code="TERMS_NOT_ACCEPTED",
            message="Accept the Terms and Privacy Policy to create your account.",
            http_status=422,
            field_errors=[
                {
                    "field": "accepted_terms",
                    "message": "Accept the Terms and Privacy Policy to continue.",
                }
            ],
        )
    validate_password_strength(payload.password)
    if db.query(User).filter(User.email == payload.email).first():
        raise EmailTaken()
    user = User(
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        status=UserStatus.pending_verification,
        terms_accepted_at=datetime.now(UTC) if payload.accepted_terms else None,
        terms_version=settings.TERMS_VERSION if payload.accepted_terms else None,
    )
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    _send_verification(db, user)
    write_audit(
        db,
        "auth.user.registered",
        actor_user_id=user.id,
        ip=ip,
        details={"terms_version": settings.TERMS_VERSION} if payload.accepted_terms else None,
    )
    event_bus.publish(db, "auth.user.registered", {"user_id": str(user.id)})
    db.commit()
    return success_response(
        {"user": {"id": str(user.id), "email": user.email}, "verification_sent": True}
    )


@router.post("/verify")
def verify(payload: TokenRequest, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    user = consume_auth_token(db, AuthTokenPurpose.email_verification, payload.token)
    # A token issued before a staff suspension must not lift it.
    if user.status != UserStatus.disabled:
        user.status = UserStatus.active
    user.email_verified_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(db, "auth.user.verified", {"user_id": str(user.id)})
    db.commit()
    return success_response({"verified": True})


@router.post("/verify/resend")
def resend(
    payload: EmailRequest, request: Request, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    ip = request.client.host if request.client else None
    if ip:
        enforce("resend_ip", ip, limit=settings.AUTH_RESEND_PER_IP_HOURLY, window_seconds=3600)
    redis = get_redis()
    cooldown_key = f"verify_resend_cooldown:{payload.email}"
    # The cooldown and the hourly cap are keyed on the typed address, never on whether it has an
    # account, so `sent` / `retry_after` are the same for every address: no enumeration signal.
    capped = blocked_for(
        "resend_email",
        payload.email,
        limit=settings.AUTH_RESEND_PER_EMAIL_HOURLY,
        window_seconds=3600,
    )
    # set(..., nx=True): only the first caller within the 60s window claims the key and
    # proceeds to issue+send; every other caller gets a no-op with the time left to wait.
    if capped is None and redis.set(cooldown_key, "1", ex=_RESEND_COOLDOWN_SECONDS, nx=True):
        user = (
            db.query(User)
            .filter(User.email == payload.email, User.status == UserStatus.pending_verification)
            .first()
        )
        if user is not None:
            try:
                # SAVEPOINT: a failed send rolls back the just-issued token, not the session.
                with db.begin_nested():
                    _send_verification(db, user)
            except EmailDeliveryFailed:
                # Swallowed: only a real pending account reaches the send, so a 503 here
                # would reveal that the email is registered. The cooldown still applies.
                log.warning("verification resend failed: email provider refused the send")
            db.commit()
        return success_response({"sent": True, "retry_after": _RESEND_COOLDOWN_SECONDS})
    if capped is not None:
        return success_response({"sent": False, "retry_after": capped})
    wait = int(cast(Any, redis.ttl(cooldown_key)))
    return success_response(
        {"sent": False, "retry_after": wait if wait > 0 else _RESEND_COOLDOWN_SECONDS}
    )
