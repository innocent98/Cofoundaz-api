from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import InvitationStatus, MembershipRole
from app.db.models.invitation import Invitation
from tests.factories import create_startup, create_user


def test_invitation_persists(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner)
    inv = Invitation(
        startup_id=s.id,
        email="new@x.com",
        role=MembershipRole.team_member,
        token_hash="h1",
        invited_by=owner.id,
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(inv)
    db.flush()
    assert inv.status == InvitationStatus.pending
    assert inv.accepted_at is None


def test_token_hash_unique(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner)
    db.add(
        Invitation(
            startup_id=s.id,
            email="a@x.com",
            role=MembershipRole.mentor,
            token_hash="dup",
            invited_by=owner.id,
            expires_at=datetime.now(UTC) + timedelta(days=14),
        )
    )
    db.flush()
    db.add(
        Invitation(
            startup_id=s.id,
            email="b@x.com",
            role=MembershipRole.mentor,
            token_hash="dup",
            invited_by=owner.id,
            expires_at=datetime.now(UTC) + timedelta(days=14),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_startup_name_nullable_and_assessment_pending_default(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner, name=None)  # draft workspace, no name yet
    db.flush()
    assert s.name is None
    assert s.profile.assessment_pending is True
