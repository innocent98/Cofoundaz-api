from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, UserStatus
from tests.factories import create_membership, create_startup, create_user


def test_me_returns_identity_and_memberships(client, db):
    u = create_user(db, email="me@x.com", status=UserStatus.active)
    u.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=u, name="Cofoundaz")
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    r = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["email"] == "me@x.com"
    assert data["profile"]["full_name"] == "Ada Founder"
    assert data["memberships"][0]["role"] == "founder"
    assert data["active_workspace_id"] == str(s.id)


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401
