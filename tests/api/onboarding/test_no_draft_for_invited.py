"""Gap 11: the wizard is for founders. Reading or writing onboarding state must not create a
founder draft startup for someone who joined a workspace or holds a pending invitation, and the
"Founder & CEO" title is only for founders."""

from datetime import UTC, datetime, timedelta

from app.db.models.enums import InvitationStatus, MembershipRole
from app.db.models.startup import Startup
from tests.auth_helpers import active_user, bearer
from tests.factories import create_invitation, create_membership, create_startup

STATE = "/api/v1/onboarding/state"


def _workspace(db, *, onboarded):
    founder = active_user(db, "owner@x.com")
    s = create_startup(db, owner=founder, name="NovaPay")
    create_membership(db, founder, s, role=MembershipRole.founder)
    if onboarded:
        s.profile.onboarding_completed_at = datetime.now(UTC)
    return founder, s


def test_a_member_of_a_workspace_still_in_draft_gets_no_draft_of_their_own(client, db):
    _owner, s = _workspace(db, onboarded=False)  # the founder has not finished onboarding
    mentor = active_user(db, "mentor@x.com")
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()
    r = client.get(STATE, headers=bearer(mentor))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["completed"] is True and data["pending_invite"] is False
    assert data["startup"]["id"] == str(s.id)
    assert db.query(Startup).filter_by(created_by=mentor.id).count() == 0


def test_a_pending_invitee_gets_a_not_a_founder_state_and_no_draft(client, db):
    _owner, s = _workspace(db, onboarded=True)
    invitee = active_user(db, "invitee@x.com")
    create_invitation(db, s, email="invitee@x.com", role=MembershipRole.mentor)
    db.commit()
    r = client.get(STATE, headers=bearer(invitee))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["pending_invite"] is True and data["completed"] is False
    assert data["startup"] is None and data["founder_profile"] is None
    assert db.query(Startup).filter_by(created_by=invitee.id).count() == 0


def test_writing_state_as_a_pending_invitee_is_refused_without_creating_anything(client, db):
    _owner, s = _workspace(db, onboarded=True)
    invitee = active_user(db, "invitee2@x.com")
    create_invitation(db, s, email="invitee2@x.com", role=MembershipRole.mentor)
    db.commit()
    for call in (
        lambda: client.patch(STATE, headers=bearer(invitee), json={"step": 1, "full_name": "Ib"}),
        lambda: client.post("/api/v1/onboarding/complete", headers=bearer(invitee)),
        lambda: client.post(
            "/api/v1/onboarding/invites",
            headers=bearer(invitee),
            json={"invites": [{"email": "x@x.com", "role": "mentor"}]},
        ),
    ):
        r = call()
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "ONBOARDING_NOT_FOR_INVITED"
    assert db.query(Startup).filter_by(created_by=invitee.id).count() == 0


def test_an_expired_or_used_invitation_does_not_block_a_real_founder(client, db):
    _owner, s = _workspace(db, onboarded=True)
    person = active_user(db, "founder-to-be@x.com")
    create_invitation(
        db,
        s,
        email="founder-to-be@x.com",
        token_hash="t1",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    create_invitation(
        db, s, email="founder-to-be@x.com", token_hash="t2", status=InvitationStatus.revoked
    )
    db.commit()
    r = client.get(STATE, headers=bearer(person))
    assert r.status_code == 200
    assert r.json()["data"]["pending_invite"] is False
    assert db.query(Startup).filter_by(created_by=person.id).count() == 1


def test_a_founder_with_a_draft_who_is_also_invited_elsewhere_keeps_their_draft(client, db):
    _owner, s = _workspace(db, onboarded=True)
    person = active_user(db, "both@x.com")
    create_startup(db, owner=person, name="Mine")
    create_invitation(db, s, email="both@x.com", role=MembershipRole.mentor)
    db.commit()
    r = client.get(STATE, headers=bearer(person))
    assert r.status_code == 200
    assert r.json()["data"]["startup"]["name"] == "Mine"
    assert r.json()["data"]["pending_invite"] is False


def test_only_a_founder_gets_the_founder_and_ceo_title(client, db):
    # a fresh account has no title...
    founder = active_user(db, "ada@x.com")
    assert founder.profile.role_title == ""
    db.commit()
    # ...and is given the default title when their startup draft is created
    r = client.get(STATE, headers=bearer(founder))
    assert r.json()["data"]["founder_profile"]["role_title"] == "Founder & CEO"

    _owner, s = _workspace(db, onboarded=True)
    mentor = active_user(db, "mentor2@x.com")
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()
    client.get(STATE, headers=bearer(mentor))
    db.refresh(mentor.profile)
    assert mentor.profile.role_title == ""  # never labelled a CEO
