"""Sign in / sign up with Google (OIDC), product calls of 2026-11-02:

1. A verified Google email that matches an existing account signs into it and links Google to it.
2. 2FA still applies: the exchange ends in the same MFA challenge as a password sign-in.
3. A first Google sign-in creates the account (email already verified); an invite link survives
   the round trip and is honoured only when the Google email is the invited email.

Moving parts (all in Redis, all single-use, all short-lived):
  * login state   `google_signin_state:{jti}`   PKCE verifier, nonce, invite token, FE return path.
                  The browser carries only a signed JWT holding the `jti`, plus an HttpOnly proof
                  cookie bound to the same `jti` (stops login-CSRF: a callback URL started in
                  someone else's browser is useless in the victim's).
  * ticket        `google_signin_ticket:{t}`    what the callback decided; the FE trades it at
                  /exchange for the session (or the MFA challenge). Tokens never ride in a URL.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.auth import MfaBackupCode, OAuthAccount
from app.db.models.enums import (
    AuthTokenPurpose,
    InvitationStatus,
    MfaType,
    OAuthProvider,
    UserStatus,
)
from app.db.models.invitation import Invitation
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.platform.state import maintenance_state
from app.services.auth import google_oidc
from app.services.auth.sessions import hash_token, revoke_all_for_user
from app.services.auth.tokens import invalidate_unconsumed_tokens

STATE_TTL_SECONDS = 600
TICKET_TTL_SECONDS = 120
STATE_TYP = "google_signin_state"
PROOF_TYP = "google_signin_proof"
_STATE_KEY = "google_signin_state:"
_TICKET_KEY = "google_signin_ticket:"
_RATE_KEY = "google_signin_rate:"
# Generous: behind a proxy every caller can share one address, so this only stops a flood.
RATE_LIMIT = 100
RATE_WINDOW_SECONDS = 600

# Error codes the callback hands the FE in `?error=`.
ERR_DENIED = "access_denied"
ERR_STATE = "invalid_state"
ERR_EXCHANGE = "exchange_failed"
ERR_EMAIL_UNVERIFIED = "email_not_verified"
ERR_DISABLED = "account_disabled"
ERR_UNAVAILABLE = "account_unavailable"
ERR_MISMATCH = "google_account_mismatch"
ERR_INVITE_INVALID = "invite_invalid"
ERR_INVITE_MISMATCH = "invite_email_mismatch"
ERR_MAINTENANCE = "maintenance"
ERR_RATE = "rate_limited"
ERR_CONFLICT = "try_again"

_NEXT_RE = re.compile(r"^/[A-Za-z0-9/_\-.?=&]{0,199}$")


def _now() -> datetime:
    return datetime.now(UTC)


def safe_next(value: str | None) -> str | None:
    """A same-site FE path or nothing. Never a URL: no scheme, no host, no `//`, no backslash."""
    if not value or "//" in value or "\\" in value or not _NEXT_RE.match(value):
        return None
    return value


def rate_limited(ip: str | None) -> bool:
    key = f"{_RATE_KEY}{ip or 'unknown'}"
    redis = get_redis()
    count = int(cast(Any, redis.incr(key)))
    if count == 1:
        redis.expire(key, RATE_WINDOW_SECONDS)
    return count > RATE_LIMIT


def _sign(typ: str, jti: str) -> str:
    claims = {"typ": typ, "jti": jti, "exp": _now() + timedelta(seconds=STATE_TTL_SECONDS)}
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _read_jti(token: str | None, typ: str) -> str | None:
    if not token:
        return None
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        return None
    jti = claims.get("jti")
    return str(jti) if claims.get("typ") == typ and jti else None


def _pop(key: str) -> dict[str, Any] | None:
    """Read and delete in one MULTI/EXEC, so two racing callers cannot both get the value."""
    pipe = get_redis().pipeline(transaction=True)
    pipe.get(key)
    pipe.delete(key)
    raw = pipe.execute()[0]
    if raw is None:
        return None
    value = json.loads(str(raw))
    return value if isinstance(value, dict) else None


def invitation_by_token(db: Session, token: str) -> Invitation | None:
    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()
    if inv is None or inv.status != InvitationStatus.pending or inv.expires_at < _now():
        return None
    return inv


def start(db: Session, *, invite_token: str | None, next_path: str | None) -> tuple[str, str]:
    """(auth_url, proof cookie value). An unusable invite is refused now, not after the detour."""
    if not google_oidc.configured():
        raise google_oidc.GoogleSignInNotConfigured()
    if invite_token and invitation_by_token(db, invite_token) is None:
        from app.core.errors import NotFound

        raise NotFound()
    jti = secrets.token_urlsafe(24)
    verifier, challenge = google_oidc.new_pkce()
    nonce = secrets.token_urlsafe(24)
    record = {
        "verifier": verifier,
        "nonce": nonce,
        "invite": invite_token,
        "next": safe_next(next_path),
    }
    get_redis().setex(f"{_STATE_KEY}{jti}", STATE_TTL_SECONDS, json.dumps(record))
    url = google_oidc.build_auth_url(state=_sign(STATE_TYP, jti), nonce=nonce, challenge=challenge)
    return url, _sign(PROOF_TYP, jti)


def _ticket(
    user: User, *, jti: str, new_user: bool, invite: str | None, next_path: str | None
) -> str:
    ticket = secrets.token_urlsafe(32)
    record = {
        "user_id": str(user.id),
        "jti": jti,
        "new_user": new_user,
        "invite": invite,
        "next": next_path,
    }
    get_redis().setex(f"{_TICKET_KEY}{ticket}", TICKET_TTL_SECONDS, json.dumps(record))
    return ticket


def read_ticket(ticket: str, proof: str | None) -> dict[str, Any] | None:
    """Pop the ticket, but only for the browser that started the flow: otherwise an attacker could
    park their own ticket URL in a victim's browser (login CSRF)."""
    record = _pop(f"{_TICKET_KEY}{ticket}")
    if record is None or _read_jti(proof, PROOF_TYP) != record.get("jti"):
        return None
    return record


def _reject(db: Session, reason: str, *, ip: str | None, user: User | None = None) -> str:
    write_audit(
        db,
        "auth.google.rejected",
        actor_user_id=user.id if user else None,
        ip=ip,
        details={"reason": reason},
    )
    return reason


def complete_callback(
    db: Session,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    proof: str | None,
    ip: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Finish the round trip. Returns (ticket, error_code, next_path); exactly one of the first
    two is set. The route is public, so identity comes ONLY from the verified id_token."""
    jti = _read_jti(state, STATE_TYP)
    # Consume the state first, whatever happens next: a state is good for one callback.
    record = _pop(f"{_STATE_KEY}{jti}") if jti else None
    if record is None or _read_jti(proof, PROOF_TYP) != jti:
        return None, ERR_STATE, None
    next_path = cast("str | None", record.get("next"))
    if error or not code:
        return None, ERR_DENIED, next_path
    try:
        claims = google_oidc.verify_id_token(
            google_oidc.exchange_code(code, verifier=str(record["verifier"])),
            nonce=str(record["nonce"]),
        )
    except google_oidc.GoogleIdentityError:
        return None, _reject(db, ERR_EXCHANGE, ip=ip), next_path
    sub = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified") in (True, "true")
    if not email or not verified:
        return None, _reject(db, ERR_EMAIL_UNVERIFIED, ip=ip), next_path

    invite_token = cast("str | None", record.get("invite"))
    inv = None
    if invite_token:
        inv = invitation_by_token(db, invite_token)
        if inv is None:
            return None, _reject(db, ERR_INVITE_INVALID, ip=ip), next_path
        if inv.email.lower() != email:
            return None, _reject(db, ERR_INVITE_MISMATCH, ip=ip), next_path

    try:
        outcome = _resolve_user(db, sub=sub, email=email, name=claims.get("name"), ip=ip)
    except IntegrityError:
        db.rollback()  # two first sign-ins raced; the loser tries again and finds the winner
        return None, ERR_CONFLICT, next_path
    if isinstance(outcome, str):
        return None, outcome, next_path
    user, new_user = outcome
    if inv is not None and user.email.lower() != inv.email.lower():
        # Signed in through an older Google identity whose account email is not the invited one.
        return None, _reject(db, ERR_INVITE_MISMATCH, ip=ip, user=user), next_path
    return (
        _ticket(user, jti=str(jti), new_user=new_user, invite=invite_token, next_path=next_path),
        None,
        next_path,
    )


def _resolve_user(
    db: Session, *, sub: str, email: str, name: Any, ip: str | None
) -> tuple[User, bool] | str:
    """The account for this Google identity: linked by `sub`, else by verified email, else new.
    Returns (user, is_new) or an error code."""
    linked = (
        db.query(OAuthAccount)
        .filter_by(provider=OAuthProvider.google, provider_account_id=sub)
        .first()
    )
    if linked is not None:
        # The `sub` is the identity; an email change at Google must not strand or re-route it.
        user = db.query(User).filter(User.id == linked.user_id).with_for_update().one()
        blocked = _blocked(db, user, ip)
        if blocked:
            return blocked
        write_audit(db, "auth.google.signin", actor_user_id=user.id, ip=ip)
        return user, False

    existing = db.query(User).filter(User.email == email).with_for_update().first()
    if existing is None:
        if maintenance_state(db)["enabled"]:
            # The callback is a GET, which maintenance mode lets through: new sign-ups are writes.
            return _reject(db, ERR_MAINTENANCE, ip=ip)
        return _create_user(db, sub=sub, email=email, name=name, ip=ip), True
    user = existing

    blocked = _blocked(db, user, ip)
    if blocked:
        return blocked
    other = db.query(OAuthAccount).filter_by(user_id=user.id, provider=OAuthProvider.google).first()
    if other is not None:
        # Already tied to a different Google account: never silently swap or double-link.
        return _reject(db, ERR_MISMATCH, ip=ip, user=user)
    if user.email_verified_at is None:
        _take_over_unverified(db, user)
    db.add(
        OAuthAccount(
            user_id=user.id, provider=OAuthProvider.google, provider_account_id=sub, email=email
        )
    )
    db.flush()
    write_audit(db, "auth.google.linked", actor_user_id=user.id, ip=ip)
    return user, False


def _blocked(db: Session, user: User, ip: str | None) -> str | None:
    if user.deleted_at is not None:
        return _reject(db, ERR_UNAVAILABLE, ip=ip, user=user)
    if user.status == UserStatus.disabled:
        return _reject(db, ERR_DISABLED, ip=ip, user=user)
    return None


def _take_over_unverified(db: Session, user: User) -> None:
    """Google has just proved this email is the caller's. The row was an unverified password
    sign-up: whoever made it may be someone else, so their password, sessions and pending links
    die, and the email counts as verified."""
    user.password_hash = None
    # Whoever squatted the row may also have switched on 2FA: it dies with their password.
    user.mfa_type = MfaType.none
    user.mfa_secret = None
    user.mfa_enabled_at = None
    db.query(MfaBackupCode).filter(MfaBackupCode.user_id == user.id).delete()
    user.email_verified_at = _now()
    if user.status == UserStatus.pending_verification:
        user.status = UserStatus.active
    revoke_all_for_user(db, user.id)
    for purpose in (AuthTokenPurpose.email_verification, AuthTokenPurpose.password_reset):
        invalidate_unconsumed_tokens(db, user, purpose)
    db.flush()
    event_bus.publish(db, "auth.user.verified", {"user_id": str(user.id)})


def _create_user(db: Session, *, sub: str, email: str, name: Any, ip: str | None) -> User:
    user = User(
        email=email,
        password_hash=None,
        status=UserStatus.active,
        email_verified_at=_now(),
    )
    full_name = str(name).strip()[:120] if name else ""
    user.profile = UserProfile(full_name=full_name or None)
    db.add(user)
    with db.begin_nested():
        db.flush()
        db.add(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.google,
                provider_account_id=sub,
                email=email,
            )
        )
        db.flush()
    write_audit(
        db,
        "auth.user.registered",
        actor_user_id=user.id,
        ip=ip,
        details={"method": "google"},
    )
    event_bus.publish(db, "auth.user.registered", {"user_id": str(user.id)})
    event_bus.publish(db, "auth.user.verified", {"user_id": str(user.id)})
    return user
