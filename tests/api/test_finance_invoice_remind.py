from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.enums import MembershipRole
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from tests.api.test_finance import BASE, _member
from tests.api.test_invoices import _create, _create_sent


def _make_overdue(db, iid, **kw):
    inv = db.get(Invoice, iid)
    inv.due_on = datetime.now(UTC).date() - timedelta(days=3)
    for k, v in kw.items():
        setattr(inv, k, v)
    db.flush()
    return inv


@pytest.mark.parametrize(
    "role", [MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant]
)
def test_finance_roles_can_remind(client, db, role):
    _u, _s, h = _member(db, role=role)
    iid = _create_sent(client, h)
    _make_overdue(db, iid)
    resp = client.post(f"{BASE}/invoices/{iid}/remind", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "overdue"
    assert data["reminder_count"] == 1
    assert data["last_reminded_at"] is not None
    jobs = db.query(Job).filter_by(type="email.invoice_reminder").all()
    assert len(jobs) == 1 and jobs[0].payload == {"invoice_id": iid}


@pytest.mark.parametrize("role", [MembershipRole.mentor, MembershipRole.business_consultant])
def test_non_finance_roles_forbidden(client, db, role):
    _owner, startup, h = _member(db)
    iid = _create_sent(client, h)
    _make_overdue(db, iid)
    _other, _s, oh = _member(db, role=role, startup=startup)
    resp = client.post(f"{BASE}/invoices/{iid}/remind", headers=oh)
    assert resp.status_code == 403, resp.text


def test_cross_tenant_is_404(client, db):
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    iid = _create_sent(client, h1)
    _make_overdue(db, iid)
    resp = client.post(f"{BASE}/invoices/{iid}/remind", headers=h2)
    assert resp.status_code == 404, resp.text


def test_not_overdue_is_422(client, db):
    _u, _s, h = _member(db)
    draft = _create(client, h)
    sent = _create_sent(client, h)  # net_30: not past due
    for iid in (draft, sent):
        resp = client.post(f"{BASE}/invoices/{iid}/remind", headers=h)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_second_remind_within_24h_is_422(client, db):
    _u, _s, h = _member(db)
    iid = _create_sent(client, h)
    _make_overdue(db, iid)
    first = client.post(f"{BASE}/invoices/{iid}/remind", headers=h)
    assert first.status_code == 200, first.text
    second = client.post(f"{BASE}/invoices/{iid}/remind", headers=h)
    assert second.status_code == 422, second.text
    assert len(db.query(Job).filter_by(type="email.invoice_reminder").all()) == 1
