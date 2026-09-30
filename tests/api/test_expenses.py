import datetime as dt

import pytest

from app.db.models.enums import MembershipRole, TransactionDirection, TransactionSource
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member


def _body(**over):
    body = {
        "vendor": "AWS",
        "category": "Infrastructure",
        "expense_date": "2026-03-10",
        "amount_minor": 250_000,
    }
    body.update(over)
    return body


def _post(client, h, **over):
    resp = client.post(f"{BASE}/expenses", json=_body(**over), headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_create_returns_expense_and_posts_one_outflow(client, db):
    _u, s, h = _member(db)
    data = _post(client, h, recurring=True, notes="monthly hosting")
    assert data["vendor"] == "AWS"
    assert data["category"] == "Infrastructure"
    assert data["amount_minor"] == 250_000
    assert data["currency"] == "NGN"
    assert data["recurring"] is True
    assert data["has_receipt"] is False
    assert data["transaction_id"] is not None

    txns = db.query(Transaction).filter_by(startup_id=s.id).all()
    assert len(txns) == 1
    txn = txns[0]
    assert txn.source == TransactionSource.expense
    assert txn.direction == TransactionDirection.outflow
    assert txn.amount_minor == 250_000
    assert txn.date == dt.date(2026, 3, 10)
    assert txn.category == "Infrastructure"
    assert str(txn.id) == data["transaction_id"]
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    assert exp.transaction_id == txn.id


def test_create_sets_created_by_from_verified_user(client, db):
    u, s, h = _member(db)
    _post(client, h)
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    assert exp.created_by == u.id


def test_list_newest_first_and_filters(client, db):
    _u, _s, h = _member(db)
    _post(client, h, vendor="Old", expense_date="2026-01-05", category="Ops")
    _post(client, h, vendor="Mid", expense_date="2026-02-10", category="Infrastructure")
    _post(client, h, vendor="New", expense_date="2026-02-20", category="Ops", recurring=True)

    listed = client.get(f"{BASE}/expenses", headers=h)
    assert listed.status_code == 200, listed.text
    vendors = [e["vendor"] for e in listed.json()["data"]["expenses"]]
    assert vendors == ["New", "Mid", "Old"]

    by_cat = client.get(f"{BASE}/expenses?category=Ops", headers=h).json()["data"]["expenses"]
    assert [e["vendor"] for e in by_cat] == ["New", "Old"]

    by_month = client.get(f"{BASE}/expenses?month=2026-02", headers=h).json()["data"]["expenses"]
    assert [e["vendor"] for e in by_month] == ["New", "Mid"]

    rec = client.get(f"{BASE}/expenses?recurring=true", headers=h).json()["data"]["expenses"]
    assert [e["vendor"] for e in rec] == ["New"]
    nonrec = client.get(f"{BASE}/expenses?recurring=false", headers=h).json()["data"]["expenses"]
    assert [e["vendor"] for e in nonrec] == ["Mid", "Old"]

    ranged = client.get(
        f"{BASE}/expenses?date_from=2026-02-01&date_to=2026-02-15", headers=h
    ).json()["data"]["expenses"]
    assert [e["vendor"] for e in ranged] == ["Mid"]


def test_invalid_month_filter_422(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/expenses?month=2026-13", headers=h)
    assert resp.status_code == 422, resp.text


def test_get_one_and_cross_tenant_404(client, db):
    _u, _s, h = _member(db)
    created = _post(client, h)
    got = client.get(f"{BASE}/expenses/{created['id']}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["id"] == created["id"]

    _u2, _s2, h2 = _member(db)
    other = client.get(f"{BASE}/expenses/{created['id']}", headers=h2)
    assert other.status_code == 404, other.text
    listed = client.get(f"{BASE}/expenses", headers=h2).json()["data"]["expenses"]
    assert listed == []


def test_accountant_allowed(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    resp = client.get(f"{BASE}/expenses", headers=h)
    assert resp.status_code == 200, resp.text
    created = client.post(f"{BASE}/expenses", json=_body(), headers=h)
    assert created.status_code == 200, created.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_403(client, db, role):
    _f, startup, fh = _member(db)
    created = _post(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    calls = [
        client.get(f"{BASE}/expenses", headers=h),
        client.get(f"{BASE}/expenses/summary", headers=h),
        client.get(f"{BASE}/expenses/{created['id']}", headers=h),
        client.post(f"{BASE}/expenses", json=_body(), headers=h),
    ]
    assert [c.status_code for c in calls] == [403, 403, 403, 403]


def test_requires_auth(client):
    resp = client.get(f"{BASE}/expenses")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.parametrize(
    "over",
    [
        {"amount_minor": 3_000_000_000},
        {"amount_minor": -1},
        {"vendor": ""},
        {"category": ""},
    ],
)
def test_create_validation_422(client, db, over):
    _u, s, h = _member(db)
    resp = client.post(f"{BASE}/expenses", json=_body(**over), headers=h)
    assert resp.status_code == 422, resp.text
    assert db.query(Expense).filter_by(startup_id=s.id).count() == 0
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 0


def test_summary_per_category_totals_and_percents(client, db):
    _u, _s, h = _member(db)
    _post(client, h, category="Infrastructure", amount_minor=300_000, expense_date="2026-02-03")
    _post(client, h, category="Infrastructure", amount_minor=100_000, expense_date="2026-02-20")
    _post(client, h, category="Marketing", amount_minor=100_000, expense_date="2026-02-11")
    _post(client, h, category="Marketing", amount_minor=999_999, expense_date="2026-03-01")

    resp = client.get(f"{BASE}/expenses/summary?month=2026-02", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["month"] == "2026-02"
    assert d["currency"] == "NGN"
    assert d["total_minor"] == 500_000
    rows = {r["category"]: r for r in d["rows"]}
    assert rows["Infrastructure"]["total_minor"] == 400_000
    assert rows["Infrastructure"]["percent"] == 80.0
    assert rows["Marketing"]["total_minor"] == 100_000
    assert rows["Marketing"]["percent"] == 20.0
    assert abs(sum(r["percent"] for r in d["rows"]) - 100) < 0.5
    assert [r["category"] for r in d["rows"]] == ["Infrastructure", "Marketing"]


def test_summary_empty_month_is_zeroed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/expenses/summary?month=2020-01", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["rows"] == []
    assert d["total_minor"] == 0


def test_summary_all_zero_amounts_no_divide_by_zero(client, db):
    _u, _s, h = _member(db)
    _post(client, h, amount_minor=0, expense_date="2026-02-03")
    resp = client.get(f"{BASE}/expenses/summary?month=2026-02", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["rows"] == []
    assert d["total_minor"] == 0


def test_summary_defaults_to_current_month_and_route_not_shadowed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/expenses/summary", headers=h)
    assert resp.status_code == 200, resp.text
    month = resp.json()["data"]["month"]
    assert len(month) == 7 and month[4] == "-"


def test_summary_is_tenant_scoped(client, db):
    _u, _s, h = _member(db)
    _post(client, h, amount_minor=100_000, expense_date="2026-02-03")
    _u2, _s2, h2 = _member(db)
    d = client.get(f"{BASE}/expenses/summary?month=2026-02", headers=h2).json()["data"]
    assert d["rows"] == [] and d["total_minor"] == 0
