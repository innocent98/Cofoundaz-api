import io
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def _complete(client, h):
    client.get("/api/v1/onboarding/state", headers=h)
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada"})
    client.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
    client.patch(
        "/api/v1/onboarding/state",
        headers=h,
        json={"step": 3, "industry": "Fintech", "stage": "idea"},
    )
    client.patch(
        "/api/v1/onboarding/state", headers=h, json={"step": 4, "goals": ["Get first customers"]}
    )
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 200, r.text


def test_logo_upload_sets_url(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post(
        "/api/v1/onboarding/logo",
        headers=h,
        files={"file": ("logo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["logo_url"]


def test_logo_rejects_non_image(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post(
        "/api/v1/onboarding/logo",
        headers=h,
        files={"file": ("x.txt", io.BytesIO(b"nope"), "text/plain")},
    )
    assert r.status_code == 422


def test_logo_rejects_oversized_file(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    oversized = b"\x89PNG\r\n" + b"0" * (2 * 1024 * 1024 + 10)
    r = client.post(
        "/api/v1/onboarding/logo",
        headers=h,
        files={"file": ("logo.png", io.BytesIO(oversized), "image/png")},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_logo_upload_after_completion_is_409(client, db):
    u, h = _auth(db)
    db.commit()
    _complete(client, h)
    r = client.post(
        "/api/v1/onboarding/logo",
        headers=h,
        files={"file": ("logo.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "ONBOARDING_ALREADY_COMPLETE"
