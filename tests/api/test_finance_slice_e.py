"""Finance slice (e): runway reads model rows (D-FIN-12), invoice client/notes/payment details/
resend, XLSX exports, and the missing-receipt self-heal (D2)."""

import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest

from app.core.config import settings
from app.db.models.enums import (
    FinancialModelStatus,
    MembershipRole,
    TransactionDirection,
    TransactionSource,
)
from app.db.models.expense import Expense
from app.db.models.finance import Transaction
from app.db.models.financial_model import FinancialModel
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.sales import SalesAccount
from app.services.finance import invoices as invoice_svc
from app.services.finance.runway_math import _add_months, monthly_rows
from app.services.finance.xlsx import _col, build_xlsx, sheet_name
from tests.api.test_finance import BASE, FORBIDDEN_ROLES, _member
from tests.api.test_invoices import _create, _create_sent, _payload

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TODAY = datetime.now(UTC).date()


def _seed_ledger(db, startup_id):
    for amount, direction, cat in (
        (50_000_000, TransactionDirection.inflow, "Funding"),
        (6_000_000, TransactionDirection.inflow, "Revenue"),
        (9_000_000, TransactionDirection.outflow, "Rent"),
    ):
        db.add(
            Transaction(
                startup_id=startup_id,
                date=TODAY,
                description=f"seed {cat}",
                category=cat,
                amount_minor=amount,
                currency="NGN",
                direction=direction,
                source=TransactionSource.manual,
            )
        )
    db.flush()


def _model(db, startup_id, *, hires=(), one_offs=(), status=FinancialModelStatus.complete):
    m = FinancialModel(
        startup_id=startup_id,
        status=status,
        horizon_months=36,
        currency="NGN",
        assumptions={"hiring_plan": list(hires), "one_offs": list(one_offs)},
        pnl={
            "months": ["2026-11", "2026-12"],
            "rows": [{"label": "Revenue", "values": [100, 250]}],
        },
        cash_flow={"months": ["2026-11", "2026-12"], "rows": [{"label": "Net", "values": [-5, 7]}]},
        balance_sheet={
            "months": ["2026-11", "2026-12"],
            "rows": [{"label": "Cash", "values": [1, 2]}],
        },
    )
    db.add(m)
    db.flush()
    return m


# ------------------------------------------------------------------ runway (D-FIN-12)


@pytest.mark.parametrize("field", ["hiring_plan", "one_offs", "capex"])
def test_put_assumptions_rejects_model_owned_rows_with_a_clear_422(client, db, field):
    _u, _s, h = _member(db)
    resp = client.put(f"{BASE}/runway/assumptions", json={field: [{"x": 1}]}, headers=h)
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["field_errors"][0]["field"] == field
    assert "Financial Model" in err["field_errors"][0]["message"]


def test_put_assumptions_rejects_unknown_fields_and_applies_nothing(client, db):
    _u, _s, h = _member(db)
    resp = client.put(
        f"{BASE}/runway/assumptions",
        json={"mom_growth_percent": 9, "burn_rate": 1},
        headers=h,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["field_errors"][0]["field"] == "burn_rate"
    after = client.get(f"{BASE}/runway", headers=h).json()["data"]["assumptions"]
    assert after["mom_growth_percent"] == 0  # the valid half was not applied either


def test_runway_without_a_model_reports_empty_model_rows(client, db):
    _u, _s, h = _member(db)
    rows = client.get(f"{BASE}/runway", headers=h).json()["data"]["model_rows"]
    assert rows == {"model_id": None, "href": "/finance/model", "hiring_plan": [], "one_offs": []}


def test_runway_projection_reads_the_models_hiring_and_one_off_rows(client, db):
    _u, s, h = _member(db)
    _seed_ledger(db, s.id)
    base = client.get(f"{BASE}/runway", headers=h).json()["data"]["scenarios"]["base"]["by_month"]
    m1, m2 = _add_months(TODAY, 1), _add_months(TODAY, 2)
    model = _model(
        db,
        s.id,
        hires=[{"role": "Engineer", "start_month": m1, "monthly_cost_minor": 400_000}],
        one_offs=[{"label": "Laptop", "month": m2, "amount_minor": 250_000}],
    )
    data = client.get(f"{BASE}/runway", headers=h).json()["data"]
    assert data["model_rows"]["model_id"] == str(model.id)
    assert data["model_rows"]["hiring_plan"][0]["role"] == "Engineer"
    assert data["model_rows"]["one_offs"][0]["label"] == "Laptop"
    got = data["scenarios"]["base"]["by_month"]
    assert got[0]["net"] == base[0]["net"] - 400_000
    assert got[1]["net"] == base[1]["net"] - 400_000 - 250_000  # hire continues, one-off lands
    assert got[2]["net"] == base[2]["net"] - 400_000  # the one-off is a single month


def test_a_generating_model_does_not_blank_the_runway(client, db):
    _u, s, h = _member(db)
    m1 = _add_months(TODAY, 1)
    done = _model(db, s.id, hires=[{"role": "A", "start_month": m1, "monthly_cost_minor": 1}])
    _model(db, s.id, status=FinancialModelStatus.generating)
    rows = client.get(f"{BASE}/runway", headers=h).json()["data"]["model_rows"]
    assert rows["model_id"] == str(done.id)


def test_monthly_rows_placement_rules():
    first = _add_months(TODAY, 1)
    late = _add_months(TODAY, 3)
    far = _add_months(TODAY, 40)
    hires, spend = monthly_rows(
        [
            {"start_month": "2000-01", "monthly_cost_minor": 10},  # before the window: from month 1
            {"start_month": late, "monthly_cost_minor": 5},
            {"start_month": far, "monthly_cost_minor": 99},  # beyond the horizon: ignored
        ],
        [
            {"month": first, "amount_minor": 7},
            {"month": "2000-01", "amount_minor": 99},  # in the past: ignored
        ],
        today=TODAY,
    )
    assert hires[:4] == [10, 10, 15, 15] and hires[-1] == 15
    assert spend[0] == 7 and sum(spend) == 7


# ------------------------------------------------------------------ invoices


def _account(db, startup_id, name="Client Co"):
    a = SalesAccount(startup_id=startup_id, name=name)
    db.add(a)
    db.flush()
    return a


PAY = {
    "bank_name": "GTBank",
    "account_name": "Acme Ltd",
    "account_number": "0123456789",
    "instructions": "Use the invoice number as the reference.",
}


def test_invoice_links_a_client_notes_and_payment_details(client, db):
    _u, s, h = _member(db)
    acc = _account(db, s.id)
    resp = client.post(
        f"{BASE}/invoices",
        json=_payload(
            account_id=str(acc.id), notes="  Thanks!\nPay within 30 days.  ", payment_details=PAY
        ),
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["account_id"] == str(acc.id) and d["account_name"] == "Client Co"
    assert d["notes"] == "Thanks!\nPay within 30 days."
    assert d["payment_details"] == PAY
    got = client.get(f"{BASE}/invoices/{d['id']}", headers=h).json()["data"]
    assert got["payment_details"] == PAY and got["account_name"] == "Client Co"


def test_invoice_defaults_have_no_client_notes_or_payment_details(client, db):
    _u, _s, h = _member(db)
    d = client.post(f"{BASE}/invoices", json=_payload(notes="   "), headers=h).json()["data"]
    assert d["account_id"] is None and d["account_name"] is None
    assert d["notes"] is None and d["payment_details"] is None and d["last_sent_at"] is None


def test_invoice_rejects_another_workspaces_or_unknown_client(client, db):
    _u, _s, h = _member(db)
    _u2, s2, _h2 = _member(db)
    other = _account(db, s2.id, "Other Co")
    for bad in (str(other.id), "00000000-0000-0000-0000-000000000000"):
        resp = client.post(f"{BASE}/invoices", json=_payload(account_id=bad), headers=h)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["field_errors"][0]["field"] == "account_id"
    iid = _create(client, h)
    resp = client.patch(f"{BASE}/invoices/{iid}", json={"account_id": str(other.id)}, headers=h)
    assert resp.status_code == 422, resp.text


def test_draft_patch_sets_and_clears_the_extras(client, db):
    _u, s, h = _member(db)
    acc = _account(db, s.id)
    iid = _create(client, h)
    set_ = client.patch(
        f"{BASE}/invoices/{iid}",
        json={"account_id": str(acc.id), "notes": "n", "payment_details": PAY},
        headers=h,
    ).json()["data"]
    assert set_["account_id"] == str(acc.id) and set_["notes"] == "n"
    cleared = client.patch(
        f"{BASE}/invoices/{iid}",
        json={"account_id": None, "notes": None, "payment_details": None},
        headers=h,
    ).json()["data"]
    assert cleared["account_id"] is None and cleared["notes"] is None
    assert cleared["payment_details"] is None


def test_issued_invoice_extras_are_frozen(client, db):
    _u, _s, h = _member(db)
    iid = _create_sent(client, h)
    resp = client.patch(f"{BASE}/invoices/{iid}", json={"notes": "late edit"}, headers=h)
    assert resp.status_code == 422, resp.text


def test_payment_details_validation(client, db):
    _u, _s, h = _member(db)
    for bad in ({**PAY, "extra": 1}, {**PAY, "bank_name": ""}, {"bank_name": "x"}):
        resp = client.post(f"{BASE}/invoices", json=_payload(payment_details=bad), headers=h)
        assert resp.status_code == 422, resp.text


def test_deleting_the_account_keeps_the_invoice(client, db):
    _u, s, h = _member(db)
    acc = _account(db, s.id)
    iid = client.post(f"{BASE}/invoices", json=_payload(account_id=str(acc.id)), headers=h).json()[
        "data"
    ]["id"]
    db.delete(acc)
    db.flush()
    db.expire_all()
    got = client.get(f"{BASE}/invoices/{iid}", headers=h).json()["data"]
    assert got["account_id"] is None and got["account_name"] is None


def test_pdf_and_email_carry_notes_and_payment_details(client, db):
    from app.services.finance.invoice_pdf import render_invoice_pdf
    from app.worker.handlers.invoice_email import render_invoice_email

    _u, s, h = _member(db)
    iid = client.post(
        f"{BASE}/invoices",
        json=_payload(notes="Thanks <b>a lot</b>", payment_details=PAY),
        headers=h,
    ).json()["data"]["id"]
    inv = db.get(Invoice, iid)
    pdf = render_invoice_pdf(inv, "Acme")
    assert b"GTBank" in pdf and b"0123456789" in pdf and b"Notes" in pdf
    html = render_invoice_email(inv, "Acme")
    assert "GTBank" in html and "0123456789" in html
    assert "<b>a lot</b>" not in html and "&lt;b&gt;a lot&lt;/b&gt;" in html  # escaped


def test_pdf_wraps_long_notes(client, db):
    from app.services.finance.invoice_pdf import render_invoice_pdf

    _u, _s, h = _member(db)
    notes = " ".join(["word"] * 80) + "\nsecond line"
    iid = client.post(f"{BASE}/invoices", json=_payload(notes=notes), headers=h).json()["data"][
        "id"
    ]
    assert b"second line" in render_invoice_pdf(db.get(Invoice, iid), "Acme")


def test_resend_queues_the_invoice_email_again(client, db, monkeypatch):
    _u, _s, h = _member(db)
    iid = _create_sent(client, h)
    first_jobs = db.query(Job).filter_by(type="email.invoice_sent").count()
    # backdate the first send so the cooldown has passed
    inv = db.get(Invoice, iid)
    assert inv.last_sent_at is not None
    inv.last_sent_at = datetime.now(UTC) - timedelta(minutes=5)
    db.flush()
    resp = client.post(f"{BASE}/invoices/{iid}/resend", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "sent"
    assert db.query(Job).filter_by(type="email.invoice_sent").count() == first_jobs + 1
    again = client.post(f"{BASE}/invoices/{iid}/resend", headers=h)
    assert again.status_code == 429, again.text
    err = again.json()["error"]
    assert err["code"] == "RESEND_TOO_SOON" and "Try again in" in err["message"]
    assert db.query(Job).filter_by(type="email.invoice_sent").count() == first_jobs + 1


def test_resend_works_on_an_overdue_invoice_and_refuses_draft_and_paid(client, db):
    _u, _s, h = _member(db)
    draft = _create(client, h)
    r = client.post(f"{BASE}/invoices/{draft}/resend", headers=h)
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "status"

    sent = _create_sent(client, h)
    inv = db.get(Invoice, sent)
    inv.due_on = date.today() - timedelta(days=4)
    inv.last_sent_at = None
    db.flush()
    r = client.post(f"{BASE}/invoices/{sent}/resend", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "overdue"

    paid = _create_sent(client, h)
    client.post(f"{BASE}/invoices/{paid}/mark-paid", headers=h)
    r = client.post(f"{BASE}/invoices/{paid}/resend", headers=h)
    assert r.status_code == 422, r.text


def test_resend_is_finance_roles_only_and_tenant_scoped(client, db):
    _u, startup, h = _member(db)
    iid = _create_sent(client, h)
    db.get(Invoice, iid).last_sent_at = None
    db.flush()
    for role in FORBIDDEN_ROLES:
        _m, _s, mh = _member(db, role=role, startup=startup)
        assert client.post(f"{BASE}/invoices/{iid}/resend", headers=mh).status_code == 403
    _x, _xs, xh = _member(db)
    assert client.post(f"{BASE}/invoices/{iid}/resend", headers=xh).status_code == 404
    _a, _as, ah = _member(db, role=MembershipRole.accountant, startup=startup)
    assert client.post(f"{BASE}/invoices/{iid}/resend", headers=ah).status_code == 200


def test_resend_has_its_own_rate_limit(client, db, monkeypatch):
    from app.core import rate_limit
    from app.main import app

    monkeypatch.setattr(invoice_svc, "RESEND_COOLDOWN_SECONDS", 0)
    _u, _s, h = _member(db)
    iid = _create_sent(client, h)
    app.state.limiter.enabled = True
    rate_limit.limiter.reset()
    try:
        codes = [
            client.post(f"{BASE}/invoices/{iid}/resend", headers=h).status_code for _ in range(12)
        ]
    finally:
        app.state.limiter.enabled = False
        rate_limit.limiter.reset()
    assert codes[:10] == [200] * 10
    assert 429 in codes[10:]


# ------------------------------------------------------------------ XLSX


def _cells(xlsx: bytes, sheet: int = 1) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        assert z.testzip() is None
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    out = []
    for row in root.iterfind(".//m:row", NS):
        vals = []
        for c in row.iterfind("m:c", NS):
            t = c.find(".//m:t", NS)
            v = c.find("m:v", NS)
            vals.append(t.text if t is not None else (v.text if v is not None else ""))
        out.append(vals)
    return out


def _sheet_names(xlsx: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        root = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in root.iterfind(".//m:sheet", NS)]


def test_transactions_export_is_a_valid_workbook_with_the_ledger(client, db):
    _u, s, h = _member(db)
    _seed_ledger(db, s.id)
    resp = client.get(f"{BASE}/transactions/export.xlsx", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "private, no-store"
    assert _sheet_names(resp.content) == ["Transactions"]
    rows = _cells(resp.content)
    assert rows[0] == [
        "Date",
        "Description",
        "Category",
        "Direction",
        "Amount",
        "Currency",
        "Source",
    ]
    assert len(rows) == 4
    funding = next(r for r in rows if r[2] == "Funding")
    assert funding[4] == "500000" and funding[3] == "in" and funding[5] == "NGN"


def test_transactions_export_honours_the_list_filters_and_is_tenant_scoped(client, db):
    _u, s, h = _member(db)
    _seed_ledger(db, s.id)
    _u2, s2, h2 = _member(db)
    _seed_ledger(db, s2.id)
    rows = _cells(client.get(f"{BASE}/transactions/export.xlsx?direction=out", headers=h).content)
    assert [r[2] for r in rows[1:]] == ["Rent"]
    future = (TODAY + timedelta(days=400)).isoformat()
    empty = _cells(
        client.get(f"{BASE}/transactions/export.xlsx?date_from={future}", headers=h).content
    )
    assert len(empty) == 1  # header only


def test_transactions_export_never_evaluates_formulas(client, db):
    _u, s, h = _member(db)
    client.post(
        f"{BASE}/transactions",
        json={
            "date": TODAY.isoformat(),
            "description": '=HYPERLINK("http://evil","x")',
            "amount_minor": 100,
            "direction": "out",
        },
        headers=h,
    )
    content = client.get(f"{BASE}/transactions/export.xlsx", headers=h).content
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet and 't="inlineStr"' in sheet
    assert "=HYPERLINK" in _cells(content)[1][1]


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_exports_are_finance_roles_only(client, db, role):
    _u, startup, h = _member(db)
    model = _model(db, startup.id)
    _m, _s, mh = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/transactions/export.xlsx", headers=mh).status_code == 403
    assert client.get(f"{BASE}/model/{model.id}/export.xlsx", headers=mh).status_code == 403


def test_transactions_export_row_cap(client, db, monkeypatch):
    from app.services.finance import exports

    monkeypatch.setattr(exports, "MAX_EXPORT_ROWS", 2)
    _u, s, h = _member(db)
    _seed_ledger(db, s.id)
    resp = client.get(f"{BASE}/transactions/export.xlsx", headers=h)
    assert resp.status_code == 422 and "Narrow the date range" in resp.text


def test_model_export_has_one_sheet_per_statement_in_naira(client, db):
    _u, s, h = _member(db)
    model = _model(db, s.id)
    resp = client.get(f"{BASE}/model/{model.id}/export.xlsx", headers=h)
    assert resp.status_code == 200, resp.text
    assert _sheet_names(resp.content) == ["P&L", "Cash flow", "Balance sheet"]
    assert _cells(resp.content, 1) == [["Line", "2026-11", "2026-12"], ["Revenue", "1", "2.5"]]


def test_model_export_needs_a_finished_model_in_this_workspace(client, db):
    _u, s, h = _member(db)
    pending = _model(db, s.id, status=FinancialModelStatus.generating)
    r = client.get(f"{BASE}/model/{pending.id}/export.xlsx", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_NOT_READY"
    _u2, s2, _h2 = _member(db)
    other = _model(db, s2.id)
    assert client.get(f"{BASE}/model/{other.id}/export.xlsx", headers=h).status_code == 404


def test_xlsx_writer_helpers():
    assert [_col(i) for i in (0, 25, 26, 27, 701, 702)] == ["A", "Z", "AA", "AB", "ZZ", "AAA"]
    taken: set[str] = set()
    assert sheet_name("a/b:c*", taken) == "a b c"
    assert sheet_name("A B C", taken) == "A B C (2)"
    assert len(sheet_name("x" * 50, taken)) == 31
    book = build_xlsx([("S", [["h"], ["bad\x00char\x0b", 1.5, None, 3]])])
    assert _cells(book) == [["h"], ["badchar", "1.5", "3"]]
    assert _sheet_names(build_xlsx([])) == ["Sheet"]


# ------------------------------------------------------------------ D2: missing receipt file


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://api.test")
    return tmp_path


def _expense_with_receipt(client, h):
    eid = client.post(
        f"{BASE}/expenses",
        json={
            "vendor": "Notion",
            "category": "Software",
            "expense_date": "2026-03-10",
            "amount_minor": 1000,
        },
        headers=h,
    ).json()["data"]["id"]
    up = client.post(
        f"{BASE}/expenses/{eid}/receipt",
        files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 16), "image/png")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    return eid, up.json()["data"]


def test_receipt_with_a_missing_file_answers_410_and_clears_has_receipt(client, db, local_storage):
    _u, _s, h = _member(db)
    eid, data = _expense_with_receipt(client, h)
    assert data["has_receipt"] is True
    ok = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert ok.status_code == 200
    for f in local_storage.rglob("*.png"):
        f.unlink()  # the file vanishes (legacy upload, wiped volume)
    gone = client.get(f"{BASE}/expenses/{eid}/receipt", headers=h)
    assert gone.status_code == 410, gone.text
    assert gone.json()["error"]["code"] == "RECEIPT_FILE_MISSING"
    exp = client.get(f"{BASE}/expenses/{eid}", headers=h).json()["data"]
    assert exp["has_receipt"] is False and exp["receipt_url"] is None
    row = db.get(Expense, eid)
    assert row.receipt_key is None and row.receipt_private is False
    # now it is an ordinary expense without a receipt: the link route is a plain 404
    assert client.get(f"{BASE}/expenses/{eid}/receipt", headers=h).status_code == 404
    # and a fresh upload works again
    again = client.post(
        f"{BASE}/expenses/{eid}/receipt",
        files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 16), "image/png")},
        headers=h,
    )
    assert again.status_code == 200 and again.json()["data"]["has_receipt"] is True
