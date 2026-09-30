from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.enums import FinancialModelStatus, MembershipRole, StartupStage
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/finance"
FORBIDDEN_ROLES = [MembershipRole.mentor, MembershipRole.investor]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _generate(client, h):
    resp = client.post(f"{BASE}/model/generate", headers=h)
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


def test_generate_returns_202_and_enqueues_job(client, db):
    _u, startup, h = _member(db)
    resp = client.post(f"{BASE}/model/generate", headers=h)
    status_code = resp.status_code
    assert status_code == 202, resp.text
    data = resp.json()["data"]
    assert set(data) == {"id", "status"}
    assert data["status"] == "generating"

    rows = db.query(FinancialModel).filter_by(startup_id=startup.id).all()
    assert len(rows) == 1
    assert str(rows[0].id) == data["id"]
    assert rows[0].status == FinancialModelStatus.generating
    assert rows[0].horizon_months == 12
    assert rows[0].currency == "NGN"

    jobs = db.query(Job).filter_by(type="ai.finance.model", startup_id=startup.id).all()
    assert len(jobs) == 1
    assert jobs[0].payload == {"model_id": data["id"]}


def test_get_latest_when_none(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/model", headers=h)
    status_code = resp.status_code
    assert status_code == 200, resp.text
    assert resp.json()["data"] == {"status": "none"}


def test_get_latest_and_get_one_after_generate(client, db):
    _u, _s, h = _member(db)
    created = _generate(client, h)

    latest = client.get(f"{BASE}/model", headers=h)
    latest_status = latest.status_code
    assert latest_status == 200, latest.text
    body = latest.json()["data"]
    assert body["id"] == created["id"]
    assert body["status"] == "generating"
    assert body["pnl"] is None
    assert body["months"] is None

    one = client.get(f"{BASE}/model/{created['id']}", headers=h)
    one_status = one.status_code
    assert one_status == 200, one.text
    assert one.json()["data"]["id"] == created["id"]


def test_regenerate_creates_second_row_and_latest_is_newest(client, db):
    _u, startup, h = _member(db)
    first = _generate(client, h)
    # now() is the txn start time, identical for both rows inside the single test txn;
    # in production each POST commits separately so created_at differs. Back-date the first.
    first_row = db.get(FinancialModel, first["id"])
    first_row.created_at = datetime.now(UTC) - timedelta(hours=1)
    db.flush()
    second = _generate(client, h)
    assert first["id"] != second["id"]

    count = db.query(FinancialModel).filter_by(startup_id=startup.id).count()
    assert count == 2
    jobs = db.query(Job).filter_by(type="ai.finance.model", startup_id=startup.id).count()
    assert jobs == 2

    latest = client.get(f"{BASE}/model", headers=h)
    assert latest.json()["data"]["id"] == second["id"]


def test_serialize_completed_model_exposes_statements_and_months(client, db):
    _u, startup, h = _member(db)
    created = _generate(client, h)
    row = db.get(FinancialModel, created["id"])
    stmt = {"rows": [{"label": "Revenue", "values": [1, 2]}], "months": ["2026-10", "2026-11"]}
    row.status = FinancialModelStatus.complete
    row.pnl = stmt
    db.flush()

    resp = client.get(f"{BASE}/model/{created['id']}", headers=h)
    body = resp.json()["data"]
    assert body["status"] == "complete"
    assert body["pnl"] == {"rows": [{"label": "Revenue", "values": [1, 2]}]}
    assert body["months"] == ["2026-10", "2026-11"]
    assert body["cash_flow"] is None


def test_accountant_can_generate_and_get(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    created = _generate(client, h)
    got = client.get(f"{BASE}/model", headers=h)
    got_status = got.status_code
    assert got_status == 200, got.text
    assert got.json()["data"]["id"] == created["id"]


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_model_rbac_forbidden(client, db, role):
    _f, startup, fh = _member(db)
    created = _generate(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    post = client.post(f"{BASE}/model/generate", headers=h)
    get_latest = client.get(f"{BASE}/model", headers=h)
    get_one = client.get(f"{BASE}/model/{created['id']}", headers=h)
    assert post.status_code == 403, post.text
    assert get_latest.status_code == 403, get_latest.text
    assert get_one.status_code == 403, get_one.text


def test_cross_tenant_get_is_404(client, db):
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    created = _generate(client, h1)
    resp = client.get(f"{BASE}/model/{created['id']}", headers=h2)
    status_code = resp.status_code
    assert status_code == 404, resp.text


def test_latest_is_scoped_to_own_startup(client, db):
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    _generate(client, h1)
    resp = client.get(f"{BASE}/model", headers=h2)
    assert resp.json()["data"] == {"status": "none"}
