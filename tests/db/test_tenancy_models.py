import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import MembershipRole, UserStatus
from tests.factories import create_membership, create_startup, create_user


def test_user_defaults(db):
    u = create_user(db, email="a@b.com")
    assert u.status == UserStatus.pending_verification
    assert u.failed_login_count == 0


def test_membership_unique_per_workspace(db):
    u = create_user(db, email="c@d.com")
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    create_membership(db, u, s, role=MembershipRole.team_member)
    with pytest.raises(IntegrityError):
        db.flush()


def test_startup_profile_goals_array(db):
    u = create_user(db, email="e@f.com")
    s = create_startup(db, owner=u)
    s.profile.goals = ["Validate my idea", "Get first customers"]
    db.flush()
    db.refresh(s.profile)
    assert s.profile.goals == ["Validate my idea", "Get first customers"]
