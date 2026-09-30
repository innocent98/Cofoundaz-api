import pytest

from app.db.models.budget import Budget
from app.db.models.enums import MembershipRole
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member


def _body(**over):
    body = {
        "category": "Infrastructure",
        "period_month": "2026-03",
        "limit_minor": 500_000,
    }
    body.update(over)
    return body


def _post(client, h, **over):
    resp = client.post(f"{BASE}/budgets", json=_body(**over), headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _expense(client, h, *, amount, category="Infrastructure", date="2026-03-10"):
    resp = client.post(
        f"{BASE}/expenses",
        json={
            "vendor": "V",
            "category": category,
            "expense_date": date,
            "amount_minor": amount,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _list(client, h, month="2026-03"):
    resp = client.get(f"{BASE}/budgets", params={"month": month}, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_create_returns_budget_with_zero_actuals(client, db):
    u, s, h = _member(db)
    data = _post(client, h, notes="cloud")
    assert data["category"] == "Infrastructure"
    assert data["period_month"] == "2026-03"
    assert data["limit_minor"] == 500_000
    assert data["currency"] == "NGN"
    assert data["notes"] == "cloud"
    assert data["spent_minor"] == 0
    assert data["variance_minor"] == -500_000
    assert data["over_budget"] is False
    assert data["percent_used"] == 0.0
    row = db.query(Budget).filter_by(startup_id=s.id).one()
    assert row.created_by == u.id


def test_duplicate_category_month_is_422_and_session_survives(client, db):
    _u, _s, h = _member(db)
    _post(client, h)
    dup = client.post(f"{BASE}/budgets", json=_body(limit_minor=1), headers=h)
    assert dup.status_code == 422, dup.text
    assert dup.json()["error"]["code"] == "VALIDATION_ERROR"
    follow_up = client.get(f"{BASE}/budgets", params={"month": "2026-03"}, headers=h)
    assert follow_up.status_code == 200, follow_up.text
    assert len(follow_up.json()["data"]["budgets"]) == 1


def test_same_category_different_month_ok(client, db):
    _u, _s, h = _member(db)
    _post(client, h, period_month="2026-03")
    other = _post(client, h, period_month="2026-04")
    assert other["period_month"] == "2026-04"


def test_actuals_equal_sum_of_expenses_and_match_summary(client, db):
    _u, _s, h = _member(db)
    _expense(client, h, amount=100_000, date="2026-03-01")
    _expense(client, h, amount=150_000, date="2026-03-31")  # last day is inclusive
    _expense(client, h, amount=999_000, date="2026-04-01")  # next month: excluded
    _expense(client, h, amount=50_000, category="Ops")
    created = _post(client, h, limit_minor=400_000)
    assert created["spent_minor"] == 250_000

    fetched = client.get(f"{BASE}/budgets/{created['id']}", headers=h)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["spent_minor"] == 250_000

    summary = client.get(f"{BASE}/expenses/summary", params={"month": "2026-03"}, headers=h)
    rows = {r["category"]: r["total_minor"] for r in summary.json()["data"]["rows"]}
    assert rows["Infrastructure"] == fetched.json()["data"]["spent_minor"]


@pytest.mark.parametrize(
    ("limit", "spent", "variance", "over", "pct"),
    [
        (100_000, 150_000, 50_000, True, 150.0),
        (100_000, 40_000, -60_000, False, 40.0),
        (100_000, 100_000, 0, False, 100.0),
        (0, 30_000, 30_000, True, None),
        (0, 0, 0, False, None),
    ],
)
def test_variance_over_budget_percent(client, db, limit, spent, variance, over, pct):
    _u, _s, h = _member(db)
    if spent:
        _expense(client, h, amount=spent)
    data = _post(client, h, limit_minor=limit)
    assert data["spent_minor"] == spent
    assert data["variance_minor"] == variance
    assert data["over_budget"] is over
    assert data["percent_used"] == pct


def test_category_with_expenses_but_no_budget_is_not_listed(client, db):
    _u, _s, h = _member(db)
    _expense(client, h, amount=70_000, category="Marketing")
    _post(client, h, category="Infrastructure")
    listed = _list(client, h)
    assert [b["category"] for b in listed["budgets"]] == ["Infrastructure"]
    assert listed["budgets"][0]["spent_minor"] == 0


def test_list_computes_each_cards_actuals(client, db):
    _u, _s, h = _member(db)
    _expense(client, h, amount=10_000, category="Ops")
    _expense(client, h, amount=20_000, category="Ops")
    _expense(client, h, amount=300_000, category="Infrastructure")
    _post(client, h, category="Ops", limit_minor=25_000)
    _post(client, h, category="Infrastructure", limit_minor=400_000)
    _post(client, h, category="Ops", period_month="2026-04", limit_minor=1)  # other month
    listed = _list(client, h)
    by_cat = {b["category"]: b for b in listed["budgets"]}
    assert set(by_cat) == {"Ops", "Infrastructure"}
    assert by_cat["Ops"]["spent_minor"] == 30_000
    assert by_cat["Ops"]["over_budget"] is True
    assert by_cat["Infrastructure"]["spent_minor"] == 300_000
    assert by_cat["Infrastructure"]["over_budget"] is False
    assert listed["month"] == "2026-03"


def test_manual_outflow_does_not_change_spent(client, db):
    _u, _s, h = _member(db)
    _expense(client, h, amount=100_000)
    created = _post(client, h)
    txn = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-12",
            "description": "manual",
            "category": "Infrastructure",
            "amount_minor": 777_000,
            "direction": "out",
        },
        headers=h,
    )
    assert txn.status_code == 200, txn.text
    fetched = client.get(f"{BASE}/budgets/{created['id']}", headers=h)
    assert fetched.json()["data"]["spent_minor"] == 100_000


def test_patch_updates_limit_and_notes_with_actuals(client, db):
    _u, _s, h = _member(db)
    _expense(client, h, amount=100_000)
    created = _post(client, h, limit_minor=500_000)
    resp = client.patch(
        f"{BASE}/budgets/{created['id']}", json={"limit_minor": 80_000, "notes": "tight"}, headers=h
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["limit_minor"] == 80_000
    assert data["notes"] == "tight"
    assert data["spent_minor"] == 100_000
    assert data["over_budget"] is True
    cleared = client.patch(f"{BASE}/budgets/{created['id']}", json={"notes": None}, headers=h)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["notes"] is None


def test_patch_explicit_null_limit_is_422(client, db):
    _u, _s, h = _member(db)
    created = _post(client, h)
    resp = client.patch(f"{BASE}/budgets/{created['id']}", json={"limit_minor": None}, headers=h)
    assert resp.status_code == 422, resp.text


def test_delete_removes_budget(client, db):
    _u, _s, h = _member(db)
    created = _post(client, h)
    resp = client.delete(f"{BASE}/budgets/{created['id']}", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"deleted": True}
    gone = client.get(f"{BASE}/budgets/{created['id']}", headers=h)
    assert gone.status_code == 404


@pytest.mark.parametrize(
    "over",
    [
        {"period_month": "2026-13"},
        {"period_month": "2026-3"},
        {"limit_minor": 2_147_483_648},
        {"limit_minor": -1},
        {"category": ""},
    ],
)
def test_create_validation_422(client, db, over):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/budgets", json=_body(**over), headers=h)
    assert resp.status_code == 422, resp.text


def test_list_requires_valid_month(client, db):
    _u, _s, h = _member(db)
    missing = client.get(f"{BASE}/budgets", headers=h)
    assert missing.status_code == 422, missing.text
    bad = client.get(f"{BASE}/budgets", params={"month": "2026-99"}, headers=h)
    assert bad.status_code == 422, bad.text


def test_accountant_allowed(client, db):
    _f, startup, fh = _member(db)
    _post(client, fh)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    resp = client.get(f"{BASE}/budgets", params={"month": "2026-03"}, headers=h)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]["budgets"]) == 1


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_403(client, db, role):
    _f, startup, fh = _member(db)
    created = _post(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    bid = created["id"]
    calls = [
        client.post(f"{BASE}/budgets", json=_body(), headers=h),
        client.get(f"{BASE}/budgets", params={"month": "2026-03"}, headers=h),
        client.get(f"{BASE}/budgets/{bid}", headers=h),
        client.patch(f"{BASE}/budgets/{bid}", json={"limit_minor": 1}, headers=h),
        client.delete(f"{BASE}/budgets/{bid}", headers=h),
    ]
    assert [c.status_code for c in calls] == [403, 403, 403, 403, 403]


def test_cross_tenant_get_patch_delete_404_and_list_isolated(client, db):
    _a, _sa, ha = _member(db)
    created = _post(client, ha)
    _b, _sb, hb = _member(db)
    bid = created["id"]
    calls = [
        client.get(f"{BASE}/budgets/{bid}", headers=hb),
        client.patch(f"{BASE}/budgets/{bid}", json={"limit_minor": 1}, headers=hb),
        client.delete(f"{BASE}/budgets/{bid}", headers=hb),
    ]
    assert [c.status_code for c in calls] == [404, 404, 404]
    assert _list(client, hb)["budgets"] == []
    still = client.get(f"{BASE}/budgets/{bid}", headers=ha)
    assert still.status_code == 200, still.text


def test_unauthenticated_rejected(client):
    resp = client.get(f"{BASE}/budgets", params={"month": "2026-03"})
    assert resp.status_code in (401, 403, 422)
