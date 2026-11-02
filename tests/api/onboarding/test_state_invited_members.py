"""Invited members (mentor, consultant, team member) are not founders of a new startup: reading
the onboarding state must not create a draft workspace for them or route them into onboarding.
Found in the 2026-10-19 re-walk: every invited role was pushed to /onboarding on login."""

from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, UserStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from tests.factories import create_membership, create_startup, create_user


def _verified(db, email):
    return create_user(
        db, email=email, status=UserStatus.active, email_verified_at=datetime.now(UTC)
    )


def _onboarded_workspace(db):
    founder = _verified(db, "founder@example.com")
    s = create_startup(db, owner=founder, name="NovaPay")
    s.profile.onboarding_completed_at = datetime.now(UTC)
    s.profile.onboarding_step = 7
    create_membership(db, founder, s, role=MembershipRole.founder)
    return s


def _h(user, workspace=None):
    h = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    if workspace is not None:
        h["X-Workspace-Id"] = str(workspace.id)
    return h


def test_invited_mentor_gets_the_joined_workspace_not_a_new_draft(client, db):
    s = _onboarded_workspace(db)
    mentor = _verified(db, "mentor@example.com")
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()

    r = client.get("/api/v1/onboarding/state", headers=_h(mentor))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["completed"] is True
    assert data["startup"]["id"] == str(s.id)
    assert data["invites"] == []  # the founder's invite list is not a member's business
    assert db.query(Startup).filter_by(created_by=mentor.id).count() == 0


def test_header_picks_the_joined_workspace_over_a_stray_draft(client, db):
    s = _onboarded_workspace(db)
    mentor = _verified(db, "mentor@example.com")
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    draft = create_startup(db, owner=mentor, name=None)
    create_membership(db, mentor, draft, role=MembershipRole.founder)
    db.commit()

    r = client.get("/api/v1/onboarding/state", headers=_h(mentor, s))
    assert r.json()["data"]["completed"] is True
    assert r.json()["data"]["startup"]["id"] == str(s.id)


def test_founder_without_a_workspace_still_gets_a_draft(client, db):
    u = _verified(db, "new@example.com")
    db.commit()
    r = client.get("/api/v1/onboarding/state", headers=_h(u))
    assert r.json()["data"]["completed"] is False
    assert db.query(Startup).filter_by(created_by=u.id).count() == 1


def test_header_for_a_workspace_the_user_is_not_in_is_ignored(client, db):
    s = _onboarded_workspace(db)
    stranger = _verified(db, "stranger@example.com")
    db.commit()
    r = client.get("/api/v1/onboarding/state", headers=_h(stranger, s))
    assert r.status_code == 200
    assert r.json()["data"]["startup"]["id"] != str(s.id)
    assert r.json()["data"]["completed"] is False


def test_me_hides_an_untouched_draft_when_the_user_has_another_workspace(client, db):
    s = _onboarded_workspace(db)
    mentor = _verified(db, "mentor@example.com")
    draft = create_startup(db, owner=mentor, name=None)
    create_membership(db, mentor, draft, role=MembershipRole.founder)
    db.flush()
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()

    data = client.get("/api/v1/auth/me", headers=_h(mentor)).json()["data"]
    assert [m["startup_id"] for m in data["memberships"]] == [str(s.id)]
    assert data["active_workspace_id"] == str(s.id)


def test_me_keeps_a_lone_draft_and_a_named_one(client, db):
    u = _verified(db, "solo@example.com")
    draft = create_startup(db, owner=u, name=None)
    create_membership(db, u, draft, role=MembershipRole.founder)
    db.commit()
    data = client.get("/api/v1/auth/me", headers=_h(u)).json()["data"]
    assert data["active_workspace_id"] == str(draft.id)
    assert db.query(Membership).filter_by(user_id=u.id).count() == 1
