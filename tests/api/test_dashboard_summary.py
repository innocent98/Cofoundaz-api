from datetime import UTC, datetime

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


def test_summary_returns_all_sections(client, db):
    _u, s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/dashboard/summary", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert set(data) >= {
        "greeting",
        "health",
        "mission",
        "upcoming",
        "kpis",
        "calibration",
        "briefing",
        "risks",
        "opportunities",
    }
    assert data["greeting"]["startup_name"] == s.name


def test_summary_requires_membership(client, db):
    _u, s, _h = _member(db)
    outsider = create_user(db, email_verified_at=datetime.now(UTC))
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(outsider.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.get("/api/v1/dashboard/summary", headers=headers)
    assert r.status_code == 403


def test_summary_requires_verified_email(client, db):
    u = create_user(db, email_verified_at=None)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.get("/api/v1/dashboard/summary", headers=headers)
    assert r.status_code == 403


def _seed_txn(client, h):
    resp = client.post(
        "/api/v1/finance/transactions",
        json={
            "date": datetime.now(UTC).date().isoformat(),
            "description": "seed",
            "amount_minor": 3_000_000,
            "currency": "NGN",
            "direction": "in",
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def test_summary_kpi_metrics_for_founder(client, db):
    _u, _s, h = _member(db)
    db.commit()
    _seed_txn(client, h)

    kpis = client.get("/api/v1/dashboard/summary", headers=h).json()["data"]["kpis"]
    assert kpis["metrics"]["revenue"]["status"] == "ok"
    assert kpis["metrics"]["revenue"]["value_minor"] == 1_000_000
    assert kpis["metrics"]["pipeline"]["status"] == "no_data"


def test_summary_kpi_metrics_restricted_for_investor(client, db):
    _f, s, fh = _member(db)
    investor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, investor, s, role=MembershipRole.investor)
    db.commit()
    _seed_txn(client, fh)
    ih = {
        "Authorization": f"Bearer {create_access_token(str(investor.id))}",
        "X-Workspace-Id": str(s.id),
    }

    metrics = client.get("/api/v1/dashboard/summary", headers=ih).json()["data"]["kpis"]["metrics"]
    assert metrics["revenue"] == {
        "status": "restricted",
        "value_minor": None,
        "currency": None,
        "basis": "trailing_3mo_avg",
    }
    assert metrics["runway"]["status"] == "restricted"
    assert metrics["runway"]["cash_on_hand_minor"] is None
    assert metrics["campaign_ctr"]["status"] == "restricted"
