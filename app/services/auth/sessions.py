import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Unauthorized
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.auth import AuthSession
from app.db.models.enums import UserStatus
from app.db.models.user import User
from app.platform.state import lock_session_issue
from app.services.auth.csrf import is_cookie_mode


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
    lock_session_issue(db)
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
    lock_session_issue(db)  # before the claim below, so it can't deadlock with a global sign-out
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
        # Grace window: the immediately-previous token of a live family, presented within
        # REFRESH_GRACE_SECONDS of its rotation, is a lost response or a second tab, not theft.
        grace = _rotate_in_grace(db, session, now, ip=ip, user_agent=user_agent)
        if grace is not None:
            return grace
        # Already rotated/revoked/expired by the time we tried to claim it, or a
        # concurrent caller just won the race for this exact token -- either way this is
        # a reuse signal. Revoke every session in the family, including one a concurrent
        # winner may have just issued in the same family: a legitimate concurrent
        # double-submit logs the whole family out, same as actual token theft. We can't
        # tell the two apart, so we don't try.
        _revoke_family(db, session.family_id)
        # Commit BEFORE raising: get_db never commits and the error rolls the request back, which
        # would undo the revocation and leave a thief's rotated token alive.
        db.commit()
        raise Unauthorized()

    user = db.query(User).filter(User.id == session.user_id).first()
    # A refresh in flight while staff suspend the account must not mint a new session row that
    # the suspension's revoke-all never saw.
    if user is None or user.deleted_at is not None or user.status == UserStatus.disabled:
        raise Unauthorized()

    access, raw = issue_token_pair(
        db, user, ip=ip, user_agent=user_agent, family_id=session.family_id
    )
    return access, raw, user


def _rotate_in_grace(
    db: Session,
    session: AuthSession,
    now: datetime,
    *,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, str, User] | None:
    """Serve a re-presented, just-rotated refresh token with an equivalent new pair, or None when
    this is real reuse (the caller then revokes the family).

    We store only hashes, so the pair the first rotation returned can't be replayed: the family's
    live head is rotated and a fresh child issued instead. Two concurrent callers serialise on
    the head's row lock and both succeed, leaving a single live head. The window is anchored to
    the old token's own `rotated_at`, so a stolen token stops working REFRESH_GRACE_SECONDS after
    the owner's rotation; a token only ever re-enters the window by being rotated itself."""
    # `session` was read before the claim; a concurrent winner has committed `rotated_at` since.
    db.refresh(session)
    if (
        session.rotated_at is None
        or session.revoked_at is not None
        or session.expires_at < now
        or now - session.rotated_at > timedelta(seconds=settings.REFRESH_GRACE_SECONDS)
    ):
        return None
    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None or user.deleted_at is not None or user.status == UserStatus.disabled:
        raise Unauthorized()
    head = None
    # A row lock that waits on a concurrent rotation returns nothing once the row is rotated;
    # the next statement then sees the child that rotation committed.
    for _ in range(3):
        head = (
            db.query(AuthSession)
            .filter(
                AuthSession.family_id == session.family_id,
                AuthSession.rotated_at.is_(None),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at >= now,
            )
            .order_by(AuthSession.created_at.desc(), AuthSession.id)
            .with_for_update()
            .first()
        )
        if head is not None:
            break
    if head is None:
        return None
    head.rotated_at = now
    db.flush()
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


def revoke_session(db: Session, raw_refresh: str) -> bool:
    """Revoke the sign-in this token belongs to. True when the token was a real session's."""
    session = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_hash == hash_token(raw_refresh))
        .first()
    )
    if session is None:
        return False
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.flush()
    return True


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


def family_of(db: Session, user_id: uuid.UUID, raw_refresh: str | None) -> uuid.UUID | None:
    """The sign-in (refresh family) a raw refresh token belongs to, only if it is `user_id`'s."""
    if not raw_refresh:
        return None
    row = (
        db.query(AuthSession)
        .filter(
            AuthSession.refresh_token_hash == hash_token(raw_refresh),
            AuthSession.user_id == user_id,
        )
        .first()
    )
    return row.family_id if row is not None else None


def list_active(db: Session, user_id: uuid.UUID) -> list[AuthSession]:
    """One row per live sign-in: the unrotated, unrevoked, unexpired head of each refresh family
    (a rotation leaves the previous rows behind, so the family id is the stable session id)."""
    now = datetime.now(UTC)
    return (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.rotated_at.is_(None),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at >= now,
        )
        .order_by(AuthSession.created_at.desc(), AuthSession.id)
        .all()
    )


def revoke_family_for_user(db: Session, user_id: uuid.UUID, family_id: uuid.UUID) -> int:
    n = (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.family_id == family_id,
            AuthSession.revoked_at.is_(None),
        )
        .update({AuthSession.revoked_at: datetime.now(UTC)}, synchronize_session=False)
    )
    db.flush()
    return int(n)


def revoke_all_except(db: Session, user_id: uuid.UUID, keep_family: uuid.UUID | None) -> int:
    q = db.query(AuthSession).filter(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if keep_family is not None:
        q = q.filter(AuthSession.family_id != keep_family)
    n = q.update({AuthSession.revoked_at: datetime.now(UTC)}, synchronize_session=False)
    db.flush()
    return int(n)


def token_body(request: Request, access: str, refresh: str, **extra: object) -> dict[str, object]:
    """Response body for an endpoint that issues a session.

    A client that sends `X-Auth-Mode: cookie` keeps the refresh token only in the HttpOnly
    cookie, so its body carries `refresh_token: null` (nothing for an XSS to read). Every other
    client (the current FE, scripts) still gets the token in the body as before.
    """
    return {
        "access_token": access,
        "refresh_token": None if is_cookie_mode(request) else refresh,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **extra,
    }
