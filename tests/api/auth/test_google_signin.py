"""Sign in / sign up with Google, with Google faked at the transport layer (google_stub): the
id_token is a real RS256 token, so signature, iss, aud, exp, nonce and PKCE all run for real."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import get_password_hash
from app.db.models.audit import AuditLog
from app.db.models.auth import AuthSession, OAuthAccount
from app.db.models.enums import MfaType, OAuthProvider, UserStatus
from app.db.models.user import User
from app.platform.state import set_maintenance
from app.services.auth import google_oidc, google_signin, mfa
from app.services.auth.google_stub import stub_authorize
from app.services.auth.sessions import hash_token
from tests.factories import create_invitation, create_startup, create_user

START = "/api/v1/auth/oauth/google/start"
CALLBACK = "/api/v1/auth/oauth/google/callback"
EXCHANGE = "/api/v1/auth/oauth/google/exchange"


@pytest.fixture(autouse=True)
def _google(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.example")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "placeholder-not-a-real-secret")
    monkeypatch.setattr(settings, "GOOGLE_SIGNIN_STUB", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "REFRESH_COOKIE_SECURE", False)  # http://testserver
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    google_oidc._jwks_cache.update(at=0.0, keys={})
    get_redis().delete("google_signin_rate:testclient")


def _begin(client, **body):
    r = client.post(START, json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]["auth_url"]


def _callback(client, url):
    """Play the browser landing on the callback; returns the FE redirect's query as a dict."""
    r = client.get(url.replace("https://api.example.test", ""), follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert (
        f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://app.example.test/auth/google/callback"
    )
    return {k: v[0] for k, v in {**parse_qs(loc.query), **parse_qs(loc.fragment)}.items()}


def _signin(client, *, sub="g-sub-1", email="new@example.com", verified=True, **body):
    auth_url = _begin(client, **body)
    q = _callback(client, stub_authorize(auth_url, sub=sub, email=email, email_verified=verified))
    return q


def _exchange(client, ticket):
    return client.post(EXCHANGE, json={"ticket": ticket})


def _audit(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def test_first_google_sign_in_creates_a_verified_account(client, db):
    q = _signin(client, email="Ada@Example.com")
    assert set(q) == {"ticket"}
    r = _exchange(client, q["ticket"])
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["is_new_user"] is True and d["mfa_required"] is False
    assert d["access_token"] and d["refresh_token"] and d["invite_token"] is None
    user = db.query(User).filter(User.email == "ada@example.com").one()
    assert user.email_verified_at is not None and user.status == UserStatus.active
    assert user.password_hash is None and user.profile.full_name == "Stub Person"
    acct = db.query(OAuthAccount).filter_by(user_id=user.id).one()
    assert acct.provider == OAuthProvider.google and acct.provider_account_id == "g-sub-1"
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {d['access_token']}"})
    assert me.status_code == 200 and "ada@example.com" in me.text
    assert (
        len(_audit(db, "auth.user.registered")) == 1 and len(_audit(db, "auth.login.success")) == 1
    )
    assert settings.REFRESH_COOKIE_NAME in r.cookies


def test_returning_user_is_signed_in_by_sub_even_after_a_google_email_change(client, db):
    first = _exchange(client, _signin(client, sub="g-9", email="a@example.com")["ticket"])
    uid = db.query(User).filter(User.email == "a@example.com").one().id
    assert first.json()["data"]["is_new_user"] is True
    second = _exchange(client, _signin(client, sub="g-9", email="renamed@example.com")["ticket"])
    assert second.status_code == 200 and second.json()["data"]["is_new_user"] is False
    assert db.query(User).count() == 1 and db.query(User).one().id == uid
    assert len(_audit(db, "auth.google.signin")) == 1


def test_existing_password_account_is_linked_by_verified_email(client, db):
    u = create_user(
        db,
        email="old@example.com",
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
        password_hash=get_password_hash("password1"),
    )
    r = _exchange(client, _signin(client, sub="g-link", email="OLD@example.com")["ticket"])
    d = r.json()["data"]
    assert d["is_new_user"] is False and d["access_token"]
    assert db.query(OAuthAccount).filter_by(user_id=u.id).one().provider_account_id == "g-link"
    assert u.password_hash is not None  # a verified account keeps its password
    assert len(_audit(db, "auth.google.linked")) == 1


def test_linking_an_unverified_signup_takes_it_over(client, db):
    """Pre-hijack guard: an unverified row may have been created by someone else with a password."""
    u = create_user(
        db,
        email="victim@example.com",
        status=UserStatus.pending_verification,
        password_hash=get_password_hash("attackerknows"),
        mfa_type=MfaType.totp,
        mfa_secret="squatter-secret",
    )
    db.add(
        AuthSession(
            user_id=u.id,
            refresh_token_hash=hash_token("r"),
            family_id=u.id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.flush()
    r = _exchange(client, _signin(client, sub="g-v", email="victim@example.com")["ticket"])
    assert r.status_code == 200
    db.refresh(u)
    assert u.password_hash is None and u.email_verified_at is not None
    assert u.status == UserStatus.active
    assert u.mfa_type == MfaType.none and u.mfa_secret is None
    old = db.query(AuthSession).filter(AuthSession.refresh_token_hash == hash_token("r")).one()
    assert old.revoked_at is not None
    pw = client.post(
        "/api/v1/auth/login", json={"email": "victim@example.com", "password": "attackerknows"}
    )
    assert pw.status_code == 401


def test_account_cannot_be_linked_to_a_second_google_sub(client, db):
    u = create_user(db, email="two@example.com", status=UserStatus.active)
    db.add(OAuthAccount(user_id=u.id, provider=OAuthProvider.google, provider_account_id="first"))
    db.flush()
    q = _signin(client, sub="second", email="two@example.com")
    assert q == {"error": "google_account_mismatch"}
    assert db.query(OAuthAccount).filter_by(user_id=u.id).count() == 1


def test_account_with_2fa_ends_in_the_mfa_challenge_and_no_session(client, db):
    import pyotp

    secret = mfa.generate_totp_secret()
    u = create_user(
        db,
        email="mfa@example.com",
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
        mfa_type=MfaType.totp,
        mfa_secret=mfa.encrypt_secret(secret),
        mfa_enabled_at=datetime.now(UTC),
    )
    r = _exchange(client, _signin(client, sub="g-mfa", email="mfa@example.com")["ticket"])
    d = r.json()["data"]
    assert d["mfa_required"] is True and d["mfa_ticket"] and d["access_token"] is None
    assert settings.REFRESH_COOKIE_NAME not in r.cookies
    assert db.query(AuthSession).filter_by(user_id=u.id).count() == 0
    done = client.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": d["mfa_ticket"], "code": pyotp.TOTP(secret).now()},
    )
    assert done.status_code == 200 and done.json()["data"]["access_token"]


def test_suspended_account_is_refused(client, db):
    u = create_user(db, email="sus@example.com", status=UserStatus.disabled)
    db.add(OAuthAccount(user_id=u.id, provider=OAuthProvider.google, provider_account_id="g-s"))
    db.flush()
    assert _signin(client, sub="g-s", email="sus@example.com") == {"error": "account_disabled"}
    # by email, with no link yet
    create_user(db, email="sus2@example.com", status=UserStatus.disabled)
    assert _signin(client, sub="g-s2", email="sus2@example.com") == {"error": "account_disabled"}
    assert db.query(AuthSession).count() == 0
    assert any(
        a.details == {"reason": "account_disabled"} for a in _audit(db, "auth.google.rejected")
    )


def test_suspension_between_callback_and_exchange_blocks_the_session(client, db):
    u = create_user(db, email="late@example.com", status=UserStatus.active)
    q = _signin(client, sub="g-late", email="late@example.com")
    u.status = UserStatus.disabled
    db.flush()
    r = _exchange(client, q["ticket"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "GOOGLE_TICKET_INVALID"


def test_maintenance_blocks_new_accounts_but_not_existing_ones(client, db):
    admin = create_user(db, email="staff@example.com")
    create_user(db, email="here@example.com", status=UserStatus.active)
    set_maintenance(db, enabled=True, message=None, actor_id=admin.id)
    assert _signin(client, sub="g-new", email="brandnew@example.com") == {"error": "maintenance"}
    assert db.query(User).filter(User.email == "brandnew@example.com").count() == 0
    ok = _signin(client, sub="g-here", email="here@example.com")
    assert "ticket" in ok


def test_unverified_google_email_is_rejected(client, db):
    assert _signin(client, email="u@example.com", verified=False) == {"error": "email_not_verified"}
    assert db.query(User).count() == 0


def test_user_denies_consent(client):
    auth_url = _begin(client)
    q = _callback(
        client, stub_authorize(auth_url, sub="x", email="x@example.com", error="access_denied")
    )
    assert q == {"error": "access_denied"}


def test_matching_invite_survives_the_round_trip(client, db):
    owner = create_user(db, email="owner@example.com", status=UserStatus.active)
    startup = create_startup(db, owner=owner)
    create_invitation(db, startup, email="Mate@Example.com", token_hash=hash_token("rawinvite"))
    q = _signin(
        client, sub="g-mate", email="mate@example.com", invite_token="rawinvite", next="/team"
    )
    assert q["next"] == "/team"
    d = _exchange(client, q["ticket"]).json()["data"]
    assert d["invite_token"] == "rawinvite" and d["is_new_user"] is True
    acc = client.post(
        "/api/v1/invitations/accept",
        json={"token": "rawinvite"},
        headers={"Authorization": f"Bearer {d['access_token']}"},
    )
    assert acc.status_code == 200, acc.text


def test_mismatched_invite_is_refused_and_creates_nothing(client, db):
    owner = create_user(db, email="owner2@example.com", status=UserStatus.active)
    startup = create_startup(db, owner=owner)
    create_invitation(db, startup, email="invited@example.com", token_hash=hash_token("rawinvite2"))
    q = _signin(client, sub="g-other", email="someoneelse@example.com", invite_token="rawinvite2")
    assert q == {"error": "invite_email_mismatch"}
    assert db.query(User).filter(User.email == "someoneelse@example.com").count() == 0
    assert db.query(OAuthAccount).count() == 0


def test_unknown_invite_is_refused_at_start(client):
    r = client.post(START, json={"invite_token": "nope"})
    assert r.status_code == 404


def test_state_is_single_use(client):
    auth_url = _begin(client)
    cb = stub_authorize(auth_url, sub="g-r", email="r@example.com")
    first = _callback(client, cb)
    assert "ticket" in first
    # the browser's proof cookie was cleared too, so re-send it by starting nothing new: replay
    assert _callback(client, cb) == {"error": "invalid_state"}


def test_ticket_is_useless_in_another_browser(client):
    """Login CSRF: an attacker's ticket URL parked in a victim's browser must not sign them in."""
    ticket = _signin(client, sub="g-csrf", email="csrf@example.com")["ticket"]
    client.cookies.clear()
    r = _exchange(client, ticket)
    assert r.status_code == 400 and r.json()["error"]["code"] == "GOOGLE_TICKET_INVALID"


def test_stub_is_ignored_outside_development_and_test(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    assert google_oidc.http_client()._transport.__class__.__name__ != "MockTransport"


def test_ticket_is_single_use(client):
    ticket = _signin(client, sub="g-t", email="t@example.com")["ticket"]
    assert _exchange(client, ticket).status_code == 200
    again = _exchange(client, ticket)
    assert again.status_code == 400 and again.json()["error"]["code"] == "GOOGLE_TICKET_INVALID"


def test_forged_and_missing_state_are_refused(client):
    _begin(client)
    forged = jwt.encode(
        {"typ": "google_signin_state", "jti": "x", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "wrong-key-padded-to-a-valid-hmac-length-xx",
        algorithm="HS256",
    )
    for bad in (forged, "garbage", None):
        qs = f"?code=c&state={bad}" if bad else "?code=c"
        r = client.get(CALLBACK + qs, follow_redirects=False)
        assert "error=invalid_state" in r.headers["location"]


def test_callback_from_another_browser_is_refused(client, db):
    """Login-CSRF: the attacker's callback URL is completed in a browser with no proof cookie."""
    auth_url = _begin(client)
    cb = stub_authorize(auth_url, sub="g-c", email="c@example.com")
    client.cookies.clear()
    assert _callback(client, cb) == {"error": "invalid_state"}
    assert db.query(User).count() == 0


def test_a_rejected_id_token_creates_no_account(client, monkeypatch, db):
    cb = stub_authorize(_begin(client), sub="g-n", email="n@example.com")
    monkeypatch.setattr(google_oidc, "verify_id_token", _bad("bad_nonce"))
    assert _callback(client, cb) == {"error": "exchange_failed"}
    assert db.query(User).count() == 0


def test_id_token_for_another_client_is_rejected(client, monkeypatch, db):
    aud_url = _begin(client)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "someone-elses-client")
    # the id_token's aud is the client id in the URL we built, not the one now configured
    assert _callback(client, stub_authorize(aud_url, sub="g-a", email="a@example.com")) == {
        "error": "exchange_failed"
    }
    assert db.query(User).count() == 0


def _bad(reason):
    def raiser(*_a, **_k):
        raise google_oidc.GoogleIdentityError(reason)

    return raiser


def test_verify_id_token_checks_iss_and_signature(monkeypatch):
    from app.services.auth import google_stub

    now = int(datetime.now(UTC).timestamp())
    base = {
        "aud": "test-client-id.apps.example",
        "sub": "s",
        "iat": now,
        "exp": now + 60,
        "nonce": "n",
    }

    def make(**over):
        return jwt.encode(
            {**base, "iss": "https://accounts.google.com", **over},
            google_stub._private_key(),
            algorithm="RS256",
            headers={"kid": google_stub.KID},
        )

    assert google_oidc.verify_id_token(make(), nonce="n")["sub"] == "s"
    for tok, nonce in (
        (make(iss="https://evil.example"), "n"),
        (make(exp=now - 3600), "n"),
        (make(), "other-nonce"),
        (
            jwt.encode({**base, "iss": "https://accounts.google.com"}, "k" * 40, algorithm="HS256"),
            "n",
        ),
    ):
        with pytest.raises(google_oidc.GoogleIdentityError):
            google_oidc.verify_id_token(tok, nonce=nonce)


def test_pkce_wrong_verifier_is_rejected_by_google(client, monkeypatch, db):
    auth_url = _begin(client)
    monkeypatch.setattr(google_oidc, "new_pkce", lambda: ("v", "c"))  # no effect on this attempt
    real = google_oidc.exchange_code
    monkeypatch.setattr(
        google_oidc, "exchange_code", lambda code, verifier: real(code, verifier="tampered")
    )
    cb = stub_authorize(auth_url, sub="g-p", email="p@example.com")
    assert _callback(client, cb) == {"error": "exchange_failed"}
    assert db.query(User).count() == 0


def test_next_is_never_an_open_redirect(client):
    for evil in (
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
    ):
        assert google_signin.safe_next(evil) is None
    assert google_signin.safe_next("/roadmap?tab=1") == "/roadmap?tab=1"
    q = _signin(client, sub="g-o", email="o@example.com", next="//evil.example")
    assert "next" not in q and "ticket" in q


def test_callback_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(google_signin, "RATE_LIMIT", 2)
    get_redis().delete("google_signin_rate:testclient")
    codes = []
    for _ in range(3):
        r = client.get(CALLBACK + "?code=c&state=s", follow_redirects=False)
        codes.append("rate_limited" in r.headers["location"])
    assert codes == [False, False, True]


def test_not_configured_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)
    r = client.post(START, json={})
    assert r.status_code == 503 and r.json()["error"]["code"] == "GOOGLE_SIGNIN_NOT_CONFIGURED"


def test_auth_url_uses_pkce_openid_scopes_only_and_its_own_redirect(client):
    q = {k: v[0] for k, v in parse_qs(urlparse(_begin(client)).query).items()}
    assert q["scope"] == "openid email profile"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] and q["nonce"]
    assert q["redirect_uri"] == "https://api.example.test/api/v1/auth/oauth/google/callback"
    assert "calendar" not in q["scope"]


def test_exchange_in_cookie_mode_keeps_the_refresh_token_out_of_the_body(client, db):
    """D-AUTH-6: a client that sends `X-Auth-Mode: cookie` gets the session in the HttpOnly
    cookie only; the body has `refresh_token: null` and the lifetime to schedule a refresh."""
    q = _signin(client, email="cookiemode@example.com")
    r = client.post(EXCHANGE, json={"ticket": q["ticket"]}, headers={"X-Auth-Mode": "cookie"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["refresh_token"] is None and d["access_token"]
    assert d["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert settings.REFRESH_COOKIE_NAME in r.cookies


# ---- consent: a Google-created account records Terms acceptance like email sign-up ----


def test_new_google_account_records_terms_acceptance(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    q = _signin(client, email="consent@example.com", accepted_terms=True)
    assert set(q) == {"ticket"}
    user = db.query(User).filter(User.email == "consent@example.com").one()
    assert user.terms_accepted_at is not None
    assert user.terms_version == settings.TERMS_VERSION
    reg = [a for a in _audit(db, "auth.user.registered") if a.actor_user_id == user.id]
    assert reg and reg[0].details == {
        "method": "google",
        "terms_version": settings.TERMS_VERSION,
    }


def test_new_google_account_without_consent_is_refused_and_creates_nothing(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    q = _signin(client, email="noconsent@example.com")  # accepted_terms omitted
    assert q == {"error": "terms_not_accepted"}
    assert db.query(User).filter(User.email == "noconsent@example.com").first() is None
    assert db.query(OAuthAccount).filter_by(provider_account_id="g-sub-1").first() is None
    rej = _audit(db, "auth.google.rejected")
    assert rej and rej[-1].details == {"reason": "terms_not_accepted"}
    # the FE then re-starts with the box ticked, and that works
    q2 = _signin(client, email="noconsent@example.com", accepted_terms=True)
    assert set(q2) == {"ticket"}
    assert db.query(User).filter(User.email == "noconsent@example.com").one().terms_accepted_at


def test_existing_google_user_signs_in_without_the_checkbox(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", False)
    _signin(client, email="returning@example.com", sub="g-ret")  # creates it (gate off)
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    q = _signin(client, email="returning@example.com", sub="g-ret")  # now only signing in
    assert set(q) == {"ticket"}
    assert _exchange(client, q["ticket"]).json()["data"]["is_new_user"] is False


def test_terms_gate_off_creates_account_without_a_record(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", False)
    _signin(client, email="legacy@example.com")
    user = db.query(User).filter(User.email == "legacy@example.com").one()
    assert user.terms_accepted_at is None and user.terms_version is None


def test_linking_google_to_an_account_without_consent_needs_the_checkbox(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    user = create_user(db, email="old@example.com", email_verified_at=datetime.now(UTC))
    user.terms_accepted_at = None
    db.commit()
    q = _signin(client, email="old@example.com", sub="g-old")
    assert q == {"error": "terms_not_accepted"}
    db.expire_all()
    assert db.query(OAuthAccount).filter_by(provider_account_id="g-old").first() is None
    q = _signin(client, email="old@example.com", sub="g-old", accepted_terms=True)
    assert set(q) == {"ticket"}
    db.expire_all()
    u = db.query(User).filter(User.email == "old@example.com").one()
    assert u.terms_accepted_at is not None and u.terms_version == settings.TERMS_VERSION


def test_linking_an_account_that_already_consented_needs_no_checkbox(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_REQUIRE_TERMS", True)
    user = create_user(db, email="done@example.com", email_verified_at=datetime.now(UTC))
    user.terms_accepted_at = datetime.now(UTC)
    db.commit()
    assert set(_signin(client, email="done@example.com", sub="g-done")) == {"ticket"}
