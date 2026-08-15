from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import InvitationStatus, UserStatus
from app.db.models.invitation import Invitation
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def test_invites_create_rows_and_email(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post(
        "/api/v1/onboarding/invites",
        headers=h,
        json={"invites": [{"email": "teammate@x.com", "role": "team_member"}]},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["created"]) == 1
    rows = db.query(Invitation).filter(Invitation.email == "teammate@x.com").all()
    assert len(rows) == 1 and rows[0].status == InvitationStatus.pending


def test_invites_dedupe_pending(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    body = {"invites": [{"email": "dup@x.com", "role": "mentor"}]}
    client.post("/api/v1/onboarding/invites", headers=h, json=body)
    r = client.post("/api/v1/onboarding/invites", headers=h, json=body)  # again
    assert r.status_code == 200
    assert r.json()["data"]["skipped"] == ["dup@x.com"]
    assert db.query(Invitation).filter(Invitation.email == "dup@x.com").count() == 1
