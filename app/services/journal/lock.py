"""Optional journal passcode (D-JNL-1) and per-founder journal settings.

The passcode is a screen lock enforced by the API, not a second encryption key: entries stay
Fernet-encrypted with the server key. While a passcode is set, every content route needs an unlock
token (`X-Journal-Token`) minted by `POST /journal/unlock`; without a valid one they answer
`423 JOURNAL_LOCKED`. A token is an HMAC over (founder, passcode version, expiry), so changing or
removing the passcode kills every outstanding token. Five wrong tries lock unlocking for 15 minutes.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.errors import AppError
from app.core.security import get_password_hash, verify_password
from app.db.models.journal import JournalSettings

TOKEN_TTL_SECONDS = 30 * 60
MAX_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
MAX_LOCKOUT = timedelta(hours=24)
_PURPOSE = "journal-unlock"


class JournalLocked(AppError):  # noqa: N818
    code = "JOURNAL_LOCKED"
    message = "Unlock your journal to continue."
    http_status = 423


def get_settings(db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID) -> JournalSettings:
    """The founder's settings row, created on first use (race-safe)."""
    db.execute(
        pg_insert(JournalSettings)
        .values(id=uuid.uuid4(), startup_id=startup_id, founder_id=founder_id)
        .on_conflict_do_nothing(constraint="uq_journal_settings_startup_founder")
    )
    return (
        db.query(JournalSettings)
        .filter_by(startup_id=startup_id, founder_id=founder_id)
        .with_for_update()
        .one()
    )


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------


def _key() -> str:
    # Not SECRET_KEY itself: an unlock token must never verify as a login token (both are
    # HS256 JWTs carrying the founder id as `sub`), so the key is purpose-scoped as well.
    return f"{_PURPOSE}:{app_settings.SECRET_KEY}"


def mint_token(row: JournalSettings, *, now: float | None = None) -> str:
    """A 30-minute HS256 JWT: sub = founder, sid = workspace, ver = passcode version."""
    issued = now if now is not None else time.time()
    claims = {
        "sub": str(row.founder_id),
        "sid": str(row.startup_id),
        "ver": row.passcode_version,
        "purpose": _PURPOSE,
        "exp": int(issued + TOKEN_TTL_SECONDS),
    }
    return jwt.encode(claims, _key(), algorithm="HS256")


def _token_ok(row: JournalSettings, token: str) -> bool:
    try:
        claims = jwt.decode(
            token, _key(), algorithms=["HS256"], options={"require": ["exp", "sub", "sid", "ver"]}
        )
    except jwt.PyJWTError:
        return False
    return (
        claims.get("purpose") == _PURPOSE
        and claims["sub"] == str(row.founder_id)
        and claims["sid"] == str(row.startup_id)
        and claims["ver"] == row.passcode_version
    )


def require_unlocked(
    db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID, token: str | None
) -> None:
    """Raise `423 JOURNAL_LOCKED` when a passcode is set and the token is missing or stale."""
    row = db.query(JournalSettings).filter_by(startup_id=startup_id, founder_id=founder_id).first()
    if row is None or row.passcode_hash is None:
        return
    if token is None or not _token_ok(row, token):
        raise JournalLocked()


# ----------------------------------------------------------------------
# Passcode lifecycle
# ----------------------------------------------------------------------


def set_passcode(
    db: Session,
    row: JournalSettings,
    *,
    passcode: str,
    current_passcode: str | None,
) -> str:
    """Set the first passcode, or change it (needs the current one). Returns a fresh unlock token
    so the founder is not locked out of the screen they just configured."""
    if row.passcode_hash is not None:
        _check_not_locked_out(row)
        if current_passcode is None or not verify_password(current_passcode, row.passcode_hash):
            _register_failure(db, row)
            raise AppError(
                "PASSCODE_INCORRECT",
                "That passcode did not match.",
                403,
                field_errors=[{"field": "current_passcode", "message": "Incorrect passcode."}],
            )
    row.passcode_hash = get_password_hash(passcode)
    row.passcode_version += 1
    row.failed_attempts = 0
    row.locked_until = None
    db.flush()
    return mint_token(row)


def _check_not_locked_out(row: JournalSettings) -> None:
    if row.locked_until is not None and row.locked_until > datetime.now(UTC):
        raise AppError(
            "PASSCODE_LOCKED_OUT",
            "Too many wrong tries. Try again in a few minutes.",
            429,
        )


def _register_failure(db: Session, row: JournalSettings) -> None:
    # The counter keeps running (reset only by a success), and each further round of
    # MAX_ATTEMPTS doubles the lockout (15 min, 30, 60 ... capped at 24 h).
    row.failed_attempts += 1
    if row.failed_attempts % MAX_ATTEMPTS == 0:
        rounds = row.failed_attempts // MAX_ATTEMPTS
        exponent = min(rounds - 1, 7)  # 15 min * 2**7 already exceeds the 24 h cap
        row.locked_until = datetime.now(UTC) + min(LOCKOUT * 2**exponent, MAX_LOCKOUT)
    # The failure must survive the error response (the request transaction rolls back on raise).
    db.commit()


def unlock(db: Session, row: JournalSettings, *, passcode: str) -> str:
    if row.passcode_hash is None:
        raise AppError("PASSCODE_NOT_SET", "No journal passcode is set.", 409)
    _check_not_locked_out(row)
    if not verify_password(passcode, row.passcode_hash):
        _register_failure(db, row)
        raise AppError(
            "PASSCODE_INCORRECT",
            "That passcode did not match.",
            403,
            field_errors=[{"field": "passcode", "message": "Incorrect passcode."}],
        )
    row.failed_attempts = 0
    row.locked_until = None
    db.flush()
    return mint_token(row)


def remove_passcode(db: Session, row: JournalSettings) -> None:
    """Drop the passcode (the caller has already verified the account password)."""
    row.passcode_hash = None
    row.passcode_version += 1
    row.failed_attempts = 0
    row.locked_until = None
    db.flush()


def lock_now(db: Session, row: JournalSettings) -> None:
    """Invalidate every outstanding unlock token (the 'Lock journal' button)."""
    if row.passcode_hash is not None:
        row.passcode_version += 1
        db.flush()
