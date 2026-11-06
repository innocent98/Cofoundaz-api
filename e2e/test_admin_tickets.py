"""Live: Admin Tickets. A founder opens a ticket from Help; a support Admin triages it, leaves an
internal note, replies, escalates and resolves it; the founder replies and reopens it.

One real server on a database every other journey shares, so nothing here asserts exact counts of
staff or tickets: every list check filters to this run's ticket. Responses go to
e2e/_captures/admin_tickets/ and are the verbatim source of
docs/fe-integration-guide-admin-tickets.md. Email is read back from the file mailbox.
"""

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

A = "/api/v1/admin"
S = "/api/v1/super-admin"
F = "/api/v1/support"
G = "admin_tickets"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: a code not yet spent on a step-up (3 fit the ~90 s window,
    each works once), waiting for the next one when all are used."""

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


def _staff(http, sa, sa_app, user, scopes):
    app = _enroll(http, user)
    r = http.post(
        f"{S}/staff",
        json={
            "email": user["email"],
            "role": "admin",
            "scopes": scopes,
            "reason": "Support rota for November",
            "totp_code": sa_app.fresh(),
        },
        headers=sa,
    )
    assert r.status_code == 201, r.text
    return _sign_in(http, user, app)


def test_admin_tickets_journey(
    http: httpx.Client, base_url, make_verified_user, mailbox, capture, capture_json
):
    # ---- people: Super Admin by command, a support Admin, a content-only Admin -------------
    sa_user = make_verified_user(http)
    sa_app = _enroll(http, sa_user)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", sa_user["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert run.returncode == 0, run.stderr
    sa = _sign_in(http, sa_user, sa_app)
    sup_user, content_user = make_verified_user(http), make_verified_user(http)
    sup = _staff(http, sa, sa_app, sup_user, ["support"])
    content = _staff(http, sa, sa_app, content_user, ["content"])

    def founder(name):
        u = make_verified_user(http)
        c = httpx.Client(base_url=base_url, timeout=15.0)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        ws = _onboard(c, auth, name)
        return u, {**auth, "X-Workspace-Id": ws}, ws

    f_user, f, ws = founder("Ticket Co")
    other_user, other, _ = founder("Other Ticket Co")

    # ---- founder opens a ticket --------------------------------------------------------------
    for name, bad in (
        ("open_bad_category", {"category": "nope", "subject": "Export fails", "body": "Help"}),
        ("open_short_subject", {"category": "bug", "subject": "x", "body": "Help"}),
        (
            "open_extra_field",
            {"category": "bug", "subject": "Export", "body": "H", "priority": "P1"},
        ),
    ):
        r = http.post(f"{F}/tickets", json=bad, headers=f)
        assert r.status_code == 422, r.text
        capture(G, name, r)
    r = http.post(f"{F}/tickets", json={"category": "bug", "subject": "Export fails", "body": "x"})
    assert r.status_code in (401, 422)
    capture(G, "open_no_auth", r)
    r = http.post(
        f"{F}/tickets",
        json={
            "category": "billing",
            "subject": "Invoice PDF is blank",
            "body": "The PDF export of invoice INV-0004 comes out blank.\nIt worked last week.",
        },
        headers=f,
    )
    assert r.status_code == 201, r.text
    capture(G, "open_ok", r)
    t = r.json()["data"]
    tid, number = t["id"], t["number"]
    assert t["status"] == "open" and "priority" not in t and "notes" not in t

    r = http.get(f"{F}/tickets", headers=f)
    assert r.status_code == 200 and tid in [x["id"] for x in r.json()["data"]["tickets"]]
    capture(G, "founder_list", r)
    r = http.get(f"{F}/tickets/{tid}", headers=f)
    assert r.status_code == 200
    capture(G, "founder_thread", r)
    r = http.get(f"{F}/tickets/{tid}", headers=other)  # another workspace's founder
    assert r.status_code == 404
    capture(G, "founder_other_workspace_404", r)
    r = http.post(f"{F}/tickets/{tid}/messages", json={"body": "hijack"}, headers=other)
    assert r.status_code == 404

    # ---- staff guards ------------------------------------------------------------------------
    r = http.get(f"{A}/support/tickets", headers=f)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    capture(G, "staff_list_founder_404", r)
    r = http.get(f"{A}/support/tickets", headers=content)
    assert r.status_code == 403
    capture(G, "staff_list_wrong_scope_403", r)

    # ---- staff triage ------------------------------------------------------------------------
    r = http.get(f"{A}/support/counts", headers=sup)
    assert r.status_code == 200 and r.json()["data"]["open"] >= 1
    capture(G, "counts", r)
    r = http.get(f"{A}/support/tickets", params={"query": number}, headers=sup)
    assert r.status_code == 200 and [x["id"] for x in r.json()["data"]["tickets"]] == [tid]
    capture(G, "staff_list_search_by_number", r)
    r = http.get(f"{A}/support/tickets", params={"tab": "open", "category": "billing"}, headers=sup)
    assert tid in [x["id"] for x in r.json()["data"]["tickets"]]
    r = http.get(f"{A}/support/tickets/{tid}", headers=sup)
    assert r.status_code == 200 and r.json()["data"]["priority"] == "P2"
    capture(G, "staff_detail_new", r)
    r = http.get(f"{A}/support/assignees", headers=sup)
    me = next(s for s in r.json()["data"]["staff"] if s["email"] == sup_user["email"])
    capture(G, "assignees", r)
    r = http.post(
        f"{A}/support/tickets/{tid}/assign", json={"assignee_user_id": me["id"]}, headers=sup
    )
    assert r.status_code == 200 and r.json()["data"]["assignee"]["id"] == me["id"]
    capture(G, "assign", r)
    r = http.get(f"{A}/support/tickets", params={"tab": "mine", "query": number}, headers=sup)
    assert [x["id"] for x in r.json()["data"]["tickets"]] == [tid]

    r = http.post(
        f"{A}/support/tickets/{tid}/notes",
        json={"body": "INTERNAL: invoice template changed in 0.9.4; check PDF worker."},
        headers=sup,
    )
    assert r.status_code == 200 and len(r.json()["data"]["notes"]) == 1
    capture(G, "note", r)
    r = http.patch(f"{A}/support/tickets/{tid}/priority", json={"priority": "P1"}, headers=sup)
    assert r.status_code == 200 and r.json()["data"]["priority"] == "P1"
    capture(G, "priority", r)
    r = http.post(f"{A}/support/tickets/{tid}/escalate", json={"reason": "short"}, headers=sup)
    assert r.status_code == 422
    capture(G, "escalate_reason_too_short", r)

    # ---- canned replies ----------------------------------------------------------------------
    r = http.post(
        f"{A}/support/canned",
        json={
            "title": f"Looking into it {number}",
            "body": "Thanks for flagging this. We're looking into it now.",
        },
        headers=sup,
    )
    assert r.status_code == 201, r.text
    capture(G, "canned_create", r)
    cid = r.json()["data"]["id"]
    r = http.patch(
        f"{A}/support/canned/{cid}",
        json={"body": "Thanks for flagging this, we are on it."},
        headers=sup,
    )
    assert r.status_code == 200
    capture(G, "canned_update", r)
    r = http.get(f"{A}/support/canned", headers=sup)
    assert cid in [c["id"] for c in r.json()["data"]["canned"]]
    capture_json(
        G,
        "canned_list_trimmed_to_this_run",
        {**r.json(), "data": {"canned": [c for c in r.json()["data"]["canned"] if c["id"] == cid]}},
    )
    r = http.delete(f"{A}/support/canned/{cid}", headers=sup)
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True
    capture(G, "canned_delete", r)

    # ---- staff reply: the founder is emailed, and never sees the note -------------------------
    before = mailbox.count_for(f_user["email"])
    r = http.post(
        f"{A}/support/tickets/{tid}/reply",
        json={"body": "Thanks for flagging this. We found the cause and are fixing it today."},
        headers=sup,
    )
    assert r.status_code == 200 and r.json()["data"]["first_response_at"]
    capture(G, "staff_reply", r)
    assert mailbox.count_for(f_user["email"]) == before + 1
    mail = mailbox.latest_for(f_user["email"])
    assert number in mail["subject"]
    capture_json(G, "email_staff_reply", mail)
    r = http.get(f"{F}/tickets/{tid}", headers=f)
    assert "INTERNAL" not in r.text and "Cofoundaz Support" in r.text
    assert sup_user["email"] not in r.text
    capture(G, "founder_thread_after_staff_reply", r)

    # ---- founder replies; staff escalate ------------------------------------------------------
    r = http.post(f"{F}/tickets/{tid}/messages", json={"body": "Great, thank you!"}, headers=f)
    assert r.status_code == 201 and len(r.json()["data"]["messages"]) == 3
    capture(G, "founder_reply", r)
    r = http.post(
        f"{A}/support/tickets/{tid}/escalate",
        json={"reason": "Founder is a design partner and invoices are blocked"},
        headers=sup,
    )
    assert r.status_code == 200 and r.json()["data"]["escalated"] is True
    capture(G, "escalate", r)
    r = http.post(
        f"{A}/support/tickets/{tid}/escalate",
        json={"reason": "Founder is a design partner and invoices are blocked"},
        headers=sup,
    )
    assert r.status_code == 409
    capture(G, "escalate_again_409", r)

    # ---- resolve: closing email; founder sees it resolved; a reply reopens it ---------------
    before = mailbox.count_for(f_user["email"])
    r = http.post(
        f"{A}/support/tickets/{tid}/resolve",
        json={"message": "Fixed in today's release. Reply here if it happens again."},
        headers=sup,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "resolved"
    capture(G, "resolve", r)
    assert mailbox.count_for(f_user["email"]) == before + 1
    mail = mailbox.latest_for(f_user["email"])
    assert mail["subject"].startswith("Resolved:")
    capture_json(G, "email_resolved", mail)
    r = http.post(f"{A}/support/tickets/{tid}/reply", json={"body": "late"}, headers=sup)
    assert r.status_code == 409
    capture(G, "reply_on_resolved_409", r)
    r = http.get(f"{A}/support/tickets", params={"tab": "resolved", "query": number}, headers=sup)
    assert [x["id"] for x in r.json()["data"]["tickets"]] == [tid]
    capture(G, "staff_list_resolved_tab", r)
    r = http.get(f"{F}/tickets", params={"status": "resolved"}, headers=f)
    assert tid in [x["id"] for x in r.json()["data"]["tickets"]]
    r = http.post(f"{F}/tickets/{tid}/messages", json={"body": "It is back, sorry."}, headers=f)
    assert r.status_code == 201 and r.json()["data"]["status"] == "open"
    capture(G, "founder_reply_reopens", r)
    r = http.post(f"{A}/support/tickets/{tid}/reopen", json={}, headers=sup)
    assert r.status_code == 409
    capture(G, "reopen_not_resolved_409", r)
    r = http.post(f"{A}/support/tickets/{tid}/resolve", json={}, headers=sup)
    assert r.status_code == 200
    r = http.post(
        f"{A}/support/tickets/{tid}/reopen",
        json={"reason": "Customer reports it again"},
        headers=sup,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "open"
    capture(G, "staff_reopen", r)

    # ---- dashboard card and audit trail -------------------------------------------------------
    r = http.get(f"{A}/dashboard/attention", headers=content)  # any staff, even without support
    assert r.status_code == 200 and r.json()["data"]["tickets"]["open"] >= 1
    capture(G, "dashboard_attention", r)
    r = http.get(f"{S}/audit", params={"workspace_id": ws, "limit": 50}, headers=sa)
    actions = {e["action"] for e in r.json()["data"]["entries"]}
    assert {
        "ticket.assigned",
        "ticket.note_added",
        "ticket.priority_changed",
        "ticket.replied",
        "ticket.escalated",
        "ticket.resolved",
        "ticket.reopened",
    } <= actions
    assert "INTERNAL" not in r.text and "fixing it today" not in r.text
    capture(G, "audit_ticket_actions", r)
