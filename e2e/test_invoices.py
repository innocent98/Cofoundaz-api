"""Live Finance Hub invoices journey (Module 12, Slice 3).

`test_finance_invoices_journey`: a founder onboards and seeds a small ledger (a raise and a hosting
bill, so cash-flow and runway have real numbers), then walks one invoice through its whole life:

  create (draft, server-computed totals + INV number; a client-supplied total is ignored)
  -> PATCH a line item (totals recompute) -> send (status `sent`, issued_on / due_on set, client
  emailed -- the queued `email.invoice_sent` job is drained in-process and the delivered message is
  read back out of the file mailbox) -> mark-paid (inflow created; cash-flow AND runway move;
  a second mark-paid is an idempotent no-op) -> the inflow shows in the transactions ledger with
  `source == "invoice"` and is protected from PATCH / DELETE -> list filters -> mark-unpaid (the
  inflow is reversed exactly; cash-flow returns to its baseline).

It also captures the error shapes the FE has to handle (illegal transitions, unknown id, 401) and
proves the DERIVED `overdue` status live: an invoice is sent, its `due_on` is backdated directly in
the database (there is no other way to make time pass in a live run), and it then reads back as
`overdue` on GET / list; `PATCH {"status": "overdue"}` cannot set it.

Every response body is captured to `e2e/_captures/invoices/*.json` -- those files are the verbatim
source for docs/fe-integration-guide-finance-invoices.md. They must be REAL bodies from this live
run, complete and untrimmed. The one non-HTTP capture is the delivered email (read from the file
mailbox), written with `capture_json`.

Finance is founder/team_member/accountant RBAC, so this needs nothing but a freshly onboarded
founder + workspace. The file email backend (EMAIL_BACKEND=file, set by scripts/e2e_run.sh) accepts
any recipient; the address still uses the `delivered+<uniq>@resend.dev` form so the journey would
also be valid against real Resend.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx

# NOTE: app-internal imports (SessionLocal, worker) are done LAZILY inside the helpers, not at
# module level -- see e2e/test_notifications_email.py: importing them at collection time would
# instantiate Settings, which the remote live-e2e gate (no app env) cannot do.


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_finance.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def _mid_month(today: date, months_back: int) -> date:
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


def _drain() -> None:
    """Drains the in-process job queue (same `run_once` the real `python -m app.worker` loop calls).

    The whole e2e run shares one `jobs` table, so loop until a batch claims nothing.
    """
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import (  # noqa: F401  (registers email.invoice_sent)
        invoice_email as _invoice_email,
    )

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _backdate_due_on(invoice_id: str, due_on: date) -> None:
    """Moves an invoice's stored `due_on` into the past (the only way to make a live invoice
    overdue without waiting). Bound parameters only."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE invoices SET due_on = :due_on WHERE id = :id"),
            {"due_on": due_on, "id": uuid.UUID(invoice_id)},
        )
        db.commit()
    finally:
        db.close()


def test_finance_invoices_journey(base_url, make_verified_user, capture, capture_json, mailbox):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Invoices")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = datetime.now(UTC).date()
        year = today.year

        # Seed a ledger so cash-flow / runway have real numbers to move.
        # cash = 100,000,000 - 12,000,000 = 88,000,000 ; burn window: out 12M / 3 = 4,000,000/mo.
        for body in (
            {
                "date": _mid_month(today, 4).isoformat(),
                "description": "Pre-seed round",
                "category": "Fundraising",
                "amount_minor": 100_000_000,
                "currency": "NGN",
                "direction": "in",
            },
            {
                "date": today.isoformat(),
                "description": "Cloud hosting",
                "category": "Infrastructure",
                "amount_minor": 12_000_000,
                "currency": "NGN",
                "direction": "out",
            },
        ):
            seeded = c.post("/api/v1/finance/transactions", headers=wh, json=body)
            assert seeded.status_code == 200, seeded.text

        cash_before = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cash_before.status_code == 200, cash_before.text
        cf_before = cash_before.json()["data"]
        assert cf_before["cash_on_hand"] == 88_000_000
        capture("invoices", "cash_flow_before", cash_before)
        runway_before = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_before.status_code == 200, runway_before.text
        assert runway_before.json()["data"]["baseline"]["cash_on_hand"] == 88_000_000
        capture("invoices", "runway_before", runway_before)

        client_email = f"delivered+inv-{uuid.uuid4().hex[:12]}@resend.dev"

        # 1. Create a draft. Subtotal = 1 x 100,000 + 5 x 5,000 = 125,000; tax 7.5% = 9,375;
        # total = 134,375 (NGN 1,343.75). The request ALSO carries a bogus client-side total --
        # the server must ignore it.
        create_body = {
            "client_name": "Acme Traders Ltd",
            "client_email": client_email,
            "line_items": [
                {"description": "Design retainer", "quantity": 1, "unit_price_minor": 100_000},
                {"description": "Hosting setup", "quantity": 5, "unit_price_minor": 5_000},
            ],
            "tax_percent": 7.5,
            "currency": "NGN",
            "terms": "net_30",
            "total_minor": 1,
        }
        created = c.post("/api/v1/finance/invoices", headers=wh, json=create_body)
        assert created.status_code == 200, created.text
        inv = created.json()["data"]
        invoice_id = inv["id"]
        assert inv["number"] == f"INV-{year}-001"
        assert inv["status"] == "draft"
        assert inv["subtotal_minor"] == 125_000
        assert inv["tax_minor"] == 9_375
        assert inv["total_minor"] == 134_375  # NOT the client's 1
        assert inv["issued_on"] is None and inv["due_on"] is None
        assert inv["paid_at"] is None and inv["transaction_id"] is None
        assert inv["auto_remind"] is False
        assert [li["amount_minor"] for li in inv["line_items"]] == [100_000, 25_000]
        capture("invoices", "invoice_created", created)

        # 2. Edit a line item while still draft -- totals recompute server-side.
        # Subtotal = 100,000 + 10 x 5,000 = 150,000 ; tax 7.5% = 11,250 ; total = 161,250.
        edited = c.patch(
            f"/api/v1/finance/invoices/{invoice_id}",
            headers=wh,
            json={
                "line_items": [
                    {"description": "Design retainer", "quantity": 1, "unit_price_minor": 100_000},
                    {"description": "Hosting setup", "quantity": 10, "unit_price_minor": 5_000},
                ]
            },
        )
        assert edited.status_code == 200, edited.text
        e = edited.json()["data"]
        assert e["subtotal_minor"] == 150_000
        assert e["tax_minor"] == 11_250
        assert e["total_minor"] == 161_250
        assert e["status"] == "draft"
        capture("invoices", "invoice_edited", edited)
        total = e["total_minor"]

        # 2b. A second draft, only used to show the draft filter and DELETE.
        second = c.post(
            "/api/v1/finance/invoices",
            headers=wh,
            json={
                "client_name": "Beta Foods",
                "client_email": f"delivered+inv-{uuid.uuid4().hex[:12]}@resend.dev",
                "line_items": [
                    {"description": "Consulting", "quantity": 2, "unit_price_minor": 7_000}
                ],
                "tax_percent": 0,
                "terms": "due_on_receipt",
            },
        )
        assert second.status_code == 200, second.text
        second_id = second.json()["data"]["id"]
        assert second.json()["data"]["number"] == f"INV-{year}-002"
        capture("invoices", "invoice_created_second_draft", second)

        # 2c. Illegal transitions on a draft: mark-paid / mark-unpaid -> 422.
        paid_on_draft = c.post(f"/api/v1/finance/invoices/{second_id}/mark-paid", headers=wh)
        assert paid_on_draft.status_code == 422, paid_on_draft.text
        capture("invoices", "error_mark_paid_on_draft", paid_on_draft)
        unpaid_on_draft = c.post(f"/api/v1/finance/invoices/{second_id}/mark-unpaid", headers=wh)
        assert unpaid_on_draft.status_code == 422, unpaid_on_draft.text
        capture("invoices", "error_mark_unpaid_on_draft", unpaid_on_draft)

        # 3. Send -> status `sent`, issued_on = today, due_on = today + 30 (net_30).
        sent = c.post(f"/api/v1/finance/invoices/{invoice_id}/send", headers=wh)
        assert sent.status_code == 200, sent.text
        s = sent.json()["data"]
        assert s["status"] == "sent"
        assert s["issued_on"] == today.isoformat()
        assert s["due_on"] == (today + timedelta(days=30)).isoformat()
        assert s["total_minor"] == total
        capture("invoices", "invoice_sent", sent)

        # 3b. Sending is async: nothing is emailed until the worker claims the job. Drain the
        # queue in-process, then read the delivered message out of the file mailbox.
        _drain()
        assert mailbox.count_for(client_email) == 1
        mail = mailbox.latest_for(client_email)
        assert mail is not None
        assert mail["to"].lower() == client_email.lower()
        assert f"INV-{year}-001" in mail["subject"]
        assert "1,612.50 NGN" in mail["html"]  # MAJOR units (161,250 minor), grouped + 2dp
        assert "161250" not in mail["html"] and "161,250" not in mail["html"]  # never raw minor
        assert "Acme Traders Ltd" in mail["html"]
        capture_json("invoices", "invoice_email_delivered", mail)

        # 3c. Once sent the commercial terms are frozen: PATCH / DELETE / re-send -> 422.
        patch_sent = c.patch(
            f"/api/v1/finance/invoices/{invoice_id}", headers=wh, json={"client_name": "Renamed"}
        )
        assert patch_sent.status_code == 422, patch_sent.text
        capture("invoices", "error_patch_sent_invoice", patch_sent)
        delete_sent = c.delete(f"/api/v1/finance/invoices/{invoice_id}", headers=wh)
        assert delete_sent.status_code == 422, delete_sent.text
        capture("invoices", "error_delete_sent_invoice", delete_sent)
        resend = c.post(f"/api/v1/finance/invoices/{invoice_id}/send", headers=wh)
        assert resend.status_code == 422, resend.text
        capture("invoices", "error_send_twice", resend)
        # auto_remind is the one field that stays editable after send.
        remind = c.patch(
            f"/api/v1/finance/invoices/{invoice_id}", headers=wh, json={"auto_remind": True}
        )
        assert remind.status_code == 200, remind.text
        assert remind.json()["data"]["auto_remind"] is True
        assert remind.json()["data"]["status"] == "sent"
        capture("invoices", "invoice_auto_remind_after_send", remind)

        # 4. Mark paid -> status paid, paid_at + transaction_id set.
        paid = c.post(f"/api/v1/finance/invoices/{invoice_id}/mark-paid", headers=wh)
        assert paid.status_code == 200, paid.text
        p = paid.json()["data"]
        assert p["status"] == "paid"
        assert p["paid_at"] is not None
        assert p["transaction_id"] is not None
        txn_id = p["transaction_id"]
        capture("invoices", "invoice_marked_paid", paid)

        # 4b. Idempotent: a second mark-paid returns 200 with the SAME transaction, no 2nd inflow.
        paid_again = c.post(f"/api/v1/finance/invoices/{invoice_id}/mark-paid", headers=wh)
        assert paid_again.status_code == 200, paid_again.text
        assert paid_again.json()["data"]["transaction_id"] == txn_id
        assert paid_again.json()["data"]["paid_at"] == p["paid_at"]
        capture("invoices", "invoice_marked_paid_again", paid_again)

        # 5. Cash-flow rose by exactly the invoice total (once, despite two mark-paid calls).
        cash_after = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cash_after.status_code == 200, cash_after.text
        cf_after = cash_after.json()["data"]
        assert cf_after["cash_on_hand"] == cf_before["cash_on_hand"] + total
        assert cf_after["monthly_revenue"] == cf_before["monthly_revenue"] + round(total / 3)
        capture("invoices", "cash_flow_after_paid", cash_after)
        runway_after = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_after.status_code == 200, runway_after.text
        assert runway_after.json()["data"]["baseline"]["cash_on_hand"] == 88_000_000 + total
        capture("invoices", "runway_after_paid", runway_after)

        # 5b. The inflow is in the ledger, tagged source == "invoice" ...
        ledger = c.get("/api/v1/finance/transactions", headers=wh, params={"direction": "in"})
        assert ledger.status_code == 200, ledger.text
        inflow = next(r for r in ledger.json()["data"]["transactions"] if r["id"] == txn_id)
        assert inflow["source"] == "invoice"
        assert inflow["amount_minor"] == total
        assert inflow["direction"] == "in"
        assert inflow["category"] == "Revenue"
        assert inflow["description"] == f"Invoice INV-{year}-001 — Acme Traders Ltd"
        capture("invoices", "transactions_list_inflow", ledger)

        # ... and is protected from direct edit / delete (must unpay the invoice instead).
        patch_txn = c.patch(
            f"/api/v1/finance/transactions/{txn_id}", headers=wh, json={"category": "Other"}
        )
        assert patch_txn.status_code == 422, patch_txn.text
        capture("invoices", "error_patch_invoice_transaction", patch_txn)
        delete_txn = c.delete(f"/api/v1/finance/transactions/{txn_id}", headers=wh)
        assert delete_txn.status_code == 422, delete_txn.text
        capture("invoices", "error_delete_invoice_transaction", delete_txn)

        # 6. List filters.
        paid_list = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "paid"})
        assert paid_list.status_code == 200, paid_list.text
        assert [r["id"] for r in paid_list.json()["data"]["invoices"]] == [invoice_id]
        capture("invoices", "invoices_list_paid", paid_list)
        draft_list = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "draft"})
        assert draft_list.status_code == 200, draft_list.text
        assert [r["id"] for r in draft_list.json()["data"]["invoices"]] == [second_id]
        capture("invoices", "invoices_list_draft", draft_list)
        overdue_list = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "overdue"})
        assert overdue_list.status_code == 200, overdue_list.text
        assert overdue_list.json()["data"]["invoices"] == []
        capture("invoices", "invoices_list_overdue_empty", overdue_list)
        all_list = c.get("/api/v1/finance/invoices", headers=wh)
        assert all_list.status_code == 200, all_list.text
        assert {r["id"] for r in all_list.json()["data"]["invoices"]} == {invoice_id, second_id}
        capture("invoices", "invoices_list_all", all_list)
        bad_filter = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "bogus"})
        assert bad_filter.status_code == 422, bad_filter.text
        capture("invoices", "error_list_bad_status", bad_filter)

        # 6b. GET one.
        one = c.get(f"/api/v1/finance/invoices/{invoice_id}", headers=wh)
        assert one.status_code == 200, one.text
        assert one.json()["data"]["id"] == invoice_id
        assert one.json()["data"]["status"] == "paid"
        capture("invoices", "invoice_get_one", one)

        # 7. Mark unpaid -> back to `sent`, link cleared, inflow gone, cash reverted EXACTLY.
        unpaid = c.post(f"/api/v1/finance/invoices/{invoice_id}/mark-unpaid", headers=wh)
        assert unpaid.status_code == 200, unpaid.text
        u_body = unpaid.json()["data"]
        assert u_body["status"] == "sent"
        assert u_body["paid_at"] is None
        assert u_body["transaction_id"] is None
        capture("invoices", "invoice_marked_unpaid", unpaid)

        cash_reverted = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cash_reverted.status_code == 200, cash_reverted.text
        assert cash_reverted.json()["data"] == cf_before
        capture("invoices", "cash_flow_after_unpaid", cash_reverted)
        runway_reverted = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_reverted.status_code == 200, runway_reverted.text
        assert runway_reverted.json()["data"] == runway_before.json()["data"]
        capture("invoices", "runway_after_unpaid", runway_reverted)
        gone = c.get("/api/v1/finance/transactions", headers=wh, params={"direction": "in"})
        assert all(r["id"] != txn_id for r in gone.json()["data"]["transactions"])
        capture("invoices", "transactions_list_inflow_removed", gone)

        # 8. Delete the second (draft) invoice -> 200; it is gone.
        deleted = c.delete(f"/api/v1/finance/invoices/{second_id}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"] == {"deleted": True}
        capture("invoices", "invoice_deleted", deleted)
        after_delete = c.get(f"/api/v1/finance/invoices/{second_id}", headers=wh)
        assert after_delete.status_code == 404, after_delete.text
        capture("invoices", "error_get_deleted_invoice", after_delete)

        # 9. Other error shapes: unauthenticated, unknown id, bad create payloads.
        unauth = c.get("/api/v1/finance/invoices")
        assert unauth.status_code == 401, unauth.text
        capture("invoices", "error_unauthenticated", unauth)
        unknown = c.get(f"/api/v1/finance/invoices/{uuid.uuid4()}", headers=wh)
        assert unknown.status_code == 404, unknown.text
        capture("invoices", "error_unknown_invoice", unknown)
        bad_create = c.post(
            "/api/v1/finance/invoices",
            headers=wh,
            json={
                "client_name": "Acme",
                "client_email": "not-an-email",
                "line_items": [],
                "tax_percent": 150,
            },
        )
        assert bad_create.status_code == 422, bad_create.text
        capture("invoices", "error_create_validation", bad_create)

        # 10. DERIVED overdue: the invoice is `sent` again (step 7). Backdate its stored due_on
        # directly in the DB -- then GET / list report `overdue` although the stored status is
        # still `sent`, and the FE cannot PATCH its way there.
        _backdate_due_on(invoice_id, today - timedelta(days=3))
        overdue_one = c.get(f"/api/v1/finance/invoices/{invoice_id}", headers=wh)
        assert overdue_one.status_code == 200, overdue_one.text
        assert overdue_one.json()["data"]["status"] == "overdue"
        assert overdue_one.json()["data"]["due_on"] == (today - timedelta(days=3)).isoformat()
        capture("invoices", "invoice_get_overdue", overdue_one)
        overdue_list2 = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "overdue"})
        assert [r["id"] for r in overdue_list2.json()["data"]["invoices"]] == [invoice_id]
        capture("invoices", "invoices_list_overdue", overdue_list2)
        sent_list = c.get("/api/v1/finance/invoices", headers=wh, params={"status": "sent"})
        assert sent_list.json()["data"]["invoices"] == []  # overdue is NOT also `sent`
        capture("invoices", "invoices_list_sent_excludes_overdue", sent_list)

        patch_status = c.patch(
            f"/api/v1/finance/invoices/{invoice_id}", headers=wh, json={"status": "paid"}
        )
        capture("invoices", "patch_status_is_ignored", patch_status)
        assert patch_status.status_code == 200, patch_status.text
        assert patch_status.json()["data"]["status"] == "overdue"  # status is not writable

        # An overdue invoice can still be marked paid (cash arrives late) -> `paid`.
        paid_overdue = c.post(f"/api/v1/finance/invoices/{invoice_id}/mark-paid", headers=wh)
        assert paid_overdue.status_code == 200, paid_overdue.text
        assert paid_overdue.json()["data"]["status"] == "paid"
        capture("invoices", "invoice_overdue_marked_paid", paid_overdue)
