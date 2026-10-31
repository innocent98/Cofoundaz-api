import uuid

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, EmailNotVerified
from app.db.models.enums import UserStatus
from app.db.models.user import User
from app.db.session import get_db
from app.platform.state import session_cutoff_ts

security = HTTPBearer(auto_error=False)


class Unauthorized(AppError):  # noqa: N818
    code, http_status, message = "UNAUTHORIZED", 401, "Not authenticated."


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> User:
    if credentials is None:
        raise Unauthorized()
    try:
        payload = jwt.decode(
            credentials.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id = payload.get("sub")
        # Access tokens carry no `typ`. Other short-lived tokens signed with the same key (the
        # calendar OAuth state, which travels in a URL) also carry `sub`: never accept them here.
        if not user_id or "typ" in payload:
            raise Unauthorized()
        subject = uuid.UUID(user_id)
        issued_at = float(payload["iat"]) if "iat" in payload else None
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise Unauthorized() from exc
    # Global sign-out: refuse any token issued at or before the cutoff. A token with no `iat`
    # predates this check and so predates any cutoff.
    cutoff = session_cutoff_ts(db)
    if cutoff is not None and (issued_at is None or issued_at <= cutoff):
        raise Unauthorized()
    # Exclude soft-deleted users so a still-valid token for a deleted account can't
    # authenticate.
    user = db.query(User).filter(User.id == subject, User.deleted_at.is_(None)).first()
    if user is None:
        raise Unauthorized()
    if user.status == UserStatus.disabled:
        raise Unauthorized()
    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> User | None:
    if credentials is None:
        return None
    try:
        return get_current_user(credentials, db)
    except AppError:
        return None


def get_verified_user(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    if user.email_verified_at is None:
        raise EmailNotVerified()
    return user
