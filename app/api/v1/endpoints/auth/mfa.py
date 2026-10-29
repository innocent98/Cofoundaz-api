from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.envelope import success_response
from app.core.errors import AppError, MfaInvalidCode
from app.core.security import verify_password
from app.db.models.auth import MfaBackupCode
from app.db.models.enums import MfaType
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.schemas.auth import (
    MfaChallengeRequest,
    MfaDisableRequest,
    MfaRegenerateRequest,
    TotpVerifyRequest,
)
from app.services.auth import mfa
from app.services.auth.sessions import issue_token_pair, set_refresh_cookie
from app.services.auth.throttle import assert_not_locked, clear, record_failure

router = APIRouter(prefix="/mfa")


def _wrong_password() -> AppError:
    return AppError(
        code="CURRENT_PASSWORD_WRONG", message="That isn't your current password.", http_status=422
    )


def _check_password(user: User, password: str) -> None:
    """Sensitive 2FA changes re-ask for the password, throttled per user like the password change."""
    key = f"mfa_manage_fail:{user.id}"
    assert_not_locked(key)
    if not user.password_hash or not verify_password(password, user.password_hash):
        record_failure(key)
        raise _wrong_password()
    clear(key)


@router.get("")
def mfa_status(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enabled = user.mfa_type == MfaType.totp
    remaining = (
        db.query(MfaBackupCode)
        .filter(MfaBackupCode.user_id == user.id, MfaBackupCode.consumed_at.is_(None))
        .count()
        if enabled
        else 0
    )
    return success_response(
        {
            "enabled": enabled,
            "method": "totp" if enabled else None,
            "enabled_at": user.mfa_enabled_at.isoformat() if user.mfa_enabled_at else None,
            "backup_codes_remaining": remaining,
        }
    )


@router.post("/disable")
def mfa_disable(
    payload: MfaDisableRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if user.mfa_type != MfaType.totp or not user.mfa_secret:
        raise AppError(code="MFA_NOT_ENABLED", message="Two-factor isn't on.", http_status=409)
    _check_password(user, payload.password)
    if not (
        mfa.verify_totp(mfa.decrypt_secret(user.mfa_secret), payload.code)
        or mfa.consume_backup_code(db, user, payload.code)
    ):
        raise MfaInvalidCode()
    user.mfa_type = MfaType.none
    user.mfa_secret = None
    user.mfa_enabled_at = None
    db.query(MfaBackupCode).filter(MfaBackupCode.user_id == user.id).delete()
    write_audit(db, "auth.mfa.disabled", actor_user_id=user.id)
    db.commit()
    return success_response({"enabled": False})


@router.post("/backup-codes/regenerate")
def mfa_regenerate(
    payload: MfaRegenerateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if user.mfa_type != MfaType.totp:
        raise AppError(code="MFA_NOT_ENABLED", message="Two-factor isn't on.", http_status=409)
    _check_password(user, payload.password)
    codes = mfa.generate_backup_codes(db, user)
    write_audit(db, "auth.mfa.backup_codes.regenerated", actor_user_id=user.id)
    db.commit()
    return success_response({"backup_codes": codes})


@router.post("/totp/setup")
def totp_setup(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if user.mfa_type == MfaType.totp:
        raise AppError(
            code="MFA_ALREADY_ENABLED",
            message="Two-factor is already on. Turn it off before setting it up again.",
            http_status=409,
        )
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
    write_audit(db, "auth.mfa.enabled", actor_user_id=user.id)
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
    ip = request.client.host if request.client else None
    ok = mfa.verify_totp(
        mfa.decrypt_secret(user.mfa_secret), payload.code
    ) or mfa.consume_backup_code(db, user, payload.code)
    if not ok:
        write_audit(db, "auth.mfa.challenge.failed", actor_user_id=user.id, ip=ip)
        db.commit()
        raise MfaInvalidCode()
    user.last_login_at = datetime.now(UTC)
    access, refresh = issue_token_pair(
        db, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    write_audit(db, "auth.mfa.challenge.success", actor_user_id=user.id, ip=ip)
    db.commit()
    set_refresh_cookie(response, refresh)
    return success_response({"access_token": access, "refresh_token": refresh})
