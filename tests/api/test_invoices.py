from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.enums import InvoiceStatus, InvoiceTerms, MembershipRole
from app.db.models.invoice import Invoice
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member

YEAR = datetime.now(UTC).year


def _payload(**overrides):
    body = {
        "client_name": "Acme Ltd",
        "client_email": "billing@acme-client.com",
        "line_items": [
            {"description": "Design", "quantity": 2, "unit_price_minor": 50000},
            {"description": "Build", "quantity": 1, "unit_price_minor": 125000},
        ],
        "tax_percent": 7.5,
        "terms": "net_30",
    }
    body.update(overrides)
    return body


def test_create_computes_totals_and_number(client, db):
    _u, _s, h = _member(db)
    # subtotal = 2*50000 + 125000 = 225000; tax = round(225000 * 7.5 / 100) = 16875
    resp = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["subtotal_minor"] == 225000
    assert data["tax_minor"] == 16875
    assert data["total_minor"] == 241875
    assert data["number"] == f"INV-{YEAR}-001"
    assert data["status"] == "draft"
    assert data["currency"] == "NGN"
    assert data["terms"] == "net_30"
    assert data["issued_on"] is None
    assert data["due_on"] is None
    assert data["transaction_id"] is None
    assert [ln["amount_minor"] for ln in data["line_items"]] == [100000, 125000]


def test_client_supplied_totals_are_ignored(client, db):
    _u, _s, h = _member(db)
    body = _payload(total_minor=1, subtotal_minor=1, tax_minor=1, number="INV-1999-777")
    resp = client.post(f"{BASE}/invoices", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_minor"] == 241875
    assert data["subtotal_minor"] == 225000
    assert data["number"] == f"INV-{YEAR}-001"


def test_numbers_increment_per_startup(client, db):
    _u, _s, h = _member(db)
    first = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    second = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    assert first.json()["data"]["number"] == f"INV-{YEAR}-001"
    assert second.json()["data"]["number"] == f"INV-{YEAR}-002"
    _u2, _s2, h2 = _member(db)  # other startup starts its own sequence
    other = client.post(f"{BASE}/invoices", json=_payload(), headers=h2)
    assert other.json()["data"]["number"] == f"INV-{YEAR}-001"


def test_list_newest_first_and_status_filter(client, db):
    _u, _s, h = _member(db)
    first = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    second = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    listed = client.get(f"{BASE}/invoices", headers=h)
    assert listed.status_code == 200, listed.text
    ids = [i["id"] for i in listed.json()["data"]["invoices"]]
    assert ids == [second.json()["data"]["id"], first.json()["data"]["id"]]

    drafts = client.get(f"{BASE}/invoices?status=draft", headers=h)
    assert len(drafts.json()["data"]["invoices"]) == 2
    paid = client.get(f"{BASE}/invoices?status=paid", headers=h)
    assert paid.status_code == 200, paid.text
    assert paid.json()["data"]["invoices"] == []
    bad = client.get(f"{BASE}/invoices?status=bogus", headers=h)
    assert bad.status_code == 422, bad.text


def test_get_one_and_cross_tenant_404(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    iid = created.json()["data"]["id"]
    got = client.get(f"{BASE}/invoices/{iid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["id"] == iid

    _u2, _s2, h2 = _member(db)
    foreign = client.get(f"{BASE}/invoices/{iid}", headers=h2)
    assert foreign.status_code == 404, foreign.text


def test_accountant_allowed(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    listed = client.get(f"{BASE}/invoices", headers=h)
    assert listed.status_code == 200, listed.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_rbac_forbidden(client, db, role):
    _f, startup, fh = _member(db)
    created = client.post(f"{BASE}/invoices", json=_payload(), headers=fh)
    iid = created.json()["data"]["id"]
    _u, _s, h = _member(db, role=role, startup=startup)
    listed = client.get(f"{BASE}/invoices", headers=h)
    posted = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    fetched = client.get(f"{BASE}/invoices/{iid}", headers=h)
    assert listed.status_code == 403, listed.text
    assert posted.status_code == 403, posted.text
    assert fetched.status_code == 403, fetched.text


def _item(**overrides):
    item = {"description": "x", "quantity": 1, "unit_price_minor": 100}
    item.update(overrides)
    return item


@pytest.mark.parametrize(
    "overrides",
    [
        {"line_items": [_item(unit_price_minor=-1)]},
        {"line_items": [_item(unit_price_minor=2_147_483_648)]},
        {"line_items": [_item(quantity=0)]},
        {"line_items": []},
        {"line_items": [_item() for _ in range(51)]},
        {"client_email": "not-an-email"},
        {"terms": "net_90"},
        {"tax_percent": 101},
        {"tax_percent": -1},
    ],
    ids=[
        "negative-price",
        "over-int32-price",
        "zero-quantity",
        "empty-items",
        "too-many-items",
        "bad-email",
        "bad-terms",
        "tax-over-100",
        "tax-negative",
    ],
)
def test_validation_422(client, db, overrides):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/invoices", json=_payload(**overrides), headers=h)
    assert resp.status_code == 422, resp.text


def test_overflowing_total_is_domain_422(client, db):
    # Each line is individually valid (<= int32) but the subtotal overflows the INTEGER column.
    _u, _s, h = _member(db)
    items = [_item(unit_price_minor=2_000_000_000), _item(unit_price_minor=2_000_000_000)]
    resp = client.post(f"{BASE}/invoices", json=_payload(line_items=items), headers=h)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Subtotal fits, but subtotal + tax overflows.
    near = [_item(unit_price_minor=2_100_000_000)]
    resp2 = client.post(
        f"{BASE}/invoices", json=_payload(line_items=near, tax_percent=10), headers=h
    )
    assert resp2.status_code == 422, resp2.text
    assert resp2.json()["error"]["code"] == "VALIDATION_ERROR"


def test_number_retry_recovers_from_stale_number(client, db, monkeypatch):
    # Simulate the concurrent-create race: the first number handed out already exists.
    from app.services.finance import invoices as svc

    _u, _s, h = _member(db)
    seed = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    assert seed.json()["data"]["number"] == f"INV-{YEAR}-001"

    real = svc._next_number
    calls = {"n": 0}

    def stale_then_real(db_, *, startup_id, year):
        calls["n"] += 1
        if calls["n"] == 1:
            return f"INV-{year}-001"  # collides with the seed row
        return real(db_, startup_id=startup_id, year=year)

    monkeypatch.setattr(svc, "_next_number", stale_then_real)
    resp = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["number"] == f"INV-{YEAR}-002"
    assert calls["n"] == 2

    # The failed savepoint must not have poisoned the session: a follow-up read works.
    listed = client.get(f"{BASE}/invoices", headers=h)
    assert len(listed.json()["data"]["invoices"]) == 2


@pytest.mark.parametrize(
    ("subtotal", "pct", "expected_tax"),
    [
        (10, 5, 1),  # 0.5 -> 1 (banker's rounding would give 0)
        (30, 5, 2),  # 1.5 -> 2
        (50, 5, 3),  # 2.5 -> 3 (banker's rounding would give 2)
        (225000, 7.5, 16875),  # exact, no rounding
    ],
)
def test_tax_rounds_half_up(client, db, subtotal, pct, expected_tax):
    _u, _s, h = _member(db)
    body = _payload(
        line_items=[_item(unit_price_minor=subtotal)],
        tax_percent=pct,
    )
    resp = client.post(f"{BASE}/invoices", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["subtotal_minor"] == subtotal
    assert data["tax_minor"] == expected_tax
    assert data["total_minor"] == subtotal + expected_tax


def test_overdue_derivation_and_filter(client, db):
    _u, startup, h = _member(db)
    today = datetime.now(UTC).date()
    overdue = Invoice(
        startup_id=startup.id,
        number=f"INV-{YEAR}-901",
        client_name="Late Payer",
        client_email="late@acme-client.com",
        line_items=[{"description": "x", "quantity": 1, "unit_price_minor": 100}],
        subtotal_minor=100,
        tax_percent=0,
        tax_minor=0,
        total_minor=100,
        currency="NGN",
        terms=InvoiceTerms.net_30,
        status=InvoiceStatus.sent,
        issued_on=today - timedelta(days=35),
        due_on=today - timedelta(days=5),
    )
    db.add(overdue)
    db.flush()
    draft = client.post(f"{BASE}/invoices", json=_payload(), headers=h)
    draft_id = draft.json()["data"]["id"]
    overdue_id = str(overdue.id)

    listed = client.get(f"{BASE}/invoices", headers=h)
    by_id = {i["id"]: i for i in listed.json()["data"]["invoices"]}
    assert by_id[overdue_id]["status"] == "overdue"
    assert by_id[draft_id]["status"] == "draft"

    only_overdue = client.get(f"{BASE}/invoices?status=overdue", headers=h)
    overdue_ids = [i["id"] for i in only_overdue.json()["data"]["invoices"]]
    assert overdue_ids == [overdue_id]

    only_sent = client.get(f"{BASE}/invoices?status=sent", headers=h)
    sent_ids = [i["id"] for i in only_sent.json()["data"]["invoices"]]
    assert overdue_id not in sent_ids

    one = client.get(f"{BASE}/invoices/{overdue_id}", headers=h)
    assert one.status_code == 200, one.text
    assert one.json()["data"]["status"] == "overdue"


def test_cross_tenant_list_exclusion(client, db):
    _a, _sa, ha = _member(db)
    created = client.post(f"{BASE}/invoices", json=_payload(), headers=ha)
    iid = created.json()["data"]["id"]
    _b, _sb, hb = _member(db)
    listed = client.get(f"{BASE}/invoices", headers=hb)
    assert listed.status_code == 200, listed.text
    ids = [i["id"] for i in listed.json()["data"]["invoices"]]
    assert iid not in ids


def _create(client, h, **overrides):
    resp = client.post(f"{BASE}/invoices", json=_payload(**overrides), headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_patch_draft_recomputes_totals(client, db):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    resp = client.patch(
        f"{BASE}/invoices/{iid}",
        json={"line_items": [_item(unit_price_minor=1000, quantity=3)], "tax_percent": 10},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["subtotal_minor"] == 3000
    assert data["tax_minor"] == 300
    assert data["total_minor"] == 3300


def test_patch_sent_invoice_rejected_but_auto_remind_allowed(client, db):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    sent = client.post(f"{BASE}/invoices/{iid}/send", headers=h)
    assert sent.status_code == 200, sent.text

    blocked = client.patch(f"{BASE}/invoices/{iid}", json={"client_name": "Nope"}, headers=h)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["error"]["code"] == "VALIDATION_ERROR"

    toggled = client.patch(f"{BASE}/invoices/{iid}", json={"auto_remind": False}, headers=h)
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["data"]["auto_remind"] is False
    assert toggled.json()["data"]["status"] == "sent"


@pytest.mark.parametrize(
    "field", ["client_name", "client_email", "line_items", "tax_percent", "currency", "terms"]
)
def test_patch_explicit_null_rejected(client, db, field):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    resp = client.patch(f"{BASE}/invoices/{iid}", json={field: None}, headers=h)
    assert resp.status_code == 422, resp.text


def test_patch_invalid_terms_and_empty_items_422(client, db):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    bad_terms = client.patch(f"{BASE}/invoices/{iid}", json={"terms": "net_90"}, headers=h)
    empty = client.patch(f"{BASE}/invoices/{iid}", json={"line_items": []}, headers=h)
    assert bad_terms.status_code == 422, bad_terms.text
    assert empty.status_code == 422, empty.text


def test_delete_draft_ok_and_sent_rejected(client, db):
    _u, _s, h = _member(db)
    draft_id = _create(client, h)
    sent_id = _create(client, h)
    client.post(f"{BASE}/invoices/{sent_id}/send", headers=h)

    deleted = client.delete(f"{BASE}/invoices/{draft_id}", headers=h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"deleted": True}
    gone = client.get(f"{BASE}/invoices/{draft_id}", headers=h)
    assert gone.status_code == 404, gone.text

    blocked = client.delete(f"{BASE}/invoices/{sent_id}", headers=h)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["error"]["code"] == "VALIDATION_ERROR"


def test_send_sets_sent_and_enqueues_once(client, db):
    from app.db.models.job import Job

    _u, startup, h = _member(db)
    iid = _create(client, h, terms="net_15")
    resp = client.post(f"{BASE}/invoices/{iid}/send", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    today = datetime.now(UTC).date()
    assert data["status"] == "sent"
    assert data["issued_on"] == today.isoformat()
    assert data["due_on"] == (today + timedelta(days=15)).isoformat()
    jobs = db.query(Job).filter_by(type="email.invoice_sent", startup_id=startup.id).all()
    assert len(jobs) == 1
    assert jobs[0].payload == {"invoice_id": iid}

    again = client.post(f"{BASE}/invoices/{iid}/send", headers=h)
    assert again.status_code == 422, again.text
    assert db.query(Job).filter_by(type="email.invoice_sent", startup_id=startup.id).count() == 1


def test_cross_tenant_patch_delete_send_404(client, db):
    _a, _sa, ha = _member(db)
    iid = _create(client, ha)
    _b, _sb, hb = _member(db)
    patched = client.patch(f"{BASE}/invoices/{iid}", json={"client_name": "X"}, headers=hb)
    deleted = client.delete(f"{BASE}/invoices/{iid}", headers=hb)
    sent = client.post(f"{BASE}/invoices/{iid}/send", headers=hb)
    assert patched.status_code == 404, patched.text
    assert deleted.status_code == 404, deleted.text
    assert sent.status_code == 404, sent.text
    intact = client.get(f"{BASE}/invoices/{iid}", headers=ha)
    assert intact.json()["data"]["status"] == "draft"
    assert intact.json()["data"]["client_name"] == "Acme Ltd"


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_lifecycle_rbac_forbidden(client, db, role):
    _f, startup, fh = _member(db)
    iid = _create(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    patched = client.patch(f"{BASE}/invoices/{iid}", json={"auto_remind": False}, headers=h)
    deleted = client.delete(f"{BASE}/invoices/{iid}", headers=h)
    sent = client.post(f"{BASE}/invoices/{iid}/send", headers=h)
    assert patched.status_code == 403, patched.text
    assert deleted.status_code == 403, deleted.text
    assert sent.status_code == 403, sent.text
