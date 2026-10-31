"""Fresh TOTP step-up for Super Admin mutations.

Every mutation carries its own `totp_code`. The code must be valid for the current or previous
30-second step (so at most about a minute old) and is single use: a replay inside that window is
refused. Wrong codes share a 5-strikes / 15-minute lock per staff user.
"""

from cryptography.fernet import InvalidToken

from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.user import User
from app.services.auth import mfa
from app.services.auth.throttle import assert_not_locked, clear, record_failure

STEP_UP_REPLAY_TTL = 90  # seconds; longer than the code's ~60 s validity


class StepUpInvalid(AppError):  # noqa: N818
    # 403, not 401: the FE treats 401 as "session expired" and signs the user out.
    code, http_status = "STEP_UP_INVALID", 403
    message = "That authenticator code isn't right, or was already used. Enter a new one."


def verify_step_up(user: User, code: str) -> None:
    key = f"staff_stepup_fail:{user.id}"
    assert_not_locked(key)
    try:
        ok = bool(user.mfa_secret) and mfa.verify_totp(
            mfa.decrypt_secret(user.mfa_secret or ""), code
        )
    except InvalidToken:
        ok = False
    if not ok:
        record_failure(key)
        raise StepUpInvalid()
    first_use = get_redis().set(
        f"staff_stepup_used:{user.id}:{code}", "1", nx=True, ex=STEP_UP_REPLAY_TTL
    )
    if not first_use:
        record_failure(key)
        raise StepUpInvalid()
    clear(key)
