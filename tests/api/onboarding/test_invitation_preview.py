from app.db.models.enums import MembershipRole
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
