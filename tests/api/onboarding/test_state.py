from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db, **kw):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC), **kw)
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def test_get_state_creates_draft(client, db):
    u, h = _auth(db)
    db.commit()
    r = client.get("/api/v1/onboarding/state", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["step"] == 1 and data["completed"] is False
    assert data["startup"]["id"] and data["startup"]["name"] is None


def test_get_state_requires_verified(client, db):
    u = create_user(db, status=UserStatus.pending_verification)
    db.commit()
    r = client.get(
        "/api/v1/onboarding/state",
        headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
