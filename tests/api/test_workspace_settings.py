"""Settings → Workspace: GET/PATCH /workspace and the logo upload."""

from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.audit import AuditLog
from app.db.models.enums import MembershipRole, UserStatus
from tests.factories import create_membership, create_startup, create_user

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _setup(db, role=MembershipRole.founder, email="ws@x.com"):
    owner = create_user(
        db, email="owner-" + email, status=UserStatus.active, email_verified_at=datetime.now(UTC)
    )
    s = create_startup(db, owner=owner, name="Acme")
    u = (
        owner
        if role == MembershipRole.founder
        else create_user(
            db, email=email, status=UserStatus.active, email_verified_at=datetime.now(UTC)
        )
    )
    create_membership(db, owner, s, role=MembershipRole.founder)
    if u is not owner:
        create_membership(db, u, s, role=role)
    db.flush()
    return s, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def test_get_workspace_for_founder_and_member(client, db):
    s, h = _setup(db)
    d = client.get("/api/v1/workspace", headers=h).json()["data"]
    assert d["name"] == "Acme" and d["can_edit"] is True and d["id"] == str(s.id)
    _, mh = _setup(db, MembershipRole.team_member, "tm@x.com")
    assert client.get("/api/v1/workspace", headers=mh).json()["data"]["can_edit"] is False


def test_patch_workspace_updates_and_audits(client, db):
    _, h = _setup(db)
    r = client.patch(
        "/api/v1/workspace",
        json={
            "name": "  Nova  Pay ",
            "description": "Savings",
            "website": "https://novapay.example",
            "industry": "Fintech",
            "business_model": "b2c",
            "stage": "build",
            "country": "ng",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["name"] == "Nova Pay" and d["stage"] == "build" and d["country"] == "NG"
    assert d["business_model"] == "b2c" and d["website"] == "https://novapay.example"
    assert db.query(AuditLog).filter_by(action="workspace.updated").count() == 1
    cleared = client.patch("/api/v1/workspace", json={"website": "", "industry": None}, headers=h)
    assert cleared.json()["data"]["website"] is None and cleared.json()["data"]["industry"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"owner": "x"},
        {"name": None},
        {"name": " "},
        {"name": "n" * 121},
        {"name": "a\x00"},
        {"description": "d" * 1001},
        {"website": "ftp://x"},
        {"website": "not a url"},
        {"industry": "i" * 81},
        {"country": "NGA"},
        {"stage": "huge"},
    ],
)
def test_patch_workspace_validation(client, db, body):
    _, h = _setup(db)
    assert client.patch("/api/v1/workspace", json=body, headers=h).status_code == 422


def test_non_founders_cannot_edit(client, db):
    _, h = _setup(db, MembershipRole.team_member, "tm2@x.com")
    assert client.patch("/api/v1/workspace", json={"name": "X"}, headers=h).status_code == 403
    r = client.post(
        "/api/v1/workspace/logo", files={"file": ("l.png", PNG, "image/png")}, headers=h
    )
    assert r.status_code == 403


def test_logo_upload_and_rejections(client, db):
    _, h = _setup(db)
    ok = client.post(
        "/api/v1/workspace/logo", files={"file": ("l.png", PNG, "image/png")}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["data"]["logo_url"]
    svg = client.post(
        "/api/v1/workspace/logo", files={"file": ("l.svg", b"<svg/>", "image/svg+xml")}, headers=h
    )
    assert svg.status_code == 422
    fake = client.post(
        "/api/v1/workspace/logo", files={"file": ("l.png", b"GIF89a", "image/png")}, headers=h
    )
    assert fake.status_code == 422
    big = client.post(
        "/api/v1/workspace/logo",
        files={"file": ("l.png", PNG + b"0" * (2 * 1024 * 1024), "image/png")},
        headers=h,
    )
    assert big.status_code == 422


def test_workspace_requires_header_and_membership(client, db):
    owner = create_user(
        db, email="o@x.com", status=UserStatus.active, email_verified_at=datetime.now(UTC)
    )
    s = create_startup(db, owner=owner)
    stranger = create_user(
        db, email="s@x.com", status=UserStatus.active, email_verified_at=datetime.now(UTC)
    )
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(stranger.id))}",
        "X-Workspace-Id": str(s.id),
    }
    assert client.get("/api/v1/workspace", headers=h).status_code == 403
