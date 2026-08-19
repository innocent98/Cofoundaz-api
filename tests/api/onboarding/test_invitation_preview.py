from datetime import UTC, datetime, timedelta

from app.db.models.enums import InvitationStatus, MembershipRole
from app.services.auth.sessions import hash_token
from tests.factories import create_invitation, create_startup, create_user


def test_preview_returns_invite_details(client, db):
    owner = create_user(db, email="founder@x.com")
    owner.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=owner, name="Cofoundaz")
    create_invitation(
        db,
        s,
        email="invitee@x.com",
        role=MembershipRole.mentor,
        inviter=owner,
        token_hash=hash_token("rawtok"),
    )
    db.commit()
    r = client.get("/api/v1/invitations/rawtok")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["startup_name"] == "Cofoundaz" and d["role"] == "mentor"
    assert d["email"] == "invitee@x.com" and d["inviter_name"] == "Ada Founder"


def test_preview_unknown_token_404(client):
    assert client.get("/api/v1/invitations/nope").status_code == 404


def test_preview_expired_token_404(client, db):
    owner = create_user(db, email="founder2@x.com")
    s = create_startup(db, owner=owner, name="Cofoundaz")
    create_invitation(
        db,
        s,
        email="invitee2@x.com",
        inviter=owner,
        token_hash=hash_token("expiredtok"),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.commit()
    r = client.get("/api/v1/invitations/expiredtok")
    assert r.status_code == 404, r.text


def test_preview_accepted_token_404(client, db):
    owner = create_user(db, email="founder3@x.com")
    s = create_startup(db, owner=owner, name="Cofoundaz")
    create_invitation(
        db,
        s,
        email="invitee3@x.com",
        inviter=owner,
        token_hash=hash_token("acceptedtok"),
        status=InvitationStatus.accepted,
    )
    db.commit()
    r = client.get("/api/v1/invitations/acceptedtok")
    assert r.status_code == 404, r.text
