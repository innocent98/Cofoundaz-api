import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.auth import AuthSession
from app.db.models.user import User


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_refresh() -> str:
    return secrets.token_urlsafe(48)


def issue_token_pair(
    db: Session,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    family_id: uuid.UUID | None = None,
) -> tuple[str, str]:
    raw = _new_refresh()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        ip=ip,
        user_agent=user_agent,
        expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)
    db.flush()
    access = create_access_token(str(user.id))
    return access, raw


def rotate_refresh(
    db: Session,
    raw_refresh: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str, User]:
    session = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_hash == hash_token(raw_refresh))
        .first()
    )
    if session is None:
        raise Unauthorized()

    now = datetime.now(UTC)
    # Atomic claim: consume this exact row only if it's still live. A plain
    # read-then-write here is a TOCTOU race -- two concurrent callers can both read
    # `rotated_at IS NULL` before either writes, and both would pass. Conditioning the
    # UPDATE itself on the same predicate makes only one concurrent claimant's UPDATE
    # matched (Postgres row-locks the row for the transaction; the loser's UPDATE
    # blocks until the winner commits/rolls back, then re-evaluates against the
    # now-committed row and matches zero rows).
    claimed = (
        db.query(AuthSession)
        .filter(
            AuthSession.id == session.id,
            AuthSession.rotated_at.is_(None),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at >= now,
        )
        .update({AuthSession.rotated_at: now}, synchronize_session=False)
    )
    if claimed == 0:
        # Already rotated/revoked/expired by the time we tried to claim it, or a
        # concurrent caller just won the race for this exact token -- either way this is
        # a reuse signal. Revoke every session in the family, including one a concurrent
        # winner may have just issued in the same family: a legitimate concurrent
        # double-submit logs the whole family out, same as actual token theft. We can't
        # tell the two apart, so we don't try.
        _revoke_family(db, session.family_id)
        raise Unauthorized()

    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None:
        raise Unauthorized()

    access, raw = issue_token_pair(
        db, user, ip=ip, user_agent=user_agent, family_id=session.family_id
    )
    return access, raw, user


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    (
        db.query(AuthSession)
        .filter(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .update({AuthSession.revoked_at: now})
    )
    db.flush()


def revoke_session(db: Session, raw_refresh: str) -> None:
    session = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_hash == hash_token(raw_refresh))
        .first()
    )
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.flush()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> int:
    now = datetime.now(UTC)
    n = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .update({AuthSession.revoked_at: now})
    )
    db.flush()
    return int(n)


def set_refresh_cookie(response: Response, raw_refresh: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=raw_refresh,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=cast(Literal["lax", "strict", "none"], settings.REFRESH_COOKIE_SAMESITE),
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/api/v1/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.REFRESH_COOKIE_NAME, path="/api/v1/auth")
