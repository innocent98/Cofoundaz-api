from datetime import UTC, datetime

from app.core.security import get_password_hash
from app.db.models.enums import UserStatus
from tests.factories import create_user


def _login(client, db, email="s@x.com"):
    create_user(
        db,
        email=email,
        password_hash=get_password_hash("password1"),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    db.flush()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "password1"})
    return r.json()["data"]["refresh_token"]


def test_refresh_rotates(client, db):
    refresh = _login(client, db)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"] != refresh


def test_refresh_reuse_is_401(client, db):
    refresh = _login(client, db, email="s2@x.com")
    client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # rotate once
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})  # reuse
    assert r.status_code == 401


def test_logout_clears(client, db):
    refresh = _login(client, db, email="s3@x.com")
    r = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    # subsequent refresh with the revoked token fails
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401
