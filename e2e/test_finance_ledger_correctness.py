"""Live Finance ledger-correctness journey (slice c): private receipts, upload sniffing, NGN only,
positive amounts, categories, budgets from the ledger, pagination and the model-generate guard.

Walk: founder onboards, invites team_member / accountant / mentor (the investor 403 is unit-tested: investors cannot be invited yet), plus an unrelated
second workspace -> receipts (HTML labelled image/png 422, real PNG accepted, the API exposes the
route only, signed link per role, mentor / investor 403, other workspace 404, the link serves the
bytes, tampered link 404, a LEGACY row opened for the first time) -> 422s (zero amount, USD) ->
categories -> budgets (manual outflow counted, unbudgeted spend rows) -> pagination on all three
lists -> `POST /finance/model/generate` twice (202 then 409), drain the worker, generate again.

Every response body is captured, complete and untrimmed, to
`e2e/_captures/finance_ledger/*.json` (the verbatim source for
docs/fe-integration-guide-finance-ledger.md).

NOT live-verifiable here (labelled in the FE guide): Cloudinary `authenticated` upload, the signed
Cloudinary download URL and the legacy `upload` -> `authenticated` move (real Cloudinary
credentials are never used), and the 5 minute link expiry (covered by unit tests with a
controllable clock).
"""

import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

G = "finance_ledger"
PNG = b"\x89PNG\r\n\x1a\n" + b"e2e-placeholder-receipt-bytes"
STORAGE_DIR = os.environ.get("E2E_LOCAL_STORAGE_DIR", "./var/storage")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c, auth: dict, *, founder: str, startup: str) -> str:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": founder})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": startup})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _join(c, wh, ws, role, unique_email, mailbox):
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Member-{uuid.uuid4().hex[:10]}-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = _auth(access)
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": ws}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import (  # noqa: F401  (registers ai.finance.model)
        finance_model as _finance_model,
    )

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _insert_legacy_expense(startup_id: str) -> str:
    """A receipt row exactly as the old code wrote it: a raw URL/path in `receipt_url`, no private
    flag. The file is placed where local storage keeps it."""
    from app.db.models.expense import Expense
    from app.db.session import SessionLocal

    key = f"receipts/{startup_id}/{uuid.uuid4().hex}.png"
    path = Path(STORAGE_DIR) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    db = SessionLocal()
    try:
        row = Expense(
            startup_id=uuid.UUID(startup_id),
            vendor="Legacy Vendor",
            category="Software",
            expense_date=date(2026, 1, 5),
            amount_minor=150_000,
            currency="NGN",
            receipt_url=f"var/storage/{key}",
            receipt_key=key,
            receipt_private=False,
        )
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


def _receipt_private_flag(expense_id: str) -> bool:
    from app.db.models.expense import Expense
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return bool(db.get(Expense, uuid.UUID(expense_id)).receipt_private)
    finally:
        db.close()


def test_finance_ledger_correctness_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    cap = capture
    month = datetime.now(UTC).strftime("%Y-%m")
    today = datetime.now(UTC).date()
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        # 0. Workspaces and roles.
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        ws = _onboard(c, auth, founder="Ada Founder", startup="Ledger Co")
        f = {**auth, "X-Workspace-Id": ws}
        team = _join(c, f, ws, "team_member", unique_email, mailbox)
        accountant = _join(c, f, ws, "accountant", unique_email, mailbox)
        mentor = _join(c, f, ws, "mentor", unique_email, mailbox)
        other_user = make_verified_user(c)
        other_auth = _auth(
            c.post("/api/v1/auth/login", json=other_user).json()["data"]["access_token"]
        )
        other_ws = _onboard(c, other_auth, founder="Other Founder", startup="Other Co")
        other = {**other_auth, "X-Workspace-Id": other_ws}

        # 1. Positive amounts and NGN only, on every money write.
        zero = c.post(
            "/api/v1/finance/transactions",
            headers=f,
            json={"date": str(today), "description": "Zero", "amount_minor": 0, "direction": "out"},
        )
        assert zero.status_code == 422, zero.text
        cap(G, "error_transaction_zero_amount", zero)
        usd = c.post(
            "/api/v1/finance/transactions",
            headers=f,
            json={
                "date": str(today),
                "description": "USD",
                "amount_minor": 100,
                "currency": "USD",
                "direction": "in",
            },
        )
        assert usd.status_code == 422, usd.text
        cap(G, "error_transaction_non_ngn", usd)
        usd_exp = c.post(
            "/api/v1/finance/expenses",
            headers=f,
            json={
                "vendor": "Stripe",
                "category": "Software",
                "expense_date": str(today),
                "amount_minor": 100,
                "currency": "USD",
            },
        )
        assert usd_exp.status_code == 422, usd_exp.text
        cap(G, "error_expense_non_ngn", usd_exp)
        usd_bud = c.post(
            "/api/v1/finance/budgets",
            headers=f,
            json={"category": "Rent", "period_month": month, "limit_minor": 100, "currency": "USD"},
        )
        assert usd_bud.status_code == 422, usd_bud.text
        cap(G, "error_budget_non_ngn", usd_bud)

        # 2. Receipts. An expense, then the upload checks.
        exp = c.post(
            "/api/v1/finance/expenses",
            headers=f,
            json={
                "vendor": "AWS",
                "category": "Infrastructure",
                "expense_date": str(today),
                "amount_minor": 250_000,
            },
        )
        assert exp.status_code == 200, exp.text
        eid = exp.json()["data"]["id"]
        cap(G, "expense_created_no_receipt", exp)
        no_receipt = c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=f)
        assert no_receipt.status_code == 404, no_receipt.text
        cap(G, "error_receipt_link_none_attached", no_receipt)

        html = c.post(
            f"/api/v1/finance/expenses/{eid}/receipt",
            headers=f,
            files={"file": ("receipt.png", b"<html><script>alert(1)</script></html>", "image/png")},
        )
        assert html.status_code == 422, html.text
        cap(G, "error_receipt_html_labelled_png", html)
        up = c.post(
            f"/api/v1/finance/expenses/{eid}/receipt",
            headers=f,
            files={"file": ("anything.txt", PNG, "text/plain")},  # name and type are ignored
        )
        assert up.status_code == 200, up.text
        cap(G, "receipt_uploaded", up)
        data = up.json()["data"]
        assert data["has_receipt"] is True
        assert data["receipt_url"] == f"/api/v1/finance/expenses/{eid}/receipt"
        listed = c.get("/api/v1/finance/expenses", headers=f)
        cap(G, "expenses_list_with_receipt", listed)
        assert "var/storage" not in listed.text and "receipts/" not in listed.text

        link = c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=f)
        assert link.status_code == 200, link.text
        cap(G, "receipt_link_founder", link)
        url = link.json()["data"]["url"]
        for name, headers in (("team_member", team), ("accountant", accountant)):
            r = c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=headers)
            assert r.status_code == 200, (name, r.text)
            cap(G, f"receipt_link_{name}", r)
        for name, headers in (("mentor", mentor),):
            r = c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=headers)
            assert r.status_code == 403, (name, r.text)
            assert "token=" not in r.text
            cap(G, f"error_receipt_link_{name}_403", r)
        xws = c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=other)
        assert xws.status_code == 404, xws.text
        assert "token=" not in xws.text
        cap(G, "error_receipt_link_other_workspace_404", xws)

        # The link itself is the credential: no bearer token.
        bare = httpx.Client(timeout=20.0)
        served = bare.get(url)
        assert served.status_code == 200, served.text
        assert served.content == PNG
        capture_json(
            G,
            "receipt_download",
            {
                "status": served.status_code,
                "headers": {
                    k: served.headers[k]
                    for k in (
                        "content-type",
                        "x-content-type-options",
                        "cache-control",
                        "content-disposition",
                    )
                },
                "bytes": len(served.content),
                "url_shape": url.split("token=")[0] + "token=<signed token>",
            },
        )
        token = url.split("token=")[1]
        body, mac = token.split(".")
        tampered = bare.get(url.split("token=")[0], params={"token": f"{body}.{mac[:-3]}AAA"})
        assert tampered.status_code == 404, tampered.text
        cap(G, "error_receipt_download_tampered", tampered)
        garbage = bare.get(url.split("token=")[0], params={"token": "nonsense"})
        assert garbage.status_code == 404
        # The raw storage key is not reachable through any static path.
        key_guess = f"/var/storage/receipts/{ws}/x.png"
        assert bare.get(base_url + key_guess).status_code == 404
        bare.close()

        # A legacy row (uploaded when receipts were public): no raw URL in any response, and the
        # first open moves it behind authentication.
        legacy_id = _insert_legacy_expense(ws)
        legacy_get = c.get(f"/api/v1/finance/expenses/{legacy_id}", headers=f)
        assert legacy_get.status_code == 200, legacy_get.text
        cap(G, "expense_legacy_receipt", legacy_get)
        assert "var/storage" not in legacy_get.text
        assert _receipt_private_flag(legacy_id) is False
        legacy_link = c.get(f"/api/v1/finance/expenses/{legacy_id}/receipt", headers=f)
        assert legacy_link.status_code == 200, legacy_link.text
        cap(G, "receipt_link_legacy_first_open", legacy_link)
        assert _receipt_private_flag(legacy_id) is True

        gone = c.delete(f"/api/v1/finance/expenses/{eid}/receipt", headers=f)
        assert gone.status_code == 200, gone.text
        cap(G, "receipt_removed", gone)
        assert c.get(f"/api/v1/finance/expenses/{eid}/receipt", headers=f).status_code == 404

        # 3. Categories: one spelling per category.
        for desc, cat, amount in (
            ("Walk test: equipment", "Equipment", 4_500_000),
            ("Hosting plan", "Hosting", 20_000),
            ("Software seats", "Software", 33_000),
        ):
            r = c.post(
                "/api/v1/finance/transactions",
                headers=f,
                json={
                    "date": str(today),
                    "description": desc,
                    "category": cat,
                    "amount_minor": amount,
                    "direction": "out",
                },
            )
            assert r.status_code == 200, r.text
        lower = c.post(
            "/api/v1/finance/expenses",
            headers=f,
            json={
                "vendor": "Cloud host",
                "category": "  hosting ",
                "expense_date": str(today),
                "amount_minor": 5_000,
            },
        )
        assert lower.status_code == 200, lower.text
        assert lower.json()["data"]["category"] == "Hosting"
        cap(G, "expense_category_resolved_to_existing_spelling", lower)
        cats = c.get("/api/v1/finance/categories", headers=f)
        assert cats.status_code == 200, cats.text
        cap(G, "categories", cats)
        names = [x["name"] for x in cats.json()["data"]["categories"]]
        assert len({n.casefold() for n in names}) == len(names)
        assert c.get("/api/v1/finance/categories", headers=mentor).status_code == 403

        # 4. Budgets from the ledger (D-FIN-3) with unbudgeted rows.
        bud = c.post(
            "/api/v1/finance/budgets",
            headers=f,
            json={"category": "equipment", "period_month": month, "limit_minor": 10_000_000},
        )
        assert bud.status_code == 200, bud.text
        cap(G, "budget_created_for_equipment", bud)
        assert bud.json()["data"]["category"] == "Equipment"
        assert bud.json()["data"]["spent_minor"] == 4_500_000  # the MANUAL outflow counts
        listing = c.get("/api/v1/finance/budgets", headers=f, params={"month": month})
        assert listing.status_code == 200, listing.text
        cap(G, "budgets_list_with_unbudgeted_rows", listing)
        rows = {r["category"]: r for r in listing.json()["data"]["budgets"]}
        assert rows["Software"]["limit_minor"] is None and rows["Software"]["unbudgeted"] is True
        assert rows["Software"]["spent_minor"] == 33_000

        # 5. Pagination on the three lists.
        for i in range(5):
            c.post(
                "/api/v1/finance/transactions",
                headers=f,
                json={
                    "date": str(today),
                    "description": f"Sale {i}",
                    "amount_minor": 1_000 + i,
                    "direction": "in",
                },
            )
        page1 = c.get("/api/v1/finance/transactions", headers=f, params={"limit": 2})
        assert page1.status_code == 200, page1.text
        cap(G, "transactions_page_1", page1)
        cur = page1.json()["meta"]["next_cursor"]
        assert cur
        page2 = c.get("/api/v1/finance/transactions", headers=f, params={"limit": 2, "cursor": cur})
        cap(G, "transactions_page_2", page2)
        ids = [t["id"] for t in page1.json()["data"]["transactions"]] + [
            t["id"] for t in page2.json()["data"]["transactions"]
        ]
        assert len(set(ids)) == 4
        bad_cursor = c.get("/api/v1/finance/transactions", headers=f, params={"cursor": "garbage"})
        assert bad_cursor.status_code == 422, bad_cursor.text
        cap(G, "error_bad_cursor", bad_cursor)
        too_big = c.get("/api/v1/finance/transactions", headers=f, params={"limit": 500})
        assert too_big.status_code == 422, too_big.text
        cap(G, "error_limit_too_large", too_big)
        filtered = c.get(
            "/api/v1/finance/transactions", headers=f, params={"direction": "in", "limit": 1}
        )
        cap(G, "transactions_filtered_total", filtered)
        assert filtered.json()["meta"]["total_estimate"] == 5
        exp_page = c.get("/api/v1/finance/expenses", headers=f, params={"limit": 1})
        assert exp_page.status_code == 200, exp_page.text
        cap(G, "expenses_page_1", exp_page)
        for i in range(3):
            inv = c.post(
                "/api/v1/finance/invoices",
                headers=f,
                json={
                    "client_name": f"Client {i}",
                    "client_email": "client@example.com",
                    "line_items": [{"description": "Work", "quantity": 1, "unit_price_minor": 100}],
                },
            )
            assert inv.status_code == 200, inv.text
        inv_page = c.get("/api/v1/finance/invoices", headers=f, params={"limit": 2})
        assert inv_page.status_code == 200, inv_page.text
        cap(G, "invoices_page_1", inv_page)
        assert (
            inv_page.json()["meta"]["total_estimate"] == 3
            and inv_page.json()["meta"]["next_cursor"]
        )

        # 6. One model generation at a time.
        first = c.post("/api/v1/finance/model/generate", headers=f)
        assert first.status_code == 202, first.text
        cap(G, "model_generate_started", first)
        second = c.post("/api/v1/finance/model/generate", headers=team)
        assert second.status_code == 409, second.text
        cap(G, "error_model_generate_already_generating", second)
        _drain()
        done = c.get("/api/v1/finance/model", headers=f)
        assert done.json()["data"]["status"] == "complete", done.text
        again = c.post("/api/v1/finance/model/generate", headers=f)
        assert again.status_code == 202, again.text
        cap(G, "model_generate_after_complete", again)
        _drain()
