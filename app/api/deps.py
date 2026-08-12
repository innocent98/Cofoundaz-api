import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.user import User
from app.db.session import get_db

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
        if not user_id:
            raise Unauthorized()
        subject = uuid.UUID(user_id)
    except (JWTError, ValueError) as exc:
        raise Unauthorized() from exc
    # Exclude soft-deleted users so a still-valid token for a deleted account can't
    # authenticate. Status-based enforcement (disabled/locked users) is a Plan 2
    # follow-up — not implemented here.
    user = db.query(User).filter(User.id == subject, User.deleted_at.is_(None)).first()
    if user is None:
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
