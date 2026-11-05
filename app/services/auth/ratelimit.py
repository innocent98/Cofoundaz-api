"""Redis counters behind the auth surface's brute-force defences.

Two things live here, both keyed on hashes (an email or IP never appears in a Redis key):

* `enforce` / `allow`: fixed-window per-route limits (sign-up per IP, forgot per email, ...).
* The sign-in lockout (Module 01 gap 1, D-AUTH-2): failures are counted per (email, IP), and the
  answer is the same for an address that has no account. A second counter per email, summed over
  all IPs, caps a distributed guess at one account without letting one noisy address lock the
  real owner out from their own IP.

A lock holds for `LOGIN_LOCKOUT_MINUTES` from the failure that tripped it: locked attempts are
refused before the password is checked and are not counted, so they cannot extend it.
"""

import hashlib
from typing import Any, cast

from app.core.config import settings
from app.core.errors import AccountLocked, RateLimited
from app.core.redis import get_redis

_LOGIN_WINDOW = 15 * 60


def _h(value: str, n: int = 32) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:n]


def _count_and_ttl(key: str) -> tuple[int, int]:
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.get(key)
    pipe.ttl(key)
    raw, ttl = cast(list[Any], pipe.execute())
    return int(raw or 0), max(int(ttl), 0)


def _hit(scope: str, subject: str, window_seconds: int) -> tuple[int, int]:
    """Count one hit in a fixed window; return (hits so far, seconds left in the window)."""
    key = f"rl:{scope}:{_h(subject)}"
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = cast(list[Any], pipe.execute())
    if int(ttl) < 0:  # first hit (or a key that lost its expiry): start the window
        redis.expire(key, window_seconds)
        ttl = window_seconds
    return int(count), int(ttl)


def enforce(scope: str, subject: str, *, limit: int, window_seconds: int) -> None:
    """Count a hit and refuse (429 RATE_LIMITED, with `retry_after_seconds`) past `limit`."""
    count, ttl = _hit(scope, subject, window_seconds)
    if count > limit:
        raise RateLimited(max(ttl, 1))


def blocked_for(scope: str, subject: str, *, limit: int, window_seconds: int) -> int | None:
    """Count a hit; None when it is within `limit`, else the seconds until the window reopens.
    For limits that must stay silent (a per-email cap on mail whose response is identical for
    every address, so the caller answers the same either way)."""
    count, ttl = _hit(scope, subject, window_seconds)
    return None if count <= limit else max(ttl, 1)


# --- sign-in lockout -------------------------------------------------------------------------


def _pair_key(email: str, ip: str | None) -> str:
    return f"lf:{_h(email)}:{_h(ip or 'unknown', 16)}"


def _email_key(email: str) -> str:
    return f"lfe:{_h(email)}"


def assert_login_allowed(email: str, ip: str | None) -> None:
    """Raise ACCOUNT_LOCKED when this (email, IP) -- or the email overall -- is locked."""
    pair, pair_ttl = _count_and_ttl(_pair_key(email, ip))
    if pair >= settings.LOGIN_MAX_FAILS:
        raise AccountLocked(max(pair_ttl, 1))
    total, total_ttl = _count_and_ttl(_email_key(email))
    if total >= settings.LOGIN_EMAIL_MAX_FAILS:
        raise AccountLocked(max(total_ttl, 1))


def record_login_failure(email: str, ip: str | None) -> None:
    """Count a wrong password (or an unknown email: same counters, same lock)."""
    lock_seconds = settings.LOGIN_LOCKOUT_MINUTES * 60
    redis = get_redis()
    for key, ceiling in (
        (_pair_key(email, ip), settings.LOGIN_MAX_FAILS),
        (_email_key(email), settings.LOGIN_EMAIL_MAX_FAILS),
    ):
        count = int(cast(Any, redis.incr(key)))
        if count == 1:
            redis.expire(key, _LOGIN_WINDOW)
        if count >= ceiling:  # tripped: hold the lock for the full duration from now
            redis.expire(key, lock_seconds)


def clear_login_failures(email: str, ip: str | None = None, *, everywhere: bool = False) -> None:
    """A good sign-in clears this (email, IP). A password reset (`everywhere`) clears every IP
    and the email total: the message tells a locked person to reset, so the reset must work."""
    redis = get_redis()
    redis.delete(_pair_key(email, ip))
    if everywhere:
        redis.delete(_email_key(email))
        for key in redis.scan_iter(match=f"lf:{_h(email)}:*"):
            redis.delete(key)


# --- MFA challenge, per user ----------------------------------------------------------------
# The ticket's own attempt cap bounds guesses per sign-in; this bounds them per account across
# tickets, so someone who knows the password cannot mint ticket after ticket (each is just a
# login) and keep guessing the code.


def _mfa_key(user_id: object) -> str:
    return f"mfa_fail:{user_id}"


def assert_mfa_allowed(user_id: object) -> None:
    count, ttl = _count_and_ttl(_mfa_key(user_id))
    if count >= settings.MFA_USER_MAX_FAILS:
        raise AccountLocked(max(ttl, 1))


def record_mfa_failure(user_id: object) -> None:
    redis = get_redis()
    key = _mfa_key(user_id)
    count = int(cast(Any, redis.incr(key)))
    if count == 1:
        redis.expire(key, _LOGIN_WINDOW)
    if count >= settings.MFA_USER_MAX_FAILS:
        redis.expire(key, settings.LOGIN_LOCKOUT_MINUTES * 60)


def clear_mfa_failures(user_id: object) -> None:
    get_redis().delete(_mfa_key(user_id))
