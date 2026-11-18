"""Live Google sign-in / sign-up journey.

Google is the in-process fake (GOOGLE_SIGNIN_STUB, app/services/auth/google_stub.py): the server
answers the token and JWKS calls itself and signs REAL RS256 id_tokens, so signature, iss, aud,
exp, nonce and PKCE all run for real. The Google CONSENT SCREEN is NOT exercised: `stub_authorize`
plays Google's part and builds the redirect Google would send the browser to.

Captures land in `e2e/_captures/google_sign_in/*.json`. Tokens and tickets are replaced by labelled
placeholders (they are bearer secrets); every other byte is as the server sent it.
"""

import json
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest

from app.services.auth.google_stub import stub_authorize

G = "google_sign_in"
BASE = "/api/v1/auth/oauth/google"
_SECRET_KEYS = {"access_token", "refresh_token", "mfa_ticket", "ticket", "invite_token"}
_QS_SECRETS = re.compile(r"(state|nonce|code_challenge|ticket)=[^&\"\s]+")


def _scrub(value):
    if isinstance(value, dict):
        return {
            k: (f"<{k}-placeholder>" if k in _SECRET_KEYS and isinstance(v, str) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _QS_SECRETS.sub(lambda m: f"{m.group(1)}=<{m.group(1)}-placeholder>", value)
    return value


def _save(name: str, body) -> None:
    out = Path(__file__).parent / "_captures" / G
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(_scrub(body), indent=2) + "\n")


def _start(c: httpx.Client, name: str | None = None, **body) -> str:
    r = c.post(f"{BASE}/start", json=body)
    assert r.status_code == 200, r.text
    if name:
        _save(f"{name}_start", r.json())
        _save(
            f"{name}_start_set_cookie",
            {
                "set-cookie": r.headers["set-cookie"].split("=")[0]
                + "=<proof-placeholder>; "
                + r.headers["set-cookie"].split(";", 1)[1]
            },
        )
    return r.json()["data"]["auth_url"]


def _land(c: httpx.Client, callback_url: str, name: str | None = None) -> dict:
    """The browser lands on the API callback; capture the redirect to the FE."""
    path = callback_url.replace(c.base_url.scheme + "://" + c.base_url.netloc.decode(), "")
    r = c.get(path, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    if name:
        _save(f"{name}_callback_redirect", {"status": 302, "location": loc})
    u = urlparse(loc)
    return {k: v[0] for k, v in {**parse_qs(u.query), **parse_qs(u.fragment)}.items()}


def _signin(c, *, name=None, sub, email, verified=True, **body) -> dict:
    url = _start(c, name, **body)
    return _land(c, stub_authorize(url, sub=sub, email=email, email_verified=verified), name)


def _capture_resp(name: str, r: httpx.Response) -> None:
    _save(name, {"status": r.status_code, "body": r.json()})


@pytest.mark.critical
def test_google_sign_in_journey(base_url, make_verified_user, unique_email, mailbox):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 1. First Google sign-in creates the account, email already verified.
        email = unique_email()
        sub = f"g-{uuid.uuid4().hex[:10]}"
        q = _signin(c, name="new_user", sub=sub, email=email, accepted_terms=True)
        assert set(q) == {"ticket"}
        r = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert r.status_code == 200, r.text
        _capture_resp("new_user_exchange", r)
        d = r.json()["data"]
        assert d["is_new_user"] is True and d["mfa_required"] is False
        auth = {"Authorization": f"Bearer {d['access_token']}"}
        me = c.get("/api/v1/auth/me", headers=auth)
        assert me.status_code == 200
        _capture_resp("new_user_me", me)

        # 2. The same Google account again: signs into the same account.
        q = _signin(c, name="returning", sub=sub, email=email)
        r = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert r.status_code == 200 and r.json()["data"]["is_new_user"] is False
        _capture_resp("returning_exchange", r)

        # 3. A one-time ticket cannot be traded twice.
        again = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert again.status_code == 400
        _capture_resp("error_ticket_reused", again)

        # 4. An existing password account is linked by verified email.
        pw_user = make_verified_user(c)
        q = _signin(
            c, name="link_existing", sub=f"g-{uuid.uuid4().hex[:10]}", email=pw_user["email"]
        )
        r = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert r.status_code == 200 and r.json()["data"]["is_new_user"] is False
        _capture_resp("link_existing_exchange", r)
        pw = c.post("/api/v1/auth/login", json=pw_user)
        assert pw.status_code == 200  # the password still works for a verified account

        # 5. A 2FA account ends in the MFA challenge: no session before the code.
        mfa_user = make_verified_user(c)
        tok = c.post("/api/v1/auth/login", json=mfa_user).json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        secret = c.post("/api/v1/auth/mfa/totp/setup", headers=h).json()["data"]["secret"]
        ok = c.post(
            "/api/v1/auth/mfa/totp/verify", headers=h, json={"code": pyotp.TOTP(secret).now()}
        )
        assert ok.status_code == 200
        q = _signin(c, name="mfa", sub=f"g-{uuid.uuid4().hex[:10]}", email=mfa_user["email"])
        r = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert r.status_code == 200
        _capture_resp("mfa_exchange", r)
        data = r.json()["data"]
        assert data["mfa_required"] is True and data["access_token"] is None
        done = c.post(
            "/api/v1/auth/mfa/challenge",
            json={"mfa_ticket": data["mfa_ticket"], "code": pyotp.TOTP(secret).now()},
        )
        assert done.status_code == 200, done.text
        _capture_resp("mfa_challenge", done)

        # 6. Unverified Google email is refused; so is a replayed callback.
        url = _start(c)
        cb = stub_authorize(url, sub="g-unverified", email=unique_email(), email_verified=False)
        q = _land(c, cb, "error_unverified")
        assert q == {"error": "email_not_verified"}
        replay = _land(c, cb, "error_replayed_state")
        assert replay == {"error": "invalid_state"}

        # 7. The person declines on Google's screen.
        url = _start(c)
        q = _land(
            c,
            stub_authorize(url, sub="x", email="x@example.com", error="access_denied"),
            "error_denied",
        )
        assert q == {"error": "access_denied"}

        # 8. Invites: an unknown token is refused at start; the 404 body is captured.
        bad = c.post(f"{BASE}/start", json={"invite_token": "not-a-real-invite"})
        assert bad.status_code == 404
        _capture_resp("error_invite_unknown", bad)

        # 9. A founder invites two people; the invite token rides the Google round trip.
        founder = make_verified_user(c)
        ftok = c.post("/api/v1/auth/login", json=founder).json()["data"]["access_token"]
        fh = {"Authorization": f"Bearer {ftok}"}
        c.patch(
            "/api/v1/onboarding/state", headers=fh, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch(
            "/api/v1/onboarding/state", headers=fh, json={"step": 2, "name": "Cofoundaz Google"}
        )
        c.patch(
            "/api/v1/onboarding/state",
            headers=fh,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch(
            "/api/v1/onboarding/state",
            headers=fh,
            json={"step": 4, "goals": ["Get first customers"]},
        )
        assert c.post("/api/v1/onboarding/complete", headers=fh).status_code == 200
        wid = c.get("/api/v1/auth/me", headers=fh).json()["data"]["active_workspace_id"]
        wh = {**fh, "X-Workspace-Id": wid}
        mate, other = unique_email(), unique_email()
        inv = c.post(
            "/api/v1/team/invitations",
            headers=wh,
            json={
                "invites": [
                    {"email": mate, "role": "team_member"},
                    {"email": other, "role": "team_member"},
                ]
            },
        )
        assert inv.status_code in (200, 201), inv.text
        mate_tok = mailbox.latest_token_for(mate, subject_contains="invited")
        other_tok = mailbox.latest_token_for(other, subject_contains="invited")

        # 9a. Matching Google email: the invite token comes back on exchange; accept it.
        q = _signin(
            c,
            name="invite_match",
            sub=f"g-{uuid.uuid4().hex[:10]}",
            email=mate,
            invite_token=mate_tok,
            next="/dashboard",
        )
        assert q["next"] == "/dashboard"
        r = c.post(f"{BASE}/exchange", json={"ticket": q["ticket"]})
        assert r.status_code == 200, r.text
        _capture_resp("invite_match_exchange", r)
        d = r.json()["data"]
        assert d["invite_token"] == mate_tok and d["is_new_user"] is True
        acc = c.post(
            "/api/v1/invitations/accept",
            json={"token": d["invite_token"]},
            headers={"Authorization": f"Bearer {d['access_token']}"},
        )
        assert acc.status_code == 200, acc.text
        _capture_resp("invite_match_accept", acc)

        # 9b. A different Google email: refused, nothing created.
        q = _signin(
            c,
            name="invite_mismatch",
            sub=f"g-{uuid.uuid4().hex[:10]}",
            email=unique_email(),
            invite_token=other_tok,
        )
        assert q == {"error": "invite_email_mismatch"}
