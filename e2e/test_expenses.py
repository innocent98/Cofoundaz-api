"""Live Finance Hub expenses journey (Module 12, Slice 4a).

`test_finance_expenses_journey`: a founder onboards and seeds a raise (so cash-on-hand has a real
starting number), then logs four expenses in different categories -- three dated today, one dated
last month (recurring) -- and walks the whole expense lifecycle:

  create (each returns the expense + a linked `transaction_id`; a client cannot set it)
  -> list + filter by category / month -> category summary (per-category totals + percents)
  -> cash-flow AND runway move (each expense posted an outflow)
  -> the outflow shows in the transactions ledger with `source == "expense"` and is protected
     from PATCH / DELETE (422 "managed by an expense")
  -> upload a receipt (PNG) -> replace it (PDF; the old file is deleted from storage)
  -> delete a receipt via DELETE /receipt
  -> PATCH an amount (the linked outflow is edited in place; cash-flow reflects it)
  -> DELETE an expense that has a receipt (the outflow is reversed AND the receipt file is
     removed from storage; cash-flow reverts).

It also captures the error shapes the FE has to handle (bad receipt type, validation, explicit
null on a required PATCH field, bad month, unknown id, 401).

Every response body is captured to `e2e/_captures/expenses/*.json` -- those files are the verbatim
source for docs/fe-integration-guide-finance-expenses.md. They must be REAL bodies from this live
run, complete and untrimmed.

Finance is founder/team_member/accountant RBAC, so this needs nothing but a freshly onboarded
founder + workspace. The e2e runner boots the server on STORAGE_BACKEND=local, so `receipt_url` is a
local filesystem path (staging/prod use Cloudinary and return an https URL). The on-disk existence
checks below only run for a local path.

Dates are computed relative to today (UTC, matching the server's cash-flow clock): the burn window
is "this month and the two before it".
"""

import io
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

# A real, decodable 1x1 red PNG (70 bytes) -- small enough to inline, valid enough to be a genuine
# image (every chunk CRC checks out).
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)
_PDF = b"%PDF-1.4\n%a tiny fake PDF receipt for e2e\n%%EOF"


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
    """The 15th of the month `months_back` months before `today`'s month (always in the past)."""
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


def _local_path(url: str) -> Path | None:
    """The receipt's on-disk path when the server runs on LocalStorage, else None (Cloudinary)."""
    return None if url.startswith(("http://", "https://")) else Path(url)


def test_finance_expenses_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- finance needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Expenses")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = datetime.now(UTC).date()
        this_month = today.strftime("%Y-%m")
        last_month_day = _mid_month(today, 1)
        last_month = last_month_day.strftime("%Y-%m")

        # 1. Seed a raise (inflow) outside the 3-month burn window: 100,000,000 minor = NGN 1,000,000.
        raised = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": _mid_month(today, 4).isoformat(),
                "description": "Pre-seed round",
                "category": "Fundraising",
                "amount_minor": 100_000_000,
                "currency": "NGN",
                "direction": "in",
            },
        )
        assert raised.status_code == 200, raised.text

        cf_before = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cf_before.status_code == 200, cf_before.text
        assert cf_before.json()["data"]["cash_on_hand"] == 100_000_000
        assert cf_before.json()["data"]["monthly_burn"] == 0
        capture("expenses", "cash_flow_before", cf_before)
        runway_before = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_before.status_code == 200, runway_before.text
        assert runway_before.json()["data"]["baseline"]["cash_on_hand"] == 100_000_000
        capture("expenses", "runway_before", runway_before)

        # 2. Create four expenses in different categories. Each returns the expense with a
        # server-linked `transaction_id` (the outflow it posted into the ledger).
        def _create(name: str, body: dict) -> dict:
            r = c.post("/api/v1/finance/expenses", headers=wh, json=body)
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["transaction_id"], data
            assert data["amount_minor"] == body["amount_minor"]
            assert data["category"] == body["category"]
            assert data["has_receipt"] is False
            assert data["receipt_url"] is None
            capture("expenses", name, r)
            return data

        hosting = _create(
            "expense_created_hosting",
            {
                "vendor": "AWS",
                "category": "Infrastructure",
                "expense_date": today.isoformat(),
                "amount_minor": 12_000_000,
                "currency": "NGN",
                "notes": "Production cluster, monthly bill",
            },
        )
        payroll = _create(
            "expense_created_payroll",
            {
                "vendor": "Team salaries",
                "category": "Payroll",
                "expense_date": today.isoformat(),
                "amount_minor": 20_000_000,
            },
        )
        ads = _create(
            "expense_created_marketing",
            {
                "vendor": "Google Ads",
                "category": "Marketing",
                "expense_date": today.isoformat(),
                "amount_minor": 8_000_000,
            },
        )
        notion = _create(
            "expense_created_last_month_recurring",
            {
                "vendor": "Notion",
                "category": "Software",
                "expense_date": last_month_day.isoformat(),
                "amount_minor": 4_000_000,
                "recurring": True,
            },
        )
        assert notion["recurring"] is True
        assert hosting["currency"] == "NGN"  # currency defaults to NGN when omitted
        hosting_id, payroll_id, ads_id, notion_id = (
            hosting["id"],
            payroll["id"],
            ads["id"],
            notion["id"],
        )
        assert (
            len({hosting["transaction_id"], payroll["transaction_id"], ads["transaction_id"]}) == 3
        )

        # 3. List: all four, newest expense_date first; then filter by category and by month.
        listed = c.get("/api/v1/finance/expenses", headers=wh)
        assert listed.status_code == 200, listed.text
        rows = listed.json()["data"]["expenses"]
        assert len(rows) == 4
        assert rows[-1]["id"] == notion_id  # last month's expense sorts last
        capture("expenses", "expenses_list_all", listed)

        by_cat = c.get("/api/v1/finance/expenses", headers=wh, params={"category": "Payroll"})
        assert by_cat.status_code == 200, by_cat.text
        assert [r["id"] for r in by_cat.json()["data"]["expenses"]] == [payroll_id]
        capture("expenses", "expenses_list_by_category", by_cat)

        by_month = c.get("/api/v1/finance/expenses", headers=wh, params={"month": last_month})
        assert by_month.status_code == 200, by_month.text
        assert [r["id"] for r in by_month.json()["data"]["expenses"]] == [notion_id]
        capture("expenses", "expenses_list_by_month", by_month)

        recurring = c.get("/api/v1/finance/expenses", headers=wh, params={"recurring": "true"})
        assert recurring.status_code == 200, recurring.text
        assert [r["id"] for r in recurring.json()["data"]["expenses"]] == [notion_id]
        capture("expenses", "expenses_list_recurring", recurring)

        one = c.get(f"/api/v1/finance/expenses/{hosting_id}", headers=wh)
        assert one.status_code == 200, one.text
        assert one.json()["data"]["id"] == hosting_id
        capture("expenses", "expense_get_one", one)

        # 4. Category summary for this month: payroll 20M (50%), infra 12M (30%), marketing 8M (20%).
        summary = c.get(
            "/api/v1/finance/expenses/summary", headers=wh, params={"month": this_month}
        )
        assert summary.status_code == 200, summary.text
        s = summary.json()["data"]
        assert s["month"] == this_month
        assert s["currency"] == "NGN"
        assert s["total_minor"] == 40_000_000
        assert [(r["category"], r["total_minor"], r["percent"]) for r in s["rows"]] == [
            ("Payroll", 20_000_000, 50.0),
            ("Infrastructure", 12_000_000, 30.0),
            ("Marketing", 8_000_000, 20.0),
        ]
        assert round(sum(r["percent"] for r in s["rows"])) == 100
        capture("expenses", "summary_this_month", summary)

        summary_last = c.get(
            "/api/v1/finance/expenses/summary", headers=wh, params={"month": last_month}
        )
        assert summary_last.status_code == 200, summary_last.text
        assert summary_last.json()["data"]["rows"] == [
            {"category": "Software", "total_minor": 4_000_000, "percent": 100.0}
        ]
        capture("expenses", "summary_last_month", summary_last)

        summary_empty = c.get(
            "/api/v1/finance/expenses/summary", headers=wh, params={"month": "2020-01"}
        )
        assert summary_empty.status_code == 200, summary_empty.text
        assert summary_empty.json()["data"]["rows"] == []
        assert summary_empty.json()["data"]["total_minor"] == 0
        capture("expenses", "summary_empty_month", summary_empty)

        # 5. Cash-flow + runway reflect the four outflows.
        # cash = 100M - (12+20+8+4)M = 56M ; burn window (this + 2 prior months) out 44M -> burn
        # round(44M/3) = 14,666,667 ; runway = 56M / 14,666,667 = 3.8 (< 6 -> runway_low).
        cf_after = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cf_after.status_code == 200, cf_after.text
        cf = cf_after.json()["data"]
        assert cf["cash_on_hand"] == 56_000_000
        assert cf["monthly_burn"] == 14_666_667
        assert cf["runway_months"] == 3.8
        assert cf["runway_low"] is True
        by_m = {row["month"]: row for row in cf["by_month"]}
        assert by_m[this_month]["outflow"] == 40_000_000
        assert by_m[last_month]["outflow"] == 4_000_000
        capture("expenses", "cash_flow_after_create", cf_after)
        runway_after = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_after.status_code == 200, runway_after.text
        assert runway_after.json()["data"]["baseline"]["cash_on_hand"] == 56_000_000
        assert runway_after.json()["data"]["baseline"]["monthly_burn"] == 14_666_667
        capture("expenses", "runway_after_create", runway_after)

        # 6. The outflow is in the ledger with source == "expense" and is protected from direct
        # edit / delete -- the FE must send the user to the expense instead.
        ledger = c.get("/api/v1/finance/transactions", headers=wh, params={"direction": "out"})
        assert ledger.status_code == 200, ledger.text
        out_rows = ledger.json()["data"]["transactions"]
        assert len(out_rows) == 4
        assert {r["source"] for r in out_rows} == {"expense"}
        hosting_txn = next(r for r in out_rows if r["id"] == hosting["transaction_id"])
        assert hosting_txn["amount_minor"] == 12_000_000
        assert hosting_txn["direction"] == "out"
        assert hosting_txn["category"] == "Infrastructure"
        assert hosting_txn["description"] == "Expense: AWS"
        capture("expenses", "transactions_list_outflows", ledger)

        patch_txn = c.patch(
            f"/api/v1/finance/transactions/{hosting['transaction_id']}",
            headers=wh,
            json={"category": "Other"},
        )
        assert patch_txn.status_code == 422, patch_txn.text
        assert "managed by an expense" in patch_txn.json()["error"]["message"]
        capture("expenses", "error_patch_expense_transaction", patch_txn)
        delete_txn = c.delete(
            f"/api/v1/finance/transactions/{hosting['transaction_id']}", headers=wh
        )
        assert delete_txn.status_code == 422, delete_txn.text
        assert "managed by an expense" in delete_txn.json()["error"]["message"]
        capture("expenses", "error_delete_expense_transaction", delete_txn)

        # 7. Receipts. Upload a PNG to the hosting expense.
        uploaded = c.post(
            f"/api/v1/finance/expenses/{hosting_id}/receipt",
            headers=wh,
            files={"file": ("aws-invoice.png", io.BytesIO(_PNG_1X1), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        up = uploaded.json()["data"]
        assert up["has_receipt"] is True
        assert up["receipt_url"]
        assert up["id"] == hosting_id
        assert up["transaction_id"] == hosting["transaction_id"]  # the link is untouched
        capture("expenses", "receipt_uploaded", uploaded)
        first_path = _local_path(up["receipt_url"])
        if first_path is not None:
            assert first_path.read_bytes() == _PNG_1X1

        # 7b. Replace it with a PDF: new url, and the OLD file is deleted from storage.
        replaced = c.post(
            f"/api/v1/finance/expenses/{hosting_id}/receipt",
            headers=wh,
            files={"file": ("aws-invoice.pdf", io.BytesIO(_PDF), "application/pdf")},
        )
        assert replaced.status_code == 200, replaced.text
        rep = replaced.json()["data"]
        assert rep["has_receipt"] is True
        assert rep["receipt_url"] != up["receipt_url"]
        assert rep["receipt_url"].endswith(".pdf")
        capture("expenses", "receipt_replaced", replaced)
        if first_path is not None:
            assert not first_path.exists(), "replace must delete the old receipt file"
            assert Path(rep["receipt_url"]).read_bytes() == _PDF

        # 7c. A disallowed content-type -> 422 and the expense is unchanged.
        bad_type = c.post(
            f"/api/v1/finance/expenses/{hosting_id}/receipt",
            headers=wh,
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        )
        assert bad_type.status_code == 422, bad_type.text
        assert bad_type.json()["error"]["code"] == "VALIDATION_ERROR"
        capture("expenses", "error_receipt_bad_type", bad_type)
        still = c.get(f"/api/v1/finance/expenses/{hosting_id}", headers=wh).json()["data"]
        assert still["receipt_url"] == rep["receipt_url"]

        # 7d. Upload to marketing, then remove it with DELETE /receipt.
        ads_up = c.post(
            f"/api/v1/finance/expenses/{ads_id}/receipt",
            headers=wh,
            files={"file": ("ads.jpg", io.BytesIO(_PNG_1X1), "image/jpeg")},
        )
        assert ads_up.status_code == 200, ads_up.text
        ads_path = _local_path(ads_up.json()["data"]["receipt_url"])
        if ads_path is not None:
            assert ads_path.exists()
        removed = c.delete(f"/api/v1/finance/expenses/{ads_id}/receipt", headers=wh)
        assert removed.status_code == 200, removed.text
        assert removed.json()["data"]["has_receipt"] is False
        assert removed.json()["data"]["receipt_url"] is None
        capture("expenses", "receipt_removed", removed)
        if ads_path is not None:
            assert not ads_path.exists(), "DELETE /receipt must remove the stored file"

        # 8. PATCH the hosting amount 12M -> 10M: the linked outflow is edited in place.
        # cash = 58M ; burn window out 42M -> 14,000,000 ; runway = 58/14 = 4.1.
        edited = c.patch(
            f"/api/v1/finance/expenses/{hosting_id}",
            headers=wh,
            json={"amount_minor": 10_000_000, "notes": "Negotiated a discount"},
        )
        assert edited.status_code == 200, edited.text
        ed = edited.json()["data"]
        assert ed["amount_minor"] == 10_000_000
        assert ed["notes"] == "Negotiated a discount"
        assert ed["transaction_id"] == hosting["transaction_id"]  # same ledger row, edited in place
        assert ed["has_receipt"] is True  # the receipt survives an edit
        capture("expenses", "expense_edited", edited)

        cf_edit = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cf_edit.status_code == 200, cf_edit.text
        assert cf_edit.json()["data"]["cash_on_hand"] == 58_000_000
        assert cf_edit.json()["data"]["monthly_burn"] == 14_000_000
        assert cf_edit.json()["data"]["runway_months"] == 4.1
        capture("expenses", "cash_flow_after_edit", cf_edit)
        runway_edit = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_edit.json()["data"]["baseline"]["cash_on_hand"] == 58_000_000
        capture("expenses", "runway_after_edit", runway_edit)

        # The ledger row followed the edit (amount + the re-derived description).
        moved = c.patch(
            f"/api/v1/finance/expenses/{hosting_id}",
            headers=wh,
            json={"vendor": "Amazon Web Services", "category": "Cloud"},
        )
        assert moved.status_code == 200, moved.text
        capture("expenses", "expense_edited_vendor_category", moved)
        ledger2 = c.get("/api/v1/finance/transactions", headers=wh, params={"category": "Cloud"})
        moved_rows = ledger2.json()["data"]["transactions"]
        assert len(moved_rows) == 1
        assert moved_rows[0]["id"] == hosting["transaction_id"]
        assert moved_rows[0]["amount_minor"] == 10_000_000
        assert moved_rows[0]["description"] == "Expense: Amazon Web Services"
        capture("expenses", "transactions_list_after_edit", ledger2)

        # 9. DELETE the payroll expense. Give it a receipt first so the delete also proves the
        # storage cascade. Cash reverts by exactly its amount: 58M + 20M = 78M ; burn window out
        # 22M -> 7,333,333 ; runway = 78M / 7,333,333 = 10.6 (not low).
        pay_up = c.post(
            f"/api/v1/finance/expenses/{payroll_id}/receipt",
            headers=wh,
            files={"file": ("payslips.pdf", io.BytesIO(_PDF), "application/pdf")},
        )
        assert pay_up.status_code == 200, pay_up.text
        pay_path = _local_path(pay_up.json()["data"]["receipt_url"])
        if pay_path is not None:
            assert pay_path.exists()

        deleted = c.delete(f"/api/v1/finance/expenses/{payroll_id}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"] == {"deleted": True}
        capture("expenses", "expense_deleted", deleted)
        if pay_path is not None:
            assert not pay_path.exists(), "deleting an expense must remove its receipt file"

        gone = c.get(f"/api/v1/finance/expenses/{payroll_id}", headers=wh)
        assert gone.status_code == 404, gone.text
        capture("expenses", "error_get_deleted_expense", gone)

        cf_del = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cf_del.status_code == 200, cf_del.text
        d = cf_del.json()["data"]
        assert d["cash_on_hand"] == 78_000_000
        assert d["monthly_burn"] == 7_333_333
        assert d["runway_months"] == 10.6
        assert d["runway_low"] is False
        capture("expenses", "cash_flow_after_delete", cf_del)
        runway_del = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_del.json()["data"]["baseline"]["cash_on_hand"] == 78_000_000
        capture("expenses", "runway_after_delete", runway_del)

        # The payroll outflow left the ledger too: only three outflows remain.
        ledger3 = c.get("/api/v1/finance/transactions", headers=wh, params={"direction": "out"})
        assert len(ledger3.json()["data"]["transactions"]) == 3
        capture("expenses", "transactions_list_after_delete", ledger3)

        # The summary followed: payroll is gone from this month.
        summary_after = c.get(
            "/api/v1/finance/expenses/summary", headers=wh, params={"month": this_month}
        )
        assert summary_after.json()["data"]["total_minor"] == 18_000_000
        capture("expenses", "summary_after_delete", summary_after)

        # 10. Error shapes the FE must handle.
        bad_create = c.post(
            "/api/v1/finance/expenses",
            headers=wh,
            json={
                "vendor": "",
                "category": "Ops",
                "expense_date": today.isoformat(),
                "amount_minor": -5,
            },
        )
        assert bad_create.status_code == 422, bad_create.text
        capture("expenses", "error_create_validation", bad_create)

        null_patch = c.patch(
            f"/api/v1/finance/expenses/{notion_id}", headers=wh, json={"amount_minor": None}
        )
        assert null_patch.status_code == 422, null_patch.text
        capture("expenses", "error_patch_null_required_field", null_patch)

        bad_month = c.get(
            "/api/v1/finance/expenses/summary", headers=wh, params={"month": "2026-13"}
        )
        assert bad_month.status_code == 422, bad_month.text
        capture("expenses", "error_summary_bad_month", bad_month)

        unknown = c.get(f"/api/v1/finance/expenses/{uuid.uuid4()}", headers=wh)
        assert unknown.status_code == 404, unknown.text
        capture("expenses", "error_unknown_expense", unknown)

        unauth = c.get("/api/v1/finance/expenses")
        assert unauth.status_code == 401, unauth.text
        capture("expenses", "error_unauthenticated", unauth)
