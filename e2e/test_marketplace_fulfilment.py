"""Live: Marketplace slice D. A founder briefs a package with an attached file, pays (signed
webhook), the expert accepts, both sides message (before and after booking), the expert delivers,
the founder asks for changes twice, hits the revision limit and accepts; a Document is shared with
the expert for 30 days and revoked when the booking closes; a consultation gets its Calendar
meeting and .ics; roles and other workspaces are refused. Paystack is the in-process stub, the
webhook is signed with this run's test key. Responses go to e2e/_captures/marketplace_fulfilment/
and are the verbatim source of docs/fe-integration-guide-marketplace-fulfilment.md.

THE CLOCK. The server runs on real time, so the journey moves it the only honest way: it rewrites
the booking's slot / auto-release / due timestamps IN THE E2E DATABASE (marked `[sql]` below) and
runs the marketplace timer in-process against that same database (`timers.tick`, as the worker
does). Those steps are real HTTP calls on top of a doctored clock, not live waiting; the guide says
so. The staff attention flags are likewise set by SQL because the Paystack stub cannot produce a
mismatched amount."""

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
from sqlalchemy import create_engine, text

A = "/api/v1/admin/marketplace"
S = "/api/v1/super-admin"
P = "/api/v1/provider"
M = "/api/v1/marketplace"
T = "/api/v1/team"
WEBHOOK = "/api/v1/webhooks/billing/paystack"
G = "marketplace_fulfilment"
PDF = b"%PDF-1.4\n%placeholder credential scan\n"
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."
DESC = "A focused one hour session reviewing your contract or term sheet, with written notes after."
BRIEF = "We need a shareholder agreement drafted before we close our seed round next month."


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


def _webhook(http: httpx.Client, body: dict) -> httpx.Response:
    raw = json.dumps(body, separators=(",", ":")).encode()
    key = os.environ["PAYSTACK_SECRET_KEY"]
    sig = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return http.post(
        WEBHOOK,
        content=raw,
        headers={"x-paystack-signature": sig, "content-type": "application/json"},
    )


def idem(name: str) -> str:
    return f"journey-{name}-0001"


def sql(statement: str, **params):
    """[sql] move the clock / set a flag in the e2e database. Never used to skip an API step."""
    eng = create_engine(os.environ["DATABASE_URL"])
    try:
        with eng.begin() as conn:
            return conn.execute(text(statement), params)
    finally:
        eng.dispose()


def tick(at: datetime) -> dict:
    """Run the marketplace timer in-process against the e2e database, as the worker would."""
    from app.db.session import SessionLocal
    from app.services.marketplace import timers
    from app.services.notifications.registry import register  # the worker does this at start-up

    register()
    db = SessionLocal()
    try:
        return timers.tick(db, at)
    finally:
        db.close()


def test_marketplace_fulfilment_journey(
    http: httpx.Client, base_url, make_verified_user, mailbox, capture, capture_json
):
    # ---- staff and two approved experts ----------------------------------------------------------
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

    def approved_expert(name: str):
        user = make_verified_user(http)
        r = http.post(
            f"{A}/providers/invite",
            json={"email": user["email"], "category": "Legal", "display_name": name},
            headers=mod,
        )
        pid = r.json()["data"]["provider"]["id"]
        token = mailbox.latest_token_for(user["email"], subject_contains="invited")
        e = _login(http, user)
        assert (
            http.post(
                f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=e
            ).status_code
            == 200
        )
        http.patch(
            f"{P}/me",
            json={
                "display_name": name,
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
                    {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60}
                    for d in range(7)
                ],
                "exceptions": [],
            },
            headers=e,
        )
        return user, e, pid

    expert, e, pid = approved_expert("Ngozi Eze")
    _, e2, _ = approved_expert("Tunde Bello")

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
                "revisions_included": 2,
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

    # ---- two founders, a team member, a mentor ---------------------------------------------------
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
    slot_a, slot_b, slot_c = (s["starts_at"] for s in slots[:3])

    def book(client, headers, listing_id, *, key=None, **over):
        body = {"listing_id": listing_id, "brief": BRIEF, "accept_terms": True, **over}
        return client.post(
            f"{M}/bookings",
            json=body,
            headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
        )

    def pay(booking: dict) -> None:
        r = _webhook(
            http, {"event": "charge.success", "data": {"reference": booking["order"]["reference"]}}
        )
        assert r.status_code == 200 and r.json()["data"]["outcome"] == "paid", r.text

    def upload(
        client, headers, base, purpose, content=PDF, name="brief.pdf", ctype="application/pdf"
    ):
        return client.post(
            f"{base}/files",
            data={"purpose": purpose},
            files={"file": (name, content, ctype)},
            headers=headers,
        )

    # ---- before any booking: a conversation with the expert -------------------------------------------
    r = tm_c.post(f"{M}/threads", json={"provider_id": pid}, headers=tm)
    assert r.status_code == 403
    capture(G, "thread_open_team_member_403", r)
    r = fc.post(f"{M}/threads", json={"provider_id": str(uuid.uuid4())}, headers=f)
    assert r.status_code == 404
    capture(G, "thread_open_unknown_expert_404", r)
    r = fc.post(f"{M}/threads", json={"provider_id": pid}, headers=f)
    assert r.status_code == 201, r.text
    capture(G, "thread_open_201", r)
    thread0 = r.json()["data"]["id"]
    r = fc.post(f"{M}/threads", json={"provider_id": pid}, headers=f)
    assert r.status_code == 200 and r.json()["data"]["id"] == thread0
    capture(G, "thread_open_again_200", r)
    r = http.get(f"{P}/threads", headers=e)
    assert r.json()["data"]["threads"] == []  # an empty conversation is invisible to the expert
    capture(G, "provider_threads_empty_before_first_message", r)
    r = fc.post(
        f"{M}/threads/{thread0}/messages",
        json={"body": "Hi Ngozi, do you draft shareholder agreements for seed rounds?"},
        headers=f,
    )
    assert r.status_code == 201, r.text
    capture(G, "message_post_founder_201", r)
    r = fc.post(f"{M}/threads/{thread0}/messages", json={"body": "   "}, headers=f)
    assert r.status_code == 422
    capture(G, "message_post_empty_422", r)
    r = tm_c.post(f"{M}/threads/{thread0}/messages", json={"body": "Can I join?"}, headers=tm)
    assert r.status_code == 403
    capture(G, "message_post_team_member_403", r)
    r = men_c.get(f"{M}/threads", headers=men)
    assert r.status_code == 403
    capture(G, "threads_mentor_403", r)
    r = oc.get(f"{M}/threads/{thread0}", headers=other)
    assert r.status_code == 404
    capture(G, "thread_get_other_workspace_404", r)
    r = http.get(f"{P}/threads/{thread0}", headers=e2)
    assert r.status_code == 404
    capture(G, "provider_thread_get_other_expert_404", r)
    mail = mailbox.latest_for(expert["email"])
    assert mail and "new message" in mail["subject"]
    assert "shareholder" not in mail["html"]  # the email never carries the text
    capture_json(G, "email_provider_new_message", mail)

    r = http.get(f"{P}/threads", headers=e)
    assert r.json()["data"]["unread_total"] == 1
    capture(G, "provider_threads_unread_1", r)
    r = http.get(f"{P}/threads/{thread0}/messages", headers=e)
    assert [m["sender_side"] for m in r.json()["data"]["messages"]] == ["founder"]
    capture(G, "provider_messages_read", r)
    assert http.get(f"{P}/threads", headers=e).json()["data"]["unread_total"] == 0
    r = http.post(
        f"{P}/threads/{thread0}/messages",
        json={"body": "Yes, I do. Send me the details of the round."},
        headers=e,
    )
    assert r.status_code == 201, r.text
    capture(G, "message_post_provider_201", r)
    r = fc.get(f"{M}/threads", headers=f)
    assert r.json()["data"]["unread_total"] == 1
    capture(G, "threads_unread_1", r)
    r = fc.get(f"{M}/threads/{thread0}/messages", headers=f)
    assert [m["sender_side"] for m in r.json()["data"]["messages"]] == ["founder", "provider"]
    capture(G, "messages_list", r)
    assert fc.get(f"{M}/threads", headers=f).json()["data"]["unread_total"] == 0
    r = fc.get(f"{M}/threads/{thread0}/messages", params={"limit": 1}, headers=f)
    assert r.json()["data"]["has_more"] is True
    capture(G, "messages_list_page_back", r)

    # ---- a brief file, a package booking, payment -------------------------------------------------------
    r = tm_c.post(
        f"{M}/files",
        data={"purpose": "brief"},
        files={"file": ("a.pdf", PDF, "application/pdf")},
        headers=tm,
    )
    assert r.status_code == 403
    capture(G, "file_upload_team_member_403", r)
    r = upload(fc, f, M, "delivery")
    assert r.status_code == 422
    capture(G, "file_upload_founder_delivery_422", r)
    r = upload(fc, f, M, "brief", content=b"plain text", name="a.txt", ctype="text/plain")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    capture(G, "file_upload_unsupported_type_422", r)
    r = upload(fc, f, M, "brief", name="seed-terms.pdf")
    assert r.status_code == 201, r.text
    capture(G, "file_upload_brief_201", r)
    brief_file = r.json()["data"]["id"]
    r = upload(http, e, P, "brief")
    assert r.status_code == 422
    capture(G, "provider_file_upload_brief_422", r)

    r = book(fc, f, listings["package"], key=idem("package"), file_ids=[str(uuid.uuid4())])
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_FILE"
    capture(G, "booking_create_bad_file_422", r)
    r = book(fc, f, listings["package"], key=idem("package"), file_ids=[brief_file])
    assert r.status_code == 201, r.text
    capture(G, "booking_create_package_with_brief_file_201", r)
    pkg = r.json()["data"]
    pbid = pkg["id"]
    assert pkg["brief_files"][0]["id"] == brief_file
    r = http.get(f"{P}/bookings/{pbid}", headers=e)
    assert r.status_code == 404  # unpaid: the expert cannot see it
    capture(G, "provider_booking_unpaid_404", r)
    r = http.get(f"{P}/files/{brief_file}/content", headers=e)
    assert r.status_code == 404
    capture(G, "provider_file_unpaid_booking_404", r)
    r = fc.post(f"{M}/bookings/{pbid}/messages", json={"body": "hello"}, headers=f)
    assert r.status_code == 409 and r.json()["error"]["code"] == "THREAD_NOT_AVAILABLE"
    capture(G, "booking_message_unpaid_409", r)
    pay(pkg)

    r = http.get(f"{P}/bookings/{pbid}", headers=e)
    assert r.status_code == 200 and r.json()["data"]["brief_files"][0]["id"] == brief_file
    assert "Book Co" not in r.text and ws not in r.text
    capture(G, "provider_booking_awaiting_with_brief_files", r)
    r = http.get(f"{P}/files/{brief_file}/content", headers=e)
    assert r.status_code == 200 and r.content == PDF
    r = http.get(f"{P}/files/{brief_file}/content", headers=e2)
    assert r.status_code == 404
    capture(G, "provider_file_other_expert_404", r)
    r = oc.get(f"{M}/files/{brief_file}/content", headers=other)
    assert r.status_code == 404
    capture(G, "file_other_workspace_404", r)
    r = tm_c.get(f"{M}/files/{brief_file}/content", headers=tm)
    assert r.status_code == 200 and r.content == PDF
    r = fc.get(f"{M}/bookings/{pbid}", headers=f)
    d = r.json()["data"]
    assert d["thread_id"] == thread0  # the earlier conversation moved onto the booking
    capture(G, "booking_get_awaiting_provider_with_thread", r)
    r = fc.post(
        f"{M}/bookings/{pbid}/messages",
        json={"body": "Brief attached. Draft by Friday?"},
        headers=f,
    )
    assert r.status_code == 201
    capture(G, "booking_message_alias_post_201", r)
    r = fc.get(f"{M}/bookings/{pbid}/messages", headers=f)
    assert len(r.json()["data"]["messages"]) == 3
    capture(G, "booking_messages_list", r)
    r = http.get(f"{P}/threads", headers=e)
    row = next(t for t in r.json()["data"]["threads"] if t["id"] == thread0)
    assert row["booking_id"] == pbid and row["unread_count"] == 1
    capture(G, "provider_threads_with_booking", r)

    # ---- accept, then Documents ---------------------------------------------------------------------------
    r = http.post(f"{P}/bookings/{pbid}/accept", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "in_progress"
    capture(G, "provider_accept_package_200", r)
    assert fc.get(f"{M}/bookings/{pbid}", headers=f).json()["data"]["calendar_event_id"] is None

    r = fc.post("/api/v1/documents", headers=f, json={"template_key": "business_plan"})
    assert r.status_code == 201, r.text
    doc_id = r.json()["data"]["id"]
    r = tm_c.post(f"{M}/bookings/{pbid}/documents", json={"document_id": doc_id}, headers=tm)
    assert r.status_code == 403
    capture(G, "document_attach_team_member_403", r)
    r = oc.post(f"{M}/bookings/{pbid}/documents", json={"document_id": doc_id}, headers=other)
    assert r.status_code == 404
    capture(G, "document_attach_other_workspace_404", r)
    r = fc.post(f"{M}/bookings/{pbid}/documents", json={"document_id": doc_id}, headers=f)
    assert r.status_code == 201, r.text
    capture(G, "document_attach_201", r)
    r = fc.get(f"{M}/bookings/{pbid}/documents", headers=f)
    assert r.json()["data"]["documents"][0]["status"] == "active"
    capture(G, "document_list_founder", r)
    r = http.get(f"{P}/bookings/{pbid}/documents/{doc_id}", headers=e)
    assert r.status_code == 200 and r.json()["data"]["sections"]
    assert ws not in r.text and "Book Co" not in r.text
    capture(G, "provider_document_read_200", r)
    r = http.get(f"{P}/bookings/{pbid}/documents/{doc_id}", headers=e2)
    assert r.status_code == 404
    capture(G, "provider_document_other_expert_404", r)
    r = http.get(f"{P}/bookings/{pbid}", headers=e)
    assert r.json()["data"]["documents"][0]["url"].endswith(f"/documents/{doc_id}")
    capture(G, "provider_booking_in_progress_with_documents", r)

    # ---- deliver, ask for changes (twice), the limit, accept ------------------------------------------------
    r = http.post(f"{P}/bookings/{pbid}/deliver", json={"message": "x", "file_ids": []}, headers=e2)
    assert r.status_code == 404
    capture(G, "provider_deliver_other_expert_404", r)
    r = http.post(f"{P}/bookings/{pbid}/deliver", json={"message": "  ", "file_ids": []}, headers=e)
    assert r.status_code == 422
    capture(G, "provider_deliver_empty_message_422", r)
    r = upload(http, e, P, "delivery", name="shareholder-agreement-v1.pdf")
    assert r.status_code == 201, r.text
    capture(G, "provider_file_upload_delivery_201", r)
    d1 = r.json()["data"]["id"]
    r = upload(
        http, e, P, "delivery", content=b"a,b\n=1+1,2\n", name="schedule.csv", ctype="text/csv"
    )
    assert r.status_code == 201
    capture(G, "provider_file_upload_delivery_csv_201", r)
    r = http.post(
        f"{P}/bookings/{pbid}/deliver",
        json={"message": "First draft attached.", "file_ids": [d1]},
        headers=e,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "delivered", r.text
    capture(G, "provider_deliver_200", r)
    r = http.post(f"{P}/bookings/{pbid}/deliver", json={"message": "again"}, headers=e)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    capture(G, "provider_deliver_twice_409", r)
    r = fc.get(f"{M}/bookings/{pbid}", headers=f)
    d = r.json()["data"]
    assert d["status"] == "delivered" and d["auto_release_at"] and d["revisions_left"] == 2
    capture(G, "booking_get_delivered", r)
    r = fc.get(f"{M}/files/{d1}/content", headers=f)
    assert r.status_code == 200 and r.content == PDF
    r = oc.get(f"{M}/files/{d1}/content", headers=other)
    assert r.status_code == 404
    capture(G, "delivery_file_other_workspace_404", r)
    r = http.delete(f"{P}/files/{d1}", headers=e)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_IN_USE"
    capture(G, "provider_file_delete_sent_409", r)
    notif = fc.get("/api/v1/notifications", headers=f).json()
    kinds = [n["type"] for n in notif["data"]["notifications"]]
    assert "marketplace.delivery.submitted" in kinds and "marketplace.booking.confirmed" in kinds
    capture(G, "founder_notifications_after_delivery", fc.get("/api/v1/notifications", headers=f))

    for who, headers, name in ((tm_c, tm, "team_member"), (men_c, men, "mentor")):
        r = who.post(
            f"{M}/bookings/{pbid}/request-changes",
            json={"reason": "Please soften clause 4."},
            headers=headers,
        )
        assert r.status_code == 403
        capture(G, f"request_changes_{name}_403", r)
    r = oc.post(
        f"{M}/bookings/{pbid}/request-changes",
        json={"reason": "Please soften clause 4."},
        headers=other,
    )
    assert r.status_code == 404
    capture(G, "request_changes_other_workspace_404", r)
    r = fc.post(f"{M}/bookings/{pbid}/request-changes", json={"reason": "short"}, headers=f)
    assert r.status_code == 422
    capture(G, "request_changes_reason_too_short_422", r)
    r = fc.post(
        f"{M}/bookings/{pbid}/request-changes",
        json={"reason": "Please soften clause 4 & add a vesting schedule."},
        headers=f,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "in_progress"
    assert r.json()["data"]["revisions_used"] == 1 and r.json()["data"]["revisions_left"] == 1
    capture(G, "request_changes_1_200", r)
    mail = mailbox.latest_for(expert["email"])
    assert mail and "Changes requested" in mail["subject"] and "&amp;" in mail["html"]
    capture_json(G, "email_provider_changes_requested", mail)
    r = fc.post(
        f"{M}/bookings/{pbid}/request-changes",
        json={"reason": "Please soften clause 4 & add a vesting schedule."},
        headers=f,
    )
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    capture(G, "request_changes_replay_200", r)
    r = http.post(
        f"{P}/bookings/{pbid}/deliver", json={"message": "Second draft.", "file_ids": []}, headers=e
    )
    assert r.status_code == 200
    capture(G, "provider_redeliver_round_2_200", r)
    r = fc.post(
        f"{M}/bookings/{pbid}/request-changes",
        json={"reason": "Vesting cliff should be twelve months."},
        headers=f,
    )
    assert r.status_code == 200 and r.json()["data"]["revisions_left"] == 0
    capture(G, "request_changes_2_200", r)
    r = http.post(
        f"{P}/bookings/{pbid}/deliver", json={"message": "Third draft.", "file_ids": []}, headers=e
    )
    assert r.status_code == 200
    r = fc.post(
        f"{M}/bookings/{pbid}/request-changes",
        json={"reason": "One more small change, please."},
        headers=f,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_LIMIT_REACHED"
    capture(G, "request_changes_limit_reached_409", r)
    r = fc.get(f"{M}/bookings/{pbid}", headers=f)
    assert [x["round"] for x in r.json()["data"]["deliveries"]] == [1, 2, 3]
    capture(G, "booking_get_three_rounds", r)

    r = tm_c.post(f"{M}/bookings/{pbid}/accept-delivery", headers=tm)
    assert r.status_code == 403
    capture(G, "accept_delivery_team_member_403", r)
    r = fc.post(f"{M}/bookings/{pbid}/accept-delivery", headers=f)
    assert r.status_code == 200 and r.json()["data"]["status"] == "completed"
    assert r.json()["data"]["replayed"] is False
    capture(G, "accept_delivery_200", r)
    order = r.json()["data"]["order"]
    assert order["status"] == "paid" and order["refunded_minor"] == 0  # no money moved
    r = fc.post(f"{M}/bookings/{pbid}/accept-delivery", headers=f)
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    capture(G, "accept_delivery_replay_200", r)
    mail = mailbox.latest_for(expert["email"])
    assert mail and "Delivery accepted" in mail["subject"]
    capture_json(G, "email_provider_delivery_accepted", mail)
    r = http.get(f"{P}/bookings/{pbid}/documents/{doc_id}", headers=e)
    assert r.status_code == 404  # the share ended with the booking
    capture(G, "provider_document_after_completion_404", r)
    r = fc.get(f"{M}/bookings/{pbid}/documents", headers=f)
    assert r.json()["data"]["documents"][0]["status"] == "revoked"
    capture(G, "document_list_after_completion", r)
    r = fc.post(f"{M}/bookings/{pbid}/documents", json={"document_id": doc_id}, headers=f)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    capture(G, "document_attach_after_completion_409", r)
    r = fc.post(f"{M}/bookings/{pbid}/messages", json={"body": "Thank you!"}, headers=f)
    assert r.status_code == 201  # a completed booking's conversation stays open
    r = http.get(f"{P}/threads/{thread0}/messages", headers=e)
    capture(G, "provider_messages_after_completion", r)

    # ---- a consultation: Calendar meeting, .ics, complete after the session --------------------------------------------
    r = book(fc, f, listings["consult"], key=idem("consult"), starts_at=slot_a)
    assert r.status_code == 201, r.text
    cons = r.json()["data"]
    cbid = cons["id"]
    pay(cons)
    r = fc.get(f"{M}/bookings/{cbid}/calendar.ics", headers=f)
    assert r.status_code == 404  # no meeting before the expert accepts
    capture(G, "calendar_ics_before_accept_404", r)
    r = http.post(f"{P}/bookings/{cbid}/accept", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "confirmed"
    r = fc.get(f"{M}/bookings/{cbid}", headers=f)
    d = r.json()["data"]
    assert d["calendar_event_id"] and d["calendar_ics_url"] and d["meeting_url"]
    capture(G, "booking_get_confirmed_with_calendar", r)
    r = fc.get(f"{M}/bookings/{cbid}/calendar.ics", headers=f)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in r.text and "https://meet.example.com/ngozi-eze" in r.text.replace(
        "\r\n ", ""
    )
    capture_json(
        G,
        "calendar_ics_200",
        {"status": r.status_code, "content_type": r.headers["content-type"], "body": r.text},
    )
    r = men_c.get(f"{M}/bookings/{cbid}/calendar.ics", headers=men)
    assert r.status_code == 403
    capture(G, "calendar_ics_mentor_403", r)
    r = oc.get(f"{M}/bookings/{cbid}/calendar.ics", headers=other)
    assert r.status_code == 404
    capture(G, "calendar_ics_other_workspace_404", r)
    cal = fc.get(
        "/api/v1/calendar/events",
        params={"from": day, "to": (date.today() + timedelta(days=3)).isoformat()},
        headers=f,
    )
    capture_json(G, "calendar_events_list_after_accept", cal.json())
    r = http.post(f"{P}/bookings/{cbid}/complete", headers=e)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_NOT_ENDED"
    capture(G, "provider_complete_before_session_ends_409", r)
    # [sql] the session happened an hour ago
    sql(
        "UPDATE marketplace_bookings SET starts_at = now() - interval '2 hours', "
        "ends_at = now() - interval '1 hour' WHERE id = :i",
        i=cbid,
    )
    r = http.post(f"{P}/bookings/{cbid}/complete", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "delivered", r.text
    capture(G, "provider_complete_200", r)
    r = fc.get(f"{M}/bookings/{cbid}", headers=f)
    assert r.json()["data"]["auto_release_at"]
    capture(G, "booking_get_consultation_delivered", r)
    r = fc.post(
        f"{M}/bookings/{cbid}/request-changes",
        json={"reason": "Please redo the session."},
        headers=f,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_APPLICABLE"
    capture(G, "request_changes_consultation_422", r)

    # ---- the timer, on a doctored clock: an unmarked session and an unanswered delivery ------------------------------------
    r = book(fc, f, listings["consult"], key=idem("consult2"), starts_at=slot_b)
    c2 = r.json()["data"]
    pay(c2)
    assert http.post(f"{P}/bookings/{c2['id']}/accept", headers=e).status_code == 200
    sql(
        "UPDATE marketplace_bookings SET starts_at = now() - interval '4 hours', "
        "ends_at = now() - interval '3 hours' WHERE id = :i",
        i=c2["id"],
    )
    res = tick(datetime.now(UTC))
    assert res["consultations_delivered"] >= 1
    capture_json(G, "timer_tick_result_session_delivered", res)
    r = fc.get(f"{M}/bookings/{c2['id']}", headers=f)
    assert r.json()["data"]["status"] == "delivered"
    capture(G, "booking_get_session_delivered_by_timer", r)
    sql(
        "UPDATE marketplace_bookings SET auto_release_at = now() - interval '1 minute' WHERE id = :i",
        i=c2["id"],
    )
    res = tick(datetime.now(UTC))
    assert res["auto_released"] >= 1
    r = fc.get(f"{M}/bookings/{c2['id']}", headers=f)
    d = r.json()["data"]
    assert d["status"] == "completed" and d["deliveries"][0]["decision"] == "auto_released"
    capture(G, "booking_get_auto_released", r)
    r = fc.post(f"{M}/bookings/{c2['id']}/accept-delivery", headers=f)
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    capture(G, "accept_delivery_after_auto_release_200", r)
    kinds = [
        n["type"]
        for n in fc.get("/api/v1/notifications", headers=f).json()["data"]["notifications"]
    ]
    assert "marketplace.delivery.auto_released" in kinds

    r = book(fc, f, listings["package"], key=idem("package2"))
    p2 = r.json()["data"]
    pay(p2)
    assert http.post(f"{P}/bookings/{p2['id']}/accept", headers=e).status_code == 200
    sql(
        "UPDATE marketplace_bookings SET due_at = now() - interval '1 hour' WHERE id = :i",
        i=p2["id"],
    )
    res = tick(datetime.now(UTC))
    assert res["overdue_flagged"] >= 1
    capture_json(G, "timer_tick_result_overdue", res)
    assert fc.get(f"{M}/bookings/{p2['id']}", headers=f).json()["data"]["status"] == "in_progress"
    mail = mailbox.latest_for(expert["email"])
    assert mail and mail["subject"].startswith("Overdue")
    capture_json(G, "email_provider_overdue", mail)
    kinds = [
        n["type"]
        for n in fc.get("/api/v1/notifications", headers=f).json()["data"]["notifications"]
    ]
    assert "marketplace.booking.overdue" in kinds

    # ---- staff: the attention queue and the resolve action ------------------------------------------------------------------
    unv = fc.get(f"{M}/orders", headers=f).json()["data"]["orders"][0]["id"]
    mism = fc.get(f"{M}/orders", headers=f).json()["data"]["orders"][1]["id"]
    sql(  # [sql] the stub Paystack cannot produce a mismatch or an unverified close
        "UPDATE marketplace_orders SET attention_reason = 'unverified_close', attention_at = now() WHERE id = :i",
        i=unv,
    )
    sql(
        "UPDATE marketplace_orders SET attention_reason = 'payment_mismatch', attention_at = now(), "
        "attention_amount_minor = 1020000 WHERE id = :i",
        i=mism,
    )
    r = http.get(f"{A}/orders/attention", headers=mod)
    # the staff queue is global: other journeys on a shared database leave flags of their own
    assert {unv, mism} <= {o["id"] for o in r.json()["data"]["orders"]}
    capture(G, "admin_attention_queue", r)
    r = http.post(f"{A}/orders/{unv}/attention/resolve", json={"reason": "too short"}, headers=mod)
    assert r.status_code == 422
    capture(G, "admin_resolve_reason_too_short_422", r)
    r = http.post(
        f"{A}/orders/{unv}/attention/resolve",
        json={"reason": "Checked with Paystack: nothing was captured."},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["resolved_at"], r.text
    capture(G, "admin_resolve_unverified_close_200", r)
    r = http.post(
        f"{A}/orders/{unv}/attention/resolve",
        json={"reason": "Checked with Paystack: nothing was captured."},
        headers=mod,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_FLAGGED"
    capture(G, "admin_resolve_again_409", r)
    r = http.post(
        f"{A}/orders/{mism}/attention/resolve",
        json={"reason": "Refunded the difference by hand."},
        headers=mod,
    )
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "totp_code"
    capture(G, "admin_resolve_money_flag_needs_code_422", r)
    r = http.post(
        f"{A}/orders/{mism}/attention/resolve",
        json={"reason": "Refunded the difference by hand.", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 200, r.text
    capture(G, "admin_resolve_payment_mismatch_200", r)
    r = http.get(f"{A}/orders/attention", params={"resolved": "true"}, headers=mod)
    assert {unv, mism} <= {o["id"] for o in r.json()["data"]["orders"]}
    capture(G, "admin_attention_queue_resolved", r)
    still = http.get(f"{A}/orders/attention", headers=mod).json()["data"]["orders"]
    assert not {unv, mism} & {o["id"] for o in still}
    r = fc.post(f"{A}/orders/{unv}/attention/resolve", json={"reason": "x" * 12}, headers=f)
    assert r.status_code == 404
