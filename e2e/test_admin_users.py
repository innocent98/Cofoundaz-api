"""Live: Admin slice B. Users search, profile, suspend, unsuspend, reset password, resend
verification, read-only Workspaces and Dashboard KPIs, driven by a real support Admin.

One real server. Responses go to e2e/_captures/admin_users/ and are the verbatim source of
docs/fe-integration-guide-admin-users.md. Staff are made the way an operator would: the first
Super Admin by command, the support Admin by invite.
"""

import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

A = "/api/v1/admin"
S = "/api/v1/super-admin"


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
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _staff_by_email(http: httpx.Client, headers: dict, email: str) -> dict:
    """The e2e database is shared by every journey, so never pick staff by role alone."""
    rows = http.get(f"{S}/staff", headers=headers).json()["data"]
    return next(s for s in rows if s["email"] == email)


def _act(
    http, who: dict, app: Authenticator, path: str, reason: str = "Support ticket 4821 follow-up"
):
    return http.post(f"{A}{path}", json={"reason": reason, "totp_code": app.fresh()}, headers=who)


def test_admin_users_journey(
    http: httpx.Client, base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    # ---- staff: a Super Admin by command, a support Admin by invite -------------------------
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
    adm_user = make_verified_user(http)
    adm_app = _enroll(http, adm_user)
    r = http.post(
        f"{S}/staff",
        json={
            "email": adm_user["email"],
            "role": "admin",
            "scopes": ["support"],
            "reason": "Support rota for November",
            "totp_code": sa_app.fresh(),
        },
        headers=sa,
    )
    assert r.status_code == 201, r.text
    adm = _sign_in(http, adm_user, adm_app)

    # ---- two ordinary accounts: a founder with a workspace, and one who never verified -----
    victim_user = make_verified_user(http)
    vc = httpx.Client(base_url=base_url, timeout=15.0)
    login = vc.post("/api/v1/auth/login", json=victim_user).json()["data"]
    victim = _auth(login["access_token"])
    ws_id = _onboard(vc, victim, "NovaPay")
    pending_email = unique_email("pending")
    pending_pw = f"Ev-{uuid.uuid4().hex[:12]}-9"
    r = http.post("/api/v1/auth/signup", json={"email": pending_email, "password": pending_pw})
    assert r.status_code == 201, r.text
    mails_before = mailbox.count_for(pending_email)

    # ---- hidden from non-staff ---------------------------------------------------------------
    r = http.get(f"{A}/users", headers=victim)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    capture("admin_users", "users_list_non_staff_404", r)

    # ---- search and profile ------------------------------------------------------------------
    r = http.get(f"{A}/users", params={"query": victim_user["email"]}, headers=adm)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["users"]
    assert len(rows) == 1 and rows[0]["primary_workspace"]["workspace_name"] == "NovaPay"
    capture("admin_users", "users_search", r)
    victim_id = rows[0]["id"]
    r = http.get(f"{A}/users", params={"query": "NovaPay", "billing_status": "free"}, headers=adm)
    assert r.status_code == 200 and any(u["id"] == victim_id for u in r.json()["data"]["users"])
    capture("admin_users", "users_search_by_workspace", r)
    r = http.get(f"{A}/users", params={"limit": 2}, headers=adm)
    assert r.status_code == 200 and r.json()["meta"]["next_cursor"]
    capture("admin_users", "users_page1", r)
    r = http.get(f"{A}/users", params={"billing_status": "gold"}, headers=adm)
    assert r.status_code == 422
    capture("admin_users", "users_bad_filter", r)
    r = http.get(f"{A}/users/{victim_id}", headers=adm)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["can"]["suspend"] is True
    capture("admin_users", "user_profile", r)
    r = http.get(f"{A}/users/{uuid.uuid4()}", headers=adm)
    assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"
    capture("admin_users", "user_profile_unknown", r)

    # ---- suspend: the user is out at once, same token ---------------------------------------
    assert vc.get("/api/v1/auth/me", headers=victim).status_code == 200
    r = http.post(
        f"{A}/users/{victim_id}/suspend",
        json={"reason": "short", "totp_code": adm_app.fresh()},
        headers=adm,
    )
    assert r.status_code == 422
    capture("admin_users", "suspend_reason_too_short", r)
    r = http.post(
        f"{A}/users/{victim_id}/suspend",
        json={"reason": "Abuse report 17 confirmed", "totp_code": "000000"},
        headers=adm,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_users", "suspend_wrong_code", r)
    r = _act(http, adm, adm_app, f"/users/{victim_id}/suspend", "Abuse report 17 confirmed")
    assert r.status_code == 200, r.text
    capture("admin_users", "suspend_ok", r)
    r = vc.get("/api/v1/auth/me", headers=victim)
    assert r.status_code == 401
    capture("admin_users", "suspended_user_old_token_401", r)
    r = http.post("/api/v1/auth/login", json=victim_user)
    assert r.status_code == 401
    capture("admin_users", "suspended_user_login_401", r)
    r = http.get(f"{A}/users/{victim_id}", headers=adm)
    assert r.json()["data"]["status"] == "disabled" and r.json()["data"]["can"]["unsuspend"] is True
    capture("admin_users", "user_profile_suspended", r)
    r = _act(http, sa, sa_app, f"/users/{victim_id}/suspend", "Second look at the same case")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_SUSPENDED"
    capture("admin_users", "suspend_already_suspended", r)
    r = _act(http, adm, adm_app, f"/users/{victim_id}/reset-password")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RESET_NOT_ALLOWED"
    capture("admin_users", "reset_password_suspended", r)

    # ---- unsuspend ---------------------------------------------------------------------------
    r = _act(
        http, adm, adm_app, f"/users/{victim_id}/unsuspend", "Reviewed, reinstating the account"
    )
    assert r.status_code == 200, r.text
    capture("admin_users", "unsuspend_ok", r)
    assert http.post("/api/v1/auth/login", json=victim_user).status_code == 200

    # ---- reset password and resend verification (real emails) -------------------------------
    victim_mails = mailbox.count_for(victim_user["email"])
    r = _act(http, adm, adm_app, f"/users/{victim_id}/reset-password")
    assert r.status_code == 200, r.text
    capture("admin_users", "reset_password_ok", r)
    assert mailbox.count_for(victim_user["email"]) == victim_mails + 1
    r = _act(http, sa, sa_app, f"/users/{victim_id}/reset-password")
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_SOON"
    capture("admin_users", "reset_password_too_soon", r)
    pending_id = http.get(f"{A}/users", params={"query": pending_email}, headers=adm).json()[
        "data"
    ]["users"][0]["id"]
    r = _act(http, adm, adm_app, f"/users/{pending_id}/resend-verification")
    assert r.status_code == 200, r.text
    capture("admin_users", "resend_verification_ok", r)
    assert mailbox.count_for(pending_email) == mails_before + 1
    r = _act(http, sa, sa_app, f"/users/{victim_id}/resend-verification")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_VERIFIED"
    capture("admin_users", "resend_verification_already_verified", r)

    # ---- staff targets -----------------------------------------------------------------------
    sa_id = _staff_by_email(http, sa, sa_user["email"])["user_id"]
    r = _act(http, adm, adm_app, f"/users/{sa_id}/suspend", "Trying to suspend a Super Admin")
    assert r.status_code == 403 and r.json()["error"]["code"] == "STAFF_TARGET"
    capture("admin_users", "suspend_staff_as_admin", r)
    r = _act(http, sa, sa_app, f"/users/{sa_id}/suspend", "Trying to suspend myself here")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    capture("admin_users", "suspend_self", r)
    r = http.get(f"{A}/users/{sa_id}", headers=adm)
    assert r.json()["data"]["is_staff"] is True and not any(r.json()["data"]["can"].values())
    capture("admin_users", "user_profile_staff_as_admin", r)
    assert r.json()["data"]["sessions"] == [] and r.json()["data"]["phone"] is None

    # ---- change a staff member's scopes without revoking them (P9) ---------------------------
    adm_id = _staff_by_email(http, sa, adm_user["email"])["id"]
    own_staff_id = _staff_by_email(http, sa, sa_user["email"])["id"]

    def patch(staff_id, role, scopes, reason="Adding the content scope", code=None):
        return http.patch(
            f"{S}/staff/{staff_id}",
            json={
                "role": role,
                "scopes": scopes,
                "reason": reason,
                "totp_code": code or sa_app.fresh(),
            },
            headers=sa,
        )

    r = patch(adm_id, "admin", ["support", "content"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["scopes"] == ["content", "support"]
    capture("admin_users", "staff_patch_ok", r)
    r = http.get(f"{A}/me", headers=adm)
    assert r.json()["data"]["scopes"] == ["content", "support"]
    capture("admin_users", "admin_me_after_patch", r)
    r = patch(adm_id, "admin", ["support", "content"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHANGE"
    capture("admin_users", "staff_patch_no_change", r)
    r = patch(own_staff_id, "admin", ["support"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_EDIT_SELF"
    capture("admin_users", "staff_patch_self", r)
    r = patch(adm_id, "admin", [])
    assert r.status_code == 422
    capture("admin_users", "staff_patch_admin_without_scopes", r)
    r = patch(adm_id, "admin", ["support"], code="000000")
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_users", "staff_patch_wrong_code", r)
    r = http.patch(
        f"{S}/staff/{own_staff_id}",
        json={
            "role": "admin",
            "scopes": ["support"],
            "reason": "An Admin trying this",
            "totp_code": adm_app.fresh(),
        },
        headers=adm,
    )
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    capture("admin_users", "staff_patch_as_admin_404", r)

    # ---- workspaces and dashboard ------------------------------------------------------------
    r = http.get(f"{A}/workspaces", params={"query": "NovaPay"}, headers=adm)
    assert r.status_code == 200 and r.json()["data"]["workspaces"][0]["id"] == ws_id
    capture("admin_users", "workspaces_search", r)
    r = http.get(f"{A}/workspaces/{ws_id}", headers=adm)
    assert r.status_code == 200, r.text
    capture("admin_users", "workspace_detail", r)
    r = http.get(f"{A}/workspaces/{uuid.uuid4()}", headers=adm)
    assert r.status_code == 404 and r.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    capture("admin_users", "workspace_unknown", r)
    r = http.get(f"{A}/dashboard/kpis", headers=adm)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total_workspaces"]["value"] >= 1
    capture("admin_users", "dashboard_kpis", r)

    # ---- audit and staff list ----------------------------------------------------------------
    r = http.get(f"{S}/audit", params={"action": "user.", "limit": 10}, headers=sa)
    assert r.status_code == 200, r.text
    actions = [e["action"] for e in r.json()["data"]["entries"]]
    assert {
        "user.suspended",
        "user.unsuspended",
        "user.password_reset_sent",
        "user.verification_resent",
    } <= set(actions)
    capture("admin_users", "audit_user_actions", r)
    r = http.get(f"{S}/staff", headers=sa)
    assert _staff_by_email(http, sa, sa_user["email"])["last_login_at"]
    capture("admin_users", "staff_list_with_last_login", r)
