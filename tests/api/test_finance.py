from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
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


def test_transaction_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "AWS",
            "amount_minor": 12050000,
            "currency": "NGN",
            "direction": "out",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    tid = created.json()["data"]["id"]
    assert created.json()["data"]["source"] == "manual"  # body source ignored/forced

    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200
    assert any(t["id"] == tid for t in listed.json()["data"]["transactions"])

    uncat_before = client.get(f"{BASE}/transactions?uncategorized=true", headers=h)
    assert any(t["id"] == tid for t in uncat_before.json()["data"]["transactions"])

    patched = client.patch(
        f"{BASE}/transactions/{tid}", json={"category": "Infrastructure"}, headers=h
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["category"] == "Infrastructure"

    uncat = client.get(f"{BASE}/transactions?uncategorized=true", headers=h)
    assert uncat.status_code == 200
    assert all(t["id"] != tid for t in uncat.json()["data"]["transactions"])  # now categorized

    deleted = client.delete(f"{BASE}/transactions/{tid}", headers=h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"deleted": True}


def test_client_cannot_set_source(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "x",
            "amount_minor": 1,
            "direction": "in",
            "source": "import",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["source"] == "manual"


def test_negative_amount_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": -5, "direction": "out"},
        headers=h,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": 1, "direction": "out"},
        headers=h,
    )
    assert posted.status_code == 403, posted.text


def test_accountant_allowed(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200, listed.text


def test_patch_explicit_null_on_required_field_422(client, db):
    # Explicit null on a non-nullable field must 422 (not 500 at flush); category-null is allowed.
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": 100, "direction": "out"},
        headers=h,
    )
    tid = created.json()["data"]["id"]
    bad = client.patch(f"{BASE}/transactions/{tid}", json={"amount_minor": None}, headers=h)
    assert bad.status_code == 422, bad.text
    ok = client.patch(f"{BASE}/transactions/{tid}", json={"category": None}, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["category"] is None


def test_cross_tenant_transaction_404(client, db):
    _u, s1, h1 = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": 100, "direction": "out"},
        headers=h1,
    )
    tid = created.json()["data"]["id"]
    _u2, _s2, h2 = _member(db)  # different startup
    got = client.patch(f"{BASE}/transactions/{tid}", json={"category": "x"}, headers=h2)
    assert got.status_code == 404, got.text
    deleted = client.delete(f"{BASE}/transactions/{tid}", headers=h2)
    assert deleted.status_code == 404, deleted.text


def _seed(client, h, txns):
    for t in txns:
        r = client.post(f"{BASE}/transactions", json=t, headers=h)
        assert r.status_code == 200, r.text


def _today_utc() -> str:
    return datetime.now(UTC).date().isoformat()


def test_cash_flow_empty_is_zeroed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/cash-flow", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["cash_on_hand"] == 0 and d["monthly_burn"] == 0
    assert d["monthly_revenue"] == 0
    assert d["runway_months"] is None and d["runway_low"] is False
    assert d["currency"] == "NGN"
    assert len(d["by_month"]) == 6
    assert all(m["inflow"] == 0 and m["outflow"] == 0 and m["net"] == 0 for m in d["by_month"])
    months = [m["month"] for m in d["by_month"]]
    assert months == sorted(months)
    assert months[-1] == _today_utc()[:7]


def _txn(day: str, amount: int, direction: str, desc: str = "t") -> dict:
    return {
        "date": day,
        "description": desc,
        "amount_minor": amount,
        "currency": "NGN",
        "direction": direction,
    }


def test_cash_flow_runway_and_low_flag(client, db):
    _u, _s, h = _member(db)
    today = datetime.now(UTC).date()
    old = today.replace(year=today.year - 1, day=1).isoformat()  # outside the trailing-3mo window
    # The raise is old, so it counts toward cash on hand but not toward the trailing burn window.
    _seed(client, h, [_txn(old, 1_000_000, "in", "raise"), _txn(today.isoformat(), 600_000, "out")])
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    assert d["cash_on_hand"] == 400_000
    assert d["monthly_burn"] == 200_000  # 600_000 / 3
    assert d["runway_months"] == 2.0
    assert d["runway_low"] is True  # < 6 months


def test_cash_flow_runway_not_low_and_null_when_cash_not_positive(client, db):
    _u, _s, h = _member(db)
    today = datetime.now(UTC).date()
    old = today.replace(year=today.year - 1, day=1).isoformat()
    _seed(
        client, h, [_txn(old, 10_000_000, "in", "raise"), _txn(today.isoformat(), 300_000, "out")]
    )
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    assert d["monthly_burn"] == 100_000
    assert d["runway_months"] == 97.0
    assert d["runway_low"] is False

    # Overspent: cash on hand <= 0 while burning -> no runway number, but the danger flag MUST fire.
    _u2, _s2, h2 = _member(db)
    _seed(client, h2, [_txn(today.isoformat(), 300_000, "out")])
    d2 = client.get(f"{BASE}/cash-flow", headers=h2).json()["data"]
    assert d2["cash_on_hand"] == -300_000 and d2["monthly_burn"] == 100_000
    assert d2["runway_months"] is None and d2["runway_low"] is True


def test_cash_flow_runway_null_when_net_positive(client, db):
    _u, _s, h = _member(db)
    today = _today_utc()
    _seed(
        client,
        h,
        [
            {
                "date": today,
                "description": "raise",
                "amount_minor": 5_000_000,
                "currency": "NGN",
                "direction": "in",
            },
            {
                "date": today,
                "description": "spend",
                "amount_minor": 100_000,
                "currency": "NGN",
                "direction": "out",
            },
        ],
    )
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    assert d["monthly_burn"] == 0 and d["runway_months"] is None and d["runway_low"] is False
    assert d["cash_on_hand"] == 4_900_000
    assert d["monthly_revenue"] == round(5_000_000 / 3)
    assert d["by_month"][-1]["inflow"] == 5_000_000 and d["by_month"][-1]["net"] == 4_900_000


def test_amount_over_int32_422(client, db):
    # A value above Postgres int32 max must 422 at validation, not 500 at flush.
    _u, _s, h = _member(db)
    resp = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "big raise",
            "amount_minor": 3_000_000_000,
            "direction": "in",
        },
        headers=h,
    )
    assert resp.status_code == 422, resp.text


def test_future_dated_excluded_from_cash_flow(client, db):
    from datetime import UTC, datetime, timedelta

    _u, _s, h = _member(db)
    today = datetime.now(UTC).date()
    future = (today.replace(day=1) + timedelta(days=40)).isoformat()  # next month-ish
    _seed(client, h, [_txn(today.isoformat(), 500_000, "in"), _txn(future, 900_000, "out")])
    d = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    # the future-dated outflow must not reduce cash-on-hand or appear in the 6-month series
    assert d["cash_on_hand"] == 500_000
    assert all(m["outflow"] == 0 for m in d["by_month"])
