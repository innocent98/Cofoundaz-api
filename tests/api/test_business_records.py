from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


@pytest.fixture()
def founder(db):
    _u, s, h = _member(db)
    return h, s


def test_list_empty_and_fields(client, founder):
    headers, _ = founder
    r = client.get("/api/v1/business-builder/personas", headers=headers)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["records"] == []
    assert any(f["key"] == "name" for f in body["fields"])


def test_create_returns_201(client, founder):
    headers, _ = founder
    r = client.post(
        "/api/v1/business-builder/personas",
        json={"data": {"name": "Busy Founder", "goals": ["ship"]}},
        headers=headers,
    )
    assert r.status_code == 201
    rec = r.json()["data"]
    assert rec["kind"] == "persona" and rec["data"]["name"] == "Busy Founder"


def test_create_bad_data_422(client, founder):
    headers, _ = founder
    r = client.post(
        "/api/v1/business-builder/personas",
        json={"data": {"goals": "not-a-list"}},
        headers=headers,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_kind_404(client, founder):
    headers, _ = founder
    assert client.get("/api/v1/business-builder/gremlins", headers=headers).status_code == 404


def test_create_mentor_forbidden_403(client, db):
    _, _, headers = _member(db, role=MembershipRole.mentor)
    r = client.post(
        "/api/v1/business-builder/personas", json={"data": {"name": "x"}}, headers=headers
    )
    assert r.status_code == 403
