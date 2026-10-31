import importlib.util
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.staff_deps import require_staff
from app.core.config import settings
from app.core.errors import Forbidden, NotFound
from app.core.redis import get_redis
from app.core.security import create_access_token
from app.db.models.audit import AuditLog
from app.db.models.enums import MfaType, StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.scripts import create_super_admin
from app.services.auth import mfa
from tests.factories import create_startup, create_user

S = "/api/v1/super-admin"
A = "/api/v1/admin"
REASON = "Quarterly access review"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clean_redis_keys():
    r = get_redis()
    for pat in ("staff_stepup_*",):
        for k in r.scan_iter(pat):
            r.delete(k)
    yield


def mk_user(db, *, mfa_on=True, verified=True, status=UserStatus.active, email=None):
    secret = mfa.generate_totp_secret()
    u = create_user(
        db,
        email=email,
        status=status,
        email_verified_at=datetime.now(UTC) if verified else None,
        mfa_type=MfaType.totp if mfa_on else MfaType.none,
        mfa_secret=mfa.encrypt_secret(secret) if mfa_on else None,
        mfa_enabled_at=datetime.now(UTC) if mfa_on else None,
    )
    u.test_secret = secret  # type: ignore[attr-defined]
    return u


def mk_staff(db, role=StaffRole.super_admin, scopes=None, **kw):
    u = mk_user(db, **kw)
    row = PlatformStaff(user_id=u.id, role=role, scopes=scopes or [])
    db.add(row)
    db.flush()
    return u, row


def hdr(u):
    return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def code(u, offset=0):
    return pyotp.TOTP(u.test_secret).at(datetime.now(UTC) + timedelta(seconds=30 * offset))


# ---------------- guards ----------------


def test_unknown_callers_get_the_same_404_as_an_unknown_route(client, db):
    plain = mk_user(db)
    expected = client.get("/api/v1/definitely-not-a-route")
    assert expected.status_code == 404
    for headers in ({}, hdr(plain), {"Authorization": "Bearer garbage"}):
        for url in (f"{S}/staff", f"{S}/audit", f"{A}/me"):
            r = client.get(url, headers=headers)
            assert r.status_code == 404, (url, headers)
            assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "kw",
    [
        {"mfa_on": False},
        {"verified": False},
        {"status": UserStatus.disabled},
        {"status": UserStatus.locked},
    ],
)
def test_staff_row_alone_is_not_enough(client, db, kw):
    u, _ = mk_staff(db, **kw)
    assert client.get(f"{A}/me", headers=hdr(u)).status_code == 404
    assert client.get(f"{S}/staff", headers=hdr(u)).status_code == 404


def test_revoked_staff_get_404(client, db):
    u, row = mk_staff(db)
    assert client.get(f"{A}/me", headers=hdr(u)).status_code == 200
    row.revoked_at = datetime.now(UTC)
    db.flush()
    assert client.get(f"{A}/me", headers=hdr(u)).status_code == 404


def test_turning_mfa_off_removes_staff_access_at_once(client, db):
    u, _ = mk_staff(db)
    h = hdr(u)
    assert client.get(f"{A}/me", headers=h).status_code == 200
    u.mfa_type = MfaType.none
    db.flush()
    assert client.get(f"{A}/me", headers=h).status_code == 404


def test_admin_me_and_admin_cannot_see_super_admin_routes(client, db):
    u, _ = mk_staff(db, StaffRole.admin, ["support"])
    r = client.get(f"{A}/me", headers=hdr(u))
    assert r.status_code == 200
    assert r.json()["data"] == {
        "user_id": str(u.id),
        "email": u.email,
        "role": "admin",
        "scopes": ["support"],
    }
    assert client.get(f"{S}/staff", headers=hdr(u)).status_code == 404
    assert client.post(f"{S}/staff", json={}, headers=hdr(u)).status_code == 404


def test_scope_check(db):
    admin, _ = mk_staff(db, StaffRole.admin, ["support"])
    sa, _ = mk_staff(db)

    class _Dep:
        pass

    from app.api.staff_deps import _staff_context

    assert require_staff("support", "content")(user=admin, db=db).staff.scopes == ["support"]
    with pytest.raises(Forbidden):
        require_staff("content")(user=admin, db=db)
    assert require_staff("content")(user=sa, db=db).is_super_admin
    assert _staff_context(db, sa).is_super_admin
    with pytest.raises(NotFound):
        require_staff()(user=None, db=db)


# ---------------- list / invite / revoke ----------------


def test_list_staff(client, db):
    sa, _ = mk_staff(db)
    ad, ad_row = mk_staff(db, StaffRole.admin, ["content", "support"])
    ad_row.invited_by = sa.id
    ad_row.revoked_at = datetime.now(UTC)
    db.flush()
    rows = client.get(f"{S}/staff", headers=hdr(sa)).json()["data"]
    assert {r["email"] for r in rows} == {sa.email, ad.email}
    revoked = next(r for r in rows if r["email"] == ad.email)
    assert revoked["invited_by_email"] == sa.email and revoked["revoked_at"]
    active = client.get(f"{S}/staff?include_revoked=false", headers=hdr(sa)).json()["data"]
    assert [r["email"] for r in active] == [sa.email]


def invite(client, sa, target, *, role="admin", scopes=("support",), reason=REASON, c=None, **kw):
    body = {
        "email": target.email,
        "role": role,
        "scopes": list(scopes),
        "reason": reason,
        "totp_code": c if c is not None else code(sa),
    }
    body.update(kw)
    return client.post(f"{S}/staff", json=body, headers=hdr(sa))


def test_invite_admin_writes_audit_and_grants_access(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    r = invite(client, sa, target, scopes=("moderation", "support", "support"))
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["role"] == "admin" and d["scopes"] == ["moderation", "support"]
    assert d["invited_by_email"] == sa.email and d["revoked_at"] is None
    assert client.get(f"{A}/me", headers=hdr(target)).json()["data"]["scopes"] == [
        "moderation",
        "support",
    ]
    log = db.query(AuditLog).filter_by(action="staff.invited").one()
    assert log.actor_user_id == sa.id and log.reason == REASON
    assert log.entity_type == "platform_staff" and str(log.entity_id) == d["id"]
    assert log.details == {
        "email": target.email,
        "role": "admin",
        "scopes": ["moderation", "support"],
    }


def test_invite_super_admin(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    r = invite(client, sa, target, role="super_admin", scopes=())
    assert r.status_code == 201 and r.json()["data"]["scopes"] == []


@pytest.mark.parametrize(
    "override",
    [
        {"reason": "too short"},
        {"reason": "   short   "},
        {"reason": ""},
        {"scopes": []},
        {"scopes": ["root"]},
        {"role": "super_admin", "scopes": ["support"]},
        {"role": "owner"},
        {"totp_code": "12345"},
        {"totp_code": "abcdef"},
        {"extra": 1},
    ],
)
def test_invite_validation(client, db, override):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    r = invite(client, sa, target, **override)
    assert r.status_code == 422, r.text
    assert db.query(PlatformStaff).filter_by(user_id=target.id).count() == 0


def test_invite_missing_code_or_reason_is_422(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    r = client.post(
        f"{S}/staff",
        json={"email": target.email, "role": "admin", "scopes": ["support"], "reason": REASON},
        headers=hdr(sa),
    )
    assert r.status_code == 422
    assert db.query(PlatformStaff).filter_by(user_id=target.id).count() == 0


def test_step_up_wrong_code_replay_and_lockout(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    r = invite(client, sa, target, c="000000")
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    assert db.query(PlatformStaff).filter_by(user_id=target.id).count() == 0
    good = code(sa)
    assert invite(client, sa, target, c=good).status_code == 201
    other = mk_user(db)
    replay = invite(client, sa, other, c=good)  # same code again inside its window
    assert replay.status_code == 403 and replay.json()["error"]["code"] == "STEP_UP_INVALID"
    # Five strikes lock further attempts, even with a fresh valid code.
    for _ in range(4):  # the replay was strike 1; a success earlier reset the count
        invite(client, sa, other, c="000000")
    locked = invite(client, sa, other, c=code(sa, 1))
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_step_up_accepts_previous_step_only(client, db):
    sa, _ = mk_staff(db)
    t1, t2 = mk_user(db), mk_user(db)
    assert invite(client, sa, t1, c=code(sa, -1)).status_code == 201
    assert invite(client, sa, t2, c=code(sa, -3)).status_code == 403


def test_step_up_secret_unreadable_is_refused(client, db, monkeypatch):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    c = code(sa)
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert invite(client, sa, target, c=c).status_code == 403


def test_a_staff_user_cannot_step_up_with_someone_elses_code(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    assert invite(client, sa, target, c=code(target)).status_code == 403


@pytest.mark.parametrize(
    ("make", "status", "err"),
    [
        (lambda db: mk_user(db, mfa_on=False), 422, "STAFF_MFA_REQUIRED"),
        (lambda db: mk_user(db, verified=False), 422, "STAFF_ACCOUNT_NOT_ELIGIBLE"),
        (lambda db: mk_user(db, status=UserStatus.disabled), 422, "STAFF_ACCOUNT_NOT_ELIGIBLE"),
    ],
)
def test_invite_target_must_be_eligible(client, db, make, status, err):
    sa, _ = mk_staff(db)
    r = invite(client, sa, make(db))
    assert r.status_code == status and r.json()["error"]["code"] == err


def test_invite_unknown_email_and_deleted_user(client, db):
    sa, _ = mk_staff(db)
    ghost = mk_user(db, email="ghost@example.com")
    ghost.deleted_at = datetime.now(UTC)
    db.flush()
    for email in ("nobody@example.com", "ghost@example.com"):
        body = {
            "email": email,
            "role": "admin",
            "scopes": ["support"],
            "reason": REASON,
            "totp_code": code(sa, 0),
        }
        r = client.post(f"{S}/staff", json=body, headers=hdr(sa))
        assert r.status_code == 404 and r.json()["error"]["code"] == "USER_NOT_FOUND"
        get_redis().delete(f"staff_stepup_used:{sa.id}:{body['totp_code']}")


def test_invite_existing_staff_conflicts_and_revoked_is_reinstated(client, db):
    sa, _ = mk_staff(db)
    target = mk_user(db)
    assert invite(client, sa, target).status_code == 201
    dup = invite(client, sa, target, c=code(sa, 1))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ALREADY_STAFF"
    row = db.query(PlatformStaff).filter_by(user_id=target.id).one()
    r = client.post(
        f"{S}/staff/{row.id}/revoke",
        json={"reason": REASON, "totp_code": code(sa, -1)},
        headers=hdr(sa),
    )
    assert r.status_code == 200
    for k in get_redis().scan_iter("staff_stepup_used:*"):  # only 3 distinct codes fit the window
        get_redis().delete(k)
    again = invite(client, sa, target, role="admin", scopes=("content",), c=code(sa))
    assert again.status_code == 201
    assert again.json()["data"]["id"] == str(row.id)
    assert (
        again.json()["data"]["scopes"] == ["content"] and again.json()["data"]["revoked_at"] is None
    )
    assert db.query(AuditLog).filter_by(action="staff.reinstated").count() == 1


def test_revoke_locks_out_the_target_at_once_and_audits(client, db):
    sa, _ = mk_staff(db)
    ad, row = mk_staff(db, StaffRole.admin, ["support"])
    h = hdr(ad)  # token issued before the revoke keeps working as a plain login, not as staff
    assert client.get(f"{A}/me", headers=h).status_code == 200
    r = client.post(
        f"{S}/staff/{row.id}/revoke",
        json={"reason": REASON, "totp_code": code(sa)},
        headers=hdr(sa),
    )
    assert r.status_code == 200 and r.json()["data"]["revoked_at"]
    assert client.get(f"{A}/me", headers=h).status_code == 404
    log = db.query(AuditLog).filter_by(action="staff.revoked").one()
    assert log.reason == REASON and log.actor_user_id == sa.id
    assert log.details["email"] == ad.email
    again = client.post(
        f"{S}/staff/{row.id}/revoke",
        json={"reason": REASON, "totp_code": code(sa, 1)},
        headers=hdr(sa),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REVOKED"


def test_revoke_guards(client, db):
    sa, sa_row = mk_staff(db)
    sa2, sa2_row = mk_staff(db)
    body = lambda n: {"reason": REASON, "totp_code": code(sa, n)}  # noqa: E731
    me = client.post(f"{S}/staff/{sa_row.id}/revoke", json=body(0), headers=hdr(sa))
    assert me.status_code == 422 and me.json()["error"]["code"] == "CANNOT_REVOKE_SELF"
    missing = client.post(f"{S}/staff/{uuid.uuid4()}/revoke", json=body(1), headers=hdr(sa))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "STAFF_NOT_FOUND"
    short = client.post(
        f"{S}/staff/{sa2_row.id}/revoke",
        json={"reason": "no", "totp_code": code(sa, -1)},
        headers=hdr(sa),
    )
    assert short.status_code == 422
    assert sa2_row.revoked_at is None
    # sa revokes sa2, leaving sa as the only Super Admin; sa2 then cannot revoke sa.
    ok = client.post(f"{S}/staff/{sa2_row.id}/revoke", json=body(-1), headers=hdr(sa))
    assert ok.status_code == 200
    assert client.get(f"{S}/staff", headers=hdr(sa2)).status_code == 404


def test_last_super_admin_cannot_be_revoked(client, db):
    sa, sa_row = mk_staff(db)
    ad, _ = mk_staff(db, StaffRole.admin, ["support"])
    # Make an in-flight revoke of the sole Super Admin by a second one who is itself revoked mid-way:
    # exercise the service directly, as the route forbids self-revoke.
    from app.api.staff_deps import StaffContext
    from app.core.errors import AppError
    from app.services.staff.access import revoke_staff

    other, other_row = mk_staff(db)
    other_row.revoked_at = datetime.now(UTC)
    db.flush()
    ctx = StaffContext(user=other, staff=other_row)
    with pytest.raises(AppError) as e:
        revoke_staff(db, ctx, staff_id=sa_row.id, reason=REASON, ip=None, user_agent=None)
    assert e.value.code == "LAST_SUPER_ADMIN" and e.value.http_status == 409
    assert sa_row.revoked_at is None and ad  # unchanged


# ---------------- audit explorer ----------------


def seed_audit(db, actor, n=3, **kw):
    base = datetime.now(UTC) - timedelta(days=1)
    rows = []
    for i in range(n):
        r = AuditLog(
            action=kw.get("action", "team.member.removed"),
            actor_user_id=actor.id,
            startup_id=kw.get("startup_id"),
            created_at=base + timedelta(seconds=i),
        )
        db.add(r)
        rows.append(r)
    db.flush()
    return rows


def test_audit_explorer_filters_and_pages(client, db):
    sa, _ = mk_staff(db)
    founder = create_user(db, email="founder@example.com")
    ws = create_startup(db, owner=founder)
    seed_audit(db, founder, 3, action="team.member.removed", startup_id=ws.id)
    seed_audit(db, founder, 2, action="billing.plan.changed")
    seed_audit(db, sa, 1, action="team.member.role_changed")
    h = hdr(sa)
    got = client.get(f"{S}/audit?limit=100", headers=h).json()
    assert got["meta"]["next_cursor"] is None
    e = got["data"]["entries"]
    assert len(e) == 6
    assert e[0]["created_at"] >= e[-1]["created_at"]
    assert set(e[0]) == {
        "id", "created_at", "action", "actor_user_id", "actor_email", "workspace_id",
        "entity_type", "entity_id", "reason", "details", "ip",
    }  # fmt: skip

    def q(qs):
        return client.get(f"{S}/audit?limit=100&{qs}", headers=h).json()["data"]["entries"]

    assert len(q(f"actor_id={founder.id}")) == 5
    assert q(f"actor_id={sa.id}")[0]["actor_email"] == sa.email
    assert len(q("action=team.member.removed")) == 3
    assert len(q("action=team.")) == 4  # the family
    assert q("action=team_") == []  # `_` is not a wildcard
    assert q("action=%25") == []  # nor is `%`
    assert len(q(f"workspace_id={ws.id}")) == 3
    assert (
        len(
            q(
                f"date_from={(datetime.now(UTC) + timedelta(hours=1)).isoformat().replace('+', '%2B')}"
            )
        )
        == 0
    )
    assert (
        len(
            q(f"date_to={(datetime.now(UTC) - timedelta(hours=1)).isoformat().replace('+', '%2B')}")
        )
        == 6
    )
    # paging: 6 rows, 2 per page, no row twice
    seen, cursor = [], None
    for _ in range(5):
        url = f"{S}/audit?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=h).json()
        seen += [x["id"] for x in body["data"]["entries"]]
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 6
    bad = client.get(f"{S}/audit?cursor=nonsense", headers=h)
    assert bad.status_code == 422


def test_audit_explorer_is_super_admin_only(client, db):
    ad, _ = mk_staff(db, StaffRole.admin, ["support", "content", "moderation"])
    assert client.get(f"{S}/audit", headers=hdr(ad)).status_code == 404
    assert client.get(f"{S}/audit/export?reason={REASON}", headers=hdr(ad)).status_code == 404


def test_audit_export_is_csv_safe_and_audited(client, db):
    sa, _ = mk_staff(db)
    evil = create_user(db, email="=cmd@example.com")
    seed_audit(db, evil, 1, action="x.y")
    h = hdr(sa)
    r = client.get(f"{S}/audit/export?reason=Compliance+request+17&action=x.y", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="audit-log.csv"' in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "when,actor,action,workspace_id,entity_type,entity_id,reason"
    assert len(lines) == 2 and "'=cmd@example.com" in lines[1]
    log = db.query(AuditLog).filter_by(action="audit.exported").one()
    assert log.actor_user_id == sa.id and log.reason == "Compliance request 17"
    assert log.details["rows"] == 1 and log.details["filters"]["action"] == "x.y"
    assert client.get(f"{S}/audit/export", headers=h).status_code == 422
    assert client.get(f"{S}/audit/export?reason=short", headers=h).status_code == 422
    assert db.query(AuditLog).filter_by(action="audit.exported").count() == 1


# ---------------- bootstrap command ----------------


@contextmanager
def _same_session(db):
    yield db


def test_create_super_admin_command(db, monkeypatch, capsys):
    monkeypatch.setattr(create_super_admin, "SessionLocal", lambda: _same_session(db))
    run = lambda email: create_super_admin.main(["x", email])  # noqa: E731
    assert create_super_admin.main(["x"]) == 2
    assert run("nobody@example.com") == 1
    u = mk_user(db, mfa_on=False)
    assert run(u.email) == 1
    assert "authenticator" in capsys.readouterr().err
    unverified = mk_user(db, verified=False)
    assert run(unverified.email) == 1
    good = mk_user(db)
    assert run(good.email) == 0
    row = db.query(PlatformStaff).filter_by(user_id=good.id).one()
    assert row.role == StaffRole.super_admin and row.scopes == []
    log = db.query(AuditLog).filter_by(action="staff.bootstrapped").one()
    assert log.actor_user_id is None and log.details["email"] == good.email
    assert run(good.email) == 0 and "already" in capsys.readouterr().out
    # promotes a revoked admin
    row.role, row.scopes, row.revoked_at = StaffRole.admin, ["support"], datetime.now(UTC)
    db.flush()
    assert run(good.email) == 0
    db.refresh(row)
    assert row.role == StaffRole.super_admin and row.revoked_at is None and row.scopes == []


# ---------------- append-only trigger ----------------


def _migration():
    path = Path(__file__).parents[2] / "alembic/versions/0083_platform_staff.py"
    spec = importlib.util.spec_from_file_location("m0083", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_audit_log_trigger_rejects_update_and_delete(db):
    """The migration's own SQL, installed inside the test transaction (DDL rolls back with it)."""
    m = _migration()
    db.execute(text("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log"))
    db.execute(text("DROP FUNCTION IF EXISTS audit_log_reject_change()"))
    db.execute(text(m.FUNCTION_SQL))
    db.execute(text(m.TRIGGER_SQL))
    row = AuditLog(action="a.b")
    db.add(row)
    db.flush()  # INSERT stays allowed
    for stmt in ("UPDATE audit_log SET action = 'c.d'", "DELETE FROM audit_log"):
        sp = db.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(stmt))
        sp.rollback()
    assert db.query(AuditLog).filter_by(action="a.b").count() == 1
