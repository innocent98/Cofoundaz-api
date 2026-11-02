"""Live Finance payments journey (Paystack + Flutterwave connections, sync, pay links, webhooks).

A founder connects their own payment provider account, imports its NGN transactions into the Finance
ledger, puts a "Pay now" link on a sent invoice, and has the invoice marked paid from a SIGNED
provider webhook. The server runs with PAYMENT_PROVIDER_STUB=true (scripts/e2e_run.sh), so the
provider HTTP calls (key check, listing, link creation) are answered by the deterministic stub in
`app/services/finance/payments/stub_transport.py` INSIDE the real uvicorn process. What is therefore
exercised live is OUR side: routes, RBAC, encryption at rest, import, idempotency, signature
checks, state changes. What is NOT exercised is the real Paystack / Flutterwave side (see the
verification table in docs/fe-integration-guide-finance-payments.md).

The test computes webhook signatures itself, with the key it connected: HMAC-SHA512 hex of the raw
body (`x-paystack-signature`) for Paystack, the stored secret hash (`verif-hash`) for Flutterwave.

Walk: invite team_member / accountant / mentor -> GET integrations (empty) -> 403 matrix -> connect
validation errors -> connect Paystack -> 409 for the other provider -> sync (charge + fee rows) ->
imported rows are read-only except `category` -> sent invoice gets a pay link, emailed -> webhook
(bad / missing signature 401, valid marks paid, duplicate acked) -> mark-unpaid 422 -> amount
mismatch ignored -> needs_attention (forced in the DB, no other way to age a key) -> replace ->
disconnect keeps the ledger -> pay-link without a connection 422 -> connect Flutterwave -> pay-link
retry -> `verif-hash` webhook.

Every response body is captured, complete and untrimmed, to `e2e/_captures/finance_payments/*.json`
(the verbatim source for docs/fe-integration-guide-finance-payments.md). Every captured body is
also asserted to contain none of the secrets this test sent.

The journey reads the invoice email back out of the file mailbox, so it uses the `mailbox` fixture:
remote mode (E2E_REMOTE=1, a deployed environment where the stub must never be on) deselects it.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import httpx

G = "finance_payments"

# NOTE: app-internal imports (SessionLocal, worker) are done LAZILY inside the helpers, not at
# module level -- importing them at collection time would instantiate Settings, which the remote
# live-e2e gate (no app env) cannot do.


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
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


def _force_status(connection_id: str, status: str, last_error: str | None) -> None:
    """Forces a connection status. The only way to reach `needs_attention` live: the stub accepts
    the saved key forever, so a provider-side key revocation cannot be simulated over HTTP."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE finance_connections SET status = :s, last_error = :e WHERE id = :id"),
            {"s": status, "e": last_error, "id": uuid.UUID(connection_id)},
        )
        db.commit()
    finally:
        db.close()


def _from(resp: httpx.Response, source: str) -> list[dict]:
    """Ledger rows of one `source` (the list endpoint has no source filter: the FE filters)."""
    return [r for r in resp.json()["data"]["transactions"] if r["source"] == source]


def _paystack_signature(secret_key: str, raw: bytes) -> str:
    return hmac.new(secret_key.encode(), raw, hashlib.sha512).hexdigest()


def _ref_from_pay_url(pay_url: str) -> str:
    """The stub embeds the pay_reference after `stub-` in the checkout URL."""
    return pay_url.rsplit("/stub-", 1)[1]


def _paystack_charge_body(reference: str, amount_minor: int, event_id: int) -> bytes:
    return json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": event_id,
                "reference": reference,
                "status": "success",
                "amount": amount_minor,
                "fees": 2420,
                "currency": "NGN",
                "paid_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
        }
    ).encode()


def _flutterwave_charge_body(reference: str, amount_minor: int, event_id: int) -> bytes:
    return json.dumps(
        {
            "event": "charge.completed",
            "data": {
                "id": event_id,
                "tx_ref": reference,
                "status": "successful",
                "amount": amount_minor / 100,
                "app_fee": 24.2,
                "currency": "NGN",
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
        }
    ).encode()


def test_finance_payments_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    # Placeholder-shaped secrets, generated per run (uuid hex: never a human-style password).
    # The stub rejects any key containing "bad", which a random hex string sometimes does.
    ps_key = f"sk_test_{uuid.uuid4().hex.replace('bad', 'bed')}"
    ps_key_2 = f"sk_test_{uuid.uuid4().hex.replace('bad', 'bed')}"
    fw_key = f"FLWSECK_TEST-{uuid.uuid4().hex}-X"
    fw_hash = f"hash{uuid.uuid4().hex}"
    secrets_sent = [ps_key, ps_key_2, fw_key, fw_hash]

    def cap(name: str, resp: httpx.Response) -> None:
        for s in secrets_sent:
            assert s not in resp.text, f"secret leaked into {name}"
        capture(G, name, resp)

    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        # 0. Onboard a founder and invite the other roles.
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Payments Co"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        team = _join(c, wh, ws, "team_member", unique_email, mailbox)
        accountant = _join(c, wh, ws, "accountant", unique_email, mailbox)
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)

        base = "/api/v1/finance/integrations"

        # 1. Empty state, readable by every finance role; not by a mentor; not unauthenticated.
        empty = c.get(base, headers=wh)
        assert empty.status_code == 200, empty.text
        d = empty.json()["data"]
        assert d["encryption_configured"] is True
        assert [p["provider"] for p in d["providers"]] == ["paystack", "flutterwave", "mono"]
        assert all(p["connection"] is None for p in d["providers"])
        cap("integrations_empty", empty)
        assert c.get(base, headers=accountant).status_code == 200
        assert c.get(base, headers=team).status_code == 200
        mentor_get = c.get(base, headers=mentor)
        assert mentor_get.status_code == 403, mentor_get.text
        cap("error_integrations_mentor_forbidden", mentor_get)
        unauth = c.get(base)
        assert unauth.status_code == 401, unauth.text
        cap("error_integrations_unauthenticated", unauth)

        # 2. Only a founder connects / disconnects.
        connect_body = {"secret_key": ps_key}
        team_connect = c.post(f"{base}/paystack/connect", headers=team, json=connect_body)
        assert team_connect.status_code == 403, team_connect.text
        cap("error_connect_team_member_forbidden", team_connect)
        acct_connect = c.post(f"{base}/paystack/connect", headers=accountant, json=connect_body)
        assert acct_connect.status_code == 403, acct_connect.text
        cap("error_connect_accountant_forbidden", acct_connect)
        mentor_connect = c.post(f"{base}/paystack/connect", headers=mentor, json=connect_body)
        assert mentor_connect.status_code == 403, mentor_connect.text
        cap("error_connect_mentor_forbidden", mentor_connect)
        acct_disc = c.post(f"{base}/paystack/disconnect", headers=accountant)
        assert acct_disc.status_code == 403, acct_disc.text
        cap("error_disconnect_accountant_forbidden", acct_disc)
        mentor_sync = c.post(f"{base}/paystack/sync", headers=mentor)
        assert mentor_sync.status_code == 403, mentor_sync.text
        cap("error_sync_mentor_forbidden", mentor_sync)

        # 3. Connect validation errors (all before any provider call except the rejected key).
        pk = c.post(
            f"{base}/paystack/connect", headers=wh, json={"secret_key": "pk_test_publickey"}
        )
        assert pk.status_code == 422, pk.text
        cap("error_connect_public_key", pk)
        wrong = c.post(f"{base}/paystack/connect", headers=wh, json={"secret_key": fw_key})
        assert wrong.status_code == 422, wrong.text
        cap("error_connect_wrong_provider_prefix", wrong)
        no_hash = c.post(f"{base}/flutterwave/connect", headers=wh, json={"secret_key": fw_key})
        assert no_hash.status_code == 422, no_hash.text
        cap("error_connect_flutterwave_missing_hash", no_hash)
        rejected = c.post(
            f"{base}/paystack/connect", headers=wh, json={"secret_key": "sk_test_badb_adbadbad"}
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "PAYMENT_KEY_REJECTED"
        cap("error_connect_key_rejected", rejected)
        unknown = c.post(f"{base}/stripe/connect", headers=wh, json=connect_body)
        assert unknown.status_code == 404, unknown.text
        cap("error_connect_unknown_provider", unknown)

        # 4. Connect Paystack.
        connected = c.post(f"{base}/paystack/connect", headers=wh, json=connect_body)
        assert connected.status_code == 200, connected.text
        conn = connected.json()["data"]
        assert conn["provider"] == "paystack" and conn["status"] == "active"
        assert conn["mode"] == "test" and conn["key_last4"] == ps_key[-4:]
        assert conn["last_sync_at"] is None
        assert conn["webhook_url"].startswith(base_url)
        assert "/webhooks/payments/paystack/" in conn["webhook_url"]
        for forbidden in ("secret_key", "key_ciphertext", "secret_hash"):
            assert forbidden not in conn
        cap("connect_paystack", connected)
        ps_conn_id = conn["id"]
        ps_webhook = conn["webhook_url"]

        listed = c.get(base, headers=wh)
        assert listed.status_code == 200, listed.text
        entries = {p["provider"]: p for p in listed.json()["data"]["providers"]}
        assert entries["paystack"]["connection"]["id"] == ps_conn_id
        assert entries["flutterwave"]["connection"] is None
        cap("integrations_paystack_connected", listed)

        # 5. One active connection per workspace.
        other = c.post(
            f"{base}/flutterwave/connect",
            headers=wh,
            json={"secret_key": fw_key, "secret_hash": fw_hash},
        )
        assert other.status_code == 409, other.text
        cap("error_connect_other_provider_conflict", other)

        # 6. Sync (an accountant may trigger it): the stub lists one NGN charge with a fee.
        synced = c.post(f"{base}/paystack/sync", headers=accountant)
        assert synced.status_code == 200, synced.text
        s = synced.json()["data"]
        assert s["summary"]["imported"] == 1 and s["summary"]["fees"] == 1
        assert s["connection"]["last_sync_at"] is not None
        cap("sync_paystack_first", synced)
        again = c.post(f"{base}/paystack/sync", headers=wh)
        assert again.status_code == 200, again.text
        again_summary = again.json()["data"]["summary"]
        assert again_summary["imported"] == 0 and again_summary["fees"] == 0  # idempotent
        cap("sync_paystack_again", again)

        ledger = c.get("/api/v1/finance/transactions", headers=wh)
        assert ledger.status_code == 200, ledger.text
        rows = _from(ledger, "paystack")
        by_cat = {r["category"]: r for r in rows}
        assert (
            by_cat["Revenue"]["direction"] == "in" and by_cat["Revenue"]["amount_minor"] == 500000
        )
        assert by_cat["Payment fees"]["direction"] == "out"
        assert by_cat["Payment fees"]["amount_minor"] == 7500
        assert all(r["source"] == "paystack" for r in rows) and len(rows) == 2
        cap("transactions_list_all_after_paystack_sync", ledger)

        # 7. Imported rows are read-only except the category.
        rev_id = by_cat["Revenue"]["id"]
        cat = c.patch(
            f"/api/v1/finance/transactions/{rev_id}", headers=wh, json={"category": "Sales"}
        )
        assert cat.status_code == 200, cat.text
        assert cat.json()["data"]["category"] == "Sales"
        cap("transaction_imported_category_patched", cat)
        amt = c.patch(
            f"/api/v1/finance/transactions/{rev_id}", headers=wh, json={"amount_minor": 1}
        )
        assert amt.status_code == 422, amt.text
        cap("error_patch_imported_amount", amt)
        dele = c.delete(f"/api/v1/finance/transactions/{rev_id}", headers=wh)
        assert dele.status_code == 422, dele.text
        cap("error_delete_imported_transaction", dele)

        # 8. A sent invoice gets a pay link, and the client email carries it.
        client_email = f"delivered+pay-{uuid.uuid4().hex[:12]}@resend.dev"

        def make_invoice(name: str, email: str, qty: int = 1, unit: int = 100_000) -> dict:
            r = c.post(
                "/api/v1/finance/invoices",
                headers=wh,
                json={
                    "client_name": name,
                    "client_email": email,
                    "line_items": [
                        {"description": "Retainer", "quantity": qty, "unit_price_minor": unit}
                    ],
                    "tax_percent": 0,
                    "terms": "net_30",
                },
            )
            assert r.status_code == 200, r.text
            return r.json()["data"]

        inv1 = make_invoice("Acme Traders Ltd", client_email)
        draft_link = c.post(f"/api/v1/finance/invoices/{inv1['id']}/pay-link", headers=wh)
        assert draft_link.status_code == 422, draft_link.text
        cap("error_pay_link_on_draft", draft_link)
        sent = c.post(f"/api/v1/finance/invoices/{inv1['id']}/send", headers=wh)
        assert sent.status_code == 200, sent.text
        i1 = sent.json()["data"]
        assert i1["pay_url"].startswith("https://checkout.paystack.com/")
        assert i1["pay_provider"] == "paystack" and i1["paid_via"] is None
        cap("invoice_sent_with_pay_link", sent)
        _drain()
        mail = mailbox.latest_for(client_email)
        assert mail is not None and i1["pay_url"] in mail["html"]
        capture_json(G, "invoice_email_with_pay_link", mail)
        reuse = c.post(f"/api/v1/finance/invoices/{inv1['id']}/pay-link", headers=wh)
        assert reuse.status_code == 200, reuse.text
        assert reuse.json()["data"]["pay_url"] == i1["pay_url"]  # existing link reused
        cap("pay_link_reused", reuse)
        mentor_link = c.post(f"/api/v1/finance/invoices/{inv1['id']}/pay-link", headers=mentor)
        assert mentor_link.status_code == 403, mentor_link.text
        cap("error_pay_link_mentor_forbidden", mentor_link)
        ref1 = _ref_from_pay_url(i1["pay_url"])
        total1 = i1["total_minor"]

        # 9. Webhooks: public route, signature over the raw bytes, checked before parsing.
        body1 = _paystack_charge_body(ref1, total1, 880001)
        good_sig = _paystack_signature(ps_key, body1)
        json_ct = {"Content-Type": "application/json"}

        bad = c.post(
            ps_webhook,
            content=body1,
            headers={**json_ct, "x-paystack-signature": _paystack_signature(ps_key_2, body1)},
        )
        assert bad.status_code == 401, bad.text
        cap("error_webhook_wrong_signature", bad)
        missing = c.post(ps_webhook, content=body1, headers=json_ct)
        assert missing.status_code == 401, missing.text
        cap("error_webhook_missing_signature", missing)
        tampered = c.post(
            ps_webhook,
            content=body1.replace(b"880001", b"880002"),
            headers={**json_ct, "x-paystack-signature": good_sig},
        )
        assert tampered.status_code == 401, tampered.text
        cap("error_webhook_tampered_body", tampered)
        unknown_wh = c.post(
            ps_webhook.rsplit("/", 1)[0] + "/" + uuid.uuid4().hex,
            content=body1,
            headers={**json_ct, "x-paystack-signature": good_sig},
        )
        assert unknown_wh.status_code == 404, unknown_wh.text
        cap("error_webhook_unknown_connection", unknown_wh)
        still_sent = c.get(f"/api/v1/finance/invoices/{inv1['id']}", headers=wh)
        assert still_sent.json()["data"]["status"] == "sent"

        ok = c.post(
            ps_webhook, content=body1, headers={**json_ct, "x-paystack-signature": good_sig}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"] == {"received": True}
        cap("webhook_paystack_charge_accepted", ok)
        dup = c.post(
            ps_webhook, content=body1, headers={**json_ct, "x-paystack-signature": good_sig}
        )
        assert dup.status_code == 200, dup.text
        assert dup.json()["data"] == {"received": True, "duplicate": True}
        cap("webhook_paystack_charge_duplicate", dup)

        paid = c.get(f"/api/v1/finance/invoices/{inv1['id']}", headers=wh)
        assert paid.status_code == 200, paid.text
        p = paid.json()["data"]
        assert p["status"] == "paid" and p["paid_via"] == "paystack"
        assert p["paid_at"] is not None and p["transaction_id"] is not None
        cap("invoice_paid_via_paystack", paid)
        inflows = c.get("/api/v1/finance/transactions", headers=wh, params={"direction": "in"})
        assert [r["id"] for r in _from(inflows, "invoice")] == [p["transaction_id"]]
        cap("transactions_list_inflows_after_invoice_paid", inflows)
        fees = c.get("/api/v1/finance/transactions", headers=wh)
        fee_rows = [r for r in _from(fees, "paystack") if r["amount_minor"] == 2420]
        assert len(fee_rows) == 1 and fee_rows[0]["category"] == "Payment fees"
        cap("transactions_list_after_webhook", fees)

        unpaid = c.post(f"/api/v1/finance/invoices/{inv1['id']}/mark-unpaid", headers=wh)
        assert unpaid.status_code == 422, unpaid.text
        cap("error_mark_unpaid_provider_paid", unpaid)
        manual = c.post(f"/api/v1/finance/invoices/{inv1['id']}/mark-paid", headers=wh)
        assert manual.status_code == 200, manual.text
        assert manual.json()["data"]["transaction_id"] == p["transaction_id"]
        cap("invoice_mark_paid_after_webhook_noop", manual)

        # 9b. Amount mismatch: acknowledged, but the invoice stays `sent`.
        inv2 = make_invoice("Beta Foods", f"delivered+pay-{uuid.uuid4().hex[:12]}@resend.dev", 2)
        i2 = c.post(f"/api/v1/finance/invoices/{inv2['id']}/send", headers=wh).json()["data"]
        assert i2["pay_url"] is not None
        body2 = _paystack_charge_body(
            _ref_from_pay_url(i2["pay_url"]), i2["total_minor"] - 1, 880003
        )
        mismatch = c.post(
            ps_webhook,
            content=body2,
            headers={**json_ct, "x-paystack-signature": _paystack_signature(ps_key, body2)},
        )
        assert mismatch.status_code == 200, mismatch.text
        cap("webhook_paystack_amount_mismatch_acked", mismatch)
        after = c.get(f"/api/v1/finance/invoices/{inv2['id']}", headers=wh)
        assert after.json()["data"]["status"] == "sent"
        cap("invoice_still_sent_after_mismatch", after)

        # 10. needs_attention (forced in the DB) and replace.
        _force_status(
            ps_conn_id,
            "needs_attention",
            "The provider rejected the saved key. Replace it to resume syncing.",
        )
        attn = c.get(base, headers=wh)
        ps_entry = next(p for p in attn.json()["data"]["providers"] if p["provider"] == "paystack")
        assert ps_entry["connection"]["status"] == "needs_attention"
        cap("integrations_needs_attention_forced", attn)
        attn_sync = c.post(f"{base}/paystack/sync", headers=wh)
        assert attn_sync.status_code == 404, attn_sync.text
        cap("error_sync_needs_attention", attn_sync)
        replaced = c.post(f"{base}/paystack/connect", headers=wh, json={"secret_key": ps_key_2})
        assert replaced.status_code == 200, replaced.text
        r = replaced.json()["data"]
        assert r["id"] == ps_conn_id and r["status"] == "active" and r["last_error"] is None
        assert r["key_last4"] == ps_key_2[-4:]
        assert r["webhook_url"] == ps_webhook  # replace keeps the webhook URL
        cap("connect_paystack_replaced", replaced)

        # 11. Disconnect keeps the imported ledger and revokes the webhook URL.
        disc = c.post(f"{base}/paystack/disconnect", headers=wh)
        assert disc.status_code == 200, disc.text
        assert disc.json()["data"]["status"] == "disconnected"
        cap("disconnect_paystack", disc)
        kept = c.get("/api/v1/finance/transactions", headers=wh)
        assert len(_from(kept, "paystack")) == 3  # charge, fee, webhook fee
        cap("transactions_list_after_disconnect", kept)
        post_disc_sync = c.post(f"{base}/paystack/sync", headers=wh)
        assert post_disc_sync.status_code == 404, post_disc_sync.text
        cap("error_sync_disconnected", post_disc_sync)
        body_late = _paystack_charge_body("late_ref", 1000, 880004)
        late = c.post(
            ps_webhook,
            content=body_late,
            headers={**json_ct, "x-paystack-signature": _paystack_signature(ps_key_2, body_late)},
        )
        assert late.status_code == 404, late.text
        cap("error_webhook_disconnected_connection", late)
        empty_again = c.get(base, headers=wh)
        assert all(p["connection"] is None for p in empty_again.json()["data"]["providers"])
        cap("integrations_after_disconnect", empty_again)

        # 12. A sent invoice with NO connection has no link; pay-link says why.
        inv3 = make_invoice("Gamma Ltd", f"delivered+pay-{uuid.uuid4().hex[:12]}@resend.dev")
        i3 = c.post(f"/api/v1/finance/invoices/{inv3['id']}/send", headers=wh).json()["data"]
        assert i3["pay_url"] is None and i3["pay_provider"] is None
        cap(
            "invoice_sent_without_connection",
            c.get(f"/api/v1/finance/invoices/{inv3['id']}", headers=wh),
        )
        no_conn = c.post(f"/api/v1/finance/invoices/{inv3['id']}/pay-link", headers=wh)
        assert no_conn.status_code == 422, no_conn.text
        cap("error_pay_link_no_connection", no_conn)

        # 13. Connect Flutterwave (secret hash required), retry the pay link, sync.
        fw = c.post(
            f"{base}/flutterwave/connect",
            headers=wh,
            json={"secret_key": fw_key, "secret_hash": fw_hash},
        )
        assert fw.status_code == 200, fw.text
        fwc = fw.json()["data"]
        assert fwc["provider"] == "flutterwave" and fwc["mode"] == "test"
        assert fwc["id"] != ps_conn_id
        cap("connect_flutterwave", fw)
        fw_webhook = fwc["webhook_url"]
        assert fw_webhook != ps_webhook and "/webhooks/payments/flutterwave/" in fw_webhook
        retry = c.post(f"/api/v1/finance/invoices/{inv3['id']}/pay-link", headers=wh)
        assert retry.status_code == 200, retry.text
        i3r = retry.json()["data"]
        assert i3r["pay_url"].startswith("https://checkout.flutterwave.com/")
        assert i3r["pay_provider"] == "flutterwave"
        cap("pay_link_retry_flutterwave", retry)
        fw_sync = c.post(f"{base}/flutterwave/sync", headers=wh)
        assert fw_sync.status_code == 200, fw_sync.text
        fw_summary = fw_sync.json()["data"]["summary"]
        assert fw_summary["imported"] == 1 and fw_summary["fees"] == 1
        cap("sync_flutterwave", fw_sync)
        fw_ledger = c.get("/api/v1/finance/transactions", headers=wh)
        fw_rows = {r["category"]: r for r in _from(fw_ledger, "flutterwave")}
        assert fw_rows["Revenue"]["amount_minor"] == 500000
        assert fw_rows["Payment fees"]["amount_minor"] == 7550
        cap("transactions_list_after_flutterwave_sync", fw_ledger)

        # 14. Flutterwave webhook: the `verif-hash` header carries the stored secret hash.
        fw_ref = _ref_from_pay_url(i3r["pay_url"])
        fw_body = _flutterwave_charge_body(fw_ref, i3r["total_minor"], 990001)
        fw_bad = c.post(fw_webhook, content=fw_body, headers={**json_ct, "verif-hash": "wrong"})
        assert fw_bad.status_code == 401, fw_bad.text
        cap("error_webhook_flutterwave_wrong_hash", fw_bad)
        fw_empty = c.post(fw_webhook, content=fw_body, headers={**json_ct, "verif-hash": ""})
        assert fw_empty.status_code == 401, fw_empty.text
        cap("error_webhook_flutterwave_empty_hash", fw_empty)
        fw_ok = c.post(fw_webhook, content=fw_body, headers={**json_ct, "verif-hash": fw_hash})
        assert fw_ok.status_code == 200, fw_ok.text
        assert fw_ok.json()["data"] == {"received": True}
        cap("webhook_flutterwave_charge_accepted", fw_ok)
        fw_dup = c.post(fw_webhook, content=fw_body, headers={**json_ct, "verif-hash": fw_hash})
        assert fw_dup.json()["data"] == {"received": True, "duplicate": True}
        cap("webhook_flutterwave_charge_duplicate", fw_dup)
        fw_paid = c.get(f"/api/v1/finance/invoices/{inv3['id']}", headers=wh)
        assert fw_paid.json()["data"]["status"] == "paid"
        assert fw_paid.json()["data"]["paid_via"] == "flutterwave"
        cap("invoice_paid_via_flutterwave", fw_paid)
        fw_unpaid = c.post(f"/api/v1/finance/invoices/{inv3['id']}/mark-unpaid", headers=wh)
        assert fw_unpaid.status_code == 422, fw_unpaid.text
        cap("error_mark_unpaid_provider_paid_flutterwave", fw_unpaid)

        # 15. The invoice list carries the new fields.
        inv_list = c.get("/api/v1/finance/invoices", headers=wh)
        assert inv_list.status_code == 200, inv_list.text
        cap("invoices_list", inv_list)
