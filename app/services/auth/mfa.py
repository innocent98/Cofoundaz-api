import secrets
import uuid
from datetime import UTC, datetime
from typing import cast

import pyotp
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.auth import MfaBackupCode
from app.db.models.user import User
from app.services.auth.sessions import hash_token

_MFA_TICKET_TTL = 300  # 5 minutes
_MFA_TICKET_PREFIX = "mfa_ticket:"


class MfaNotConfigured(AppError):  # noqa: N818
    code, http_status = "MFA_NOT_CONFIGURED", 500
    message = "MFA is not configured on the server."


def _fernet() -> Fernet:
    if not settings.MFA_ENCRYPTION_KEY:
        raise MfaNotConfigured()
    return Fernet(settings.MFA_ENCRYPTION_KEY.encode())


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Cofoundaz")


def verify_totp(secret: str, code: str) -> bool:
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))


def generate_backup_codes(db: Session, user: User) -> list[str]:
    db.query(MfaBackupCode).filter(MfaBackupCode.user_id == user.id).delete()
    codes: list[str] = []
    for _ in range(10):
        code = f"{secrets.randbelow(10**10):010d}"
        codes.append(code)
        db.add(MfaBackupCode(user_id=user.id, code_hash=hash_token(code)))
    db.flush()
    return codes


def consume_backup_code(db: Session, user: User, code: str) -> bool:
    row = (
        db.query(MfaBackupCode)
        .filter(
            MfaBackupCode.user_id == user.id,
            MfaBackupCode.code_hash == hash_token(code),
            MfaBackupCode.consumed_at.is_(None),
        )
        .first()
    )
    if row is None:
        return False
    row.consumed_at = datetime.now(UTC)
    db.flush()
    return True


def issue_mfa_ticket(user_id: uuid.UUID) -> str:
    ticket = secrets.token_urlsafe(32)
    get_redis().setex(f"{_MFA_TICKET_PREFIX}{ticket}", _MFA_TICKET_TTL, str(user_id))
    return ticket


def resolve_mfa_ticket(ticket: str) -> uuid.UUID | None:
    key = f"{_MFA_TICKET_PREFIX}{ticket}"
    val = cast(str | None, get_redis().get(key))
    if val is None:
        return None
    get_redis().delete(key)
    return uuid.UUID(val)
