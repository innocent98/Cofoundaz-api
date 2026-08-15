from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def test_patch_step1_saves_founder_profile(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)  # create draft
    r = client.patch(
        "/api/v1/onboarding/state",
        headers=h,
        json={"step": 1, "full_name": "Ada Founder", "country": "NG"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["founder_profile"]["full_name"] == "Ada Founder"
    assert r.json()["data"]["step"] >= 1


def test_patch_step2_3_saves_startup(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
    r = client.patch(
        "/api/v1/onboarding/state",
        headers=h,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    d = r.json()["data"]["startup"]
    assert d["name"] == "Cofoundaz" and d["business_model"] == "b2b" and d["stage"] == "idea"


def test_patch_goals_max_3(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.patch(
        "/api/v1/onboarding/state",
        headers=h,
        json={"step": 4, "goals": ["a", "b", "c", "d"]},
    )
    assert r.status_code == 422  # more than 3 goals rejected
