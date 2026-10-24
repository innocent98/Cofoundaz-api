"""Live journey: Finance slice (e).

Runway reads the Financial Model's hiring and one-off rows (D-FIN-12) and refuses its own;
invoices gain a client picker, notes, payment details and Resend; the advisor is grounded in the
ledger for Finance roles only; the ledger and the model export to XLSX; a receipt whose file is
gone answers 410 and clears itself (D2).

Run by scripts/e2e_run.sh against the real server (EMAIL_BACKEND=file, LLM_PROVIDER=stub). The
in-process worker drain and the advisor-context probe use the same database as the server.
Every response body is captured, untrimmed, to `e2e/_captures/finance_slice_e/*.json`.

NOT exercised live: Cloudinary receipt storage (the missing-file check only knows local storage),
and the advisor's *model reply* (the stub LLM echoes; the prompt paragraph is probed in-process).
"""

import io
import re
import uuid
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

G = "finance_slice_e"
API = "/api/v1"
F = f"{API}/finance"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mid_month(today: date, months_back: int) -> date:
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


def _month_after(today: date, n: int) -> str:
    y, m = today.year, today.month + n
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


def _join(c, wh, ws, role, unique_email, mailbox):
    email = unique_email(role)
    r = c.post(
        f"{API}/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Member-{uuid.uuid4().hex[:10]}-9"
    c.post(f"{API}/auth/signup", json={"email": email, "password": pw})
    c.post(
        f"{API}/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post(f"{API}/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    acc = c.post(f"{API}/invitations/accept", headers=_auth(access), json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**_auth(access), "X-Workspace-Id": ws}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _a  # noqa: F401
    from app.worker.handlers import finance_model as _m  # noqa: F401
    from app.worker.handlers import invoice_email as _i  # noqa: F401

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _sheet(content: bytes, n: int = 1) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        assert z.testzip() is None
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{n}.xml"))
    rows = []
    for row in root.iterfind(".//m:row", NS):
        vals = []
        for cell in row.iterfind("m:c", NS):
            t, v = cell.find(".//m:t", NS), cell.find("m:v", NS)
            vals.append(t.text if t is not None else (v.text if v is not None else ""))
        rows.append(vals)
    return rows


def _sheet_names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        root = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in root.iterfind(".//m:sheet", NS)]


def test_finance_slice_e_journey(
    base_url, make_verified_user, capture, capture_json, mailbox, unique_email
):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post(f"{API}/auth/login", json=u).json()["data"]["access_token"])
        c.get(f"{API}/onboarding/state", headers=auth)
        c.patch(
            f"{API}/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch(f"{API}/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            f"{API}/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch(f"{API}/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post(f"{API}/onboarding/complete", headers=auth).status_code == 200
        ws = c.get(f"{API}/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)

        today = datetime.now(UTC).date()
        for when, desc, cat, amt, d in [
            (_mid_month(today, 4), "Pre-seed round", "Funding", 100_000_000, "in"),
            (today, "Customer payments", "Revenue", 6_000_000, "in"),
            (today, "Team salaries", "Payroll", 20_000_000, "out"),
            (today, "=SUM(A1:A9) hosting", "Infrastructure", 12_000_000, "out"),
        ]:
            r = c.post(
                f"{F}/transactions",
                headers=wh,
                json={
                    "date": when.isoformat(),
                    "description": desc,
                    "category": cat,
                    "amount_minor": amt,
                    "currency": "NGN",
                    "direction": d,
                },
            )
            assert r.status_code == 200, r.text

        # ---- 1. Runway: no model yet ------------------------------------------------------------
        before = c.get(f"{F}/runway", headers=wh)
        assert before.status_code == 200, before.text
        capture(G, "runway_no_model", before)
        b = before.json()["data"]
        assert b["model_rows"] == {
            "model_id": None,
            "href": "/finance/model",
            "hiring_plan": [],
            "one_offs": [],
        }

        # ---- 2. PUT refuses model-owned and unknown fields (nothing is half-applied) -----------
        bad = c.put(
            f"{F}/runway/assumptions",
            headers=wh,
            json={
                "mom_growth_percent": 7,
                "hiring_plan": [{"role": "Engineer", "start_month": "2030-01"}],
                "one_offs": [],
            },
        )
        assert bad.status_code == 422, bad.text
        capture(G, "runway_put_rejects_rows", bad)
        assert {e["field"] for e in bad.json()["error"]["field_errors"]} == {
            "hiring_plan",
            "one_offs",
        }
        unknown = c.put(f"{F}/runway/assumptions", headers=wh, json={"burn_rate": 5})
        assert unknown.status_code == 422, unknown.text
        capture(G, "runway_put_rejects_unknown", unknown)
        assert (
            c.get(f"{F}/runway", headers=wh).json()["data"]["assumptions"]["mom_growth_percent"]
            == 0
        )
        ok = c.put(f"{F}/runway/assumptions", headers=wh, json={"mom_growth_percent": 5})
        assert ok.status_code == 200, ok.text
        capture(G, "runway_put_ok", ok)

        # ---- 3. Model rows flow into the runway ------------------------------------------------
        gen = c.post(f"{F}/model/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        model_id = gen.json()["data"]["id"]
        _drain()
        m1, m2 = _month_after(today, 1), _month_after(today, 2)
        patched = c.patch(
            f"{F}/model/{model_id}/assumptions",
            headers=wh,
            json={
                "hiring_plan": [
                    {"role": "Backend engineer", "start_month": m1, "monthly_cost_minor": 400_000}
                ],
                "one_offs": [{"label": "Laptops", "month": m2, "amount_minor": 250_000}],
            },
        )
        assert patched.status_code == 200, patched.text
        after = c.get(f"{F}/runway", headers=wh)
        assert after.status_code == 200, after.text
        capture(G, "runway_with_model_rows", after)
        a = after.json()["data"]
        assert a["model_rows"]["model_id"] == model_id
        assert a["model_rows"]["hiring_plan"][0]["role"] == "Backend engineer"
        # Month 1 loses the hire, month 2 the hire and the one-off, month 3 only the hire.
        ok_base = ok.json()["data"]["scenarios"]["base"]["by_month"]
        got = a["scenarios"]["base"]["by_month"]
        assert got[0]["net"] == ok_base[0]["net"] - 400_000
        assert got[1]["net"] == ok_base[1]["net"] - 400_000 - 250_000
        assert got[2]["net"] == ok_base[2]["net"] - 400_000

        # ---- 4. Invoices: client picker, notes, payment details, resend ------------------------
        acc = c.post(f"{API}/sales/accounts", headers=wh, json={"name": "Acme Logistics"})
        assert acc.status_code == 200, acc.text
        account_id = acc.json()["data"]["id"]
        client_email = unique_email("client")
        body = {
            "account_id": account_id,
            "client_name": "Acme Logistics",
            "client_email": client_email,
            "line_items": [
                {"description": "Design sprint", "quantity": 2, "unit_price_minor": 50000}
            ],
            "tax_percent": 7.5,
            "terms": "net_30",
            "notes": "Thank you for your business.\nPlease quote the invoice number.",
            "payment_details": {
                "bank_name": "GTBank",
                "account_name": "NovaPay Ltd",
                "account_number": "0123456789",
                "instructions": "Use the invoice number as the reference.",
            },
        }
        created = c.post(f"{F}/invoices", headers=wh, json=body)
        assert created.status_code == 200, created.text
        capture(G, "invoice_create", created)
        inv = created.json()["data"]
        assert inv["account_name"] == "Acme Logistics" and inv["last_sent_at"] is None
        assert inv["payment_details"] == body["payment_details"]

        wrong = c.post(f"{F}/invoices", headers=wh, json={**body, "account_id": str(uuid.uuid4())})
        assert wrong.status_code == 422, wrong.text
        capture(G, "invoice_unknown_account", wrong)

        sent = c.post(f"{F}/invoices/{inv['id']}/send", headers=wh)
        assert sent.status_code == 200, sent.text
        capture(G, "invoice_send", sent)
        frozen = c.patch(f"{F}/invoices/{inv['id']}", headers=wh, json={"notes": "late"})
        assert frozen.status_code == 422, frozen.text
        capture(G, "invoice_patch_after_send", frozen)

        too_soon = c.post(f"{F}/invoices/{inv['id']}/resend", headers=wh)
        assert too_soon.status_code == 429, too_soon.text
        capture(G, "invoice_resend_too_soon", too_soon)
        assert c.post(f"{F}/invoices/{inv['id']}/resend", headers=mentor).status_code == 403

        # Let the cooldown pass (the server shares this database), then resend for real.
        from sqlalchemy import text

        from app.db.session import SessionLocal

        with SessionLocal() as db:
            db.execute(
                text("update invoices set last_sent_at = now() - interval '5 minutes' where id=:i"),
                {"i": inv["id"]},
            )
            db.commit()
        _drain()
        first_mail = mailbox.latest_for(client_email)
        assert first_mail is not None and "GTBank" in first_mail["html"]
        resent = c.post(f"{F}/invoices/{inv['id']}/resend", headers=wh)
        assert resent.status_code == 200, resent.text
        capture(G, "invoice_resend", resent)
        _drain()
        assert mailbox.count_for(client_email) == 2
        mail = mailbox.latest_for(client_email)
        assert mail["subject"].startswith("Invoice ") and "0123456789" in mail["html"]
        assert "Please quote the invoice number." in mail["html"]
        capture_json(G, "invoice_resend_email", mail)

        drafted = c.post(f"{F}/invoices", headers=wh, json=body).json()["data"]["id"]
        refused = c.post(f"{F}/invoices/{drafted}/resend", headers=wh)
        assert refused.status_code == 422, refused.text
        capture(G, "invoice_resend_draft", refused)

        # ---- 5. Advisor finance context (probed in-process against the live database) -----------
        from app.db.models.user import User
        from app.services.ai_cofounder.finance_context import finance_context_for

        chat = c.post(
            f"{API}/ai/conversations", headers=wh, json={"message": "How long is my runway?"}
        )
        assert chat.status_code == 201, chat.text
        _drain()
        with SessionLocal() as db:
            founder = db.query(User).filter(User.email == u["email"].lower()).one()
            import jwt

            mentor_id = jwt.decode(
                mentor["Authorization"].split()[1], options={"verify_signature": False}
            )["sub"]
            para = finance_context_for(db, startup_id=uuid.UUID(ws), user_id=founder.id)
            hidden = finance_context_for(db, startup_id=uuid.UUID(ws), user_id=uuid.UUID(mentor_id))
        assert para is not None and "Finance snapshot" in para and "runway" in para
        assert "Funding" not in para and "Acme" not in para
        assert hidden is None
        capture_json(G, "advisor_finance_paragraph", {"founder": para, "mentor": hidden})

        # ---- 6. XLSX exports -------------------------------------------------------------------
        x = c.get(f"{F}/transactions/export.xlsx", headers=wh)
        assert x.status_code == 200, x.text
        assert "spreadsheetml.sheet" in x.headers["content-type"]
        rows = _sheet(x.content)
        assert rows[0][:3] == ["Date", "Description", "Category"]
        assert any(r[1].startswith("=SUM(") for r in rows[1:])  # text, never a formula
        with zipfile.ZipFile(io.BytesIO(x.content)) as z:
            assert "<f>" not in z.read("xl/worksheets/sheet1.xml").decode()
        capture_json(
            G,
            "transactions_xlsx",
            {
                "status": x.status_code,
                "content_type": x.headers["content-type"],
                "content_disposition": x.headers["content-disposition"],
                "bytes_start": x.content[:2].decode("latin-1"),
                "sheets": _sheet_names(x.content),
                "rows": rows,
            },
        )
        out_only = c.get(f"{F}/transactions/export.xlsx?direction=out", headers=wh)
        assert {r[3] for r in _sheet(out_only.content)[1:]} == {"out"}

        mx = c.get(f"{F}/model/{model_id}/export.xlsx", headers=wh)
        assert mx.status_code == 200, mx.text
        assert _sheet_names(mx.content) == ["P&L", "Cash flow", "Balance sheet"]
        pnl = _sheet(mx.content, 1)
        capture_json(
            G,
            "model_xlsx",
            {
                "status": mx.status_code,
                "content_disposition": mx.headers["content-disposition"],
                "sheets": _sheet_names(mx.content),
                "pnl_first_rows": [r[:4] for r in pnl[:4]],
                "columns_in_pnl": len(pnl[0]),
            },
        )
        denied = c.get(f"{F}/transactions/export.xlsx", headers=mentor)
        assert denied.status_code == 403, denied.text
        capture(G, "export_forbidden_mentor", denied)

        # ---- 7. D2: a receipt whose file is gone -----------------------------------------------
        e = c.post(
            f"{F}/expenses",
            headers=wh,
            json={
                "vendor": "Notion",
                "category": "Software",
                "expense_date": today.isoformat(),
                "amount_minor": 1000,
            },
        )
        eid = e.json()["data"]["id"]
        up = c.post(
            f"{F}/expenses/{eid}/receipt",
            headers=wh,
            files={"file": ("r.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\0" * 16), "image/png")},
        )
        assert up.status_code == 200 and up.json()["data"]["has_receipt"] is True
        link = c.get(f"{F}/expenses/{eid}/receipt", headers=wh)
        assert link.status_code == 200, link.text
        with SessionLocal() as db:
            key = db.execute(
                text("select receipt_key from expenses where id=:i"), {"i": eid}
            ).scalar()
        from app.core.config import settings

        Path(settings.LOCAL_STORAGE_DIR, key).unlink()
        gone = c.get(f"{F}/expenses/{eid}/receipt", headers=wh)
        assert gone.status_code == 410, gone.text
        capture(G, "receipt_file_missing", gone)
        assert gone.json()["error"]["code"] == "RECEIPT_FILE_MISSING"
        healed = c.get(f"{F}/expenses/{eid}", headers=wh)
        capture(G, "expense_after_missing_receipt", healed)
        assert healed.json()["data"]["has_receipt"] is False
        again = c.get(f"{F}/expenses/{eid}/receipt", headers=wh)
        assert again.status_code == 404, again.text
        capture(G, "receipt_after_heal", again)

        # tokens never reach a capture
        for f in Path(__file__).parent.joinpath("_captures", G).glob("*.json"):
            assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}", f.read_text()), f
