import json
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.errors import EmailDeliveryFailed
from app.core.redis import get_redis
from app.core.security import create_access_token, get_password_hash
from app.db.models.audit import AuditLog
from app.db.models.auth import AuthSession, AuthToken
from app.db.models.billing import BillingPlan, BillingSubscription
from app.db.models.enums import (
    AuthTokenPurpose,
    MembershipRole,
    MfaType,
    StaffRole,
    StartupStage,
    UserStatus,
)
from app.db.models.finance_connection import FinanceConnection
from app.db.models.health_score import HealthScore
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.platform_staff import PlatformStaff
from app.db.models.startup import StartupProfile
from app.services.auth import mfa
from app.services.staff import users as users_svc
from tests.factories import create_startup, create_user

A = "/api/v1/admin"
REASON = "Support ticket 4821 follow-up"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clean_redis():
    r = get_redis()
    for pat in ("staff_stepup_*", "staff_user_email:*"):
        for k in r.scan_iter(pat):
            r.delete(k)
    yield


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(users_svc, "get_email_sender", lambda: box)
    return box


def mk_user(db, *, mfa_on=False, verified=True, status=UserStatus.active, email=None, name=None):
    secret = mfa.generate_totp_secret()
    u = create_user(
        db,
        email=email,
        status=status,
        password_hash=get_password_hash("password1"),
        email_verified_at=datetime.now(UTC) if verified else None,
        mfa_type=MfaType.totp if mfa_on else MfaType.none,
        mfa_secret=mfa.encrypt_secret(secret) if mfa_on else None,
        mfa_enabled_at=datetime.now(UTC) if mfa_on else None,
    )
    if name:
        u.profile.full_name = name
    u.test_secret = secret  # type: ignore[attr-defined]
    return u


def mk_staff(db, role=StaffRole.admin, scopes=("support",), **kw):
    u = mk_user(db, mfa_on=True, **kw)
    row = PlatformStaff(
        user_id=u.id, role=role, scopes=list(scopes) if role == StaffRole.admin else []
    )
    db.add(row)
    db.flush()
    return u


def hdr(u):
    return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def post(client, actor, path, reason=REASON, **extra):
    """A staff mutation with a fresh code (only 3 distinct codes fit the window, so free the
    single-use marks between calls)."""
    r = get_redis()
    for k in r.scan_iter("staff_stepup_used:*"):
        r.delete(k)
    body = {"reason": reason, "totp_code": pyotp.TOTP(actor.test_secret).now(), **extra}
    return client.post(f"{A}{path}", json=body, headers=hdr(actor))


def member(db, ws, user, role=MembershipRole.founder):
    from app.db.models.membership import Membership, MembershipStatus

    db.add(
        Membership(
            user_id=user.id,
            startup_id=ws.id,
            role=role,
            status=MembershipStatus.active,
            joined_at=datetime.now(UTC),
        )
    )
    db.flush()


def mk_ws(db, owner, name="Acme", stage=StartupStage.build, plan=None, status=None):
    ws = create_startup(db, owner=owner, name=name, stage=stage)
    member(db, ws, owner)
    if plan:
        db.add(BillingSubscription(startup_id=ws.id, plan_key=plan, status=status or "active"))
    db.flush()
    return ws


# ---------------------------------------------------------------- guards on every route


ROUTES = [
    ("get", "/users"),
    ("get", f"/users/{uuid.uuid4()}"),
    ("post", f"/users/{uuid.uuid4()}/suspend"),
    ("post", f"/users/{uuid.uuid4()}/unsuspend"),
    ("post", f"/users/{uuid.uuid4()}/reset-password"),
    ("post", f"/users/{uuid.uuid4()}/resend-verification"),
    ("get", "/workspaces"),
    ("get", f"/workspaces/{uuid.uuid4()}"),
    ("get", "/dashboard/kpis"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_is_hidden_from_non_staff(client, db, method, path):
    plain = mk_user(db)
    unknown = client.get("/api/v1/definitely-not-a-route").json()
    for headers in ({}, hdr(plain)):
        r = getattr(client, method)(f"{A}{path}", headers=headers)
        assert r.status_code == 404 and r.json() == unknown


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[1] != "/dashboard/kpis"])
def test_scope_support_is_required(client, db, method, path):
    content = mk_staff(db, scopes=("content",))
    r = getattr(client, method)(f"{A}{path}", headers=hdr(content))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_kpis_need_only_any_staff_and_super_admin_passes_scopes(client, db):
    content = mk_staff(db, scopes=("content",))
    assert client.get(f"{A}/dashboard/kpis", headers=hdr(content)).status_code == 200
    sa = mk_staff(db, StaffRole.super_admin)
    assert client.get(f"{A}/users", headers=hdr(sa)).status_code == 200


def test_mutations_need_the_staff_members_own_fresh_code(client, db):
    staff = mk_staff(db)
    target = mk_user(db)
    body = {"reason": REASON, "totp_code": "000000"}
    r = client.post(f"{A}/users/{target.id}/suspend", json=body, headers=hdr(staff))
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    db.refresh(target)
    assert target.status == UserStatus.active
    no_code = client.post(
        f"{A}/users/{target.id}/suspend", json={"reason": REASON}, headers=hdr(staff)
    )
    short = client.post(
        f"{A}/users/{target.id}/suspend",
        json={"reason": "short", "totp_code": pyotp.TOTP(staff.test_secret).now()},
        headers=hdr(staff),
    )
    assert no_code.status_code == 422 and short.status_code == 422
    assert db.query(AuditLog).filter_by(action="user.suspended").count() == 0


# ---------------------------------------------------------------- list / search


def test_user_search_filters_and_paging(client, db):
    staff = mk_staff(db)
    ada = mk_user(db, email="ada@example.com", name="Ada Obi")
    bayo = mk_user(db, email="bayo@example.com", name="Bayo Ade")
    ws = mk_ws(db, ada, name="NovaPay", plan="growth", status="active")
    mk_ws(db, bayo, name="Kolo")
    ada.last_login_at = datetime.now(UTC)
    bayo.last_login_at = datetime.now(UTC) - timedelta(days=45)
    db.flush()
    h = hdr(staff)

    def ids(qs):
        r = client.get(f"{A}/users?{qs}", headers=h)
        assert r.status_code == 200, r.text
        return {u["email"] for u in r.json()["data"]["users"]}

    assert "ada@example.com" in ids("query=ada")
    assert "bayo@example.com" not in ids("query=ada")
    assert ids("query=Obi") >= {"ada@example.com"}
    assert ids("query=novapay") == {"ada@example.com"}  # by workspace name
    assert ids(f"query={ws.id}") == {"ada@example.com"}  # by workspace id
    assert ids(f"query={bayo.id}") == {"bayo@example.com"}  # by user id
    assert ids("query=%25") == set()  # % is not a wildcard
    assert ids("query=a_a") == set()  # nor is _
    assert "ada@example.com" in ids("billing_status=active")
    assert "bayo@example.com" not in ids("billing_status=active")
    assert "bayo@example.com" in ids("billing_status=free")
    assert "ada@example.com" in ids("activity=active")
    assert "bayo@example.com" in ids("activity=dormant")
    assert client.get(f"{A}/users?billing_status=gold", headers=h).status_code == 422
    row = next(u for u in client.get(f"{A}/users?query=ada@", headers=h).json()["data"]["users"])
    assert set(row) == {
        "id", "email", "full_name", "status", "email_verified", "created_at", "last_login_at",
        "activity", "workspace_count", "primary_workspace",
    }  # fmt: skip
    assert row["primary_workspace"] == {
        "workspace_id": str(ws.id),
        "workspace_name": "NovaPay",
        "role": "founder",
        "plan_key": "growth",
        "billing_status": "active",
    }
    seen, cursor = [], None
    for _ in range(10):
        url = f"{A}/users?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=h).json()
        seen += [u["id"] for u in body["data"]["users"]]
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 3  # staff, ada, bayo
    assert client.get(f"{A}/users?cursor=zzz", headers=h).status_code == 422
    deleted = mk_user(db, email="gone@example.com")
    deleted.deleted_at = datetime.now(UTC)
    db.flush()
    assert "gone@example.com" not in ids("limit=100")


def test_primary_workspace_prefers_the_founder_workspace(client, db):
    staff = mk_staff(db)
    u = mk_user(db, email="multi@example.com")
    other = mk_user(db)
    joined = mk_ws(db, other, name="Joined")
    member(db, joined, u, MembershipRole.mentor)
    mine = mk_ws(db, u, name="Mine")
    row = client.get(f"{A}/users?query=multi@", headers=hdr(staff)).json()["data"]["users"][0]
    assert row["workspace_count"] == 2
    assert row["primary_workspace"]["workspace_name"] == "Mine"
    assert row["primary_workspace"]["workspace_id"] == str(mine.id)


# ---------------------------------------------------------------- profile


def test_profile_shape_and_no_secrets(client, db):
    staff = mk_staff(db)
    u = mk_user(db, mfa_on=True, email="p@example.com", name="Pat Ade")
    ws = mk_ws(db, u, name="NovaPay", plan="growth", status="trialing")
    db.add(
        AuthSession(
            user_id=u.id,
            refresh_token_hash="REFRESH-HASH-SENTINEL",
            family_id=uuid.uuid4(),
            ip="10.0.0.9",
            user_agent="UA/1",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.flush()
    r = client.get(f"{A}/users/{u.id}", headers=hdr(staff))
    assert r.status_code == 200
    d = r.json()["data"]
    assert set(d) == {
        "id", "email", "full_name", "role_title", "country", "phone", "status", "email_verified",
        "two_factor", "created_at", "last_login_at", "activity", "is_staff", "workspaces",
        "sessions", "can",
    }  # fmt: skip
    assert d["two_factor"] is True and d["is_staff"] is False
    assert d["workspaces"][0]["workspace_id"] == str(ws.id)
    assert set(d["workspaces"][0]) == {
        "workspace_id", "name", "role", "membership_status", "plan_key", "billing_status",
        "trial_ends_at", "current_period_end", "cancel_at_period_end",
    }  # fmt: skip
    assert d["sessions"][0]["ip"] == "10.0.0.9"
    assert set(d["sessions"][0]) == {
        "id",
        "ip",
        "user_agent",
        "signed_in_at",
        "expires_at",
        "revoked_at",
    }
    assert d["can"] == {
        "suspend": True,
        "unsuspend": False,
        "reset_password": True,
        "resend_verification": False,
    }
    blob = r.text
    for secret in (u.mfa_secret, u.password_hash, "REFRESH-HASH-SENTINEL"):
        assert secret and secret not in blob
    for word in ("mfa_secret", "password_hash", "refresh_token_hash", "token_hash"):
        assert word not in blob
    assert client.get(f"{A}/users/{uuid.uuid4()}", headers=hdr(staff)).status_code == 404


def test_profile_can_flags_for_staff_targets_and_self(client, db):
    admin = mk_staff(db)
    sa = mk_staff(db, StaffRole.super_admin)
    colleague = mk_staff(db)
    as_admin = client.get(f"{A}/users/{colleague.id}", headers=hdr(admin)).json()["data"]
    assert as_admin["is_staff"] and not any(as_admin["can"].values())
    as_sa = client.get(f"{A}/users/{colleague.id}", headers=hdr(sa)).json()["data"]
    assert as_sa["can"]["suspend"] is True
    own = client.get(f"{A}/users/{sa.id}", headers=hdr(sa)).json()["data"]
    assert not any(own["can"].values())


# ---------------------------------------------------------------- suspend / unsuspend


def test_suspend_signs_the_user_out_everywhere_at_once(client, db):
    staff = mk_staff(db)
    u = mk_user(db, email="victim@example.com")
    h = hdr(u)
    for _ in range(2):
        db.add(
            AuthSession(
                user_id=u.id,
                refresh_token_hash=uuid.uuid4().hex,
                family_id=uuid.uuid4(),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    db.add(
        AuthToken(
            user_id=u.id,
            purpose=AuthTokenPurpose.password_reset,
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.flush()
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    r = post(client, staff, f"/users/{u.id}/suspend")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"id": str(u.id), "status": "disabled", "sessions_revoked": 2}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401  # same token, next call
    assert db.query(AuthSession).filter_by(user_id=u.id, revoked_at=None).count() == 0
    assert (
        db.query(AuthToken)
        .filter(AuthToken.user_id == u.id, AuthToken.consumed_at.is_(None))
        .count()
        == 0
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": "victim@example.com", "password": "password1"}
    )
    assert login.status_code == 401
    log = db.query(AuditLog).filter_by(action="user.suspended").one()
    assert log.actor_user_id == staff.id and log.entity_type == "user" and log.entity_id == u.id
    assert log.reason == REASON and log.details == {"email": "victim@example.com"}
    again = post(client, staff, f"/users/{u.id}/suspend")
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_SUSPENDED"


def test_a_pre_suspension_verification_link_cannot_lift_the_suspension(client, db):
    from app.services.auth.tokens import issue_auth_token

    staff = mk_staff(db)
    u = mk_user(db, verified=False, status=UserStatus.pending_verification)
    raw = issue_auth_token(db, u, AuthTokenPurpose.email_verification, timedelta(hours=1))
    u.status = UserStatus.disabled  # suspended by some path that left the token live
    db.flush()
    assert client.post("/api/v1/auth/verify", json={"token": raw}).status_code == 200
    db.refresh(u)
    assert u.status == UserStatus.disabled and u.email_verified_at is not None
    assert staff  # the guard fix, not the token cleanup, holds the line


def test_unsuspend_restores_the_right_state(client, db):
    staff = mk_staff(db)
    verified = mk_user(db)
    unverified = mk_user(db, verified=False, status=UserStatus.pending_verification)
    for u in (verified, unverified):
        assert post(client, staff, f"/users/{u.id}/suspend").status_code == 200
    r = post(client, staff, f"/users/{verified.id}/unsuspend")
    assert r.json()["data"] == {"id": str(verified.id), "status": "active"}
    r = post(client, staff, f"/users/{unverified.id}/unsuspend")
    assert r.json()["data"]["status"] == "pending_verification"
    again = post(client, staff, f"/users/{verified.id}/unsuspend")
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOT_SUSPENDED"
    assert db.query(AuditLog).filter_by(action="user.unsuspended").count() == 2


def test_staff_target_rules(client, db):
    admin = mk_staff(db)
    sa = mk_staff(db, StaffRole.super_admin)
    colleague = mk_staff(db)
    r = post(client, admin, f"/users/{colleague.id}/suspend")
    assert r.status_code == 403 and r.json()["error"]["code"] == "STAFF_TARGET"
    r = post(client, admin, f"/users/{admin.id}/suspend")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    r = post(client, sa, f"/users/{sa.id}/suspend")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    assert post(client, sa, f"/users/{colleague.id}/suspend").status_code == 200
    # a suspended colleague loses staff access at once (guard needs an active user)
    assert client.get(f"{A}/me", headers=hdr(colleague)).status_code == 404
    missing = post(client, sa, f"/users/{uuid.uuid4()}/suspend")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "USER_NOT_FOUND"


# ---------------------------------------------------------------- reset password / resend verification


def test_reset_password_sends_one_email_and_cools_down(client, db, outbox):
    staff = mk_staff(db)
    u = mk_user(db, email="reset@example.com")
    r = post(client, staff, f"/users/{u.id}/reset-password")
    assert r.status_code == 200 and r.json()["data"] == {"id": str(u.id), "sent": True}
    assert len(outbox.sent) == 1
    assert (
        db.query(AuthToken)
        .filter(
            AuthToken.user_id == u.id,
            AuthToken.purpose == AuthTokenPurpose.password_reset,
            AuthToken.consumed_at.is_(None),
        )
        .count()
        == 1
    )
    assert db.query(AuditLog).filter_by(action="user.password_reset_sent").one().reason == REASON
    soon = post(client, staff, f"/users/{u.id}/reset-password")
    assert soon.status_code == 429 and soon.json()["error"]["code"] == "TOO_SOON"
    assert len(outbox.sent) == 1


def test_reset_password_state_checks_and_send_failure(client, db, outbox, monkeypatch):
    staff = mk_staff(db)
    suspended = mk_user(db, status=UserStatus.disabled)
    pending = mk_user(db, verified=False, status=UserStatus.pending_verification)
    for u in (suspended, pending):
        r = post(client, staff, f"/users/{u.id}/reset-password")
        assert r.status_code == 409 and r.json()["error"]["code"] == "RESET_NOT_ALLOWED"
    ok = mk_user(db)

    class Failing:
        def send(self, msg):
            raise EmailDeliveryFailed()

    monkeypatch.setattr(users_svc, "get_email_sender", lambda: Failing())
    r = post(client, staff, f"/users/{ok.id}/reset-password")
    assert r.status_code == 503 and r.json()["error"]["code"] == "EMAIL_DELIVERY_FAILED"
    assert db.query(AuthToken).filter_by(user_id=ok.id).count() == 0  # token rolled back
    assert db.query(AuditLog).filter_by(action="user.password_reset_sent").count() == 0
    monkeypatch.setattr(users_svc, "get_email_sender", lambda: outbox)
    assert (
        post(client, staff, f"/users/{ok.id}/reset-password").status_code == 200
    )  # no cooldown left


def test_resend_verification(client, db, outbox, monkeypatch):
    staff = mk_staff(db)
    pending = mk_user(db, verified=False, status=UserStatus.pending_verification)
    r = post(client, staff, f"/users/{pending.id}/resend-verification")
    assert r.status_code == 200 and len(outbox.sent) == 1
    assert db.query(AuditLog).filter_by(action="user.verification_resent").count() == 1
    soon = post(client, staff, f"/users/{pending.id}/resend-verification")
    assert soon.status_code == 429
    done = mk_user(db)
    r = post(client, staff, f"/users/{done.id}/resend-verification")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_VERIFIED"

    class Failing:
        def send(self, msg):
            raise EmailDeliveryFailed()

    other = mk_user(db, verified=False, status=UserStatus.pending_verification)
    monkeypatch.setattr(users_svc, "get_email_sender", lambda: Failing())
    r = post(client, staff, f"/users/{other.id}/resend-verification")
    assert r.status_code == 503
    assert db.query(AuthToken).filter_by(user_id=other.id).count() == 0


# ---------------------------------------------------------------- workspaces


def test_workspace_list_and_detail(client, db):
    staff = mk_staff(db)
    owner = mk_user(db, email="own@example.com", name="Olu Own")
    owner.last_login_at = datetime.now(UTC)
    ws = mk_ws(db, owner, name="NovaPay", stage=StartupStage.launch, plan="growth", status="active")
    tm = mk_user(db, email="tm@example.com")
    member(db, ws, tm, MembershipRole.team_member)
    mentor = mk_user(db, email="mentor@example.com")
    member(db, ws, mentor, MembershipRole.mentor)
    db.add(
        HealthScore(startup_id=ws.id, score=72, band="ok", dimension_scores={}, config_version=1)
    )
    other = mk_ws(db, mk_user(db), name="Kolo")
    db.flush()
    h = hdr(staff)
    lst = client.get(f"{A}/workspaces?query=nova", headers=h)
    assert lst.status_code == 200
    rows = lst.json()["data"]["workspaces"]
    assert [r["name"] for r in rows] == ["NovaPay"]
    row = rows[0]
    assert set(row) == {
        "id", "name", "owner_email", "owner_name", "stage", "country", "created_at", "seats",
        "members", "plan_key", "billing_status", "health_score", "health_band", "last_active_at",
    }  # fmt: skip
    assert row["seats"] == 2 and row["members"] == 3  # mentors are free
    assert row["health_score"] == 72 and row["plan_key"] == "growth" and row["last_active_at"]
    assert row["owner_email"] == "own@example.com" and row["owner_name"] == "Olu Own"
    for qs, want in (
        (f"query={ws.id}", {"NovaPay"}),
        ("query=own@example", {"NovaPay"}),
        ("stage=launch", {"NovaPay"}),
        ("billing_status=free", {"Kolo"}),
        ("billing_status=active", {"NovaPay"}),
    ):
        got = client.get(f"{A}/workspaces?{qs}", headers=h).json()["data"]["workspaces"]
        assert {r["name"] for r in got} == want, qs
    assert client.get(f"{A}/workspaces?stage=huge", headers=h).status_code == 422
    page = client.get(f"{A}/workspaces?limit=1", headers=h).json()
    assert page["meta"]["next_cursor"]
    nxt = client.get(
        f"{A}/workspaces?limit=1&cursor={page['meta']['next_cursor']}", headers=h
    ).json()
    assert nxt["data"]["workspaces"][0]["id"] != page["data"]["workspaces"][0]["id"]
    assert other

    db.add(LlmUsageDaily(startup_id=ws.id, usage_date=datetime.now(UTC).date(), tokens_used=5000))
    db.add(
        FinanceConnection(
            startup_id=ws.id,
            public_id=uuid.uuid4().hex,
            provider="paystack",
            status="active",
            key_ciphertext="c",
            secret_hash_ciphertext="c",
            key_last4="1234",
            mode="test",
        )
    )
    db.flush()
    d = client.get(f"{A}/workspaces/{ws.id}", headers=h)
    assert d.status_code == 200, d.text
    data = d.json()["data"]
    assert set(data) == {
        "id", "name", "industry", "stage", "country", "created_at", "owner_email",
        "onboarding_completed_at", "health_score", "health_band", "members", "subscription",
        "usage", "integrations",
    }  # fmt: skip
    # (rows made in one test transaction share a created_at, so order is not asserted)
    assert {m["email"] for m in data["members"]} == {
        "own@example.com",
        "tm@example.com",
        "mentor@example.com",
    }
    assert set(data["members"][0]) == {
        "user_id",
        "email",
        "full_name",
        "role",
        "status",
        "joined_at",
    }
    assert (
        data["subscription"]["plan_key"] == "growth" and data["subscription"]["status"] == "active"
    )
    assert data["usage"]["ai_credits_30d"] == 100 and data["usage"]["seats_used"] == 2
    assert data["integrations"] == [{"provider": "paystack", "status": "active"}]
    gone = client.get(f"{A}/workspaces/{uuid.uuid4()}", headers=h)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"


def test_workspace_responses_never_carry_content_or_secrets(client, db):
    staff = mk_staff(db)
    owner = mk_user(db)
    ws = mk_ws(db, owner, name="Secretive")
    db.add(
        FinanceConnection(
            startup_id=ws.id,
            public_id=uuid.uuid4().hex,
            provider="paystack",
            status="active",
            key_ciphertext="CIPHER-SENTINEL",
            secret_hash_ciphertext="HASH-SENTINEL",
            key_last4="9999",
            mode="test",
        )
    )
    db.flush()
    for url in (
        f"{A}/workspaces/{ws.id}",
        f"{A}/workspaces",
        f"{A}/users/{owner.id}",
        f"{A}/users",
    ):
        text = client.get(url, headers=hdr(staff)).text
        for bad in (
            "CIPHER-SENTINEL",
            "password_hash",
            "mfa_secret",
            "token_hash",
            "ciphertext",
            "journal",
            "body",
        ):
            assert bad not in text, (url, bad)


def test_deleted_workspace_is_hidden(client, db):
    staff = mk_staff(db)
    ws = mk_ws(db, mk_user(db), name="Ghost")
    ws.deleted_at = datetime.now(UTC)
    db.flush()
    assert client.get(f"{A}/workspaces/{ws.id}", headers=hdr(staff)).status_code == 404
    assert (
        client.get(f"{A}/workspaces?query=ghost", headers=hdr(staff)).json()["data"]["workspaces"]
        == []
    )


# ---------------------------------------------------------------- KPIs


def test_dashboard_kpis(client, db):
    staff = mk_staff(db)
    now = datetime.now(UTC)
    plans = {p.key: p for p in db.query(BillingPlan).all()}
    if "growth" not in plans:
        db.add(
            BillingPlan(
                key="growth",
                name="Growth",
                tagline="t",
                rank=2,
                price_monthly_minor=2_400_000,
                price_annual_minor=24_000_000,
                ai_credits=5000,
                seats=5,
                storage_mb=1000,
                features=[],
                modules=[],
            )
        )
        db.add(
            BillingPlan(
                key="scale",
                name="Scale",
                tagline="t",
                rank=3,
                price_monthly_minor=6_800_000,
                price_annual_minor=68_000_000,
                ai_credits=20000,
                seats=None,
                storage_mb=5000,
                features=[],
                modules=[],
            )
        )
        db.flush()
    growth, scale = (db.query(BillingPlan).filter_by(key=k).one() for k in ("growth", "scale"))
    owners = [mk_user(db) for _ in range(4)]
    w1 = mk_ws(db, owners[0], name="One", plan="growth", status="active")
    w2 = mk_ws(db, owners[1], name="Two", plan="scale", status="active")
    w2sub = db.query(BillingSubscription).filter_by(startup_id=w2.id).one()
    w2sub.interval = "annual"
    mk_ws(db, owners[2], name="Three", plan="growth", status="trialing")
    w4 = mk_ws(db, owners[3], name="Four")
    for w, done_after in ((w1, timedelta(days=2)), (w2, timedelta(days=15))):
        w.created_at = now - timedelta(days=10 if w is w1 else 25)
        p = (
            db.query(StartupProfile).filter_by(startup_id=w.id).one()
            if db.query(StartupProfile).filter_by(startup_id=w.id).first()
            else None
        )
        assert p is not None
        p.onboarding_completed_at = w.created_at + done_after
    w4.created_at = now - timedelta(days=45)  # previous window, never activated
    db.flush()
    d = client.get(f"{A}/dashboard/kpis", headers=hdr(staff))
    assert d.status_code == 200, d.text
    k = d.json()["data"]
    assert set(k) == {
        "window_days", "generated_at", "total_workspaces", "activation_rate", "paid_conversion",
        "mrr", "signups_series",
    }  # fmt: skip
    assert k["total_workspaces"]["value"] == 4
    assert k["total_workspaces"]["new_in_previous_window"] == 1
    assert k["activation_rate"]["activated"] == 1  # w2 took 15 days: outside the 14-day rule
    assert k["activation_rate"]["percent"] == pytest.approx(
        100 * 1 / k["activation_rate"]["cohort"], abs=0.1
    )
    assert k["activation_rate"]["previous_percent"] == 0.0
    assert k["paid_conversion"]["paying_workspaces"] == 2
    assert k["paid_conversion"]["percent"] == 50.0
    assert k["paid_conversion"]["previous_percent"] is None
    assert k["mrr"] == {
        "minor": growth.price_monthly_minor + scale.price_annual_minor // 12,
        "currency": "NGN",
        "previous_minor": None,
    }
    assert len(k["signups_series"]) == 30
    assert sum(x["signups"] for x in k["signups_series"]) >= 1
    assert set(k["signups_series"][0]) == {"date", "signups", "activations"}
    assert sum(x["activations"] for x in k["signups_series"]) == 2


def test_staff_list_now_carries_last_login(client, db):
    sa = mk_staff(db, StaffRole.super_admin)
    sa.last_login_at = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    db.flush()
    rows = client.get("/api/v1/super-admin/staff", headers=hdr(sa)).json()["data"]
    assert rows[0]["last_login_at"].startswith("2026-10-30T09:00")
    assert json.dumps(rows)


# ---------------------------------------------------------------- suspension holds on every sign-in path


def test_mfa_ticket_issued_before_suspension_gives_no_session(client, db):
    staff = mk_staff(db)
    u = mk_user(db, mfa_on=True, email="ticket@example.com")
    ticket = mfa.issue_mfa_ticket(u.id)
    assert post(client, staff, f"/users/{u.id}/suspend").status_code == 200
    r = client.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": ticket, "code": pyotp.TOTP(u.test_secret).now()},
    )
    assert r.status_code == 401 and r.json()["error"]["code"] == "MFA_INVALID_CODE"
    assert db.query(AuthSession).filter_by(user_id=u.id, revoked_at=None).count() == 0


def test_refresh_after_suspension_is_refused_and_mints_nothing(client, db):
    staff = mk_staff(db)
    u = mk_user(db, email="refresh@example.com")
    login = client.post(
        "/api/v1/auth/login", json={"email": "refresh@example.com", "password": "password1"}
    )
    refresh = login.json()["data"]["refresh_token"]
    # a refresh in flight: the session row exists and is live when the suspension lands
    u.status = UserStatus.disabled
    db.flush()
    before = db.query(AuthSession).filter_by(user_id=u.id).count()
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    assert db.query(AuthSession).filter_by(user_id=u.id).count() == before
    assert staff


def test_password_reset_token_cannot_be_used_by_a_suspended_user(client, db):
    from app.services.auth.tokens import issue_auth_token

    u = mk_user(db, email="late-reset@example.com")
    raw = issue_auth_token(db, u, AuthTokenPurpose.password_reset, timedelta(hours=1))
    u.status = UserStatus.disabled  # suspended after the token was issued
    db.flush()
    hash_before = u.password_hash
    r = client.post(
        "/api/v1/auth/password/reset", json={"token": raw, "password": "Brand-new-pw-1"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_INVALID"
    db.refresh(u)
    assert u.password_hash == hash_before


def test_admin_cannot_see_a_staff_members_sessions_or_phone(client, db):
    admin = mk_staff(db)
    sa = mk_staff(db, StaffRole.super_admin)
    sa.profile.phone = "+2348000000000"
    db.add(
        AuthSession(
            user_id=sa.id,
            refresh_token_hash=uuid.uuid4().hex,
            family_id=uuid.uuid4(),
            ip="10.9.9.9",
            user_agent="UA/x",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.flush()
    as_admin = client.get(f"{A}/users/{sa.id}", headers=hdr(admin)).json()["data"]
    assert as_admin["sessions"] == [] and as_admin["phone"] is None
    other_sa = mk_staff(db, StaffRole.super_admin)
    as_sa = client.get(f"{A}/users/{sa.id}", headers=hdr(other_sa)).json()["data"]
    assert as_sa["sessions"][0]["ip"] == "10.9.9.9" and as_sa["phone"] == "+2348000000000"


# ---------------------------------------------------------------- P9: edit a staff member without revoking


def patch_staff(client, actor, staff_id, role, scopes, reason="Moving to the content team"):
    r = get_redis()
    for k in r.scan_iter("staff_stepup_used:*"):
        r.delete(k)
    body = {
        "role": role,
        "scopes": scopes,
        "reason": reason,
        "totp_code": pyotp.TOTP(actor.test_secret).now(),
    }
    return client.patch(f"/api/v1/super-admin/staff/{staff_id}", json=body, headers=hdr(actor))


def test_update_staff_changes_scopes_and_audits(client, db):
    sa = mk_staff(db, StaffRole.super_admin)
    ad = mk_staff(db, scopes=("support",))
    row = db.query(PlatformStaff).filter_by(user_id=ad.id).one()
    r = patch_staff(client, sa, row.id, "admin", ["moderation", "content", "content"])
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (
        d["id"] == str(row.id) and d["role"] == "admin" and d["scopes"] == ["content", "moderation"]
    )
    # the change is live on the next request
    assert client.get(f"{A}/users", headers=hdr(ad)).status_code == 403  # lost `support`
    log = db.query(AuditLog).filter_by(action="staff.updated").one()
    assert log.actor_user_id == sa.id and log.reason == "Moving to the content team"
    assert log.details == {
        "email": ad.email,
        "from": {"role": "admin", "scopes": ["support"]},
        "to": {"role": "admin", "scopes": ["content", "moderation"]},
    }
    promoted = patch_staff(client, sa, row.id, "super_admin", [], "Promoting after training")
    assert promoted.status_code == 200 and promoted.json()["data"]["role"] == "super_admin"
    assert (
        client.get(f"{A}/users", headers=hdr(ad)).status_code == 200
    )  # a Super Admin passes all scopes
    demoted = patch_staff(client, sa, row.id, "admin", ["support"], "Back to support only")
    assert demoted.status_code == 200 and demoted.json()["data"]["scopes"] == ["support"]


def test_update_staff_guards_and_errors(client, db):
    sa = mk_staff(db, StaffRole.super_admin)
    ad = mk_staff(db, scopes=("support",))
    row = db.query(PlatformStaff).filter_by(user_id=ad.id).one()
    own = db.query(PlatformStaff).filter_by(user_id=sa.id).one()
    r = patch_staff(client, sa, own.id, "admin", ["support"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_EDIT_SELF"
    r = patch_staff(client, sa, row.id, "admin", ["support"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHANGE"
    r = patch_staff(client, sa, uuid.uuid4(), "admin", ["support"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "STAFF_NOT_FOUND"
    row.revoked_at = datetime.now(UTC)
    db.flush()
    r = patch_staff(client, sa, row.id, "admin", ["content"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_REVOKED"
    row.revoked_at = None
    db.flush()
    for role, scopes in (("admin", []), ("super_admin", ["support"]), ("admin", ["root"])):
        assert patch_staff(client, sa, row.id, role, scopes).status_code == 422
    assert patch_staff(client, sa, row.id, "admin", ["content"], "short").status_code == 422
    bad = client.patch(
        f"/api/v1/super-admin/staff/{row.id}",
        json={"role": "admin", "scopes": ["content"], "reason": REASON, "totp_code": "000000"},
        headers=hdr(sa),
    )
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "STEP_UP_INVALID"
    db.refresh(row)
    assert row.scopes == ["support"]
    # an Admin cannot reach it at all
    hidden = patch_staff(client, ad, row.id, "super_admin", [])
    assert hidden.status_code == 404 and hidden.json() == {"detail": "Not Found"}
