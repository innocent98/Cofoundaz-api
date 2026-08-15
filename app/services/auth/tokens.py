import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import TokenInvalid
from app.db.models.auth import AuthToken
from app.db.models.enums import AuthTokenPurpose
from app.db.models.user import User
from app.services.auth.sessions import hash_token


def invalidate_unconsumed_tokens(db: Session, user: User, purpose: AuthTokenPurpose) -> None:
    """Mark all of a user's not-yet-consumed tokens for `purpose` as consumed.

    Callers issuing a fresh token (e.g. signup / resend-verification) should call
    this first so that only the newest token for that purpose remains valid —
    otherwise every previously-issued, still-unexpired token stays usable
    indefinitely alongside the new one.
    """
    db.query(AuthToken).filter(
        AuthToken.user_id == user.id,
        AuthToken.purpose == purpose,
        AuthToken.consumed_at.is_(None),
    ).update({"consumed_at": datetime.now(UTC)}, synchronize_session=False)
    db.flush()


def issue_auth_token(db: Session, user: User, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    db.flush()
    return raw


def consume_auth_token(db: Session, purpose: AuthTokenPurpose, raw: str) -> User:
    now = datetime.now(UTC)
    # Atomic claim, same pattern as `sessions.rotate_refresh`: a read-then-write here is a
    # TOCTOU race -- two concurrent callers (e.g. a doubly-submitted verify/reset request)
    # can both read `consumed_at IS NULL` before either writes, and both would pass.
    # Conditioning the UPDATE itself on the same predicate makes only one concurrent
    # claimant's UPDATE match a row.
    claimed = (
        db.query(AuthToken)
        .filter(
            AuthToken.token_hash == hash_token(raw),
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.expires_at >= now,
        )
        .update({AuthToken.consumed_at: now}, synchronize_session=False)
    )
    if claimed == 0:
        raise TokenInvalid()
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == hash_token(raw), AuthToken.purpose == purpose)
        .first()
    )
    user = db.query(User).filter(User.id == row.user_id).first() if row else None
    if user is None:
        raise TokenInvalid()
    return user
