"""Optional journal passcode (D-JNL-1) and per-founder journal settings.

The passcode is a screen lock enforced by the API, not a second encryption key: entries stay
Fernet-encrypted with the server key. While a passcode is set, every content route needs an unlock
token (`X-Journal-Token`) minted by `POST /journal/unlock`; without a valid one they answer
`423 JOURNAL_LOCKED`. A token is an HMAC over (founder, passcode version, expiry), so changing or
removing the passcode kills every outstanding token. Five wrong tries lock unlocking for 15 minutes.
"""

import base64
import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta

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
_PURPOSE = b"journal-unlock-v1"


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


def _mac(body: str) -> bytes:
    key = hmac.new(app_settings.SECRET_KEY.encode(), _PURPOSE, hashlib.sha256).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def mint_token(row: JournalSettings, *, now: float | None = None) -> str:
    expiry = int((now if now is not None else time.time()) + TOKEN_TTL_SECONDS)
    body = f"{row.founder_id}.{row.startup_id}.{row.passcode_version}.{expiry}"
    return f"{_b64(body.encode())}.{_b64(_mac(body))}"


def _token_ok(row: JournalSettings, token: str, *, now: float | None = None) -> bool:
    try:
        raw_body, raw_mac = token.split(".")
        pad = "=" * (-len(raw_body) % 4)
        body = base64.urlsafe_b64decode(raw_body + pad).decode()
        mac = base64.urlsafe_b64decode(raw_mac + "=" * (-len(raw_mac) % 4))
        founder, startup, version, expiry = body.split(".")
    except (ValueError, UnicodeDecodeError):
        return False
    if not hmac.compare_digest(mac, _mac(body)):
        return False
    return (
        founder == str(row.founder_id)
        and startup == str(row.startup_id)
        and version == str(row.passcode_version)
        and int(expiry) >= (now if now is not None else time.time())
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
