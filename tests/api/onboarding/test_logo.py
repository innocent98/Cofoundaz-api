import io
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


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
