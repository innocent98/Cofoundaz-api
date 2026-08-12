import uuid

from sqlalchemy.orm import Session

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
