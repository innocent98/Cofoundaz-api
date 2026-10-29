"""Per-user throttle on wrong-password guesses made through an already signed-in session.

A stolen access token must not be a free oracle for the real password, so the sensitive account
changes (change password, turn 2FA off, new backup codes) share this 5-strikes / 15-minute lock.
"""

from typing import Any, cast

from app.core.errors import AppError
from app.core.redis import get_redis

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


def assert_not_locked(key: str) -> None:
    if int(cast(Any, get_redis().get(key)) or 0) >= MAX_FAILURES:
        raise AppError(
            code="TOO_MANY_ATTEMPTS",
            message="Too many attempts. Try again in 15 minutes.",
            http_status=429,
        )


def record_failure(key: str) -> None:
    redis = get_redis()
    redis.incr(key)
    redis.expire(key, WINDOW_SECONDS)


def clear(key: str) -> None:
    get_redis().delete(key)
