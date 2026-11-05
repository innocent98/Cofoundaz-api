"""POST /invitations/decline: the invitee turns an invitation down (clears the pending state)."""

from app.core.config import settings  # noqa: F401
from app.db.models.enums import InvitationStatus, MembershipRole
from app.db.models.invitation import Invitation
from app.services.auth.sessions import hash_token
from tests.auth_helpers import active_user, bearer
from tests.factories import create_invitation, create_startup

DECLINE = "/api/v1/invitations/decline"
STATE = "/api/v1/onboarding/state"


def _invite(db, email, token="tok-decline"):
    owner = active_user(db, f"owner-{token}@x.com")
    s = create_startup(db, owner=owner, name="NovaPay")
    inv = create_invitation(
        db, s, email=email, role=MembershipRole.mentor, token_hash=hash_token(token)
    )
    return s, inv


def test_the_invitee_can_decline_and_then_the_state_is_a_normal_draft(client, db):
    _s, inv = _invite(db, "d1@x.com")
    user = active_user(db, "d1@x.com")
    db.commit()
    before = client.get(STATE, headers=bearer(user))
    assert before.json()["data"]["pending_invite"] is True
    r = client.post(DECLINE, headers=bearer(user), json={"token": "tok-decline"})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"declined": True}
    db.refresh(inv)
    assert inv.status == InvitationStatus.declined
    after = client.get(STATE, headers=bearer(user)).json()["data"]
    assert after["pending_invite"] is False and after["startup"] is not None


def test_declining_twice_is_token_invalid(client, db):
    _invite(db, "d2@x.com", "tok-d2")
    user = active_user(db, "d2@x.com")
    db.commit()
    client.post(DECLINE, headers=bearer(user), json={"token": "tok-d2"})
    again = client.post(DECLINE, headers=bearer(user), json={"token": "tok-d2"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "TOKEN_INVALID"


def test_someone_else_cannot_decline_it(client, db):
    _s, inv = _invite(db, "d3@x.com", "tok-d3")
    stranger = active_user(db, "stranger@x.com")
    db.commit()
    r = client.post(DECLINE, headers=bearer(stranger), json={"token": "tok-d3"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"
    db.refresh(inv)
    assert inv.status == InvitationStatus.pending


def test_an_accepted_or_unknown_token_cannot_be_declined(client, db):
    _s, inv = _invite(db, "d4@x.com", "tok-d4")
    inv.status = InvitationStatus.accepted
    user = active_user(db, "d4@x.com")
    db.commit()
    for token in ("tok-d4", "no-such-token"):
        r = client.post(DECLINE, headers=bearer(user), json={"token": token})
        assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_INVALID"
    assert db.query(Invitation).filter_by(status=InvitationStatus.declined).count() == 0


def test_decline_needs_a_signed_in_verified_user(client, db):
    _invite(db, "d5@x.com", "tok-d5")
    db.commit()
    r = client.post(DECLINE, json={"token": "tok-d5"})
    assert r.status_code == 401
