"""Live: Settings -> Profile, Security (password, 2FA, sessions), Workspace, notification schedule.

One founder, one real server. Responses are captured to e2e/_captures/settings/ and are the
verbatim source of docs/fe-integration-guide-settings.md. Anything secret (TOTP secret, backup
codes, tokens) is replaced with an obvious placeholder before it is written to disk.
"""

import secrets

import httpx
import pyotp

# Passwords are built per run so no password literal sits in the repo (secret scanners flag them).
WRONG_PW = "Wr-" + secrets.token_hex(6) + "-0"
NEW_PW = "Nw-" + secrets.token_hex(6) + "-7"

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    wsid = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
    return {**auth, "X-Workspace-Id": wsid}


def _login(base_url: str, u: dict) -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=base_url, timeout=15.0)
    r = c.post("/api/v1/auth/login", json=u)
    assert r.status_code == 200, r.text
    return c, r.json()["data"]["access_token"]


def test_settings_journey(base_url, make_verified_user, capture, capture_json):
    with httpx.Client(base_url=base_url, timeout=15.0) as boot:
        u = make_verified_user(boot)
    c, access = _login(base_url, u)  # keeps the refresh cookie, so "current session" is known
    auth = _auth(access)
    wh = _onboard(c, auth)

    # ---- profile -------------------------------------------------------------------------
    r = c.patch(
        "/api/v1/auth/me",
        headers=auth,
        json={
            "full_name": "Ada Obi",
            "role_title": "CEO",
            "country": "ng",
            "phone": "+2348012345678",
        },
    )
    assert r.status_code == 200, r.text
    capture("settings", "profile_patch", r)
    r = c.patch("/api/v1/auth/me", headers=auth, json={"nickname": "x"})
    assert r.status_code == 422
    capture("settings", "profile_patch_unknown_field", r)
    r = c.patch("/api/v1/auth/me", headers=auth, json={"country": "NGA"})
    assert r.status_code == 422
    capture("settings", "profile_patch_bad_country", r)
    r = c.post("/api/v1/auth/me/avatar", headers=auth, files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    capture("settings", "avatar_upload", r)
    r = c.post(
        "/api/v1/auth/me/avatar", headers=auth, files={"file": ("a.png", b"<html>", "image/png")}
    )
    assert r.status_code == 422
    capture("settings", "avatar_fake_bytes", r)
    r = c.delete("/api/v1/auth/me/avatar", headers=auth)
    assert r.status_code == 200 and r.json()["data"]["profile"]["avatar_url"] is None
    capture("settings", "avatar_remove", r)

    # ---- two-factor ----------------------------------------------------------------------
    r = c.get("/api/v1/auth/mfa", headers=auth)
    assert r.json()["data"]["enabled"] is False
    capture("settings", "mfa_status_off", r)
    setup = c.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]
    secret = setup["secret"]
    r = c.post(
        "/api/v1/auth/mfa/totp/verify", headers=auth, json={"code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200
    codes = r.json()["data"]["backup_codes"]
    r = c.get("/api/v1/auth/mfa", headers=auth)
    assert r.json()["data"]["backup_codes_remaining"] == 10
    capture("settings", "mfa_status_on", r)
    r = c.post(
        "/api/v1/auth/mfa/backup-codes/regenerate", headers=auth, json={"password": WRONG_PW}
    )
    assert r.status_code == 422
    capture("settings", "mfa_regenerate_wrong_password", r)
    r = c.post(
        "/api/v1/auth/mfa/backup-codes/regenerate", headers=auth, json={"password": u["password"]}
    )
    assert r.status_code == 200 and len(r.json()["data"]["backup_codes"]) == 10
    capture_json(
        "settings",
        "mfa_regenerate",
        {
            "data": {
                "backup_codes": ["0000000001", "...nine more, ten digits each (placeholders)"]
            },
            "meta": None,
        },
    )
    assert codes[0] not in r.json()["data"]["backup_codes"]

    # ---- sessions ------------------------------------------------------------------------
    other, _ = _login_with_mfa(base_url, u, secret)
    r = c.get("/api/v1/auth/sessions", headers=auth)
    rows = r.json()["data"]
    assert len(rows) == 2 and sum(x["current"] for x in rows) == 1
    capture_json(
        "settings", "sessions_list", {"data": [_redact_session(x) for x in rows], "meta": None}
    )
    theirs = next(x for x in rows if not x["current"])
    r = c.delete(f"/api/v1/auth/sessions/{theirs['id']}", headers=auth)
    assert r.status_code == 200
    capture("settings", "session_revoke", r)
    r = c.delete(f"/api/v1/auth/sessions/{theirs['id']}", headers=auth)
    assert r.status_code == 404
    capture("settings", "session_revoke_again", r)
    other.close()
    third, _ = _login_with_mfa(base_url, u, secret)
    r = c.post("/api/v1/auth/sessions/revoke-others", headers=auth)
    assert r.status_code == 200 and r.json()["data"]["revoked"] == 1
    capture("settings", "sessions_revoke_others", r)
    third.close()

    # ---- disable 2FA ---------------------------------------------------------------------
    r = c.post(
        "/api/v1/auth/mfa/disable",
        headers=auth,
        json={"password": u["password"], "code": "000000"},
    )
    assert r.status_code == 401
    capture("settings", "mfa_disable_bad_code", r)
    r = c.post(
        "/api/v1/auth/mfa/disable",
        headers=auth,
        json={"password": u["password"], "code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200 and r.json()["data"] == {"enabled": False}
    capture("settings", "mfa_disable", r)
    r = c.post(
        "/api/v1/auth/mfa/disable", headers=auth, json={"password": u["password"], "code": "1"}
    )
    assert r.status_code == 409
    capture("settings", "mfa_disable_not_enabled", r)

    # ---- password ------------------------------------------------------------------------
    r = c.post(
        "/api/v1/auth/password/change",
        headers=auth,
        json={"current_password": WRONG_PW, "new_password": NEW_PW},
    )
    assert r.status_code == 422
    capture("settings", "password_change_wrong_current", r)
    r = c.post(
        "/api/v1/auth/password/change",
        headers=auth,
        json={"current_password": u["password"], "new_password": "short"},
    )
    assert r.status_code == 422
    capture("settings", "password_change_weak", r)
    r = c.post(
        "/api/v1/auth/password/change",
        headers=auth,
        json={"current_password": u["password"], "new_password": NEW_PW},
    )
    assert r.status_code == 200, r.text
    capture("settings", "password_change", r)
    with httpx.Client(base_url=base_url, timeout=15.0) as fresh:
        assert fresh.post("/api/v1/auth/login", json=u).status_code == 401
        ok = fresh.post("/api/v1/auth/login", json={"email": u["email"], "password": NEW_PW})
        assert ok.status_code == 200
    # this device's refresh cookie is still valid after the change
    assert c.post("/api/v1/auth/refresh").status_code == 200

    # ---- workspace -----------------------------------------------------------------------
    r = c.get("/api/v1/workspace", headers=wh)
    assert r.status_code == 200 and r.json()["data"]["can_edit"] is True
    capture("settings", "workspace_get", r)
    r = c.patch(
        "/api/v1/workspace",
        headers=wh,
        json={
            "name": "Nova Pay",
            "website": "https://novapay.example",
            "stage": "build",
            "country": "ng",
        },
    )
    assert r.status_code == 200, r.text
    capture("settings", "workspace_patch", r)
    r = c.patch("/api/v1/workspace", headers=wh, json={"website": "ftp://nope"})
    assert r.status_code == 422
    capture("settings", "workspace_patch_bad_website", r)
    r = c.post("/api/v1/workspace/logo", headers=wh, files={"file": ("l.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["data"]["logo_url"]
    capture("settings", "workspace_logo", r)

    # ---- notification schedule -----------------------------------------------------------
    r = c.get("/api/v1/notifications/preferences", headers=wh)
    capture("settings", "notification_prefs_defaults", r)
    r = c.put(
        "/api/v1/notifications/preferences",
        headers=wh,
        json={
            "digest_mode": "weekly",
            "digest_time": "07:30",
            "digest_day": 4,
            "quiet_enabled": True,
            "quiet_from": "21:00",
            "quiet_until": "06:30",
            "timezone": "Africa/Lagos",
        },
    )
    assert r.status_code == 200, r.text
    capture("settings", "notification_prefs_saved", r)
    r = c.put("/api/v1/notifications/preferences", headers=wh, json={"digest_mode": "hourly"})
    assert r.status_code == 422
    capture("settings", "notification_prefs_bad_digest", r)
    c.close()


def _login_with_mfa(base_url: str, u: dict, secret: str) -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=base_url, timeout=15.0)
    gate = c.post("/api/v1/auth/login", json=u).json()["data"]
    assert gate["mfa_required"] is True
    r = c.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200, r.text
    return c, r.json()["data"]["access_token"]


def _redact_session(row: dict) -> dict:
    return {**row, "id": "00000000-0000-0000-0000-00000000000" + ("1" if row["current"] else "2")}
