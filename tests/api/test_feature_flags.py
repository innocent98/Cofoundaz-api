import uuid

import pytest
from sqlalchemy import func

from app.db.models.audit import AuditLog
from app.db.models.enums import FlagStage, StaffRole
from app.db.models.feature_flag import FeatureFlag
from app.db.models.startup import Startup
from app.services import flags as svc
from tests.api.test_staff_access import (  # noqa: F401  (autouse fixtures come with the import)
    _clean_redis_keys,
    _mfa_key,
    code,
    hdr,
    mk_staff,
    mk_user,
)
from tests.documents_helpers import team
from tests.factories import create_membership, create_startup

S = "/api/v1/super-admin"
REASON = "Rolling out to the beta cohort"


def body(u, offset=0, **kw):
    return {"reason": REASON, "totp_code": code(u, offset), **kw}


def mk_flag(db, key="voice_notes", **kw):
    f = FeatureFlag(
        key=key,
        name=kw.pop("name", "AI voice notes"),
        stage=kw.pop("stage", FlagStage.beta),
        **kw,
    )
    db.add(f)
    db.flush()
    return f


# ---------------- bucketing and is_enabled ----------------


def test_bucket_is_stable_and_in_range():
    w = uuid.uuid4()
    assert svc.bucket("a_flag", w) == svc.bucket("a_flag", w)
    assert {0 <= svc.bucket("a_flag", uuid.uuid4()) < 100 for _ in range(200)} == {True}
    # The key is part of the hash: one workspace is not in the same place for every flag.
    ws = [uuid.uuid4() for _ in range(50)]
    assert [svc.bucket("flag_one", x) for x in ws] != [svc.bucket("flag_two", x) for x in ws]


def test_bucket_is_roughly_uniform():
    ws = [uuid.uuid4() for _ in range(4000)]
    in_ten = sum(svc.bucket("voice_notes", w) < 10 for w in ws)
    assert 280 < in_ten < 520  # 10% of 4000 is 400


def test_rollout_only_ever_adds_workspaces():
    ws = [uuid.uuid4() for _ in range(300)]

    def on(pct):
        return {
            w
            for w in ws
            if svc.evaluate(
                key="k_flag", enabled=True, rollout_pct=pct, allowlist=[], workspace_id=w
            )
        }

    assert on(0) == set()
    assert on(25) <= on(50) <= on(100)
    assert on(100) == set(ws)


def test_disabled_flag_is_off_for_everyone_even_the_allowlist():
    w = uuid.uuid4()
    assert not svc.evaluate(
        key="k_flag", enabled=False, rollout_pct=100, allowlist=[w], workspace_id=w
    )


def test_allowlist_wins_over_a_zero_rollout():
    w = uuid.uuid4()
    assert svc.evaluate(key="k_flag", enabled=True, rollout_pct=0, allowlist=[w], workspace_id=w)
    assert not svc.evaluate(
        key="k_flag", enabled=True, rollout_pct=0, allowlist=[w], workspace_id=uuid.uuid4()
    )


def test_is_enabled_reads_the_database(db):
    owner = mk_user(db)
    s = create_startup(db, owner=owner)
    mk_flag(db, enabled=True, rollout_pct=100)
    mk_flag(db, key="off_flag", enabled=False, rollout_pct=100)
    assert svc.is_enabled(db, "voice_notes", s.id) is True
    assert svc.is_enabled(db, "off_flag", s.id) is False
    assert svc.is_enabled(db, "no_such_flag", s.id) is False  # code may ship before its flag


# ---------------- guards ----------------


def test_flag_routes_are_hidden_from_everyone_but_a_super_admin(client, db):
    plain = mk_user(db)
    admin, _ = mk_staff(db, role=StaffRole.admin, scopes=["support"])
    for who in (None, plain, admin):
        h = hdr(who) if who else {}
        for method, url in (
            ("get", f"{S}/flags"),
            ("post", f"{S}/flags"),
            ("patch", f"{S}/flags/voice_notes"),
            ("get", f"{S}/flags/voice_notes/impact"),
        ):
            r = getattr(client, method)(url, headers=h)
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (who, url)


def test_mutations_need_a_fresh_code_and_a_reason(client, db):
    sa, _ = mk_staff(db)
    mk_flag(db)
    create = {
        "key": "new_flag",
        "name": "New",
        "stage": "beta",
        "reason": REASON,
        "totp_code": "000000",
    }
    assert client.post(f"{S}/flags", json=create, headers=hdr(sa)).status_code == 403
    assert (
        client.post(f"{S}/flags", json={**create, "reason": "short"}, headers=hdr(sa)).status_code
        == 422
    )
    r = client.patch(f"{S}/flags/voice_notes", json=body(sa, enabled=True), headers=hdr(sa))
    assert r.status_code == 200
    replay = client.patch(
        f"{S}/flags/voice_notes",
        json={"reason": REASON, "enabled": False, "totp_code": code(sa)},
        headers=hdr(sa),
    )
    assert replay.status_code == 403 and replay.json()["error"]["code"] == "STEP_UP_INVALID"
    wrong = client.patch(
        f"{S}/flags/voice_notes",
        json={"reason": "no", "enabled": False, "totp_code": code(sa, 1)},
        headers=hdr(sa),
    )
    assert wrong.status_code == 422


# ---------------- create ----------------


def test_create_flag_audits_it_and_defaults_off(client, db):
    sa, _ = mk_staff(db)
    r = client.post(
        f"{S}/flags",
        json=body(sa, key="grant_matching", name="Grant matching", stage="ga", description="Fit."),
        headers=hdr(sa),
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["key"] == "grant_matching" and d["stage"] == "ga"
    assert d["enabled"] is False and d["rollout_pct"] == 0 and d["affected_workspaces"] == 0
    row = db.query(AuditLog).filter(AuditLog.action == "flag.created").one()
    assert row.actor_user_id == sa.id and row.reason == REASON
    assert row.details["key"] == "grant_matching"


@pytest.mark.parametrize(
    "bad",
    [
        {"key": "Bad Key"},
        {"key": "ab"},
        {"key": "1abc"},
        {"stage": "alpha"},
        {"rollout_pct": 101},
        {"rollout_pct": -1},
        {"name": " "},
        {"extra": 1},
    ],
)
def test_create_rejects_bad_input(client, db, bad):
    sa, _ = mk_staff(db)
    ok = {"key": "good_key", "name": "Good", "stage": "beta"}
    r = client.post(f"{S}/flags", json=body(sa, **{**ok, **bad}), headers=hdr(sa))
    assert r.status_code == 422, r.text


def test_duplicate_key_is_409(client, db):
    sa, _ = mk_staff(db)
    mk_flag(db)
    r = client.post(
        f"{S}/flags", json=body(sa, key="voice_notes", name="Again", stage="beta"), headers=hdr(sa)
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "FLAG_KEY_EXISTS"


def test_allowlist_must_name_real_workspaces(client, db):
    sa, _ = mk_staff(db)
    s = create_startup(db, owner=sa)
    ghost = str(uuid.uuid4())
    r = client.post(
        f"{S}/flags",
        json=body(sa, key="gated", name="Gated", stage="internal", workspace_allowlist=[ghost]),
        headers=hdr(sa),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_WORKSPACE"
    r = client.post(
        f"{S}/flags",
        json=body(
            sa,
            -1,
            key="gated",
            name="Gated",
            stage="internal",
            workspace_allowlist=[str(s.id), str(s.id)],
        ),
        headers=hdr(sa),
    )
    assert r.status_code == 201 and r.json()["data"]["workspace_allowlist"] == [str(s.id)]


# ---------------- update ----------------


def test_update_changes_only_what_was_sent_and_audits_before_after(client, db):
    sa, _ = mk_staff(db)
    f = mk_flag(db, description="old", enabled=True, rollout_pct=25)
    r = client.patch(
        f"{S}/flags/{f.key}", json=body(sa, rollout_pct=50, stage="ga"), headers=hdr(sa)
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["rollout_pct"], d["stage"], d["enabled"], d["description"]) == (50, "ga", True, "old")
    row = db.query(AuditLog).filter(AuditLog.action == "flag.updated").one()
    assert row.details == {
        "key": "voice_notes",
        "from": {"rollout_pct": 25, "stage": "beta"},
        "to": {"rollout_pct": 50, "stage": "ga"},
    }


def test_update_refusals(client, db, monkeypatch):
    # Step-up is covered above; a code works once, and this test makes many requests.
    monkeypatch.setattr("app.api.v1.endpoints.super_admin.verify_step_up", lambda *a: None)
    sa, _ = mk_staff(db)
    mk_flag(db, enabled=True, rollout_pct=25)

    def patch(flag_key, **kw):
        return client.patch(f"{S}/flags/{flag_key}", json=body(sa, **kw), headers=hdr(sa))

    assert patch("nope_flag", enabled=True).status_code == 404
    r = patch("voice_notes", rollout_pct=25)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHANGE"
    r = patch("voice_notes")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHANGE"
    assert patch("voice_notes", rollout_pct=None).status_code == 422
    assert patch("voice_notes", rollout_pct=120).status_code == 422
    assert patch("voice_notes", key="renamed").status_code == 422  # the key is immutable
    assert db.query(func.count(AuditLog.id)).filter(AuditLog.action == "flag.updated").scalar() == 0


def test_kill_switch_turns_it_off_for_everyone(client, db):
    sa, _ = mk_staff(db)
    startup, users = team(db)
    f = mk_flag(db, enabled=True, rollout_pct=100)
    founder = next(iter(users.values()))
    h = {**hdr(founder), "X-Workspace-Id": str(startup.id)}
    assert client.get("/api/v1/flags", headers=h).json()["data"]["flags"] == {f.key: True}
    r = client.patch(f"{S}/flags/{f.key}", json=body(sa, enabled=False), headers=hdr(sa))
    assert r.status_code == 200
    assert client.get("/api/v1/flags", headers=h).json()["data"]["flags"] == {}


# ---------------- list and impact ----------------


def test_list_and_blast_radius_count_live_workspaces(client, db):
    sa, _ = mk_staff(db)
    base = db.query(func.count(Startup.id)).scalar()
    for i in range(20):
        create_startup(db, owner=sa, name=f"W{i}")
    f = mk_flag(db, enabled=True, rollout_pct=100)
    data = client.get(f"{S}/flags", headers=hdr(sa)).json()["data"]
    assert data["total_workspaces"] == base + 20
    assert data["flags"][0]["key"] == f.key and data["flags"][0]["affected_workspaces"] == base + 20

    r = client.get(f"{S}/flags/{f.key}/impact?rollout_pct=0", headers=hdr(sa)).json()["data"]
    assert (r["affected_now"], r["affected_after"]) == (base + 20, 0)
    r = client.get(f"{S}/flags/{f.key}/impact?enabled=false", headers=hdr(sa)).json()["data"]
    assert r["affected_after"] == 0
    assert (
        client.get(f"{S}/flags/{f.key}/impact?rollout_pct=101", headers=hdr(sa)).status_code == 422
    )
    assert client.get(f"{S}/flags/nope_flag/impact", headers=hdr(sa)).status_code == 404


def test_deleted_workspaces_are_not_counted(client, db):
    from datetime import UTC, datetime

    sa, _ = mk_staff(db)
    gone = create_startup(db, owner=sa)
    gone.deleted_at = datetime.now(UTC)
    mk_flag(db, enabled=True, rollout_pct=100)
    db.flush()
    live = db.query(func.count(Startup.id)).filter(Startup.deleted_at.is_(None)).scalar()
    assert client.get(f"{S}/flags", headers=hdr(sa)).json()["data"]["total_workspaces"] == live


# ---------------- the app's read route ----------------


def test_app_read_route_is_per_workspace_and_membership_checked(client, db):
    startup, users = team(db)
    outsider = mk_user(db)
    other = create_startup(db, owner=outsider)
    create_membership(db, outsider, other)
    mk_flag(db, enabled=True, rollout_pct=0, workspace_allowlist=[startup.id])
    mk_flag(db, key="beta_board", enabled=False)
    founder = next(iter(users.values()))
    r = client.get("/api/v1/flags", headers={**hdr(founder), "X-Workspace-Id": str(startup.id)})
    assert r.status_code == 200
    assert r.json()["data"] == {
        "flags": {"voice_notes": True}
    }  # beta_board is off: its key isn't listed
    # Not a member of that workspace: refused, and a workspace not on the allow-list gets False.
    assert (
        client.get(
            "/api/v1/flags", headers={**hdr(founder), "X-Workspace-Id": str(other.id)}
        ).status_code
        == 403
    )
    r = client.get("/api/v1/flags", headers={**hdr(outsider), "X-Workspace-Id": str(other.id)})
    assert r.json()["data"]["flags"] == {}
    assert client.get("/api/v1/flags").status_code in (401, 422)


def test_a_deleted_workspace_on_the_allowlist_does_not_block_edits(client, db, monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr("app.api.v1.endpoints.super_admin.verify_step_up", lambda *a: None)
    sa, _ = mk_staff(db)
    gone = create_startup(db, owner=sa)
    live = create_startup(db, owner=sa)
    f = mk_flag(db, enabled=True, workspace_allowlist=[gone.id])
    gone.deleted_at = datetime.now(UTC)
    db.flush()
    r = client.patch(
        f"{S}/flags/{f.key}",
        json=body(sa, rollout_pct=10, workspace_allowlist=[str(gone.id)]),
        headers=hdr(sa),
    )
    assert r.status_code == 200, r.text  # resending the dead id unchanged is fine
    r = client.patch(
        f"{S}/flags/{f.key}",
        json=body(sa, workspace_allowlist=[str(gone.id), str(uuid.uuid4())]),
        headers=hdr(sa),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_WORKSPACE"
    r = client.patch(
        f"{S}/flags/{f.key}", json=body(sa, workspace_allowlist=[str(live.id)]), headers=hdr(sa)
    )
    assert r.status_code == 200
