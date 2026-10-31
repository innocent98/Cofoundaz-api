"""Admin slice E: maintenance mode and global sign-out, each with a second Super Admin approving."""

from datetime import UTC, datetime, timedelta

import jwt
import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import create_access_token, get_password_hash
from app.db.models.audit import AuditLog
from app.db.models.auth import AuthSession
from app.db.models.enums import ApprovalStatus, MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.staff_approval import StaffApprovalRequest
from app.middleware import maintenance as maintenance_mw
from app.platform import state
from tests.api.test_staff_access import hdr, mk_staff, mk_user
from tests.billing_helpers import post_event, workspace
from tests.factories import create_user

S = "/api/v1/super-admin"
REASON = "Planned database upgrade window"
OTHER = "Reviewed and agreed on the call"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _paystack(monkeypatch):
    from tests.billing_helpers import KEY

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", KEY)
    monkeypatch.setattr(settings, "PAYSTACK_TEST_SECRET_KEY", None)


def code(u):
    """A step-up code for `u`. A code works once; clear the replay marker so a test can step up
    as often as it needs (the replay rule itself is tested separately)."""
    r = get_redis()
    for k in r.scan_iter(f"staff_stepup_used:{u.id}:*"):
        r.delete(k)
    r.delete(f"staff_stepup_fail:{u.id}")
    return pyotp.TOTP(u.test_secret).now()


def body(u, **kw):
    return {"reason": REASON, "totp_code": code(u), **kw}


def ask(client, u, action="maintenance_enable", **kw):
    return client.post(f"{S}/approvals", json=body(u, action=action, **kw), headers=hdr(u))


def answer(client, u, req_id, verb="approve", **kw):
    return client.post(
        f"{S}/approvals/{req_id}/{verb}",
        json={"reason": OTHER, "totp_code": code(u), **kw},
        headers=hdr(u),
    )


@pytest.fixture()
def two(db):
    a, _ = mk_staff(db)
    b, _ = mk_staff(db)
    return a, b


def audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ---------------- access ----------------


def test_only_active_super_admins_can_see_or_use_the_routes(client, db):
    plain = mk_user(db)
    admin, _ = mk_staff(db, role=StaffRole.admin, scopes=["support"])
    sa, _ = mk_staff(db)
    expected = client.get("/api/v1/definitely-not-a-route").json()
    routes = [
        ("get", f"{S}/danger-zone"),
        ("get", f"{S}/approvals"),
        ("post", f"{S}/approvals"),
        ("post", f"{S}/approvals/{sa.id}/approve"),
        ("post", f"{S}/approvals/{sa.id}/reject"),
        ("post", f"{S}/approvals/{sa.id}/cancel"),
        ("post", f"{S}/maintenance/disable"),
    ]
    for who in ({}, hdr(plain), hdr(admin)):
        for method, url in routes:
            r = getattr(client, method)(
                url, headers=who, **({"json": {}} if method == "post" else {})
            )
            assert r.status_code == 404 and r.json() == expected, (method, url, who)
    assert client.get(f"{S}/danger-zone", headers=hdr(sa)).status_code == 200


def test_every_mutation_needs_a_fresh_authenticator_code(client, db, two):
    a, b = two
    bad = {"action": "maintenance_enable", "reason": REASON, "totp_code": "000000"}
    r = client.post(f"{S}/approvals", json=bad, headers=hdr(a))
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    r = client.post(
        f"{S}/approvals", json={"action": "maintenance_enable", "reason": REASON}, headers=hdr(a)
    )
    assert r.status_code == 422
    # A code is single use.
    used = code(a)
    ok = {"action": "maintenance_enable", "reason": REASON, "totp_code": used}
    assert client.post(f"{S}/approvals", json=ok, headers=hdr(a)).status_code == 201
    again = client.post(f"{S}/approvals", json=ok, headers=hdr(a))
    assert again.status_code == 403 and again.json()["error"]["code"] == "STEP_UP_INVALID"
    rid = db.query(StaffApprovalRequest).one().id
    for verb in ("approve", "reject", "cancel"):
        r = client.post(
            f"{S}/approvals/{rid}/{verb}",
            json={"reason": OTHER, "totp_code": "000000"},
            headers=hdr(b),
        )
        assert r.status_code == 403, verb
    r = client.post(
        f"{S}/maintenance/disable", json={"reason": REASON, "totp_code": "000000"}, headers=hdr(a)
    )
    assert r.status_code == 403
    r = client.post(
        f"{S}/approvals", json=body(a, action="maintenance_enable", reason="short"), headers=hdr(a)
    )
    assert r.status_code == 422


# ---------------- the approval flow ----------------


def test_request_does_nothing_until_a_different_super_admin_approves(client, db, two):
    a, b = two
    r = ask(client, a, message="Upgrading the database. Back by 10:00.")
    assert r.status_code == 201, r.text
    req = r.json()["data"]
    assert req["status"] == "pending" and req["action"] == "maintenance_enable"
    assert req["requested_by"]["email"] == a.email and req["decided_by"] is None
    assert req["designated_approver"] is None
    assert state.maintenance_state(db)["enabled"] is False
    assert len(audits(db, "approval.requested")) == 1

    # The requester can never answer their own request.
    for verb in ("approve", "reject"):
        r = answer(client, a, req["id"], verb)
        assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_DECIDE_OWN_REQUEST"
    assert state.maintenance_state(db)["enabled"] is False

    r = answer(client, b, req["id"])
    assert r.status_code == 200, r.text
    done = r.json()["data"]
    assert done["status"] == "approved" and done["decided_by"]["email"] == b.email
    assert done["outcome"]["maintenance"]["enabled"] is True
    assert state.maintenance_state(db) == {
        "enabled": True,
        "message": "Upgrading the database. Back by 10:00.",
        "since": state.maintenance_state(db)["since"],
    }
    assert [x.reason for x in audits(db, "approval.approved")] == [OTHER]
    enabled = audits(db, "maintenance.enabled")
    assert len(enabled) == 1 and enabled[0].actor_user_id == b.id
    assert enabled[0].reason == REASON  # the requester's reason travels with the action

    # A decided request can't be decided again.
    r = answer(client, b, req["id"], "reject")
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_PENDING"


def test_reject_leaves_everything_as_it_was(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    r = answer(client, b, rid, "reject")
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    assert r.json()["data"]["decision_reason"] == OTHER
    assert state.maintenance_state(db)["enabled"] is False
    assert len(audits(db, "approval.rejected")) == 1
    assert audits(db, "maintenance.enabled") == []


def test_cancel_by_the_requester_only_and_it_frees_the_slot(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    dup = ask(client, a)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "APPROVAL_ALREADY_PENDING"
    r = answer(client, b, rid, "cancel")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_YOUR_REQUEST"
    r = answer(client, a, rid, "cancel")
    assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
    assert len(audits(db, "approval.cancelled")) == 1
    assert ask(client, a).status_code == 201


def test_a_request_expires_and_can_no_longer_be_approved(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    row = db.get(StaffApprovalRequest, rid)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    r = answer(client, b, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_EXPIRED"
    db.refresh(row)
    assert row.status == ApprovalStatus.expired
    assert state.maintenance_state(db)["enabled"] is False
    assert len(audits(db, "approval.expired")) == 1
    # The list also settles overdue requests, with one audit row each.
    rid2 = ask(client, a).json()["data"]["id"]
    db.get(StaffApprovalRequest, rid2).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    rows = client.get(f"{S}/approvals", headers=hdr(b)).json()["data"]["requests"]
    assert {x["id"]: x["status"] for x in rows} == {rid: "expired", rid2: "expired"}
    assert len(audits(db, "approval.expired")) == 2
    # An expired request frees the requester's slot.
    assert ask(client, a).status_code == 201


def test_list_shows_pending_and_past_with_filter_and_paging(client, db, two):
    a, b = two
    first = ask(client, a).json()["data"]["id"]
    answer(client, b, first, "reject")
    # A test's whole run is one transaction, so `created_at` ties; make the order explicit.
    db.get(StaffApprovalRequest, first).created_at = datetime.now(UTC) - timedelta(hours=1)
    db.flush()
    second = ask(client, a, action="global_signout").json()["data"]["id"]
    allrows = client.get(f"{S}/approvals", headers=hdr(b)).json()
    assert [x["id"] for x in allrows["data"]["requests"]] == [second, first]
    pend = client.get(f"{S}/approvals?status=pending", headers=hdr(b)).json()["data"]["requests"]
    assert [x["id"] for x in pend] == [second]
    page = client.get(f"{S}/approvals?limit=1", headers=hdr(b)).json()
    assert [x["id"] for x in page["data"]["requests"]] == [second]
    nxt = client.get(f"{S}/approvals?limit=1&cursor={page['meta']['next_cursor']}", headers=hdr(b))
    assert [x["id"] for x in nxt.json()["data"]["requests"]] == [first]
    assert nxt.json()["meta"]["next_cursor"] is None
    bad = client.get(f"{S}/approvals?cursor=garbage", headers=hdr(b))
    assert bad.status_code == 422
    assert client.get(f"{S}/approvals?status=nope", headers=hdr(b)).status_code == 422


def test_designated_approver_is_the_only_one_who_can_answer(client, db, two):
    a, b = two
    c, _ = mk_staff(db)
    rid = ask(client, a, approver_user_id=str(b.id)).json()["data"]["id"]
    r = answer(client, c, rid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_DESIGNATED_APPROVER"
    assert answer(client, b, rid).status_code == 200


def test_the_approver_must_be_another_active_super_admin(client, db, two):
    a, b = two
    admin, _ = mk_staff(db, role=StaffRole.admin, scopes=["support"])
    nobody = create_user(db)
    gone, row = mk_staff(db)
    row.revoked_at = datetime.now(UTC)
    no_mfa = mk_user(db, mfa_on=False)
    db.add(
        __import__("app.db.models.platform_staff", fromlist=["x"]).PlatformStaff(
            user_id=no_mfa.id, role=StaffRole.super_admin, scopes=[]
        )
    )
    db.flush()
    for who in (a, admin, nobody, gone, no_mfa):
        r = ask(client, a, approver_user_id=str(who.id))
        assert r.status_code == 422 and r.json()["error"]["code"] == "APPROVER_INVALID", who.email


def test_no_second_approver_means_no_request(client, db):
    only, _ = mk_staff(db)
    r = ask(client, only)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_SECOND_APPROVER"
    r = ask(client, only, action="global_signout")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_SECOND_APPROVER"


def test_message_belongs_to_maintenance_requests_only(client, db, two):
    a, _ = two
    r = ask(client, a, action="global_signout", message="hello")
    assert r.status_code == 422 and r.json()["error"]["code"] == "MESSAGE_NOT_ALLOWED"
    assert ask(client, a, message="x" * 281).status_code == 422
    assert ask(client, a, action="restart_everything").status_code == 422


def test_a_requester_who_lost_staff_access_cannot_have_their_request_approved(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    from app.db.models.platform_staff import PlatformStaff

    db.query(PlatformStaff).filter(PlatformStaff.user_id == a.id).update(
        {PlatformStaff.revoked_at: datetime.now(UTC)}
    )
    db.flush()
    r = answer(client, b, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REQUESTER_NOT_ACTIVE"
    assert state.maintenance_state(db)["enabled"] is False
    # They can still be rejected.
    assert answer(client, b, rid, "reject").status_code == 200


def test_requesting_maintenance_when_it_is_already_on_is_refused(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    answer(client, b, rid)
    r = ask(client, b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAINTENANCE_ALREADY_ON"


def test_two_open_requests_cannot_both_switch_maintenance_on(client, db, two):
    a, b = two
    c, _ = mk_staff(db)
    r1 = ask(client, a).json()["data"]["id"]
    r2 = ask(client, c).json()["data"]["id"]
    assert answer(client, b, r1).status_code == 200
    r = answer(client, b, r2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAINTENANCE_ALREADY_ON"
    assert db.get(StaffApprovalRequest, r2).status == ApprovalStatus.pending


def test_unknown_request_is_404(client, db, two):
    import uuid

    _, b = two
    r = answer(client, b, uuid.uuid4())
    assert r.status_code == 404 and r.json()["error"]["code"] == "APPROVAL_NOT_FOUND"


def test_overview_lists_the_other_super_admins_as_approvers(client, db, two):
    a, b = two
    admin, _ = mk_staff(db, role=StaffRole.admin, scopes=["support"])
    d = client.get(f"{S}/danger-zone", headers=hdr(a)).json()["data"]
    emails = [x["email"] for x in d["eligible_approvers"]]
    assert b.email in emails and a.email not in emails and admin.email not in emails
    assert d["maintenance"] == {"enabled": False, "message": None, "since": None}
    assert d["last_global_signout_at"] is None and d["approval_ttl_minutes"] == 60
    ask(client, a)
    assert client.get(f"{S}/danger-zone", headers=hdr(a)).json()["data"]["pending_count"] == 1


# ---------------- maintenance mode ----------------


def turn_on(client, a, b, message=None):
    rid = ask(client, a, **({"message": message} if message else {})).json()["data"]["id"]
    assert answer(client, b, rid).status_code == 200


def test_maintenance_blocks_writes_but_not_reads_auth_staff_health_or_webhooks(client, db, two):
    a, b = two
    founder, startup, h = workspace(db)
    founder_pw = create_user(
        db,
        email="m@x.com",
        password_hash=get_password_hash("password1"),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    assert client.post("/api/v1/billing/trial", headers=h).status_code != 503
    turn_on(client, a, b, message="Back soon.")

    blocked = client.post("/api/v1/billing/trial", headers=h)
    assert blocked.status_code == 503
    assert blocked.json() == {
        "error": {"code": "MAINTENANCE_MODE", "message": "Back soon.", "field_errors": []}
    }
    assert blocked.headers["retry-after"] == "60"
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/api/v1/team/members/x", headers=h).status_code == 503
    # Reads still work, so founders can see their data.
    assert client.get("/api/v1/billing/subscription", headers=h).status_code == 200
    assert client.get("/health").status_code == 200
    assert client.options("/api/v1/billing/trial").status_code != 503
    # Sign-in still works.
    r = client.post("/api/v1/auth/login", json={"email": "m@x.com", "password": "password1"})
    assert r.status_code == 200 and founder_pw.id
    # Staff can use their consoles (and so can switch it off).
    assert client.get(f"{S}/danger-zone", headers=hdr(a)).status_code == 200
    # A payment event is processed, not bounced: Paystack would otherwise retry or give up.
    ev = post_event(client, "charge.success", {"reference": "no-such-ref", "status": "success"})
    assert ev.status_code != 503
    sig = client.post(
        "/api/v1/webhooks/billing/paystack",
        content=b"{}",
        headers={"x-paystack-signature": "bad"},
    )
    assert sig.status_code == 401  # it reached the handler, which checks the signature
    mono = client.post("/api/v1/webhooks/mono", content=b"{}")
    assert mono.status_code != 503
    # The banner endpoint is public.
    st = client.get("/api/v1/platform/status")
    assert st.status_code == 200
    assert st.json()["data"]["maintenance"]["enabled"] is True
    assert st.json()["data"]["maintenance"]["message"] == "Back soon."


def test_default_message_and_status_when_off(client, db, two):
    a, b = two
    off = client.get("/api/v1/platform/status")
    assert off.json() == {
        "data": {"maintenance": {"enabled": False, "message": None, "since": None}},
        "meta": None,
    }
    turn_on(client, a, b)
    on = client.get("/api/v1/platform/status").json()["data"]["maintenance"]
    assert on["enabled"] is True and on["message"] == state.DEFAULT_MAINTENANCE_MESSAGE
    assert datetime.fromisoformat(on["since"]) <= datetime.now(UTC)


def test_any_one_super_admin_can_switch_maintenance_off_alone(client, db, two):
    a, b = two
    r = client.post(f"{S}/maintenance/disable", json=body(a), headers=hdr(a))
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAINTENANCE_NOT_ON"
    turn_on(client, a, b)
    # The one who asked can switch it off without anyone else.
    r = client.post(f"{S}/maintenance/disable", json=body(a), headers=hdr(a))
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"maintenance": {"enabled": False, "message": None, "since": None}}
    assert state.maintenance_state(db)["enabled"] is False
    off = audits(db, "maintenance.disabled")
    assert len(off) == 1 and off[0].actor_user_id == a.id and off[0].reason == REASON
    _, founder_h = None, workspace(db)[2]
    assert client.post("/api/v1/billing/trial", headers=founder_h).status_code != 503


def test_a_failed_lookup_lets_requests_through(client, db, monkeypatch):
    _, _, h = workspace(db)

    def boom(_db):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(maintenance_mw, "maintenance_state", boom)
    assert client.post("/api/v1/billing/trial", headers=h).status_code != 503


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/api/v1/auth/login", True),
        ("/api/v1/auth/mfa/challenge", True),
        ("/api/v1/auth/refresh", True),
        ("/api/v1/auth/signup", False),
        ("/api/v1/auth/me/avatar", False),
        ("/api/v1/auth/mfa/totp/setup", False),
        ("/api/v1/auth", False),
        ("/api/v1/public/sales/unsubscribe/tok", True),
        ("/api/v1/notifications/stream-ticket", True),
        ("/api/v1/super-admin/approvals", True),
        ("/api/v1/admin/me", True),
        ("/api/v1/health/ready", True),
        ("/health", True),
        ("/api/v1/webhooks/billing/paystack", True),
        ("/api/v1/webhooks/payments/paystack/abc", True),
        ("/api/v1/authx/login", False),
        ("/api/v1/auth/../team/members", False),
        ("/api/v1/team/members", False),
        ("/api/v1/billing/trial", False),
        ("/api/v1/webhook", False),
        ("/", False),
    ],
)
def test_allow_list(path, allowed):
    assert maintenance_mw.is_allowed_during_maintenance(path) is allowed


# ---------------- global sign-out ----------------


def signed_in(client, db, email):
    create_user(
        db,
        email=email,
        password_hash=get_password_hash("password1"),
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "password1"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return {"Authorization": f"Bearer {d['access_token']}"}, d["refresh_token"]


def test_global_sign_out_ends_every_session_and_every_access_token(client, db, two):
    a, b = two
    f_h, f_refresh = signed_in(client, db, "f@x.com")
    assert client.get("/api/v1/auth/me", headers=f_h).status_code == 200
    staff_token = hdr(b)
    assert client.get(f"{S}/danger-zone", headers=staff_token).status_code == 200

    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    assert client.get("/api/v1/auth/me", headers=f_h).status_code == 200  # nothing happens yet
    r = answer(client, b, rid)
    assert r.status_code == 200, r.text
    out = r.json()["data"]["outcome"]
    assert out["sessions_signed_out"] >= 1 and out["signed_out_at"]

    # The founder's access token and refresh token are both dead.
    assert client.get("/api/v1/auth/me", headers=f_h).status_code == 401
    assert client.get("/api/v1/billing/subscription", headers=f_h).status_code == 401
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": f_refresh})
    assert r.status_code == 401
    # Staff are signed out like everyone else, including the one who approved.
    assert client.get(f"{S}/danger-zone", headers=staff_token).status_code == 404
    assert client.get(f"{S}/danger-zone", headers=hdr(a)).status_code == 200  # token minted later
    assert db.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).count() == 0
    assert len(audits(db, "platform.global_signout")) == 1
    assert client.get(f"{S}/danger-zone", headers=hdr(a)).json()["data"]["last_global_signout_at"]


def test_signing_in_again_after_a_global_sign_out_works_straight_away(client, db, two):
    a, b = two
    signed_in(client, db, "again@x.com")
    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    answer(client, b, rid)
    r = client.post("/api/v1/auth/login", json={"email": "again@x.com", "password": "password1"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    ref = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": r.json()["data"]["refresh_token"]}
    )
    assert ref.status_code == 200
    assert (
        client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {ref.json()['data']['access_token']}"},
        ).status_code
        == 200
    )


def test_staff_can_sign_back_in_during_maintenance_after_a_global_sign_out(client, db, two):
    a, b = two
    turn_on(client, a, b)
    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    answer(client, b, rid)
    assert client.get(f"{S}/danger-zone", headers=hdr(b)).status_code == 200
    r = client.post(f"{S}/maintenance/disable", json=body(b), headers=hdr(b))
    assert r.status_code == 200


def test_tokens_before_the_cutoff_and_tokens_without_a_stamp_are_refused(client, db, two):
    a, b = two
    plain = mk_user(db)
    old = create_access_token(str(plain.id))
    legacy = jwt.encode(
        {"sub": str(plain.id), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    odd = jwt.encode(
        {"sub": str(plain.id), "exp": datetime.now(UTC) + timedelta(minutes=5), "iat": "soon"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    me = "/api/v1/auth/me"
    # Before any sign-out, a token with no stamp is fine (tokens issued before this shipped).
    assert client.get(me, headers={"Authorization": f"Bearer {legacy}"}).status_code == 200
    assert client.get(me, headers={"Authorization": f"Bearer {odd}"}).status_code == 401
    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    answer(client, b, rid)
    for t in (old, legacy):
        assert client.get(me, headers={"Authorization": f"Bearer {t}"}).status_code == 401
    new = create_access_token(str(plain.id))
    assert client.get(me, headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert jwt.decode(new, settings.SECRET_KEY, algorithms=["HS256"])["iat"] > 1_700_000_000


def test_a_refresh_token_minted_after_the_sign_out_survives_a_second_sign_out_only_if_newer(
    client, db, two
):
    a, b = two
    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    answer(client, b, rid)
    h, refresh = signed_in(client, db, "late@x.com")
    rid2 = ask(client, a, action="global_signout").json()["data"]["id"]
    answer(client, b, rid2)
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_a_requester_whose_account_no_longer_passes_the_guard_cannot_have_a_request_run(
    client, db, two
):
    a, b = two
    rid = ask(client, a, action="global_signout").json()["data"]["id"]
    a.mfa_type = MfaType.none  # they turned their authenticator off
    db.flush()
    r = answer(client, b, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REQUESTER_NOT_ACTIVE"
    assert audits(db, "platform.global_signout") == []


def test_losing_super_admin_access_closes_open_requests_for_good(client, db, two):
    a, b = two
    c, _ = mk_staff(db)
    rid = ask(client, a).json()["data"]["id"]
    to_c = ask(client, b, action="global_signout", approver_user_id=str(c.id)).json()["data"]["id"]
    staff_id = db.query(PlatformStaff.id).filter(PlatformStaff.user_id == a.id).scalar()
    r = client.post(f"{S}/staff/{staff_id}/revoke", json=body(c), headers=hdr(c))
    assert r.status_code == 200, r.text
    assert db.get(StaffApprovalRequest, rid).status == ApprovalStatus.cancelled
    assert db.get(StaffApprovalRequest, to_c).status == ApprovalStatus.pending
    c_staff = db.query(PlatformStaff.id).filter(PlatformStaff.user_id == c.id).scalar()
    r = client.post(f"{S}/staff/{c_staff}/revoke", json=body(b), headers=hdr(b))
    assert r.status_code == 200, r.text
    assert db.get(StaffApprovalRequest, to_c).status == ApprovalStatus.cancelled
    assert len(audits(db, "approval.cancelled")) == 2


def test_a_caller_who_lost_access_gets_the_plain_404_before_anything_else(client, db, two):
    a, b = two
    rid = ask(client, a).json()["data"]["id"]
    b.mfa_type = MfaType.none
    db.flush()
    for verb in ("approve", "reject", "cancel"):
        r = client.post(
            f"{S}/approvals/{rid}/{verb}",
            json={"reason": OTHER, "totp_code": "000000"},
            headers=hdr(b),
        )
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_an_open_event_stream_closes_after_a_global_sign_out(monkeypatch):
    from app.api.v1.endpoints import notifications as n

    monkeypatch.setattr(n, "session_cutoff_ts", lambda _db: 100.0)
    assert n._signed_out_since(99.0) is True
    assert n._signed_out_since(101.0) is False
    monkeypatch.setattr(n, "session_cutoff_ts", lambda _db: None)
    assert n._signed_out_since(0.0) is False
