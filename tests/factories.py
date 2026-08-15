import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models.auth import AuthSession
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup, StartupProfile
from app.db.models.user import User, UserProfile


def create_user(db: Session, *, email: str | None = None, **kw) -> User:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(email=email, **kw)
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    return user


def create_startup(db: Session, *, owner: User, name: str = "Acme", **kw) -> Startup:
    s = Startup(name=name, created_by=owner.id, **kw)
    s.profile = StartupProfile()
    db.add(s)
    db.flush()
    return s


def create_membership(
    db: Session, user: User, startup: Startup, role: MembershipRole = MembershipRole.founder
) -> Membership:
    m = Membership(
        user_id=user.id, startup_id=startup.id, role=role, status=MembershipStatus.active
    )
    db.add(m)
    return m


def create_auth_session(
    db: Session,
    user: User,
    *,
    refresh_token_hash: str = "hash",
    family_id: uuid.UUID | None = None,
    **kw,
) -> AuthSession:
    s = AuthSession(
        user_id=user.id,
        refresh_token_hash=refresh_token_hash,
        family_id=family_id or uuid.uuid4(),
        expires_at=kw.pop("expires_at", datetime.now(UTC) + timedelta(days=30)),
        **kw,
    )
    db.add(s)
    db.flush()
    return s
