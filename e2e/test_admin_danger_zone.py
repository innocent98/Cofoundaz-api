"""Live: Admin slice E. Maintenance mode and global sign-out, each with a second Super Admin approving.

One real server, SHARED with every other journey, so this test is built to be harmless to them:
- it never asserts how many staff or requests exist (other journeys leave their own);
- maintenance mode is forced OFF before it starts and again in `finally`, even if an assert fails,
  and its own open requests are cancelled there too;
- the global sign-out only invalidates tokens issued before it ran; every other journey signs in
  for itself inside its own test, so none holds a token across this one.

Responses go to e2e/_captures/admin_danger_zone/ and are the verbatim source of
docs/fe-integration-guide-admin-danger-zone.md. Tokens are never captured.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
import pyotp
from sqlalchemy import create_engine, text

S = "/api/v1/super-admin"
KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
REASON = "Planned database upgrade window"
OTHER = "Reviewed and agreed on the call"
MESSAGE = "Cofoundaz is being upgraded. Your work is safe; we will be back by 10:00 WAT."
WRITE = {"step": 1, "full_name": "Ada Obi"}


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: a code not yet spent on a step-up (3 fit the ~90 s window,
    each works once), waiting for the next one when all are used."""

    def __init__(self, secret: str) -> None:
        self.totp = pyotp.TOTP(secret)
        self.used: set[str] = set()

    def fresh(self) -> str:
        for _ in range(100):
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
    assert gate["mfa_required"] is True
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def _make_super_admin(email: str) -> None:
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", email],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert run.returncode == 0, run.stderr


def _force_maintenance_off(engine, user_ids: list[str]) -> None:
    """Whatever happened, leave the shared server open for business and our requests closed."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platform_settings SET value = :v WHERE key = 'maintenance' "
                "AND (value->>'enabled') = 'true'"
            ),
            {"v": json.dumps({"enabled": False, "message": None, "since": None})},
        )
        if user_ids:
            conn.execute(
                text(
                    "UPDATE staff_approval_requests SET status = 'cancelled' "
                    "WHERE status = 'pending' AND requested_by = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": user_ids},
            )


def test_danger_zone_journey(http: httpx.Client, make_verified_user, capture, capture_json):
    engine = create_engine(os.environ["DATABASE_URL"])
    owner = make_verified_user(http)
    second = make_verified_user(http)
    founder = make_verified_user(http)
    owner_app, second_app = _enroll(http, owner), _enroll(http, second)
    _make_super_admin(owner["email"])
    _make_super_admin(second["email"])
    with engine.connect() as conn:
        ids = [
            str(r[0])
            for r in conn.execute(
                text("SELECT id FROM users WHERE email = ANY(:e)"),
                {"e": [owner["email"], second["email"]]},
            )
        ]
    _force_maintenance_off(engine, [])  # a failed earlier run must not poison this one
    try:
        _journey(http, engine, owner, second, founder, owner_app, second_app, capture, capture_json)
    finally:
        _force_maintenance_off(engine, ids)
    # Whatever the journey did, the platform is open again.
    r = http.get("/api/v1/platform/status")
    assert r.json()["data"]["maintenance"]["enabled"] is False


def _journey(http, engine, owner, second, founder, owner_app, second_app, capture, capture_json):
    sa1 = _sign_in(http, owner, owner_app)
    sa2 = _sign_in(http, second, second_app)

    # ---- the founder has a workspace and a live session --------------------------------------
    f_login = http.post("/api/v1/auth/login", json=founder).json()["data"]
    f_auth = _auth(f_login["access_token"])
    http.get("/api/v1/onboarding/state", headers=f_auth)

    def a_write(h):
        return http.patch("/api/v1/onboarding/state", headers=h, json=WRITE)

    # ---- the banner endpoint is public and says "off" ----------------------------------------
    r = http.get("/api/v1/platform/status")
    assert r.status_code == 200 and r.json()["data"]["maintenance"]["enabled"] is False
    capture("admin_danger_zone", "status_off", r)

    # ---- who can see it ----------------------------------------------------------------------
    unknown = http.get("/api/v1/definitely-not-a-route")
    for _who, headers in (("anonymous", {}), ("founder", f_auth)):
        r = http.get(f"{S}/danger-zone", headers=headers)
        assert r.status_code == 404 and r.json() == unknown.json()
    capture("admin_danger_zone", "danger_zone_founder_404", r)
    r = http.get(f"{S}/danger-zone", headers=sa1)
    assert r.status_code == 200 and r.json()["data"]["maintenance"]["enabled"] is False
    assert second["email"] in [x["email"] for x in r.json()["data"]["eligible_approvers"]]
    capture("admin_danger_zone", "danger_zone_overview", r)
    second_id = next(
        x["user_id"]
        for x in r.json()["data"]["eligible_approvers"]
        if x["email"] == second["email"]
    )

    # ---- request: validation and step-up -----------------------------------------------------
    ask = {"action": "maintenance_enable", "reason": REASON, "message": MESSAGE}
    r = http.post(f"{S}/approvals", json={**ask, "totp_code": "000000"}, headers=sa1)
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_danger_zone", "request_wrong_code", r)
    r = http.post(f"{S}/approvals", json=ask, headers=sa1)
    assert r.status_code == 422
    capture("admin_danger_zone", "request_missing_code", r)
    r = http.post(
        f"{S}/approvals", json={**ask, "reason": "short", "totp_code": "123456"}, headers=sa1
    )
    assert r.status_code == 422
    capture("admin_danger_zone", "request_reason_too_short", r)
    r = http.post(
        f"{S}/approvals",
        json={
            "action": "global_signout",
            "reason": REASON,
            "message": "x",
            "totp_code": owner_app.fresh(),
        },
        headers=sa1,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "MESSAGE_NOT_ALLOWED", r.text

    # ---- request 1: the requester can't answer it; a different Super Admin rejects it ---------
    r = http.post(
        f"{S}/approvals",
        json={**ask, "approver_user_id": second_id, "totp_code": owner_app.fresh()},
        headers=sa1,
    )
    assert r.status_code == 201, r.text
    capture("admin_danger_zone", "request_created", r)
    req1 = r.json()["data"]
    assert req1["status"] == "pending" and req1["designated_approver"]["email"] == second["email"]
    assert http.get("/api/v1/platform/status").json()["data"]["maintenance"]["enabled"] is False
    r = http.post(f"{S}/approvals", json={**ask, "totp_code": owner_app.fresh()}, headers=sa1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_ALREADY_PENDING"
    capture("admin_danger_zone", "request_already_pending", r)
    r = http.post(
        f"{S}/approvals/{req1['id']}/approve",
        json={"reason": OTHER, "totp_code": owner_app.fresh()},
        headers=sa1,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_DECIDE_OWN_REQUEST"
    capture("admin_danger_zone", "approve_own_request_refused", r)
    r = http.get(f"{S}/approvals?status=pending", headers=sa2)
    assert req1["id"] in [x["id"] for x in r.json()["data"]["requests"]]
    capture("admin_danger_zone", "approvals_list_pending", r)
    r = http.post(
        f"{S}/approvals/{req1['id']}/reject",
        json={"reason": "Not tonight, the release is not ready", "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    capture("admin_danger_zone", "request_rejected", r)
    r = http.post(
        f"{S}/approvals/{req1['id']}/approve",
        json={"reason": OTHER, "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_PENDING"
    capture("admin_danger_zone", "approve_already_decided", r)

    # ---- request 2: cancelled by the requester ----------------------------------------------
    r = http.post(f"{S}/approvals", json={**ask, "totp_code": owner_app.fresh()}, headers=sa1)
    req2 = r.json()["data"]
    r = http.post(
        f"{S}/approvals/{req2['id']}/cancel",
        json={"reason": "Wrong time, asking again later", "totp_code": owner_app.fresh()},
        headers=sa1,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
    capture("admin_danger_zone", "request_cancelled", r)

    # ---- request 3: approved, maintenance goes on ---------------------------------------------
    r = http.post(f"{S}/approvals", json={**ask, "totp_code": owner_app.fresh()}, headers=sa1)
    req3 = r.json()["data"]
    r = http.post(
        f"{S}/approvals/{req3['id']}/approve",
        json={"reason": OTHER, "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "approved"
    capture("admin_danger_zone", "request_approved_maintenance_on", r)

    # ---- while it is on -----------------------------------------------------------------------
    r = http.get("/api/v1/platform/status")
    assert r.json()["data"]["maintenance"]["enabled"] is True
    assert r.json()["data"]["maintenance"]["message"] == MESSAGE
    capture("admin_danger_zone", "status_on", r)
    r = a_write(f_auth)
    assert r.status_code == 503 and r.json()["error"]["code"] == "MAINTENANCE_MODE", r.text
    assert r.headers["retry-after"] == "60"
    capture("admin_danger_zone", "write_blocked_503", r)
    capture_json(
        "admin_danger_zone", "write_blocked_headers", {"retry-after": r.headers["retry-after"]}
    )
    assert http.get("/api/v1/onboarding/state", headers=f_auth).status_code == 200  # reads work
    assert http.post("/api/v1/auth/login", json=founder).status_code == 200  # sign-in works
    # A payment event is processed, not bounced with a 503.
    raw = json.dumps({"event": "charge.success", "data": {"reference": "e2e-no-such-ref"}}).encode()
    sig = hmac.new(KEY.encode(), raw, hashlib.sha512).hexdigest()
    r = http.post(
        "/api/v1/webhooks/billing/paystack",
        content=raw,
        headers={"x-paystack-signature": sig, "content-type": "application/json"},
    )
    assert r.status_code != 503, r.text
    capture("admin_danger_zone", "paystack_webhook_during_maintenance", r)
    r = http.post(
        "/api/v1/webhooks/billing/paystack",
        content=raw,
        headers={"x-paystack-signature": "bad", "content-type": "application/json"},
    )
    assert r.status_code == 401
    capture("admin_danger_zone", "paystack_webhook_bad_signature", r)
    assert http.get(f"{S}/danger-zone", headers=sa1).status_code == 200  # staff can still work

    # ---- one Super Admin can switch it off alone -----------------------------------------------
    r = http.post(
        f"{S}/maintenance/disable",
        json={"reason": "Upgrade finished, all checks green", "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 200, r.text
    capture("admin_danger_zone", "maintenance_disabled", r)
    r = http.post(
        f"{S}/maintenance/disable",
        json={"reason": "Upgrade finished, all checks green", "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAINTENANCE_NOT_ON"
    capture("admin_danger_zone", "maintenance_disable_when_off", r)
    r = a_write(f_auth)
    assert r.status_code != 503

    # ---- global sign-out ----------------------------------------------------------------------
    f_login = http.post("/api/v1/auth/login", json=founder).json()["data"]
    f_auth, f_refresh = _auth(f_login["access_token"]), f_login["refresh_token"]
    assert http.get("/api/v1/auth/me", headers=f_auth).status_code == 200
    r = http.post(
        f"{S}/approvals",
        json={
            "action": "global_signout",
            "reason": "Suspected leaked refresh token, signing everyone out",
            "totp_code": owner_app.fresh(),
        },
        headers=sa1,
    )
    assert r.status_code == 201, r.text
    capture("admin_danger_zone", "signout_requested", r)
    so = r.json()["data"]
    assert http.get("/api/v1/auth/me", headers=f_auth).status_code == 200  # nothing yet
    r = http.get(f"{S}/approvals", headers=sa2)
    capture("admin_danger_zone", "approvals_list_all", r)
    r = http.post(
        f"{S}/approvals/{so['id']}/approve",
        json={"reason": OTHER, "totp_code": second_app.fresh()},
        headers=sa2,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["outcome"]["sessions_signed_out"] >= 1
    capture("admin_danger_zone", "signout_approved", r)

    r = http.get("/api/v1/auth/me", headers=f_auth)
    assert r.status_code == 401
    capture("admin_danger_zone", "old_access_token_401", r)
    r = http.post("/api/v1/auth/refresh", json={"refresh_token": f_refresh})
    assert r.status_code == 401
    capture("admin_danger_zone", "old_refresh_token_401", r)
    r = http.get(f"{S}/danger-zone", headers=sa2)  # staff are signed out like everyone else
    assert r.status_code == 404
    capture("admin_danger_zone", "staff_old_token_404", r)

    # ---- everyone, staff included, can sign straight back in -----------------------------------
    r = http.post("/api/v1/auth/login", json=founder)
    assert r.status_code == 200
    f_new = _auth(r.json()["data"]["access_token"])
    assert http.get("/api/v1/auth/me", headers=f_new).status_code == 200
    sa1, sa2 = _sign_in(http, owner, owner_app), _sign_in(http, second, second_app)
    r = http.get(f"{S}/danger-zone", headers=sa1)
    assert r.status_code == 200 and r.json()["data"]["last_global_signout_at"]
    capture("admin_danger_zone", "danger_zone_after_signout", r)

    # ---- the audit trail ------------------------------------------------------------------------
    r = http.get(f"{S}/audit?action=approval.", headers=sa1)
    assert {e["action"] for e in r.json()["data"]["entries"]} >= {
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.cancelled",
    }
    capture("admin_danger_zone", "audit_approval_rows", r)
    for action in ("maintenance.enabled", "maintenance.disabled", "platform.global_signout"):
        r = http.get(f"{S}/audit?action={action}", headers=sa1)
        assert r.json()["data"]["entries"], action
        capture("admin_danger_zone", f"audit_{action.replace('.', '_')}", r)
