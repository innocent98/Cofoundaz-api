"""Live: Marketplace slice C. A founder books and pays for a consultation and a package; the
signed Paystack webhook (and a replay, and a forgery) confirms the payment; the expert accepts or
declines; declines and cancellations are refunded in full; a failed refund is put back on the
books; roles are enforced. Paystack is the in-process stub (PAYMENT_PROVIDER_STUB), the webhook
is signed with this run's test key. Responses go to e2e/_captures/marketplace_booking/ and are the
verbatim source of docs/fe-integration-guide-marketplace-booking.md.

Not exercised live (needs a clock or a failing Paystack): the 15-minute hold lapsing, the
48-hour acceptance expiry, refund retries, a saved card. Those are unit-tested."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pyotp

A = "/api/v1/admin/marketplace"
S = "/api/v1/super-admin"
P = "/api/v1/provider"
M = "/api/v1/marketplace"
T = "/api/v1/team"
WEBHOOK = "/api/v1/webhooks/billing/paystack"
G = "marketplace_booking"
PDF = b"%PDF-1.4\n%placeholder credential scan\n"
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."
DESC = "A focused one hour session reviewing your contract or term sheet, with written notes after."
BRIEF = "We need a review of our seed investment agreement before we sign next week."


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    def __init__(self, secret: str) -> None:
        self.totp = pyotp.TOTP(secret)
        self.used: set[str] = set()

    def fresh(self) -> str:
        for _ in range(80):
            now = datetime.now(UTC)
            for k in (0, -1, 1):
                c = self.totp.at(now + timedelta(seconds=30 * k))
                if c not in self.used:
                    self.used.add(c)
                    return c
            time.sleep(1)
        raise AssertionError("no unused authenticator code became available")


def _enroll(http: httpx.Client, u: dict) -> Authenticator:
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = _auth(login["access_token"])
    secret = http.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]["secret"]
    r = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert r.status_code == 200, r.text
    return Authenticator(secret)


def _sign_in(http: httpx.Client, u: dict, app: Authenticator) -> dict:
    gate = http.post("/api/v1/auth/login", json=u).json()["data"]
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def _login(http: httpx.Client, u: dict) -> dict:
    return _auth(http.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])


def _onboard(c: httpx.Client, auth: dict, name: str) -> str:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _webhook(http: httpx.Client, body: dict, *, signature: str | None = None) -> httpx.Response:
    raw = json.dumps(body, separators=(",", ":")).encode()
    key = os.environ["PAYSTACK_SECRET_KEY"]
    sig = (
        signature
        if signature is not None
        else hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    )
    return http.post(
        WEBHOOK,
        content=raw,
        headers={"x-paystack-signature": sig, "content-type": "application/json"},
    )


def test_marketplace_booking_journey(
    http: httpx.Client, base_url, make_verified_user, mailbox, capture, capture_json
):
    # ---- staff and an approved expert with two live listings ---------------------------------------
    sa_user = make_verified_user(http)
    sa_app = _enroll(http, sa_user)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", sa_user["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    sa = _sign_in(http, sa_user, sa_app)
    mod_user = make_verified_user(http)
    mod_app = _enroll(http, mod_user)
    r = http.post(
        f"{S}/staff",
        json={
            "email": mod_user["email"],
            "role": "admin",
            "scopes": ["moderation"],
            "reason": "Marketplace review rota",
            "totp_code": sa_app.fresh(),
        },
        headers=sa,
    )
    assert r.status_code == 201, r.text
    mod = _sign_in(http, mod_user, mod_app)

    expert = make_verified_user(http)
    r = http.post(
        f"{A}/providers/invite",
        json={"email": expert["email"], "category": "Legal", "display_name": "Ngozi Eze"},
        headers=mod,
    )
    pid = r.json()["data"]["provider"]["id"]
    token = mailbox.latest_token_for(expert["email"], subject_contains="invited")
    e = _login(http, expert)
    assert (
        http.post(
            f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=e
        ).status_code
        == 200
    )
    http.patch(
        f"{P}/me",
        json={
            "display_name": "Ngozi Eze",
            "headline": "Corporate lawyer for early-stage startups",
            "bio": BIO,
            "category": "Legal",
            "languages": ["English"],
            "timezone": "Africa/Lagos",
        },
        headers=e,
    )
    http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("bar-licence.pdf", PDF, "application/pdf")},
        headers=e,
    )
    assert http.post(f"{P}/submit", headers=e).status_code == 200
    http.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=365)).isoformat(),
                },
                "references": {"done": True, "count": 2},
            }
        },
        headers=mod,
    )
    r = http.post(
        f"{A}/providers/{pid}/approve",
        json={"reason": "Checked against the rubric", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 200, r.text
    http.put(
        f"{P}/availability",
        json={
            "min_notice_h": 0,
            "buffer_min": 0,
            "rules": [
                {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60} for d in range(7)
            ],
            "exceptions": [],
        },
        headers=e,
    )
    listings = {}
    for kind, body in (
        (
            "consult",
            {
                "type": "consultation",
                "title": "Contract review session",
                "summary": "A one hour review of your contract with written notes.",
                "description": DESC,
                "category": "Legal",
                "price_minor": 1_000_000,
                "duration_min": 60,
                "meeting_url": "https://meet.example.com/ngozi-eze",
            },
        ),
        (
            "package",
            {
                "type": "package",
                "title": "Shareholder agreement drafting",
                "summary": "A tailored shareholder agreement for your startup.",
                "description": DESC.replace(
                    "one hour session reviewing", "tailored agreement drafting"
                ),
                "category": "Legal",
                "price_minor": 25_000_000,
                "delivery_days": 7,
                "deliverables": ["Shareholder agreement (Word)", "One round of edits"],
            },
        ),
    ):
        lid = http.post(f"{P}/listings", json=body, headers=e).json()["data"]["id"]
        assert http.post(f"{P}/listings/{lid}/submit", headers=e).status_code == 200
        assert (
            http.post(
                f"{A}/listings/{lid}/approve",
                json={"reason": "Scope and price are clear"},
                headers=mod,
            ).status_code
            == 200
        )
        listings[kind] = lid

    # ---- two founders, a team member, a mentor ----------------------------------------------------
    def founder(name: str):
        user = make_verified_user(http)
        c = httpx.Client(base_url=base_url, timeout=15.0)
        auth = _login(c, user)
        ws = _onboard(c, auth, name)
        return user, c, {**auth, "X-Workspace-Id": ws}, ws

    f_user, fc, f, ws = founder("Book Co")
    _, oc, other, _ = founder("Rival Co")
    invited = {}
    for role in ("team_member", "mentor"):
        u = make_verified_user(http)
        r = fc.post(
            T + "/invitations", headers=f, json={"invites": [{"email": u["email"], "role": role}]}
        )
        assert r.status_code == 200, r.text
        tok = mailbox.latest_token_for(u["email"], subject_contains="invited")
        c = httpx.Client(base_url=base_url, timeout=15.0)
        a = _login(c, u)
        assert (
            c.post("/api/v1/invitations/accept", headers=a, json={"token": tok}).status_code == 200
        )
        invited[role] = (c, {**a, "X-Workspace-Id": ws})
    tm_c, tm = invited["team_member"]
    men_c, men = invited["mentor"]

    day = (date.today() + timedelta(days=2)).isoformat()
    slots = fc.get(
        f"{M}/listings/{listings['consult']}/slots", params={"from": day, "to": day}, headers=f
    ).json()["data"]["slots"]
    slot_a, slot_b = slots[0]["starts_at"], slots[1]["starts_at"]

    def book(client, headers, listing_id, *, key=None, **over):
        body = {"listing_id": listing_id, "brief": BRIEF, "accept_terms": True, **over}
        return client.post(
            f"{M}/bookings",
            json=body,
            headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
        )

    # ---- checkout status, roles, validation ---------------------------------------------------------
    r = fc.get(f"{M}/checkout", headers=f)
    assert r.status_code == 200 and r.json()["data"]["payments_enabled"] is True
    assert r.json()["data"]["terms_accepted"] is False and r.json()["data"]["can_book"] is True
    capture(G, "checkout_status_before_first_booking", r)
    r = tm_c.get(f"{M}/checkout", headers=tm)
    assert r.json()["data"]["can_book"] is False
    capture(G, "checkout_status_team_member", r)
    r = book(tm_c, tm, listings["consult"], starts_at=slot_a)
    assert r.status_code == 403
    capture(G, "booking_team_member_403", r)
    r = men_c.get(f"{M}/bookings", headers=men)
    assert r.status_code == 403
    capture(G, "bookings_mentor_403", r)
    r = http.post(f"{M}/bookings", json={})
    assert r.status_code == 401
    capture(G, "booking_anonymous_401", r)
    r = fc.post(
        f"{M}/bookings",
        json={"listing_id": listings["consult"], "brief": BRIEF, "starts_at": slot_a},
        headers=f,
    )
    assert (
        r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "Idempotency-Key"
    )
    capture(G, "booking_missing_idempotency_key_422", r)
    r = book(fc, f, listings["consult"], starts_at=slot_a, accept_terms=False)
    assert r.status_code == 422 and r.json()["error"]["code"] == "TERMS_REQUIRED"
    capture(G, "booking_terms_required_422", r)
    r = book(fc, f, listings["consult"], starts_at=slot_a, brief="short")
    assert r.status_code == 422
    capture(G, "booking_brief_too_short_422", r)
    r = book(fc, f, listings["consult"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "SLOT_REQUIRED"
    capture(G, "booking_slot_required_422", r)
    r = book(fc, f, listings["package"], starts_at=slot_a)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SLOT_NOT_APPLICABLE"
    capture(G, "booking_slot_not_applicable_422", r)
    r = book(
        fc,
        f,
        listings["consult"],
        starts_at=(datetime.now(UTC) + timedelta(days=2))
        .replace(hour=3, minute=0, second=0, microsecond=0)
        .isoformat(),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    capture(G, "booking_slot_unavailable_409", r)

    # ---- book a consultation: hold, replay, conflict, a rival ----------------------------------------
    r = book(fc, f, listings["consult"], key="journey-consult-0001", starts_at=slot_a)
    assert r.status_code == 201, r.text
    capture(G, "booking_create_consultation_201", r)
    booking = r.json()["data"]
    bid, ref = booking["id"], booking["order"]["reference"]
    r = book(fc, f, listings["consult"], key="journey-consult-0001", starts_at=slot_a)
    assert (
        r.status_code == 200
        and r.json()["data"]["replayed"] is True
        and r.json()["data"]["id"] == bid
    )
    capture(G, "booking_create_replay_200", r)
    r = book(fc, f, listings["consult"], key="journey-consult-0001", starts_at=slot_b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    capture(G, "booking_idempotency_conflict_409", r)
    r = book(oc, other, listings["consult"], starts_at=slot_a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    capture(G, "booking_rival_slot_held_409", r)
    r = fc.get(f"{M}/bookings/{bid}", headers=f)
    assert r.json()["data"]["status"] == "pending_payment"
    capture(G, "booking_get_pending_payment", r)
    r = fc.get(f"{M}/bookings", headers=f)
    assert r.json()["data"]["bookings"] == []  # a checkout hold is not a booking yet
    capture(G, "bookings_list_excludes_checkout_holds", r)
    r = oc.get(f"{M}/bookings/{bid}", headers=other)
    assert r.status_code == 404
    capture(G, "booking_get_other_workspace_404", r)

    # ---- the webhook: forged, signed, replayed ---------------------------------------------------------
    body = {"event": "charge.success", "data": {"reference": ref, "status": "success"}}
    r = _webhook(http, body, signature="0" * 128)
    assert r.status_code == 401
    capture(G, "webhook_bad_signature_401", r)
    r = _webhook(http, body)
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "paid", r.text
    capture(G, "webhook_charge_success_200", r)
    r = _webhook(http, body)
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "duplicate"
    capture(G, "webhook_replay_200", r)
    r = _webhook(
        http,
        {"event": "charge.success", "data": {"reference": "mp_" + "e" * 20, "status": "success"}},
    )
    assert r.status_code == 200
    capture(G, "webhook_unknown_reference_200", r)
    r = fc.post(f"{M}/bookings/{bid}/verify-payment", headers=f)
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "already_applied"
    capture(G, "booking_verify_payment_already_applied", r)
    r = fc.get(f"{M}/bookings/{bid}", headers=f)
    d = r.json()["data"]
    assert (
        d["status"] == "awaiting_provider"
        and d["order"]["status"] == "paid"
        and d["meeting_url"] is None
    )
    capture(G, "booking_get_awaiting_provider", r)
    mail = mailbox.latest_for(expert["email"])
    assert mail and "New booking request" in mail["subject"]
    capture_json(G, "email_provider_new_booking_request", mail)
    r = fc.get(f"{M}/orders", headers=f)
    assert [o["id"] for o in r.json()["data"]["orders"]] == [d["order"]["id"]]
    capture(G, "orders_list", r)
    r = tm_c.get(f"{M}/orders/{d['order']['id']}/receipt", headers=tm)
    assert r.status_code == 200 and r.json()["data"]["is_tax_invoice"] is False
    capture(G, "order_receipt", r)
    r = oc.get(f"{M}/orders/{d['order']['id']}/receipt", headers=other)
    assert r.status_code == 404
    capture(G, "order_receipt_other_workspace_404", r)

    # ---- the expert's side ---------------------------------------------------------------------------------
    r = http.get(f"{P}/bookings", headers=e)
    assert [b["id"] for b in r.json()["data"]["bookings"]] == [bid]
    assert "Book Co" not in r.text and ws not in r.text
    capture(G, "provider_bookings_list", r)
    r = http.get(f"{P}/bookings", headers=_login(http, f_user))
    assert r.status_code == 403
    capture(G, "provider_bookings_not_a_provider_403", r)
    r = http.post(f"{P}/bookings/{bid}/accept", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "confirmed"
    capture(G, "provider_accept_consultation_200", r)
    r = http.post(f"{P}/bookings/{bid}/accept", headers=e)
    assert r.status_code == 409
    capture(G, "provider_accept_twice_409", r)
    r = fc.get(f"{M}/bookings/{bid}", headers=f)
    assert r.json()["data"]["meeting_url"] == "https://meet.example.com/ngozi-eze"
    capture(G, "booking_get_confirmed_with_meeting_link", r)
    r = fc.post(f"{M}/bookings/{bid}/cancel", headers=f)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CANCEL_NOT_ALLOWED"
    capture(G, "booking_cancel_after_accept_409", r)

    # ---- a package the expert declines: full refund ------------------------------------------------------------
    r = book(fc, f, listings["package"])
    assert r.status_code == 201, r.text
    capture(G, "booking_create_package_201", r)
    pkg = r.json()["data"]
    assert (
        _webhook(
            http, {"event": "charge.success", "data": {"reference": pkg["order"]["reference"]}}
        ).status_code
        == 200
    )
    r = http.post(f"{P}/bookings/{pkg['id']}/decline", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "declined"
    capture(G, "provider_decline_200", r)
    r = fc.get(f"{M}/bookings/{pkg['id']}", headers=f)
    d2 = r.json()["data"]
    assert d2["status"] == "declined" and d2["order"]["status"] == "refunded"
    assert d2["order"]["refunded_minor"] == 25_000_000
    capture(G, "booking_get_declined_refunded", r)
    capture_json(G, "email_founder_declined_refunded", mailbox.latest_for(f_user["email"]))
    r = _webhook(
        http,
        {
            "event": "refund.failed",
            "data": {
                "transaction_reference": pkg["order"]["reference"],
                "refund_reference": "rf_e2e_1",
                "amount": 25_000_000,
            },
        },
    )
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "refund_reversed"
    capture(G, "webhook_refund_failed_reversed_200", r)
    r = fc.get(f"{M}/bookings/{pkg['id']}", headers=f)
    assert r.json()["data"]["order"]["refunded_minor"] == 0
    capture(G, "booking_get_after_refund_failed", r)

    # ---- the founder cancels before the expert answers --------------------------------------------------------------
    r = book(fc, f, listings["consult"], starts_at=slot_b)
    c3 = r.json()["data"]
    assert (
        _webhook(
            http, {"event": "charge.success", "data": {"reference": c3["order"]["reference"]}}
        ).status_code
        == 200
    )
    r = tm_c.post(f"{M}/bookings/{c3['id']}/cancel", headers=tm)
    assert r.status_code == 403
    capture(G, "booking_cancel_team_member_403", r)
    r = fc.post(f"{M}/bookings/{c3['id']}/cancel", headers=f)
    assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
    assert r.json()["data"]["order"]["status"] == "refunded"
    capture(G, "booking_cancel_refunds_200", r)
    r = fc.get(f"{M}/bookings", params={"tab": "closed"}, headers=f)
    assert {b["id"] for b in r.json()["data"]["bookings"]} == {pkg["id"], c3["id"]}
    capture(G, "bookings_list_closed_tab", r)
    r = fc.get(f"{M}/bookings", params={"tab": "upcoming"}, headers=f)
    assert [b["id"] for b in r.json()["data"]["bookings"]] == [bid]
    capture(G, "bookings_list_upcoming_tab", r)
    r = fc.get(f"{M}/checkout", headers=f)
    assert r.json()["data"]["terms_accepted"] is True
    capture(G, "checkout_status_after_booking", r)
