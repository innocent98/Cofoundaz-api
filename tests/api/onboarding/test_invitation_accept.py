from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus, UserStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.services.auth.sessions import hash_token
from tests.factories import create_invitation, create_membership, create_startup, create_user


def _verified(db, email):
    return create_user(
        db, email=email, status=UserStatus.active, email_verified_at=datetime.now(UTC)
    )


def test_accept_creates_membership(client, db):
    owner = create_user(db, email="f@x.com")
    s = create_startup(db, owner=owner, name="Cofoundaz")
    invitee = _verified(db, "invitee@x.com")
    create_invitation(
        db,
        s,
        email="invitee@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("tok1"),
    )
    db.commit()
    r = client.post(
        "/api/v1/invitations/accept",
        json={"token": "tok1"},
        headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"},
    )
    assert r.status_code == 200, r.text
    m = (
        db.query(Membership)
        .filter(Membership.user_id == invitee.id, Membership.startup_id == s.id)
        .one()
    )
    assert m.role == MembershipRole.mentor and m.status == MembershipStatus.active
    assert (
        db.query(Invitation).filter(Invitation.token_hash == hash_token("tok1")).one().status
        == InvitationStatus.accepted
    )


def test_accept_wrong_email_403(client, db):
    owner = create_user(db, email="f2@x.com")
    s = create_startup(db, owner=owner, name="X")
    other = _verified(db, "someoneelse@x.com")
    create_invitation(
        db,
        s,
        email="intended@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("tok2"),
    )
    db.commit()
    r = client.post(
        "/api/v1/invitations/accept",
        json={"token": "tok2"},
        headers={"Authorization": f"Bearer {create_access_token(str(other.id))}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"


def test_accept_expired_400(client, db):
    owner = create_user(db, email="f3@x.com")
    s = create_startup(db, owner=owner, name="X")
    invitee = _verified(db, "late@x.com")
    create_invitation(
        db,
        s,
        email="late@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("tok3"),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.commit()
    r = client.post(
        "/api/v1/invitations/accept",
        json={"token": "tok3"},
        headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"},
    )
    assert r.status_code == 400


def test_accept_already_member_is_idempotent(client, db):
    owner = create_user(db, email="f4@x.com")
    s = create_startup(db, owner=owner, name="X")
    invitee = _verified(db, "already@x.com")
    create_membership(db, invitee, s, role=MembershipRole.mentor)
    create_invitation(
        db,
        s,
        email="already@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("tok4"),
    )
    db.commit()
    r = client.post(
        "/api/v1/invitations/accept",
        json={"token": "tok4"},
        headers={"Authorization": f"Bearer {create_access_token(str(invitee.id))}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["startup_id"] == str(s.id)
    assert body["role"] == MembershipRole.mentor.value

    count = (
        db.query(Membership)
        .filter(Membership.user_id == invitee.id, Membership.startup_id == s.id)
        .count()
    )
    assert count == 1

    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token("tok4")).one()
    assert inv.status == InvitationStatus.accepted
    assert inv.accepted_at is not None
    assert inv.accepted_user_id == invitee.id
