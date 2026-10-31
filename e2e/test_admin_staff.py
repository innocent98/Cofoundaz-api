"""Live: Admin slice A. Staff access, Super Admin staff management, audit explorer, append-only log.

One real server. Responses go to e2e/_captures/admin_staff/ and are the verbatim source of
docs/fe-integration-guide-admin-staff.md. The first Super Admin is created with the real command
(`python -m app.scripts.create_super_admin`), exactly as an operator would.
"""

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
import pyotp
from sqlalchemy import create_engine, text

S = "/api/v1/super-admin"
A = "/api/v1/admin"
REASON = "Onboarding a support lead"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: hands out a code not yet used for a step-up. A code is valid
    for the previous, current and next 30 s step and is single use, so wait for a new one when
    all three are spent."""

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

    def spent(self) -> str:
        """A code already spent on a step-up (for the replay check)."""
        return next(iter(self.used))


def _enroll(http: httpx.Client, u: dict) -> Authenticator:
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = _auth(login["access_token"])
    secret = http.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]["secret"]
    r = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert r.status_code == 200, r.text
    return Authenticator(secret)


def _sign_in(http: httpx.Client, u: dict, secret_app: Authenticator) -> dict:
    gate = http.post("/api/v1/auth/login", json=u).json()["data"]
    assert gate["mfa_required"] is True
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": secret_app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def test_staff_access_journey(http: httpx.Client, make_verified_user, capture, capture_json):
    # ---- the first Super Admin, by command -------------------------------------------------
    owner = make_verified_user(http)
    founder = make_verified_user(http)  # an ordinary account that never becomes staff
    plain = _auth(http.post("/api/v1/auth/login", json=founder).json()["data"]["access_token"])
    owner_app = _enroll(http, owner)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", owner["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert run.returncode == 0, run.stderr
    capture_json("admin_staff", "create_super_admin_command", {"stdout": run.stdout.strip()})
    sa = _sign_in(http, owner, owner_app)

    # ---- unknown callers see a plain 404 ---------------------------------------------------
    unknown = http.get("/api/v1/definitely-not-a-route")
    capture("admin_staff", "unknown_route_404", unknown)
    for name, headers in (("anonymous", {}), ("plain_user", plain)):
        r = http.get(f"{S}/staff", headers=headers)
        assert r.status_code == 404 and r.json() == unknown.json(), r.text
        capture("admin_staff", f"staff_list_{name}_404", r)
    r = http.get(f"{A}/me", headers=sa)
    assert r.status_code == 200 and r.json()["data"]["role"] == "super_admin"
    capture("admin_staff", "admin_me_super_admin", r)

    r = http.get(f"{S}/staff", headers=sa)
    # The e2e database is shared by every staff journey, so look for this owner, not "the only row".
    assert r.status_code == 200
    assert [s["email"] for s in r.json()["data"]].count(owner["email"]) == 1
    capture("admin_staff", "staff_list_first", r)

    # ---- invite: eligibility ---------------------------------------------------------------
    helper = make_verified_user(http)
    body = {
        "email": helper["email"],
        "role": "admin",
        "scopes": ["support", "moderation"],
        "reason": REASON,
        "totp_code": owner_app.fresh(),
    }
    r = http.post(f"{S}/staff", json=body, headers=sa)
    assert r.status_code == 422 and r.json()["error"]["code"] == "STAFF_MFA_REQUIRED", r.text
    capture("admin_staff", "invite_target_without_mfa", r)
    helper_app = _enroll(http, helper)

    r = http.post(
        f"{S}/staff",
        json={**body, "email": "nobody-here@resend.dev", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"
    capture("admin_staff", "invite_unknown_email", r)

    # ---- invite: validation and step-up ----------------------------------------------------
    r = http.post(f"{S}/staff", json={**body, "reason": "too short"}, headers=sa)
    assert r.status_code == 422
    capture("admin_staff", "invite_reason_too_short", r)
    no_code = {k: v for k, v in body.items() if k != "totp_code"}
    r = http.post(f"{S}/staff", json=no_code, headers=sa)
    assert r.status_code == 422
    capture("admin_staff", "invite_missing_code", r)
    r = http.post(f"{S}/staff", json={**body, "scopes": []}, headers=sa)
    assert r.status_code == 422
    capture("admin_staff", "invite_admin_without_scopes", r)
    r = http.post(f"{S}/staff", json={**body, "totp_code": "000000"}, headers=sa)
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_staff", "invite_wrong_code", r)

    # ---- invite: success, then the new Admin's view ---------------------------------------
    body["totp_code"] = owner_app.fresh()
    r = http.post(f"{S}/staff", json=body, headers=sa)
    assert r.status_code == 201, r.text
    capture("admin_staff", "invite_ok", r)
    staff_id = r.json()["data"]["id"]
    adm = _sign_in(http, helper, helper_app)
    r = http.get(f"{A}/me", headers=adm)
    assert r.status_code == 200 and r.json()["data"]["scopes"] == ["moderation", "support"]
    capture("admin_staff", "admin_me_admin", r)
    r = http.get(f"{S}/staff", headers=adm)
    assert r.status_code == 404
    capture("admin_staff", "staff_list_as_admin_404", r)

    r = http.post(f"{S}/staff", json={**body, "totp_code": owner_app.fresh()}, headers=sa)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_STAFF"
    capture("admin_staff", "invite_already_staff", r)
    r = http.post(f"{S}/staff", json={**body, "totp_code": owner_app.spent()}, headers=sa)
    assert r.status_code == 403
    capture("admin_staff", "invite_replayed_code", r)

    # ---- revoke ----------------------------------------------------------------------------
    r = http.post(
        f"{S}/staff/{staff_id}/revoke",
        json={"reason": "short", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 422
    capture("admin_staff", "revoke_reason_too_short", r)
    self_id = next(
        s["id"]
        for s in http.get(f"{S}/staff", headers=sa).json()["data"]
        if s["email"] == owner["email"]
    )
    r = http.post(
        f"{S}/staff/{self_id}/revoke",
        json={"reason": "Trying to remove myself", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_REVOKE_SELF"
    capture("admin_staff", "revoke_self", r)
    r = http.post(
        f"{S}/staff/00000000-0000-4000-8000-000000000000/revoke",
        json={"reason": "No such person here", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "STAFF_NOT_FOUND"
    capture("admin_staff", "revoke_unknown", r)
    r = http.post(
        f"{S}/staff/{staff_id}/revoke",
        json={"reason": "Moved to another team", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 200 and r.json()["data"]["revoked_at"], r.text
    capture("admin_staff", "revoke_ok", r)
    r = http.get(f"{A}/me", headers=adm)  # the same token, one request later
    assert r.status_code == 404
    capture("admin_staff", "admin_me_after_revoke_404", r)
    r = http.post(
        f"{S}/staff/{staff_id}/revoke",
        json={"reason": "Moved to another team", "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_REVOKED"
    capture("admin_staff", "revoke_already_revoked", r)

    r = http.post(
        f"{S}/staff",
        json={
            **body,
            "scopes": ["content"],
            "reason": "Back on the content team",
            "totp_code": owner_app.fresh(),
        },
        headers=sa,
    )
    assert r.status_code == 201 and r.json()["data"]["id"] == staff_id
    capture("admin_staff", "invite_reinstated", r)
    r = http.get(f"{S}/staff", headers=sa)
    capture("admin_staff", "staff_list_two", r)

    # ---- audit explorer --------------------------------------------------------------------
    r = http.get(f"{S}/audit?action=staff.&limit=3", headers=sa)
    assert r.status_code == 200
    page1 = r.json()
    assert page1["meta"]["next_cursor"] and len(page1["data"]["entries"]) == 3
    capture("admin_staff", "audit_page1", r)
    r = http.get(
        f"{S}/audit?action=staff.&limit=3&cursor={page1['meta']['next_cursor']}", headers=sa
    )
    assert r.status_code == 200
    capture("admin_staff", "audit_page2", r)
    actions = {e["action"] for e in page1["data"]["entries"] + r.json()["data"]["entries"]}
    assert {"staff.invited", "staff.revoked", "staff.reinstated", "staff.bootstrapped"} <= actions
    r = http.get(f"{S}/audit?action=staff.revoked", headers=sa)
    e = r.json()["data"]["entries"][0]
    assert e["reason"] == "Moved to another team" and e["actor_email"] == owner["email"]
    capture("admin_staff", "audit_filter_action", r)
    r = http.get(f"{S}/audit?cursor=nonsense", headers=sa)
    assert r.status_code == 422
    capture("admin_staff", "audit_bad_cursor", r)
    r = http.get(f"{S}/audit", headers=adm)
    assert r.status_code == 404

    r = http.get(f"{S}/audit/export", headers=sa)
    assert r.status_code == 422
    capture("admin_staff", "audit_export_no_reason", r)
    r = http.get(f"{S}/audit/export?action=staff.&reason=Access+review+for+Q4", headers=sa)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    capture_json(
        "admin_staff",
        "audit_export_csv",
        {
            "status": r.status_code,
            "content_type": r.headers["content-type"],
            "content_disposition": r.headers["content-disposition"],
            "body": r.text,
        },
    )
    r = http.get(f"{S}/audit?action=audit.exported", headers=sa)
    assert r.json()["data"]["entries"][0]["details"]["filters"]["action"] == "staff."
    capture("admin_staff", "audit_export_is_audited", r)

    # ---- the database refuses to rewrite history ------------------------------------------
    engine = create_engine(os.environ["DATABASE_URL"])
    errors = {}
    for name, stmt in (
        ("update", "UPDATE audit_log SET action = 'tampered'"),
        ("delete", "DELETE FROM audit_log"),
    ):
        with engine.connect() as conn:
            try:
                conn.execute(text(stmt))
                raise AssertionError(f"{name} on audit_log was allowed")
            except AssertionError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors[name] = str(exc.orig).splitlines()[0]  # type: ignore[attr-defined]
    assert all("append-only" in v for v in errors.values()), errors
    capture_json("admin_staff", "audit_log_append_only", errors)
