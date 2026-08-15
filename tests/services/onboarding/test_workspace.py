from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership, MembershipStatus
from app.services.onboarding.workspace import resolve_or_create_workspace, serialize_state
from tests.factories import create_user


def test_creates_draft_workspace_and_founder_membership(db):
    u = create_user(db)
    db.flush()
    s = resolve_or_create_workspace(db, u)
    assert s.created_by == u.id and s.name is None
    m = db.query(Membership).filter(Membership.user_id == u.id, Membership.startup_id == s.id).one()
    assert m.role == MembershipRole.founder and m.status == MembershipStatus.active


def test_resolve_is_idempotent(db):
    u = create_user(db)
    db.flush()
    s1 = resolve_or_create_workspace(db, u)
    s2 = resolve_or_create_workspace(db, u)
    assert s1.id == s2.id  # does not create a second workspace


def test_serialize_state_shape(db):
    u = create_user(db)
    db.flush()
    s = resolve_or_create_workspace(db, u)
    state = serialize_state(db, s, u)
    for key in (
        "step",
        "completed",
        "assessment_pending",
        "founder_profile",
        "startup",
        "goals",
        "notes",
        "invites",
    ):
        assert key in state
