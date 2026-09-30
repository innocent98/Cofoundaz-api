import io
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.expense import Expense
from app.services.finance import expenses as expense_svc
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))


def _make(client, h):
    resp = client.post(
        f"{BASE}/expenses",
        json={
            "vendor": "AWS",
            "category": "Infrastructure",
            "expense_date": "2026-03-10",
            "amount_minor": 250_000,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _upload(client, h, expense_id, *, name="r.png", ct="image/png", body=b"\x89PNG-bytes"):
    return client.post(
        f"{BASE}/expenses/{expense_id}/receipt",
        files={"file": (name, io.BytesIO(body), ct)},
        headers=h,
    )


def _files(tmp_path):
    return [p for p in Path(tmp_path).rglob("*") if p.is_file()]


def test_upload_png_sets_receipt_and_stores_file(client, db, tmp_path):
    _u, _s, h = _member(db)
    eid = _make(client, h)
    resp = _upload(client, h, eid)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["has_receipt"] is True
    assert data["receipt_url"]
    stored = _files(tmp_path)
    assert len(stored) == 1
    assert stored[0].suffix == ".png"
    assert stored[0].read_bytes() == b"\x89PNG-bytes"


def test_upload_pdf_accepted(client, db, tmp_path):
    _u, _s, h = _member(db)
    eid = _make(client, h)
    resp = _upload(client, h, eid, name="r.pdf", ct="application/pdf", body=b"%PDF-1.4")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_receipt"] is True
    stored = _files(tmp_path)
    assert [p.suffix for p in stored] == [".pdf"]


def test_disallowed_content_type_422_and_no_receipt(client, db, tmp_path):
    _u, s, h = _member(db)
    eid = _make(client, h)
    resp = _upload(client, h, eid, name="r.txt", ct="text/plain", body=b"hi")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    assert exp.receipt_url is None
    assert exp.receipt_key is None
    assert _files(tmp_path) == []


def test_oversize_422_and_no_receipt(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(expense_svc, "MAX_RECEIPT_BYTES", 8)
    _u, s, h = _member(db)
    eid = _make(client, h)
    resp = _upload(client, h, eid, body=b"x" * 9)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    assert exp.receipt_key is None
    assert _files(tmp_path) == []


def test_replace_deletes_old_file(client, db, tmp_path):
    _u, s, h = _member(db)
    eid = _make(client, h)
    first = _upload(client, h, eid, body=b"first")
    assert first.status_code == 200, first.text
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    old_key = exp.receipt_key
    assert old_key is not None
    assert (Path(tmp_path) / old_key).is_file()

    second = _upload(client, h, eid, name="r.pdf", ct="application/pdf", body=b"second")
    assert second.status_code == 200, second.text
    db.refresh(exp)
    assert exp.receipt_key != old_key
    assert not (Path(tmp_path) / old_key).exists()
    assert (Path(tmp_path) / exp.receipt_key).is_file()
    assert len(_files(tmp_path)) == 1


def test_delete_receipt_clears_and_removes_file(client, db, tmp_path):
    _u, s, h = _member(db)
    eid = _make(client, h)
    uploaded = _upload(client, h, eid)
    assert uploaded.status_code == 200, uploaded.text
    resp = client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["has_receipt"] is False
    assert data["receipt_url"] is None
    exp = db.query(Expense).filter_by(startup_id=s.id).one()
    db.refresh(exp)
    assert exp.receipt_key is None
    assert _files(tmp_path) == []


def test_delete_receipt_when_none_is_noop_200(client, db):
    _u, _s, h = _member(db)
    eid = _make(client, h)
    resp = client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["has_receipt"] is False


def test_deleting_expense_removes_receipt_file(client, db, tmp_path):
    _u, _s, h = _member(db)
    eid = _make(client, h)
    uploaded = _upload(client, h, eid)
    assert uploaded.status_code == 200, uploaded.text
    assert len(_files(tmp_path)) == 1
    resp = client.delete(f"{BASE}/expenses/{eid}", headers=h)
    assert resp.status_code == 200, resp.text
    assert _files(tmp_path) == []


def test_accountant_allowed(client, db):
    _f, startup, fh = _member(db)
    eid = _make(client, fh)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    up = _upload(client, h, eid)
    rm = client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert (up.status_code, rm.status_code) == (200, 200)


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_rbac_forbidden(client, db, role):
    _f, startup, fh = _member(db)
    eid = _make(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    up = _upload(client, h, eid)
    rm = client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert (up.status_code, rm.status_code) == (403, 403)


def test_cross_tenant_404(client, db, tmp_path):
    _u1, _s1, h1 = _member(db)
    eid = _make(client, h1)
    _u2, _s2, h2 = _member(db)
    up = _upload(client, h2, eid)
    rm = client.delete(f"{BASE}/expenses/{eid}/receipt", headers=h2)
    assert (up.status_code, rm.status_code) == (404, 404)
    assert _files(tmp_path) == []


def test_upload_requires_auth(client):
    resp = client.post(
        f"{BASE}/expenses/00000000-0000-0000-0000-000000000000/receipt",
        files={"file": ("r.png", io.BytesIO(b"x"), "image/png")},
    )
    assert resp.status_code in (401, 403)
