"""SO4: platform staff never go through founder onboarding and never get a founder workspace."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import StaffRole, UserStatus
from app.db.models.platform_staff import PlatformStaff
from app.db.models.startup import Startup
from tests.api.test_staff_users import hdr, mk_staff, mk_user, mk_ws
from tests.factories import create_user

ONB = "/api/v1/onboarding"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    # The console's own staff (mk_staff) need TOTP, which needs a key to encrypt the secret.
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _staff(db, role=StaffRole.admin):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    scopes = ["support"] if role == StaffRole.admin else []
    db.add(PlatformStaff(user_id=u.id, role=role, scopes=scopes))
    db.flush()
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def _owned(db, u):
    return db.query(Startup).filter(Startup.created_by == u.id).count()


@pytest.mark.parametrize("role", [StaffRole.admin, StaffRole.super_admin])
def test_me_reports_the_staff_role(client, db, role):
    u, h = _staff(db, role)
    me = client.get("/api/v1/auth/me", headers=h).json()["data"]
    assert me["staff_role"] == role.value


def test_me_staff_role_is_null_for_a_founder_and_after_a_revoke(client, db):
    founder = mk_user(db)
    assert client.get("/api/v1/auth/me", headers=hdr(founder)).json()["data"]["staff_role"] is None
    u, h = _staff(db)
    db.query(PlatformStaff).filter_by(user_id=u.id).one().revoked_at = datetime.now(UTC)
    db.flush()
    assert client.get("/api/v1/auth/me", headers=h).json()["data"]["staff_role"] is None


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/state", None),
        ("patch", "/state", {"step": 1, "full_name": "Staff Person"}),
        ("post", "/complete", None),
        ("post", "/invites", {"invites": []}),
    ],
)
def test_staff_never_get_a_founder_workspace(client, db, method, path, body):
    # Even before turning 2FA on: a staff row alone is enough to skip the wizard.
    u, h = _staff(db)
    kw = {"json": body} if body is not None else {}
    r = getattr(client, method)(f"{ONB}{path}", headers=h, **kw)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "ONBOARDING_NOT_FOR_STAFF"
    assert _owned(db, u) == 0


def test_a_founder_still_gets_a_draft(client, db):
    founder = mk_user(db)
    r = client.get(f"{ONB}/state", headers=hdr(founder))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["startup"]["id"]
    assert _owned(db, founder) == 1


def test_staff_who_already_own_a_workspace_get_it_back(client, db):
    # A workspace from before the fix (or a dual-role founder's own) is returned, never a 409.
    u, h = _staff(db)
    ws = mk_ws(db, u, name="Mine")
    db.flush()
    r = client.get(f"{ONB}/state", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["startup"]["id"] == str(ws.id)
    assert _owned(db, u) == 1


def test_revoked_staff_can_start_a_startup(client, db):
    u, h = _staff(db)
    db.query(PlatformStaff).filter_by(user_id=u.id).one().revoked_at = datetime.now(UTC)
    db.flush()
    r = client.get(f"{ONB}/state", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["startup"]["id"]
    assert _owned(db, u) == 1


def test_only_staff_ghost_drafts_and_staff_signups_are_left_out_of_the_kpis(client, db):
    viewer = mk_staff(db)
    base = client.get("/api/v1/admin/dashboard/kpis", headers=hdr(viewer)).json()["data"]
    now = datetime.now(UTC)
    # A ghost draft left by a staff sign-in before this fix: never onboarded, never counts.
    ghost_owner = mk_staff(db)
    mk_ws(db, ghost_owner, name="Ghost")
    # A dual-role staff member's real, onboarded, paid workspace: counts like any customer.
    dual = mk_staff(db)
    real_staff = mk_ws(db, dual, name="StaffCo", plan="growth", status="active")
    # An ordinary founder's onboarded workspace.
    real = mk_ws(db, mk_user(db), name="Real")
    for ws in (real_staff, real):
        ws.created_at = now - timedelta(days=1)
        ws.profile.onboarding_completed_at = now - timedelta(hours=1)
    db.flush()
    k = client.get("/api/v1/admin/dashboard/kpis", headers=hdr(viewer)).json()["data"]
    assert k["total_workspaces"]["value"] == base["total_workspaces"]["value"] + 2
    assert k["total_workspaces"]["new_in_window"] == base["total_workspaces"]["new_in_window"] + 2
    assert k["activation_rate"]["activated"] == base["activation_rate"]["activated"] + 2
    assert (
        k["paid_conversion"]["paying_workspaces"]
        == base["paid_conversion"]["paying_workspaces"] + 1
    )
    signups = sum(d["signups"] for d in k["signups_series"])
    # Two staff (ghost owner, dual) and one founder signed up since `base`: only the founder counts.
    assert signups == sum(d["signups"] for d in base["signups_series"]) + 1
