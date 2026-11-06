"""Live: Admin Reported issues. A founder reports a teammate's comment and two AI Co-Founder
replies; a moderator triages (dismiss, escalate); a Super Admin removes what was escalated; the
authors see "Removed by moderation"; the removal is undone.

One real server on a database every other journey shares, so nothing here asserts exact counts:
every list check filters to this run's reports. Responses go to e2e/_captures/admin_reports/ and
are the verbatim source of docs/fe-integration-guide-admin-reports.md.
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
F = "/api/v1/reports"
T = "/api/v1/team"
G = "admin_reports"
NOTE_TEXT = "Your launch plan is hopeless and so are you."


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
            "reason": "Moderation rota for November",
            "totp_code": sa_app.fresh(),
        },
        headers=sa,
    )
    assert r.status_code == 201, r.text
    return app, _sign_in(http, user, app)


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.cofounder.reply)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_admin_reports_journey(
    http: httpx.Client, base_url, make_verified_user, mailbox, capture, capture_json
):
    # ---- people ------------------------------------------------------------------------------
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
    mod_user, sup_user = make_verified_user(http), make_verified_user(http)
    mod_app, mod = _staff(http, sa, sa_app, mod_user, ["moderation"])
    _, sup = _staff(http, sa, sa_app, sup_user, ["support"])

    def login(user):
        c = httpx.Client(base_url=base_url, timeout=15.0)
        return c, _auth(c.post("/api/v1/auth/login", json=user).json()["data"]["access_token"])

    f_user = make_verified_user(http)
    fc, f_auth = login(f_user)
    ws = _onboard(fc, f_auth, "Report Co")
    f = {**f_auth, "X-Workspace-Id": ws}
    o_user = make_verified_user(http)
    oc, o_auth = login(o_user)
    o_ws = _onboard(oc, o_auth, "Other Report Co")
    other = {**o_auth, "X-Workspace-Id": o_ws}

    # A mentor joins the founder's workspace and writes the comment that gets reported.
    m_user = make_verified_user(http)
    inv = http.post(
        f"{T}/invitations",
        headers=f,
        json={"invites": [{"email": m_user["email"], "role": "mentor"}]},
    )
    assert inv.status_code == 200, inv.text
    token = mailbox.latest_token_for(m_user["email"], subject_contains="invited")
    mc, m_auth = login(m_user)
    assert (
        mc.post("/api/v1/invitations/accept", headers=m_auth, json={"token": token}).status_code
        == 200
    )
    m = {**m_auth, "X-Workspace-Id": ws}
    ms = fc.get("/api/v1/roadmap", headers=f).json()["data"]["phases"][0]["milestones"][0]
    r = mc.post(
        f"{T}/comments",
        headers=m,
        json={"entity_type": "roadmap_milestone", "entity_id": ms["id"], "body": NOTE_TEXT},
    )
    assert r.status_code == 201, r.text
    comment_id = r.json()["data"]["id"]
    comment_url = f"{T}/comments"
    comment_params = {"entity_type": "roadmap_milestone", "entity_id": ms["id"]}

    # An AI Co-Founder chat with two replies (the worker is drained in-process).
    r = fc.post("/api/v1/ai/conversations", headers=f, json={"message": "How long is my runway?"})
    assert r.status_code == 201, r.text
    conv = r.json()["data"]["id"]
    _drain()
    r = fc.post(
        f"/api/v1/ai/conversations/{conv}/messages",
        headers=f,
        json={"content": "And what should I charge?"},
    )
    assert r.status_code == 202, r.text
    _drain()
    r = fc.get(f"/api/v1/ai/conversations/{conv}", headers=f)
    capture(G, "ai_conversation_before", r)
    replies = [x for x in r.json()["data"]["messages"] if x["role"] == "assistant"]
    assert len(replies) == 2 and all(x["status"] == "ready" for x in replies)
    ai1, ai2 = replies[0]["id"], replies[1]["id"]

    # ---- founder: report -----------------------------------------------------------------------
    for name, bad in (
        ("report_bad_item_type", {"item_type": "review", "item_id": comment_id, "reason": "spam"}),
        ("report_bad_reason", {"item_type": "comment", "item_id": comment_id, "reason": "meh"}),
        (
            "report_other_needs_note",
            {"item_type": "comment", "item_id": comment_id, "reason": "other"},
        ),
        (
            "report_extra_field",
            {"item_type": "comment", "item_id": comment_id, "reason": "spam", "x": 1},
        ),
    ):
        r = fc.post(F, json=bad, headers=f)
        assert r.status_code == 422, r.text
        capture(G, name, r)
    r = http.post(F, json={"item_type": "comment", "item_id": comment_id, "reason": "spam"})
    assert r.status_code in (401, 422)
    capture(G, "report_no_auth", r)

    r = mc.post(
        F, json={"item_type": "comment", "item_id": comment_id, "reason": "spam"}, headers=m
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "OWN_COMMENT"
    capture(G, "report_own_comment_422", r)
    r = oc.post(
        F, json={"item_type": "comment", "item_id": comment_id, "reason": "spam"}, headers=other
    )
    assert r.status_code == 404
    capture(G, "report_other_workspace_404", r)

    r = fc.post(
        F,
        json={
            "item_type": "comment",
            "item_id": comment_id,
            "reason": "abusive",
            "note": "Personal attack",
        },
        headers=f,
    )
    assert r.status_code == 201, r.text
    capture(G, "report_comment_ok", r)
    rc = r.json()["data"]["id"]
    r = fc.post(
        F, json={"item_type": "comment", "item_id": comment_id, "reason": "spam"}, headers=f
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_REPORTED"
    capture(G, "report_duplicate_409", r)
    ids = {}
    for key, mid, reason in (("a", ai1, "inaccurate"), ("b", ai2, "harmful")):
        r = fc.post(
            F,
            json={
                "item_type": "ai_message",
                "item_id": mid,
                "reason": reason,
                "note": "Wrong figures",
            },
            headers=f,
        )
        assert r.status_code == 201, r.text
        capture(G, f"report_ai_message_{key}_ok", r)
        ids[key] = r.json()["data"]["id"]
    r = fc.get(f"{F}/mine", headers=f)
    assert r.status_code == 200 and {x["id"] for x in r.json()["data"]["reports"]} >= {
        rc,
        *ids.values(),
    }
    capture(G, "mine", r)

    # ---- staff guards ---------------------------------------------------------------------------
    r = http.get(f"{A}/reports", headers=f)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    capture(G, "staff_list_founder_404", r)
    r = http.get(f"{A}/reports", headers=sup)
    assert r.status_code == 403
    capture(G, "staff_list_wrong_scope_403", r)

    # ---- moderator triage -----------------------------------------------------------------------
    r = http.get(f"{A}/reports/counts", headers=mod)
    assert r.status_code == 200 and r.json()["data"]["open"] >= 3
    capture(G, "counts", r)
    r = http.get(f"{A}/reports", params={"query": "Report Co"}, headers=mod)
    got = {x["id"] for x in r.json()["data"]["reports"]}
    assert {rc, *ids.values()} <= got
    capture(G, "list_open_search_workspace", r)
    r = http.get(
        f"{A}/reports",
        params={"type": "comment", "reason": "abusive", "query": "Report Co"},
        headers=mod,
    )
    assert [x["id"] for x in r.json()["data"]["reports"]] == [rc]
    capture(G, "list_filtered_type_reason", r)
    r = http.get(f"{A}/reports/{rc}", headers=mod)
    d = r.json()["data"]
    assert r.status_code == 200 and d["snippet"] == NOTE_TEXT and d["author"]["name"]
    assert "email" not in d["author"]
    capture(G, "detail_comment", r)
    r = http.get(f"{A}/reports/{ids['a']}", headers=mod)
    assert r.status_code == 200 and r.json()["data"]["author"] is None
    assert "How long is my runway?" not in r.text  # the prompt behind the reply is never read
    capture(G, "detail_ai_message", r)

    r = http.post(f"{A}/reports/{ids['a']}/dismiss", json={"reason": "short"}, headers=mod)
    assert r.status_code == 422
    capture(G, "dismiss_reason_too_short", r)
    r = http.post(
        f"{A}/reports/{ids['a']}/dismiss",
        json={"reason": "Figures match the workspace data; not an error"},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "dismissed"
    capture(G, "dismiss", r)
    r = http.post(
        f"{A}/reports/{ids['a']}/dismiss",
        json={"reason": "Dismissing it a second time"},
        headers=mod,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPORT_CLOSED"
    capture(G, "dismiss_closed_409", r)
    r = http.get(f"{A}/reports", params={"tab": "closed", "query": "Report Co"}, headers=mod)
    assert ids["a"] in {x["id"] for x in r.json()["data"]["reports"]}
    capture(G, "list_closed_tab", r)

    r = http.post(
        f"{A}/reports/{rc}/escalate",
        json={"reason": "Targeted abuse of a named person; needs a Super Admin call"},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "escalated"
    capture(G, "escalate", r)
    r = http.post(
        f"{A}/reports/{rc}/escalate",
        json={"reason": "Escalating it again for good measure"},
        headers=mod,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_ESCALATED"
    capture(G, "escalate_again_409", r)
    r = http.post(
        f"{A}/reports/{rc}/dismiss", json={"reason": "A moderator tries to decide it"}, headers=mod
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    capture(G, "dismiss_escalated_by_moderator_403", r)
    r = http.post(
        f"{A}/reports/{rc}/remove",
        json={"reason": "A moderator tries to remove it", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    capture(G, "remove_escalated_by_moderator_403", r)
    r = http.get(f"{A}/reports", params={"tab": "escalated", "query": "Report Co"}, headers=mod)
    assert rc in {x["id"] for x in r.json()["data"]["reports"]}
    capture(G, "list_escalated_tab", r)

    # ---- remove needs a fresh code --------------------------------------------------------------
    r = http.post(
        f"{A}/reports/{ids['b']}/remove",
        json={"reason": "Harmful financial advice to a founder", "totp_code": "000000"},
        headers=mod,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture(G, "remove_bad_code_403", r)
    r = http.post(
        f"{A}/reports/{ids['b']}/remove",
        json={"reason": "Harmful financial advice to a founder"},
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "remove_missing_code_422", r)
    r = http.post(
        f"{A}/reports/{ids['b']}/remove",
        json={"reason": "Harmful financial advice to a founder", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "removed"
    capture(G, "remove_ai_message", r)
    r = fc.get(f"/api/v1/ai/conversations/{conv}", headers=f)
    shown = next(x for x in r.json()["data"]["messages"] if x["id"] == ai2)
    assert shown["removed_by_moderation"] is True and shown["content"] == "Removed by moderation"
    capture(G, "ai_conversation_after_removal", r)

    # ---- Super Admin decides the escalation ----------------------------------------------------
    r = http.post(
        f"{A}/reports/{rc}/remove",
        json={"reason": "Abuse of a named person confirmed", "totp_code": sa_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "removed"
    capture(G, "remove_comment_by_super_admin", r)
    r = mc.get(comment_url, params=comment_params, headers=m)
    row = next(x for x in r.json()["data"] if x["id"] == comment_id)
    assert row["removed_by_moderation"] is True and NOTE_TEXT not in r.text
    capture(G, "comment_thread_author_after_removal", r)
    r = mc.patch(f"{T}/comments/{comment_id}", json={"body": "edited back"}, headers=m)
    assert r.status_code == 403
    capture(G, "comment_edit_after_removal_403", r)
    r = fc.post(
        F, json={"item_type": "comment", "item_id": comment_id, "reason": "spam"}, headers=f
    )
    assert r.status_code == 404  # a removed item can't be reported again
    r = fc.get(f"{F}/mine", headers=f)
    assert not {rc, *ids.values()} & {x["id"] for x in r.json()["data"]["reports"]}
    capture(G, "mine_after_decisions", r)

    # ---- restore ---------------------------------------------------------------------------------
    r = http.post(
        f"{A}/reports/{ids['a']}/restore",
        json={"reason": "Trying to restore a dismissed one", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_REMOVED"
    capture(G, "restore_not_removed_409", r)
    r = http.post(
        f"{A}/reports/{rc}/restore",
        json={"reason": "Second look: context was a quoted example", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    capture(G, "restore_escalated_by_moderator_403", r)
    r = http.post(
        f"{A}/reports/{rc}/restore",
        json={"reason": "Second look: context was a quoted example", "totp_code": sa_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "restored"
    capture(G, "restore", r)
    r = mc.get(comment_url, params=comment_params, headers=m)
    row = next(x for x in r.json()["data"] if x["id"] == comment_id)
    assert row["removed_by_moderation"] is False and row["body"] == NOTE_TEXT
    capture(G, "comment_thread_author_after_restore", r)

    # ---- audit trail -----------------------------------------------------------------------------
    r = http.get(f"{S}/audit", params={"workspace_id": ws, "limit": 50}, headers=sa)
    actions = {e["action"] for e in r.json()["data"]["entries"]}
    assert {
        "report.filed",
        "report.dismissed",
        "report.escalated",
        "report.item_removed",
        "report.item_restored",
    } <= actions
    assert NOTE_TEXT not in r.text
    capture(G, "audit_report_actions", r)
