from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"


def _setup(db, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return h


def test_overview_on_fresh_workspace_returns_default_stages(client, db):
    h = _setup(db)
    r = client.get(BASE, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total_pipeline_minor"] == 0 and d["weighted_forecast_minor"] == 0
    assert d["win_rate"] is None and d["currency"] == "NGN"
    assert len(d["stages"]) == 7
    assert client.get(f"{BASE}/", headers=h).status_code in (200, 307)


def test_overview_reflects_a_created_deal(client, db):
    h = _setup(db, MembershipRole.team_member)
    r = client.post(f"{BASE}/deals", json={"name": "Big", "value_minor": 10_000}, headers=h)
    assert r.status_code == 200, r.text
    d = client.get(BASE, headers=h).json()["data"]
    assert d["total_pipeline_minor"] == 10_000
    assert d["weighted_forecast_minor"] == 1_000  # New = 10%
    assert d["stages"][0]["deal_count"] == 1


def test_overview_forbidden_for_mentor(client, db):
    assert client.get(BASE, headers=_setup(db, MembershipRole.mentor)).status_code == 403
