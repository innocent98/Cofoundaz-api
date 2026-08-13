from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.core.errors import MfaInvalidCode
from app.db.models.enums import MfaType
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import MfaChallengeRequest, TotpVerifyRequest
from app.services.auth import mfa
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie

router = APIRouter(prefix="/mfa")


@router.post("/totp/setup")
def totp_setup(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    secret = mfa.generate_totp_secret()
    user.mfa_secret = mfa.encrypt_secret(secret)  # pending until verify
    db.commit()
    return success_response(
        {"secret": secret, "otpauth_uri": mfa.provisioning_uri(secret, user.email)}
    )


@router.post("/totp/verify")
def totp_verify(
    payload: TotpVerifyRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if not user.mfa_secret or not mfa.verify_totp(
        mfa.decrypt_secret(user.mfa_secret), payload.code
    ):
        raise MfaInvalidCode()
    user.mfa_type = MfaType.totp
    user.mfa_enabled_at = datetime.now(UTC)
    codes = mfa.generate_backup_codes(db, user)
    db.commit()
    return success_response({"enabled": True, "backup_codes": codes})


@router.post("/challenge")
def challenge(
    payload: MfaChallengeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    user_id = mfa.resolve_mfa_ticket(payload.mfa_ticket)
    if user_id is None:
        raise MfaInvalidCode()
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.mfa_secret:
        raise MfaInvalidCode()
    ok = mfa.verify_totp(
        mfa.decrypt_secret(user.mfa_secret), payload.code
    ) or mfa.consume_backup_code(db, user, payload.code)
    if not ok:
        raise MfaInvalidCode()
    user.last_login_at = datetime.now(UTC)
    access, refresh = issue_token_pair(
        db,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response({"access_token": access, "refresh_token": refresh})
