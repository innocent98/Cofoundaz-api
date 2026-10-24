"""Ledger correctness: positive amounts (30), NGN only (29), categories (17), budgets from the
ledger (18, 19) and pagination (28)."""

import datetime as dt

import pytest

from app.db.models.enums import MembershipRole
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member

MONTH = "2026-03"


def _txn(client, h, **over):
    body = {
        "date": "2026-03-10",
        "description": "x",
        "amount_minor": 1000,
        "direction": "out",
        **over,
    }
    return client.post(f"{BASE}/transactions", json=body, headers=h)


def _expense(client, h, **over):
    body = {
        "vendor": "AWS",
        "category": "Infrastructure",
        "expense_date": "2026-03-10",
        "amount_minor": 1000,
        **over,
    }
    return client.post(f"{BASE}/expenses", json=body, headers=h)


def _budgets(client, h, month=MONTH):
    resp = client.get(f"{BASE}/budgets", params={"month": month}, headers=h)
    assert resp.status_code == 200, resp.text
    return {b["category"]: b for b in resp.json()["data"]["budgets"]}


# ---- row 30: amount_minor > 0 ----------------------------------------------------------------- #


@pytest.mark.parametrize("amount", [0, -1])
def test_transaction_amount_must_be_positive(client, db, amount):
    _u, _s, h = _member(db)
    resp = _txn(client, h, amount_minor=amount)
    assert resp.status_code == 422
    assert resp.json()["error"]["field_errors"][0]["field"] == "amount_minor"


def test_transaction_update_amount_must_be_positive(client, db):
    _u, _s, h = _member(db)
    tid = _txn(client, h).json()["data"]["id"]
    assert (
        client.patch(f"{BASE}/transactions/{tid}", json={"amount_minor": 0}, headers=h).status_code
        == 422
    )
    assert (
        client.patch(f"{BASE}/transactions/{tid}", json={"amount_minor": 5}, headers=h).status_code
        == 200
    )


def test_expense_amount_must_be_positive_on_create_and_update(client, db):
    _u, _s, h = _member(db)
    assert _expense(client, h, amount_minor=0).status_code == 422
    eid = _expense(client, h).json()["data"]["id"]
    assert (
        client.patch(f"{BASE}/expenses/{eid}", json={"amount_minor": 0}, headers=h).status_code
        == 422
    )


# ---- row 29: NGN only ------------------------------------------------------------------------- #


@pytest.mark.parametrize("currency", ["USD", "GBP", "eur"])
def test_other_currencies_are_422_everywhere(client, db, currency):
    _u, _s, h = _member(db)
    for resp in (
        _txn(client, h, currency=currency),
        _expense(client, h, currency=currency),
        client.post(
            f"{BASE}/budgets",
            json={
                "category": "Rent",
                "period_month": MONTH,
                "limit_minor": 5,
                "currency": currency,
            },
            headers=h,
        ),
    ):
        assert resp.status_code == 422, resp.text
        err = resp.json()["error"]["field_errors"][0]
        assert err["field"] == "currency"
        assert err["message"] == "Only NGN is supported for now."


def test_currency_update_is_checked_too(client, db):
    _u, _s, h = _member(db)
    tid = _txn(client, h).json()["data"]["id"]
    assert (
        client.patch(f"{BASE}/transactions/{tid}", json={"currency": "USD"}, headers=h).status_code
        == 422
    )
    eid = _expense(client, h).json()["data"]["id"]
    assert (
        client.patch(f"{BASE}/expenses/{eid}", json={"currency": "USD"}, headers=h).status_code
        == 422
    )


def test_lowercase_ngn_is_accepted_and_stored_uppercase(client, db):
    _u, _s, h = _member(db)
    assert _txn(client, h, currency="ngn").json()["data"]["currency"] == "NGN"
    assert _expense(client, h, currency="Ngn").json()["data"]["currency"] == "NGN"


# ---- row 17: categories ----------------------------------------------------------------------- #


def test_categories_lists_defaults_and_used_casefolded(client, db):
    _u, _s, h = _member(db)
    _txn(client, h, category="Hosting")
    _expense(client, h, category="hosting")  # same category, different case
    _expense(client, h, category="software")  # a default, typed lowercase
    cats = client.get(f"{BASE}/categories", headers=h).json()["data"]["categories"]
    names = [c["name"] for c in cats]
    assert names.count("Hosting") == 1 and "hosting" not in names
    assert names.count("Software") == 1
    by_name = {c["name"]: c for c in cats}
    assert by_name["Hosting"] == {"name": "Hosting", "is_default": False, "in_use": True}
    assert by_name["Software"]["is_default"] is True and by_name["Software"]["in_use"] is True
    assert by_name["Payroll"]["in_use"] is False
    assert len(names) == len({n.casefold() for n in names})


def test_categories_are_per_workspace(client, db):
    _u, _s, h = _member(db)
    _x, _s2, other = _member(db)
    _txn(client, h, category="Secret vendor tools")
    names = [
        c["name"]
        for c in client.get(f"{BASE}/categories", headers=other).json()["data"]["categories"]
    ]
    assert "Secret vendor tools" not in names


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_categories_forbidden_for_mentor_investor(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/categories", headers=h).status_code == 403


def test_writes_resolve_to_one_spelling(client, db):
    _u, _s, h = _member(db)
    assert (
        _txn(client, h, category="  hosting   fees ").json()["data"]["category"] == "hosting fees"
    )
    # a later write in a different case lands on the first spelling
    assert _txn(client, h, category="HOSTING FEES").json()["data"]["category"] == "hosting fees"
    assert _expense(client, h, category="Hosting Fees").json()["data"]["category"] == "hosting fees"
    # defaults win over a stray spelling
    assert _txn(client, h, category="payroll").json()["data"]["category"] == "Payroll"
    # blank is "no category" on the ledger and a 422 on expenses and budgets
    assert _txn(client, h, category="   ").json()["data"]["category"] is None
    assert _expense(client, h, category="  ").status_code == 422
    assert (
        client.post(
            f"{BASE}/budgets",
            json={"category": " ", "period_month": MONTH, "limit_minor": 1},
            headers=h,
        ).status_code
        == 422
    )


def test_update_normalises_category(client, db):
    _u, _s, h = _member(db)
    _txn(client, h, category="Hosting")
    tid = _txn(client, h).json()["data"]["id"]
    out = client.patch(f"{BASE}/transactions/{tid}", json={"category": "hosting"}, headers=h)
    assert out.json()["data"]["category"] == "Hosting"
    eid = _expense(client, h).json()["data"]["id"]
    out = client.patch(f"{BASE}/expenses/{eid}", json={"category": "HOSTING"}, headers=h)
    assert out.json()["data"]["category"] == "Hosting"


def test_budget_for_hosting_matches_expense_typed_hosting(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Hosting", "period_month": MONTH, "limit_minor": 10_000},
        headers=h,
    )
    _expense(client, h, category="hosting", amount_minor=4_000)
    assert _budgets(client, h)["Hosting"]["spent_minor"] == 4_000


def test_category_filters_are_case_insensitive(client, db):
    _u, _s, h = _member(db)
    _txn(client, h, category="Hosting")
    _expense(client, h, category="Hosting")
    params = {"category": "hOsTiNg"}
    txns = client.get(f"{BASE}/transactions", params=params, headers=h).json()["data"]
    assert len(txns["transactions"]) == 2  # the manual row plus the outflow the expense posted
    exps = client.get(f"{BASE}/expenses", params=params, headers=h).json()["data"]
    assert len(exps["expenses"]) == 1


# ---- row 18 (D-FIN-3): budget actuals = every outflow ----------------------------------------- #


def test_budget_actuals_count_a_manual_outflow(client, db):
    """Regression: a manual outflow used to be invisible to budgets (only expenses counted)."""
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Equipment", "period_month": MONTH, "limit_minor": 10_000_000},
        headers=h,
    )
    _txn(
        client, h, category="Equipment", amount_minor=4_500_000, description="Walk test: equipment"
    )
    row = _budgets(client, h)["Equipment"]
    assert row["spent_minor"] == 4_500_000
    assert row["variance_minor"] == 4_500_000 - 10_000_000
    assert row["over_budget"] is False


def test_budget_actuals_sum_manual_expense_and_imported(client, db):
    from app.db.models.enums import TransactionDirection, TransactionSource
    from app.db.models.finance import Transaction

    _u, s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Payment fees", "period_month": MONTH, "limit_minor": 1_000},
        headers=h,
    )
    _txn(client, h, category="Payment fees", amount_minor=100)  # manual
    _expense(client, h, category="Payment fees", amount_minor=200)  # expense-posted outflow
    db.add(
        Transaction(
            startup_id=s.id,
            date=dt.date(2026, 3, 12),
            description="Paystack fee",
            category="Payment fees",
            amount_minor=300,
            currency="NGN",
            direction=TransactionDirection.outflow,
            source=TransactionSource.paystack,
        )
    )
    db.flush()
    assert _budgets(client, h)["Payment fees"]["spent_minor"] == 600


def test_inflows_and_other_months_do_not_count_as_spend(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Rent", "period_month": MONTH, "limit_minor": 1_000},
        headers=h,
    )
    _txn(client, h, category="Rent", direction="in", amount_minor=999)
    _txn(client, h, category="Rent", date="2026-04-01", amount_minor=999)
    _txn(client, h, category="Rent", date="2026-02-28", amount_minor=999)
    assert _budgets(client, h)["Rent"]["spent_minor"] == 0


def test_single_budget_get_uses_the_ledger_too(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/budgets",
        json={"category": "Travel", "period_month": MONTH, "limit_minor": 1_000},
        headers=h,
    ).json()["data"]
    _txn(client, h, category="Travel", amount_minor=250)
    got = client.get(f"{BASE}/budgets/{created['id']}", headers=h).json()["data"]
    assert got["spent_minor"] == 250


def test_draft_from_actuals_counts_manual_outflows(client, db):
    _u, _s, h = _member(db)
    _txn(client, h, category="Legal", date="2026-02-10", amount_minor=7_000)
    _txn(client, h, date="2026-02-11", amount_minor=500, category="  ")  # uncategorised -> Other
    out = client.post(f"{BASE}/budgets/draft-from-actuals", json={"period_month": MONTH}, headers=h)
    assert out.status_code == 200, out.text
    seeded = {b["category"]: b["limit_minor"] for b in out.json()["data"]["budgets"]}
    assert seeded == {"Legal": 7_000, "Other": 500}


# ---- row 19: unbudgeted categories + variance ------------------------------------------------- #


def test_unbudgeted_spend_shows_as_rows_with_null_limit(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Rent", "period_month": MONTH, "limit_minor": 10_000},
        headers=h,
    )
    _txn(client, h, category="Rent", amount_minor=12_000)
    _txn(client, h, category="Software", amount_minor=33_000)
    _txn(client, h, category="software", amount_minor=1_000)  # same category
    _txn(client, h, category=None, amount_minor=77)
    rows = client.get(f"{BASE}/budgets", params={"month": MONTH}, headers=h).json()["data"][
        "budgets"
    ]
    by = {r["category"]: r for r in rows}
    assert [r["category"] for r in rows] == [
        "Rent",
        "Software",
        "Uncategorized",
    ]  # budgeted first, then by spend
    soft = by["Software"]
    assert soft["spent_minor"] == 34_000
    assert soft["limit_minor"] is None and soft["id"] is None
    assert soft["unbudgeted"] is True and soft["status"] == "unbudgeted"
    assert soft["variance_minor"] is None and soft["variance_percent"] is None
    assert soft["percent_used"] is None and soft["over_budget"] is False
    assert soft["created_at"] is None and soft["updated_at"] is None
    assert by["Uncategorized"]["spent_minor"] == 77
    rent = by["Rent"]
    assert rent["unbudgeted"] is False and rent["id"]
    assert rent["variance_minor"] == 2_000 and rent["variance_percent"] == 20.0
    assert rent["over_budget"] is True and rent["status"] == "over"


@pytest.mark.parametrize(
    ("spent", "status"), [(0, "under"), (79, "under"), (80, "near"), (100, "near"), (101, "over")]
)
def test_budget_status_words(client, db, spent, status):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Rent", "period_month": MONTH, "limit_minor": 100},
        headers=h,
    )
    if spent:
        _txn(client, h, category="Rent", amount_minor=spent)
    assert _budgets(client, h)["Rent"]["status"] == status


def test_zero_limit_budget_has_no_percentages(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/budgets",
        json={"category": "Rent", "period_month": MONTH, "limit_minor": 0},
        headers=h,
    )
    _txn(client, h, category="Rent", amount_minor=5)
    row = _budgets(client, h)["Rent"]
    assert row["percent_used"] is None and row["variance_percent"] is None
    assert row["over_budget"] is True and row["status"] == "over"


def test_unbudgeted_rows_are_workspace_scoped(client, db):
    _u, _s, h = _member(db)
    _x, _s2, other = _member(db)
    _txn(client, other, category="Elsewhere", amount_minor=9)
    assert "Elsewhere" not in _budgets(client, h)


# ---- row 28: pagination ----------------------------------------------------------------------- #


def _seed(client, h, n):
    for i in range(n):
        assert (
            _txn(client, h, description=f"t{i}", date=f"2026-03-{10 + i % 5:02d}").status_code
            == 200
        )


def _walk(client, h, path, limit, **params):
    key = path
    seen, cursor, pages = [], None, 0
    while True:
        q = {"limit": limit, **params, **({"cursor": cursor} if cursor else {})}
        resp = client.get(f"{BASE}/{path}", params=q, headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        seen += [r["id"] for r in body["data"][key]]
        pages += 1
        cursor = body["meta"]["next_cursor"]
        assert body["meta"]["total_estimate"] is not None
        if cursor is None:
            return seen, pages, body["meta"]["total_estimate"]
        assert pages < 50


def test_transactions_paginate_without_gaps_or_repeats(client, db):
    _u, _s, h = _member(db)
    _seed(client, h, 11)
    seen, pages, total = _walk(client, h, "transactions", 4)
    assert pages == 3 and total == 11
    assert len(seen) == len(set(seen)) == 11
    everything = [
        t["id"]
        for t in client.get(f"{BASE}/transactions", params={"limit": 200}, headers=h).json()[
            "data"
        ]["transactions"]
    ]
    assert seen == everything  # same newest-first order as an unpaged read


def test_pagination_meta_and_defaults(client, db):
    _u, _s, h = _member(db)
    _seed(client, h, 3)
    body = client.get(f"{BASE}/transactions", params={"limit": 2}, headers=h).json()
    assert len(body["data"]["transactions"]) == 2
    assert body["meta"]["total_estimate"] == 3 and body["meta"]["next_cursor"]
    full = client.get(f"{BASE}/transactions", headers=h).json()
    assert len(full["data"]["transactions"]) == 3
    assert full["meta"] == {"next_cursor": None, "total_estimate": 3}


def test_total_reflects_filters(client, db):
    _u, _s, h = _member(db)
    for _ in range(3):
        _txn(client, h, direction="in")
    for _ in range(2):
        _txn(client, h)
    body = client.get(
        f"{BASE}/transactions", params={"direction": "in", "limit": 1}, headers=h
    ).json()
    assert body["meta"]["total_estimate"] == 3 and len(body["data"]["transactions"]) == 1


@pytest.mark.parametrize("limit", [0, 201, -1])
def test_limit_bounds_are_422(client, db, limit):
    _u, _s, h = _member(db)
    for path in ("transactions", "expenses", "invoices"):
        assert client.get(f"{BASE}/{path}", params={"limit": limit}, headers=h).status_code == 422


@pytest.mark.parametrize("path", ["transactions", "expenses", "invoices"])
@pytest.mark.parametrize("cursor", ["garbage", "e30=", "WyJ4Il0=", ""])
def test_bad_cursor_is_422_not_500(client, db, path, cursor):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/{path}", params={"cursor": cursor}, headers=h)
    if cursor == "":
        assert resp.status_code == 200  # empty cursor means first page
    else:
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["field_errors"][0]["field"] == "cursor"


def test_cursor_from_another_list_is_rejected(client, db):
    _u, _s, h = _member(db)
    _seed(client, h, 3)
    cur = client.get(f"{BASE}/transactions", params={"limit": 1}, headers=h).json()["meta"][
        "next_cursor"
    ]
    assert client.get(f"{BASE}/invoices", params={"cursor": cur}, headers=h).status_code == 422


def test_cursor_does_not_cross_workspaces(client, db):
    _u, _s, h = _member(db)
    _x, _s2, other = _member(db)
    _seed(client, h, 3)
    _txn(client, other, description="theirs")
    cur = client.get(f"{BASE}/transactions", params={"limit": 1}, headers=h).json()["meta"][
        "next_cursor"
    ]
    rows = client.get(f"{BASE}/transactions", params={"cursor": cur}, headers=other).json()["data"][
        "transactions"
    ]
    assert all(r["description"] == "theirs" for r in rows) or rows == []


def test_expenses_paginate(client, db):
    _u, _s, h = _member(db)
    for i in range(7):
        _expense(client, h, vendor=f"v{i}", expense_date=f"2026-03-{10 + i % 3:02d}")
    seen, pages, total = _walk(client, h, "expenses", 3)
    assert (pages, total, len(set(seen))) == (3, 7, 7)
    _s, _p, month_total = _walk(client, h, "expenses", 2, month="2026-04")
    assert month_total == 0


def _invoice(client, h, name):
    resp = client.post(
        f"{BASE}/invoices",
        json={
            "client_name": name,
            "client_email": "c@example.com",
            "line_items": [{"description": "Work", "quantity": 1, "unit_price_minor": 100}],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_invoices_paginate_and_total_follows_status_filter(client, db):
    _u, _s, h = _member(db)
    ids = [_invoice(client, h, f"c{i}") for i in range(5)]
    client.post(f"{BASE}/invoices/{ids[0]}/send", headers=h)
    seen, pages, total = _walk(client, h, "invoices", 2)
    assert (pages, total, len(set(seen))) == (3, 5, 5)
    _s, _p, drafts = _walk(client, h, "invoices", 2, status="draft")
    assert drafts == 4
    _s, _p, sent = _walk(client, h, "invoices", 2, status="sent")
    assert sent == 1


@pytest.mark.parametrize("role", [MembershipRole.accountant, MembershipRole.team_member])
def test_paged_lists_work_for_other_finance_roles(client, db, role):
    _f, startup, fh = _member(db)
    _seed(client, fh, 2)
    _u, _s, h = _member(db, role=role, startup=startup)
    body = client.get(f"{BASE}/transactions", params={"limit": 1}, headers=h).json()
    assert body["meta"]["total_estimate"] == 2
