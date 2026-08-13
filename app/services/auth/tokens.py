import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import TokenInvalid
from app.db.models.auth import AuthToken
from app.db.models.enums import AuthTokenPurpose
from app.db.models.user import User
from app.services.auth.sessions import hash_token


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
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == hash_token(raw), AuthToken.purpose == purpose)
        .first()
    )
    now = datetime.now(UTC)
    if row is None or row.consumed_at is not None or row.expires_at < now:
        raise TokenInvalid()
    row.consumed_at = now
    db.flush()
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise TokenInvalid()
    return user
