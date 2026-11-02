"""Live: Admin slice D. Feature flags: Super Admin create/update with step-up, the app's read
route, bucketing, allow-list, kill switch, blast radius and the audit trail.

One real server on a database every other journey shares, so each run uses its own flag keys and
every list assertion filters to them. Responses go to e2e/_captures/admin_flags/ and are the
verbatim source of docs/fe-integration-guide-admin-flags.md.
"""

import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

S = "/api/v1/super-admin"
REASON = "Rolling out to the beta cohort"


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


def _flags_for(http, auth, workspace_id) -> dict:
    r = http.get("/api/v1/flags", headers={**auth, "X-Workspace-Id": workspace_id})
    assert r.status_code == 200, r.text
    return r.json()["data"]["flags"]


def test_feature_flags_journey(http: httpx.Client, make_verified_user, capture, capture_json):
    # ---- people: a Super Admin by command, two founders in two workspaces ---------------------
    owner = make_verified_user(http)
    owner_app = _enroll(http, owner)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", owner["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert run.returncode == 0, run.stderr
    sa = _sign_in(http, owner, owner_app)

    a_user, b_user = make_verified_user(http), make_verified_user(http)
    a = _auth(http.post("/api/v1/auth/login", json=a_user).json()["data"]["access_token"])
    b = _auth(http.post("/api/v1/auth/login", json=b_user).json()["data"]["access_token"])
    ws_a, ws_b = _onboard(http, a, "Flag Co A"), _onboard(http, b, "Flag Co B")

    key = f"e2e_voice_{uuid.uuid4().hex[:8]}"
    key2 = f"e2e_board_{uuid.uuid4().hex[:8]}"
    draft = {
        "key": key,
        "name": "AI voice notes",
        "description": "Dictate journal entries and mission updates.",
        "stage": "beta",
    }

    def create(payload, code=None):
        return http.post(
            f"{S}/flags",
            json={**payload, "reason": REASON, "totp_code": code or owner_app.fresh()},
            headers=sa,
        )

    # ---- hidden from everyone who isn't a Super Admin -----------------------------------------
    unknown = http.get("/api/v1/definitely-not-a-route")
    for who, h in (("anonymous", {}), ("founder", a)):
        r = http.get(f"{S}/flags", headers=h)
        assert r.status_code == 404 and r.json() == unknown.json(), r.text
        capture("admin_flags", f"list_{who}_404", r)

    # ---- create: validation, step-up, success, duplicate --------------------------------------
    r = create({**draft, "key": "Bad Key"})
    assert r.status_code == 422
    capture("admin_flags", "create_bad_key", r)
    r = create({**draft, "rollout_pct": 101})
    assert r.status_code == 422
    capture("admin_flags", "create_rollout_out_of_range", r)
    r = create({**draft, "workspace_allowlist": [str(uuid.uuid4())]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_WORKSPACE"
    capture("admin_flags", "create_unknown_workspace", r)
    r = create(draft, code="000000")
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_flags", "create_wrong_code", r)
    r = create(draft)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["enabled"] is False and r.json()["data"]["rollout_pct"] == 0
    capture("admin_flags", "create_ok", r)
    r = create(draft)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FLAG_KEY_EXISTS"
    capture("admin_flags", "create_duplicate", r)

    # ---- the app's read route -----------------------------------------------------------------
    assert key not in _flags_for(http, a, ws_a)  # off flags aren't listed
    r = http.get("/api/v1/flags", headers={**a, "X-Workspace-Id": ws_b})
    assert r.status_code == 403  # a member of A can't ask for B
    capture("admin_flags", "app_flags_not_a_member", r)
    r = http.get("/api/v1/flags")
    assert r.status_code in (401, 422)
    capture("admin_flags", "app_flags_no_auth", r)

    # ---- update: blast radius first, then enable at 100% --------------------------------------
    r = http.get(f"{S}/flags/{key}/impact?enabled=true&rollout_pct=100", headers=sa)
    assert r.status_code == 200
    impact = r.json()["data"]
    assert impact["affected_now"] == 0 and impact["affected_after"] == impact["total_workspaces"]
    capture("admin_flags", "impact_preview", r)

    def patch(**fields):
        return http.patch(
            f"{S}/flags/{key}",
            json={**fields, "reason": REASON, "totp_code": owner_app.fresh()},
            headers=sa,
        )

    r = patch(enabled=True, rollout_pct=100, stage="ga")
    assert r.status_code == 200, r.text
    capture("admin_flags", "update_enable_100", r)
    assert _flags_for(http, a, ws_a)[key] is True and _flags_for(http, b, ws_b)[key] is True
    r = http.get("/api/v1/flags", headers={**a, "X-Workspace-Id": ws_a})
    capture_json(
        "admin_flags",
        "app_flags_on_trimmed_to_this_run",
        {**r.json(), "data": {"flags": {key: r.json()["data"]["flags"][key]}}},
    )

    # ---- kill switch, then allow-list beats a 0% rollout --------------------------------------
    r = patch(enabled=False)
    assert r.status_code == 200 and r.json()["data"]["affected_workspaces"] == 0
    capture("admin_flags", "update_kill_switch", r)
    assert key not in _flags_for(http, a, ws_a) and key not in _flags_for(http, b, ws_b)
    r = patch(enabled=True, rollout_pct=0, workspace_allowlist=[ws_a])
    assert r.status_code == 200 and r.json()["data"]["affected_workspaces"] == 1
    capture("admin_flags", "update_allowlist_only", r)
    seen = [(key in _flags_for(http, a, ws_a), key in _flags_for(http, b, ws_b)) for _ in range(3)]
    assert seen == [(True, False)] * 3  # stable across calls

    # ---- update refusals ----------------------------------------------------------------------
    r = patch(rollout_pct=0)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHANGE"
    capture("admin_flags", "update_no_change", r)
    r = patch(rollout_pct=None)
    assert r.status_code == 422
    capture("admin_flags", "update_null_field", r)
    r = patch(key="renamed_flag")
    assert r.status_code == 422
    capture("admin_flags", "update_key_is_immutable", r)
    r = http.patch(
        f"{S}/flags/no_such_flag_{uuid.uuid4().hex[:6]}",
        json={"enabled": True, "reason": REASON, "totp_code": owner_app.fresh()},
        headers=sa,
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "FLAG_NOT_FOUND"
    capture("admin_flags", "update_unknown_flag", r)

    # ---- list, with a second flag ---------------------------------------------------------------
    r = create({"key": key2, "name": "Roadmap Kanban v2", "stage": "internal"})
    assert r.status_code == 201
    listing = http.get(f"{S}/flags", headers=sa)
    assert listing.status_code == 200
    mine = [f for f in listing.json()["data"]["flags"] if f["key"] in (key, key2)]
    assert [f["key"] for f in mine] == [key, key2]
    capture_json(
        "admin_flags",
        "list_trimmed_to_this_run",
        {**listing.json(), "data": {**listing.json()["data"], "flags": mine}},
    )

    # ---- audit ---------------------------------------------------------------------------------
    r = http.get(f"{S}/audit?action=flag.&limit=100", headers=sa)
    assert r.status_code == 200
    rows = [e for e in r.json()["data"]["entries"] if e["details"].get("key") == key]
    assert [e["action"] for e in rows] == ["flag.updated"] * 3 + ["flag.created"]
    assert rows[0]["reason"] == REASON
    capture_json(
        "admin_flags",
        "audit_for_this_flag",
        {**r.json(), "data": {"entries": rows}},
    )
