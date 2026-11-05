"""Redis counters behind the auth surface's brute-force defences.

Two things live here, both keyed on hashes (an email or IP never appears in a Redis key):

* `enforce` / `blocked_for`: fixed-window per-route limits (sign-up per IP, forgot per email, ...).
* The sign-in lockout (Module 01 gap 1, D-AUTH-2): failures are counted per (email, IP), and the
  answer is the same for an address that has no account. A second counter per email, summed over
  all IPs, caps a distributed guess at one account without letting one noisy address lock the
  real owner out from their own IP.

A lock holds for `LOGIN_LOCKOUT_MINUTES` from the failure that tripped it: locked attempts are
refused before the password is checked and never extend it (EXPIRE ... NX).
"""

import hashlib
import ipaddress
import time
from typing import Any, cast

from app.core.config import settings
from app.core.errors import AccountLocked, RateLimited
from app.core.redis import get_redis

_LOGIN_WINDOW = 15 * 60


def _h(value: str, n: int = 32) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:n]


def ip_bucket(ip: str | None) -> str | None:
    """What a per-IP counter is keyed on: the address itself for IPv4, the /64 for IPv6 (one
    host or one customer holds a whole /64, so per-address keys would be free to rotate)."""
    if not ip:
        return ip
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return ip


def _count_and_ttl(key: str) -> tuple[int, int]:
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.get(key)
    pipe.ttl(key)
    raw, ttl = cast(list[Any], pipe.execute())
    return int(raw or 0), max(int(ttl), 0)


def _incr(key: str, window_seconds: int) -> tuple[int, int]:
    """Atomically count one hit: INCR and EXPIRE ... NX run in one MULTI, so a worker that dies
    mid-way can never leave a counter without an expiry. Returns (count, seconds left)."""
    pipe = get_redis().pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, window_seconds, nx=True)
    pipe.ttl(key)
    count, _, ttl = cast(list[Any], pipe.execute())
    return int(count), max(int(ttl), 1)


def _hit(scope: str, subject: str, window_seconds: int) -> tuple[int, int]:
    """Count one hit in a fixed window; return (hits so far, seconds left in the window)."""
    return _incr(f"rl:{scope}:{_h(subject)}", window_seconds)


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


def mail_blocked_for(
    kind: str, email: str, ip: str | None, *, pair_limit: int, email_limit: int
) -> int | None:
    """Silent cap on mail to one address: per (address, caller IP) and per address overall.
    The overall counter only counts calls the pair cap let through. None when allowed, else
    seconds until it reopens."""
    pair = blocked_for(
        f"{kind}_email_ip",
        f"{email}|{ip_bucket(ip) or 'unknown'}",
        limit=pair_limit,
        window_seconds=3600,
    )
    if pair is not None:
        # Refused at the pair cap: not counted against the address overall, so one noisy caller
        # adds at most `pair_limit` an hour to it and cannot use up the owner's share.
        return pair
    return blocked_for(f"{kind}_email", email, limit=email_limit, window_seconds=3600)


# --- sign-in lockout -------------------------------------------------------------------------
# The attempt is RESERVED before the password is checked: one atomic INCR per counter, refuse
# when the count is past the limit, undo on success. A check-then-act version lets a parallel
# burst pass the check together and guess far past the limit.


def _pair_key(email: str, ip: str | None) -> str:
    return f"lf:{_h(email)}:{_h(ip_bucket(ip) or 'unknown', 16)}"


def _email_key(email: str) -> str:
    return f"lfe:{_h(email)}"


def _known_ips_key(email: str) -> str:
    return f"kip:{_h(email)}"


_KNOWN_IP_DAYS = 30


def _is_known_ip(email: str, ip: str | None) -> bool:
    """Has this account signed in successfully from this address in the last 30 days?"""
    redis = get_redis()
    key = _known_ips_key(email)
    score = redis.zscore(key, _h(ip_bucket(ip) or "unknown", 16))
    return score is not None and float(cast(Any, score)) >= time.time() - _KNOWN_IP_DAYS * 86400


def record_successful_sign_in(email: str, ip: str | None) -> None:
    """Remember the address of a good sign-in (hashed, /64 for IPv6). The email-wide counter
    never refuses these, so a spread-out guess at someone's password cannot lock them out of
    their own devices."""
    redis = get_redis()
    key = _known_ips_key(email)
    now = time.time()
    pipe = redis.pipeline(transaction=True)
    pipe.zadd(key, {_h(ip_bucket(ip) or "unknown", 16): now})
    pipe.zremrangebyscore(key, 0, now - _KNOWN_IP_DAYS * 86400)
    pipe.zremrangebyrank(key, 0, -51)  # at most 50 addresses per account
    pipe.expire(key, _KNOWN_IP_DAYS * 86400)
    pipe.execute()


def reserve_login_attempt(email: str, ip: str | None) -> None:
    """Count this attempt, or raise ACCOUNT_LOCKED when the (email, IP) is past its limit, or
    the email is past its all-IP ceiling and this address has no recent good sign-in."""
    pair, pair_ttl = _incr(_pair_key(email, ip), settings.LOGIN_LOCKOUT_MINUTES * 60)
    total, total_ttl = _incr(_email_key(email), _LOGIN_WINDOW)
    if pair > settings.LOGIN_MAX_FAILS:
        raise AccountLocked(pair_ttl)
    if total > settings.LOGIN_EMAIL_MAX_FAILS and not _is_known_ip(email, ip):
        raise AccountLocked(total_ttl)


def record_login_failure(email: str, ip: str | None) -> None:
    """The reserved attempt was wrong: when it is the one that reaches the limit, hold the lock
    for the full duration from now."""
    lock_seconds = settings.LOGIN_LOCKOUT_MINUTES * 60
    redis = get_redis()
    for key, ceiling in (
        (_pair_key(email, ip), settings.LOGIN_MAX_FAILS),
        (_email_key(email), settings.LOGIN_EMAIL_MAX_FAILS),
    ):
        if int(cast(Any, redis.get(key)) or 0) >= ceiling:
            redis.expire(key, lock_seconds)


def clear_login_failures(email: str, ip: str | None = None, *, everywhere: bool = False) -> None:
    """A good sign-in clears this (email, IP) and gives back its slot in the email total. A
    password reset (`everywhere`) clears every IP and the email total: the message tells a locked
    person to reset, so the reset must work."""
    redis = get_redis()
    if everywhere:
        redis.delete(_pair_key(email, ip), _email_key(email))
        for key in redis.scan_iter(match=f"lf:{_h(email)}:*"):
            redis.delete(key)
        return
    redis.delete(_pair_key(email, ip))
    key = _email_key(email)
    if int(cast(Any, redis.get(key)) or 0) > 0:
        redis.decr(key)


# --- MFA challenge, per user ----------------------------------------------------------------
# The ticket's own attempt cap bounds guesses per sign-in; this bounds them per account across
# tickets, so someone who knows the password cannot mint ticket after ticket (each is just a
# login) and keep guessing the code. Reserved atomically like the sign-in lockout.


def _mfa_key(user_id: object) -> str:
    return f"mfa_fail:{user_id}"


def reserve_mfa_attempt(user_id: object) -> None:
    count, ttl = _incr(_mfa_key(user_id), _LOGIN_WINDOW)
    if count > settings.MFA_USER_MAX_FAILS:
        raise AccountLocked(ttl)


def record_mfa_failure(user_id: object) -> None:
    redis = get_redis()
    key = _mfa_key(user_id)
    if int(cast(Any, redis.get(key)) or 0) >= settings.MFA_USER_MAX_FAILS:
        redis.expire(key, settings.LOGIN_LOCKOUT_MINUTES * 60)


def clear_mfa_failures(user_id: object) -> None:
    get_redis().delete(_mfa_key(user_id))
