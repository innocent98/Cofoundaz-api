from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/finance"
FORBIDDEN_ROLES = [MembershipRole.mentor, MembershipRole.investor]
INT32_MAX = 2_147_483_647


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


def _seed_burning(client, h):
    today = datetime.now(UTC).date().isoformat()
    for direction, amount in (("in", 3_000_000), ("out", 9_000_000)):
        resp = client.post(
            f"{BASE}/transactions",
            json={
                "date": today,
                "description": "seed",
                "amount_minor": amount,
                "currency": "NGN",
                "direction": direction,
            },
            headers=h,
        )
        assert resp.status_code == 200, resp.text


def test_accountant_can_get_runway(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    resp = client.get(f"{BASE}/runway", headers=h)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_runway_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    got = client.get(f"{BASE}/runway", headers=h)
    assert got.status_code == 403, got.text
    put = client.put(f"{BASE}/runway/assumptions", json={"mom_growth_percent": 5}, headers=h)
    assert put.status_code == 403, put.text


def test_runway_requires_auth(client, db):
    _u, startup, _h = _member(db)
    resp = client.get(f"{BASE}/runway", headers={"X-Workspace-Id": str(startup.id)})
    assert resp.status_code in (401, 403), resp.text


def test_get_runway_default_no_settings_row(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/runway", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["assumptions"] == {
        "mom_growth_percent": 0,
        "hiring_spend_minor": 0,
        "one_off_costs_minor": 0,
    }
    assert d["horizon_months"] == 12
    assert set(d["scenarios"]) == {"base", "best", "worst"}
    assert all(len(s["by_month"]) == 12 for s in d["scenarios"].values())
    assert d["baseline"]["currency"] == "NGN"
    assert set(d["baseline"]) == {"cash_on_hand", "monthly_burn", "monthly_revenue", "currency"}


def test_put_assumptions_roundtrip_changes_scenarios(client, db):
    _u, _s, h = _member(db)
    _seed_burning(client, h)
    before = client.get(f"{BASE}/runway", headers=h)
    assert before.status_code == 200, before.text

    put = client.put(
        f"{BASE}/runway/assumptions",
        json={
            "mom_growth_percent": 20,
            "hiring_spend_minor": 500_000,
            "one_off_costs_minor": 1_000_000,
        },
        headers=h,
    )
    assert put.status_code == 200, put.text
    expected = {
        "mom_growth_percent": 20,
        "hiring_spend_minor": 500_000,
        "one_off_costs_minor": 1_000_000,
    }
    assert put.json()["data"]["assumptions"] == expected

    after = client.get(f"{BASE}/runway", headers=h)
    assert after.status_code == 200, after.text
    assert after.json()["data"]["assumptions"] == expected
    assert after.json()["data"]["scenarios"] != before.json()["data"]["scenarios"]
    assert after.json()["data"]["scenarios"] == put.json()["data"]["scenarios"]


def test_put_assumptions_is_partial(client, db):
    _u, _s, h = _member(db)
    first = client.put(
        f"{BASE}/runway/assumptions",
        json={"mom_growth_percent": 7, "hiring_spend_minor": 100},
        headers=h,
    )
    assert first.status_code == 200, first.text
    second = client.put(f"{BASE}/runway/assumptions", json={"hiring_spend_minor": 250}, headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["assumptions"] == {
        "mom_growth_percent": 7,
        "hiring_spend_minor": 250,
        "one_off_costs_minor": 0,
    }


def test_get_does_not_create_settings_row(client, db):
    from app.db.models.finance_runway import FinanceRunwaySettings

    _u, startup, h = _member(db)
    resp = client.get(f"{BASE}/runway", headers=h)
    assert resp.status_code == 200, resp.text
    count = db.query(FinanceRunwaySettings).filter_by(startup_id=startup.id).count()
    assert count == 0


@pytest.mark.parametrize(
    "field", ["mom_growth_percent", "hiring_spend_minor", "one_off_costs_minor"]
)
def test_put_explicit_null_422(client, db, field):
    _u, _s, h = _member(db)
    resp = client.put(f"{BASE}/runway/assumptions", json={field: None}, headers=h)
    assert resp.status_code == 422, resp.text


def test_put_over_int32_hiring_422(client, db):
    _u, _s, h = _member(db)
    resp = client.put(
        f"{BASE}/runway/assumptions", json={"hiring_spend_minor": INT32_MAX + 1}, headers=h
    )
    assert resp.status_code == 422, resp.text


def test_put_growth_over_1000_422(client, db):
    _u, _s, h = _member(db)
    resp = client.put(f"{BASE}/runway/assumptions", json={"mom_growth_percent": 1001}, headers=h)
    assert resp.status_code == 422, resp.text


def test_put_negative_422(client, db):
    _u, _s, h = _member(db)
    resp = client.put(f"{BASE}/runway/assumptions", json={"one_off_costs_minor": -1}, headers=h)
    assert resp.status_code == 422, resp.text


def test_cross_tenant_assumptions_isolated(client, db):
    _ua, _sa, ha = _member(db)
    _ub, _sb, hb = _member(db)
    put = client.put(f"{BASE}/runway/assumptions", json={"mom_growth_percent": 50}, headers=ha)
    assert put.status_code == 200, put.text
    got_b = client.get(f"{BASE}/runway", headers=hb)
    assert got_b.status_code == 200, got_b.text
    assert got_b.json()["data"]["assumptions"]["mom_growth_percent"] == 0
    got_a = client.get(f"{BASE}/runway", headers=ha)
    assert got_a.json()["data"]["assumptions"]["mom_growth_percent"] == 50


def test_profitable_startup_projection_uses_true_costs_not_clamped_burn(client, db):
    # Profitable: 9,000,000 in / 3,000,000 out inside the trailing window, so
    # cash_flow_summary clamps monthly_burn to 0 while the true monthly costs are 1,000,000 and
    # monthly revenue is 3,000,000. If runway_payload fed the clamped monthly_burn (0) into the
    # projection as "costs", a profitable company would look like it has zero costs (and the
    # per-month net would be overstated) for the wrong reason. This pins the real wiring.
    _u, _s, h = _member(db)
    today = datetime.now(UTC).date().isoformat()
    for direction, amount in (("in", 9_000_000), ("out", 3_000_000)):
        seeded = client.post(
            f"{BASE}/transactions",
            json={
                "date": today,
                "description": "seed",
                "amount_minor": amount,
                "currency": "NGN",
                "direction": direction,
            },
            headers=h,
        )
        assert seeded.status_code == 200, seeded.text

    resp = client.get(f"{BASE}/runway", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]

    assert d["baseline"]["monthly_burn"] == 0
    assert d["baseline"]["monthly_revenue"] == 3_000_000
    base = d["scenarios"]["base"]
    assert base["runway_months"] is None
    assert base["avg_net_burn_minor"] == 0
    # Month 1 net = revenue 3,000,000 - true costs 1,000,000 (NOT - clamped burn 0).
    first = base["by_month"][0]
    assert first["net"] == 2_000_000
    assert first["cash_balance"] == d["baseline"]["cash_on_hand"] + 2_000_000
    # Worst scenario inflates true costs by 15%: net = 3,000,000 - 1,150,000.
    assert d["scenarios"]["worst"]["by_month"][0]["net"] == 1_850_000
